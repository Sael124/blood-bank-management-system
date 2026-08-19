"""Creation of the database and its tables, executed once at startup.

Every statement is written to be idempotent, so running the application again
never destroys or duplicates existing data.
"""

from __future__ import annotations

import logging
import re

import pyodbc

from config import get_config
from core.blood_types import BloodType
from core.errors import DatabaseUnavailableError

from .connection import server_connection, transaction

logger = logging.getLogger(__name__)

#: A database name arrives from configuration and cannot be passed as a query
#: parameter to CREATE DATABASE, so it is whitelisted instead of escaped.
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")

_BLOOD_TYPE_LIST = ", ".join(f"'{blood_type.value}'" for blood_type in BloodType)

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
    """Create any missing table or index inside the application database."""
    with transaction() as connection:
        cursor = connection.cursor()
        for statement in _TABLE_STATEMENTS:
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
