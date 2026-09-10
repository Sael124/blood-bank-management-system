"""Creation of the database and its tables, executed once at startup.

Every statement is written to be idempotent, so running the application again
never destroys or duplicates existing data. The audit trail columns are added by
ALTER rather than being folded into CREATE TABLE, so that a database created by
the earlier version of the system is upgraded in place instead of having to be
rebuilt, and its existing log lines are preserved.
"""

from __future__ import annotations

import logging
import re

import pyodbc

from config import get_config
from core.audit_chain import (
    AUDIT_ENTITY_ID_LENGTH,
    AUDIT_HOST_LENGTH,
    AUDIT_REASON_LENGTH,
    AUDIT_VALUE_LENGTH,
    GENESIS_HASH,
    HASH_LENGTH,
)
from core.blood_types import BloodType
from core.errors import DatabaseUnavailableError
from core.models import AuditOperation
from core.roles import Role

from .connection import server_connection, transaction

logger = logging.getLogger(__name__)

#: A database name arrives from configuration and cannot be passed as a query
#: parameter to CREATE DATABASE, so it is whitelisted instead of escaped.
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")

_BLOOD_TYPE_LIST = ", ".join(f"'{blood_type.value}'" for blood_type in BloodType)
_OPERATION_LIST = ", ".join(f"'{operation.value}'" for operation in AuditOperation)
_ROLE_LIST = ", ".join(f"'{role.value}'" for role in Role)

_TABLE_STATEMENTS: tuple[str, ...] = (
    f"""
    IF OBJECT_ID('dbo.donors', 'U') IS NULL
    CREATE TABLE dbo.donors (
        donor_id      CHAR(9)       NOT NULL CONSTRAINT PK_donors PRIMARY KEY,
        full_name     NVARCHAR(120) NOT NULL,
        blood_type    VARCHAR(3)    NOT NULL
            CONSTRAINT CK_donors_blood_type CHECK (blood_type IN ({_BLOOD_TYPE_LIST})),
        registered_at DATETIME2(0)  NOT NULL
            CONSTRAINT DF_donors_registered_at DEFAULT SYSDATETIME()
    );
    """,
    f"""
    IF OBJECT_ID('dbo.dispenses', 'U') IS NULL
    CREATE TABLE dbo.dispenses (
        dispense_id          INT IDENTITY(1,1) NOT NULL
            CONSTRAINT PK_dispenses PRIMARY KEY,
        mode                 VARCHAR(10)   NOT NULL
            CONSTRAINT CK_dispenses_mode CHECK (mode IN ('ROUTINE', 'EMERGENCY')),
        requested_blood_type VARCHAR(3)    NULL
            CONSTRAINT CK_dispenses_requested_type
                CHECK (requested_blood_type IS NULL OR requested_blood_type IN ({_BLOOD_TYPE_LIST})),
        units_requested      INT           NOT NULL
            CONSTRAINT CK_dispenses_units_requested CHECK (units_requested >= 0),
        units_supplied       INT           NOT NULL
            CONSTRAINT CK_dispenses_units_supplied CHECK (units_supplied >= 0),
        destination          NVARCHAR(120) NOT NULL,
        supplied_breakdown   NVARCHAR(200) NOT NULL,
        created_at           DATETIME2(0)  NOT NULL
            CONSTRAINT DF_dispenses_created_at DEFAULT SYSDATETIME()
    );
    """,
    f"""
    IF OBJECT_ID('dbo.blood_units', 'U') IS NULL
    CREATE TABLE dbo.blood_units (
        unit_id       INT IDENTITY(1,1) NOT NULL
            CONSTRAINT PK_blood_units PRIMARY KEY,
        donor_id      CHAR(9)      NOT NULL
            CONSTRAINT FK_blood_units_donors FOREIGN KEY REFERENCES dbo.donors (donor_id),
        blood_type    VARCHAR(3)   NOT NULL
            CONSTRAINT CK_blood_units_blood_type CHECK (blood_type IN ({_BLOOD_TYPE_LIST})),
        donation_date DATE         NOT NULL,
        status        VARCHAR(10)  NOT NULL
            CONSTRAINT CK_blood_units_status CHECK (status IN ('IN_STOCK', 'DISPENSED')),
        recorded_at   DATETIME2(0) NOT NULL
            CONSTRAINT DF_blood_units_recorded_at DEFAULT SYSDATETIME(),
        dispense_id   INT          NULL
            CONSTRAINT FK_blood_units_dispenses FOREIGN KEY REFERENCES dbo.dispenses (dispense_id),
        -- A unit is either on the shelf with no dispense, or dispensed and
        -- traceable to exactly one dispense event. Nothing in between.
        CONSTRAINT CK_blood_units_dispense_link CHECK (
            (status = 'IN_STOCK'  AND dispense_id IS NULL) OR
            (status = 'DISPENSED' AND dispense_id IS NOT NULL)
        )
    );
    """,
    """
    IF OBJECT_ID('dbo.activity_log', 'U') IS NULL
    CREATE TABLE dbo.activity_log (
        log_id     BIGINT IDENTITY(1,1) NOT NULL
            CONSTRAINT PK_activity_log PRIMARY KEY,
        created_at DATETIME2(0)   NOT NULL
            CONSTRAINT DF_activity_log_created_at DEFAULT SYSDATETIME(),
        actor      NVARCHAR(60)   NOT NULL,
        action     VARCHAR(40)    NOT NULL,
        outcome    VARCHAR(10)    NOT NULL
            CONSTRAINT CK_activity_log_outcome
                CHECK (outcome IN ('SUCCESS', 'PARTIAL', 'REJECTED', 'FAILURE')),
        details    NVARCHAR(1000) NOT NULL
    );
    """,
    """
    IF NOT EXISTS (
        SELECT 1 FROM sys.indexes
        WHERE name = 'IX_blood_units_stock_lookup'
          AND object_id = OBJECT_ID('dbo.blood_units')
    )
    CREATE INDEX IX_blood_units_stock_lookup
        ON dbo.blood_units (status, blood_type, donation_date);
    """,
    f"""
    IF OBJECT_ID('dbo.app_users', 'U') IS NULL
    CREATE TABLE dbo.app_users (
        username      VARCHAR(40)    NOT NULL
            CONSTRAINT PK_app_users PRIMARY KEY,
        password_hash NVARCHAR(255)  NOT NULL,
        role          VARCHAR(20)    NOT NULL
            CONSTRAINT CK_app_users_role CHECK (role IN ({_ROLE_LIST})),
        display_name  NVARCHAR(120)  NOT NULL,
        is_active     BIT            NOT NULL
            CONSTRAINT DF_app_users_is_active DEFAULT 1,
        created_at    DATETIME2(0)   NOT NULL
            CONSTRAINT DF_app_users_created_at DEFAULT SYSDATETIME(),
        created_by    NVARCHAR(40)   NULL
    );
    """,
    f"""
    IF OBJECT_ID('dbo.audit_chain_head', 'U') IS NULL
    CREATE TABLE dbo.audit_chain_head (
        -- One row, forever: the CHECK constraint makes a second chain head
        -- impossible, so there can never be an argument about which one is real.
        chain_id   TINYINT NOT NULL
            CONSTRAINT PK_audit_chain_head PRIMARY KEY
            CONSTRAINT CK_audit_chain_head_single CHECK (chain_id = 1),
        head_hash  VARCHAR({HASH_LENGTH}) NOT NULL,
        updated_at DATETIME2(0) NOT NULL
            CONSTRAINT DF_audit_chain_head_updated_at DEFAULT SYSDATETIME()
    );
    """,
    f"""
    IF NOT EXISTS (SELECT 1 FROM dbo.audit_chain_head WHERE chain_id = 1)
    INSERT INTO dbo.audit_chain_head (chain_id, head_hash) VALUES (1, '{GENESIS_HASH}');
    """,
)

#: Columns added to the audit trail for 21 CFR Part 11. They are nullable
#: because lines written before this upgrade cannot be given a value after the
#: fact - inventing one would be exactly the kind of retroactive edit the audit
#: trail exists to prevent.
_AUDIT_TRAIL_COLUMNS: tuple[tuple[str, str], ...] = (
    ("entity_type", "VARCHAR(20) NULL"),
    ("entity_id", f"VARCHAR({AUDIT_ENTITY_ID_LENGTH}) NULL"),
    ("operation", "VARCHAR(10) NULL"),
    ("old_value", f"NVARCHAR({AUDIT_VALUE_LENGTH}) NULL"),
    ("new_value", f"NVARCHAR({AUDIT_VALUE_LENGTH}) NULL"),
    ("reason", f"NVARCHAR({AUDIT_REASON_LENGTH}) NULL"),
    ("created_at_utc", "DATETIME2(0) NULL"),
    ("utc_offset", "VARCHAR(6) NULL"),
    ("source_host", f"NVARCHAR({AUDIT_HOST_LENGTH}) NULL"),
    ("previous_hash", f"VARCHAR({HASH_LENGTH}) NULL"),
    ("record_hash", f"VARCHAR({HASH_LENGTH}) NULL"),
)

_AUDIT_TRAIL_STATEMENTS: tuple[str, ...] = tuple(
    f"""
    IF COL_LENGTH('dbo.activity_log', '{column}') IS NULL
    ALTER TABLE dbo.activity_log ADD {column} {definition};
    """
    for column, definition in _AUDIT_TRAIL_COLUMNS
) + (
    f"""
    IF NOT EXISTS (
        SELECT 1 FROM sys.check_constraints WHERE name = 'CK_activity_log_operation'
    )
    ALTER TABLE dbo.activity_log ADD CONSTRAINT CK_activity_log_operation
        CHECK (operation IS NULL OR operation IN ({_OPERATION_LIST}));
    """,
    """
    IF NOT EXISTS (
        SELECT 1 FROM sys.indexes
        WHERE name = 'IX_activity_log_search'
          AND object_id = OBJECT_ID('dbo.activity_log')
    )
    CREATE INDEX IX_activity_log_search
        ON dbo.activity_log (created_at DESC, action, outcome);
    """,
    # An inspector filters the trail by who acted far more often than by
    # anything else, and the operator name is not selective enough to be found
    # through the timestamp index alone.
    """
    IF NOT EXISTS (
        SELECT 1 FROM sys.indexes
        WHERE name = 'IX_activity_log_actor'
          AND object_id = OBJECT_ID('dbo.activity_log')
    )
    CREATE INDEX IX_activity_log_actor ON dbo.activity_log (actor, created_at DESC);
    """,
    # The audit trail is append only. An INSTEAD OF trigger refuses the change
    # rather than undoing it, so no partial edit is ever applied. It is written
    # through EXEC because CREATE TRIGGER has to be the first statement of its
    # batch and therefore cannot sit inside an IF.
    #
    # This stops an accidental or malicious UPDATE through any client, but a
    # database administrator can disable the trigger; that is precisely why each
    # line also carries a hash of the line before it.
    """
    IF OBJECT_ID('dbo.TR_activity_log_append_only', 'TR') IS NULL
    EXEC('
        CREATE TRIGGER dbo.TR_activity_log_append_only
        ON dbo.activity_log
        INSTEAD OF UPDATE, DELETE
        AS
        BEGIN
            SET NOCOUNT ON;
            THROW 50001, ''The audit trail is append only: its rows cannot be modified or deleted.'', 1;
        END');
    """,
)


def _validated_database_name() -> str:
    name = get_config().database.database
    if not _SAFE_IDENTIFIER.match(name):
        raise ValueError(
            f"Invalid database name {name!r}: use letters, digits and underscores only."
        )
    return name


def ensure_database_exists() -> bool:
    """Create the application database if it is missing.

    Returns:
        True when the database was created by this call.
    """
    database_name = _validated_database_name()
    with server_connection() as connection:
        cursor = connection.cursor()
        cursor.execute("SELECT DB_ID(?)", database_name)
        already_exists = cursor.fetchone()[0] is not None
        if already_exists:
            return False
        # The name is whitelisted above, so bracket quoting is safe here.
        cursor.execute(f"CREATE DATABASE [{database_name}]")
        logger.info("Created database %s", database_name)
        return True


def ensure_tables_exist() -> None:
    """Create any missing table, column, index or trigger.

    The audit trail statements run after the table statements because they alter
    a table the first group creates, and they run on every startup so that a
    database left behind by an earlier version is upgraded in place.
    """
    with transaction() as connection:
        cursor = connection.cursor()
        for statement in _TABLE_STATEMENTS + _AUDIT_TRAIL_STATEMENTS:
            cursor.execute(statement)


def initialise_database() -> bool:
    """Bring the database to the state the application expects.

    Safe to call on every startup.

    Returns:
        True when the database itself was created by this call.
    """
    try:
        created = ensure_database_exists()
        ensure_tables_exist()
    except pyodbc.Error as error:
        logger.error("Database initialisation failed: %s", error)
        raise DatabaseUnavailableError(
            "יצירת בסיס הנתונים נכשלה. יש לוודא שלמשתמש יש הרשאות ליצירת בסיס נתונים ב-SQL Server."
        ) from error

    logger.info(
        "Database ready at %s (%s)",
        get_config().database.describe(),
        "created now" if created else "already existed",
    )
    return created
