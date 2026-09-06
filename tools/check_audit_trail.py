"""Prove that the audit trail behaves as 21 CFR Part 11 requires.

The unit tests cover the hash chain as pure logic. This utility checks the parts
that only exist once a database is involved:

* a database left behind by the previous version of the system is upgraded in
  place, and the log lines it already held are still there and still readable;
* the trail records every event, including the refused ones, with the value that
  each change replaced;
* SQL Server itself refuses to modify or delete a recorded line;
* an edit made with that protection switched off is still detected afterwards.

It works on a throwaway database so it can never touch the real one, and it
drops it again at the end.

Run from the project root:
    python tools/check_audit_trail.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

#: Set before anything reads the configuration, so every connection made by this
#: script goes to the scratch database and not to the bank's own.
CHECK_DATABASE_NAME = "BloodBankAuditCheck"
os.environ["BECS_DB_NAME"] = CHECK_DATABASE_NAME

from app_logging.app_logger import configure_logging  # noqa: E402
from core.audit_chain import ChainFailure  # noqa: E402
from core.errors import BloodBankError  # noqa: E402
from data.connection import server_connection, transaction  # noqa: E402
from data.schema import ensure_database_exists, initialise_database  # noqa: E402
from services import audit_service, donation_service  # noqa: E402

_DONOR_ID = "123456782"

#: The activity_log as the previous version of the system created it, before any
#: of the Part 11 columns existed. Written out here rather than imported, so the
#: check keeps testing the upgrade even after the application's own DDL moves on.
_PREVIOUS_VERSION_ACTIVITY_LOG = """
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
"""

#: Log lines that the previous version is imagined to have written.
_LEGACY_LINES = (
    ("operator", "DONATION_INTAKE", "SUCCESS", "נקלטה מנת דם בגרסה הקודמת של המערכת"),
    ("operator", "ROUTINE_DISPENSE", "PARTIAL", "ניפוק חלקי שבוצע בגרסה הקודמת"),
    ("operator", "ROUTINE_DISPENSE", "REJECTED", "בקשה שנדחתה בגרסה הקודמת"),
)

#: Statements a database administrator would use to bypass the audit trail. They
#: are executed on purpose, against the scratch database, to show what the
#: system does about them.
_TAMPERING_ATTEMPTS = (
    "UPDATE dbo.activity_log SET details = 'tampered' WHERE log_id = 1",
    "DELETE FROM dbo.activity_log WHERE log_id = 1",
)

_failures: list[str] = []


def check(description: str, passed: bool, note: str = "") -> None:
    """Record and print the result of one check."""
    print(f"  [{'PASS' if passed else 'FAIL'}] {description}{f' - {note}' if note else ''}")
    if not passed:
        _failures.append(description)


def build_previous_version_database() -> None:
    """Create a database as the earlier version of the system left it."""
    ensure_database_exists()
    with transaction() as connection:
        cursor = connection.cursor()
        cursor.execute(_PREVIOUS_VERSION_ACTIVITY_LOG)
        for actor, action, outcome, details in _LEGACY_LINES:
            cursor.execute(
                """
                INSERT INTO dbo.activity_log (actor, action, outcome, details)
                VALUES (?, ?, ?, ?)
                """,
                actor,
                action,
                outcome,
                details,
            )


def check_upgrade_from_previous_version() -> None:
    """The upgrade must add the new columns without disturbing the old lines."""
    initialise_database()

    legacy = [
        entry
        for entry in audit_service.search(audit_service.AuditQuery())
        if entry.log_id <= len(_LEGACY_LINES)
    ]
    check(
        "lines written by the previous version are still there",
        len(legacy) == len(_LEGACY_LINES),
        f"{len(legacy)} of {len(_LEGACY_LINES)}",
    )
    check(
        "and still readable",
        {entry.details for entry in legacy} == {line[3] for line in _LEGACY_LINES},
    )
    check(
        "they are reported as unprotected rather than as tampered with",
        not any(entry.is_hash_protected for entry in legacy),
    )

    report = audit_service.verify_integrity()
    check(
        "a partly protected trail still verifies",
        report.is_intact and report.unprotected_records == len(_LEGACY_LINES),
        f"{report.unprotected_records} unprotected, intact={report.is_intact}",
    )


def seed_activity() -> None:
    """Produce one of every kind of auditable event."""
    first = donation_service.register_donation(
        raw_blood_type="A+",
        raw_donation_date="2026-01-05",
        raw_donor_id=_DONOR_ID,
        raw_full_name="יעל כהן",
    )
    check("an intake is accepted", first.unit_id > 0)

    # The same donor returns under a different name: the change is legitimate,
    # losing the previous name would not be.
    donation_service.register_donation(
        raw_blood_type="A+",
        raw_donation_date="2026-01-06",
        raw_donor_id=_DONOR_ID,
        raw_full_name="יעל לוי",
    )

    for description, arguments in (
        (
            "a contradicting blood type is refused",
            {"raw_blood_type": "O-", "raw_donor_id": _DONOR_ID, "raw_full_name": "יעל לוי"},
        ),
        (
            "an invalid identity number is refused",
            {"raw_blood_type": "A+", "raw_donor_id": "111111111", "raw_full_name": "דוד לוי"},
        ),
    ):
        try:
            donation_service.register_donation(raw_donation_date="2026-01-06", **arguments)
            check(description, False, "it was accepted")
        except BloodBankError:
            check(description, True)


def check_recorded_content() -> None:
    """Every event, including the refused ones, has to appear in the trail."""
    entries = [
        entry
        for entry in audit_service.search(audit_service.AuditQuery())
        if entry.log_id > len(_LEGACY_LINES)
    ]
    actions = [entry.action for entry in entries]
    print(f"\n{len(entries)} lines recorded: {', '.join(reversed(actions))}\n")

    for expected in (
        "DONOR_REGISTERED",
        "DONATION_INTAKE",
        "DONOR_NAME_UPDATED",
    ):
        check(f"{expected} is recorded", expected in actions)
    check(
        "refused attempts are recorded too",
        sum(1 for entry in entries if entry.outcome.value == "REJECTED") == 2,
    )

    name_change = next(entry for entry in entries if entry.action == "DONOR_NAME_UPDATED")
    check(
        "a change keeps the value it replaced",
        name_change.old_value == "יעל כהן" and name_change.new_value == "יעל לוי",
        f"{name_change.old_value!r} -> {name_change.new_value!r}",
    )
    check(
        "every line carries both timestamps and its origin",
        all(
            entry.created_at_utc and entry.utc_offset and entry.source_host
            for entry in entries
        ),
    )
    check(
        "every line written since the upgrade is hash protected",
        all(entry.is_hash_protected for entry in entries),
    )


def check_filters() -> None:
    """The filter has to narrow the trail without being fooled by its input."""
    by_action = audit_service.search(audit_service.parse_query({"action": "DONATION_INTAKE"}))
    check(
        "filtering by action returns only that action",
        by_action and all(entry.action == "DONATION_INTAKE" for entry in by_action),
    )

    try:
        audit_service.parse_query({"actor": "%"})
        check("a wildcard in the operator filter is refused", False, "it was accepted")
    except BloodBankError:
        check("a wildcard in the operator filter is refused", True)

    # An underscore is a legitimate character in a Windows account name and is
    # therefore allowed through validation, but it is also LIKE's single
    # character wildcard. Unescaped it would match every operator, so a search
    # for it must find only names that truly contain one.
    check(
        "a LIKE wildcard is searched for literally",
        audit_service.search(audit_service.parse_query({"actor": "_"})) == [],
    )

    try:
        audit_service.parse_query({"date_from": "2026-02-01", "date_to": "2026-01-01"})
        check("an inverted date range is refused", False, "it was accepted")
    except BloodBankError:
        check("an inverted date range is refused", True)


def check_append_only() -> None:
    """SQL Server itself must refuse to change or remove a recorded line."""
    for statement in _TAMPERING_ATTEMPTS:
        try:
            with transaction() as connection:
                connection.cursor().execute(statement)
            check(f"{statement.split()[0]} on the trail is refused", False, "it succeeded")
        except Exception:  # noqa: BLE001 - any refusal is the expected outcome
            check(f"{statement.split()[0]} on the trail is refused", True)


def check_tamper_evidence() -> None:
    """With the trigger disabled, tampering must still be detectable afterwards.

    Restoring the edited line word for word and verifying again matters as much
    as detecting the edit: it shows the verifier reports what the data actually
    says, rather than latching on to a failure once it has seen one.
    """
    check("the untouched chain verifies", audit_service.verify_integrity().is_intact)

    #: The first line that the hash chain protects, and therefore the first one
    #: an edit can be detected in.
    target = len(_LEGACY_LINES) + 1

    _run("DISABLE TRIGGER dbo.TR_activity_log_append_only ON dbo.activity_log")
    original_details = _scalar(f"SELECT details FROM dbo.activity_log WHERE log_id = {target}")

    _run(f"UPDATE dbo.activity_log SET details = 'tampered' WHERE log_id = {target}")
    edited = audit_service.verify_integrity()
    check(
        "an edited line is detected",
        edited.failure is ChainFailure.CONTENT_ALTERED and edited.broken_at_log_id == target,
        f"{edited.failure} at line {edited.broken_at_log_id}",
    )

    _run(
        f"UPDATE dbo.activity_log SET details = ? WHERE log_id = {target}",
        original_details,
    )
    check(
        "restoring the line exactly makes it verify again",
        audit_service.verify_integrity().is_intact,
    )

    _run(f"DELETE FROM dbo.activity_log WHERE log_id = {target}")
    removed = audit_service.verify_integrity()
    check(
        "a removed line is detected",
        removed.failure is ChainFailure.LINK_BROKEN,
        f"{removed.failure} at line {removed.broken_at_log_id}",
    )


def check_export() -> None:
    """The electronic copy has to hold every line and every column."""
    document, row_count = audit_service.export_csv(audit_service.AuditQuery())
    lines = document.splitlines()
    check(
        "the export holds a header and one line per record",
        len(lines) == row_count + 1,
        f"{row_count} records, {len(lines)} lines",
    )
    check("the export carries the hashes", "גיבוב הרשומה" in lines[0])


def _run(statement: str, *parameters: object) -> None:
    with transaction() as connection:
        connection.cursor().execute(statement, *parameters)


def _scalar(statement: str) -> object:
    with transaction() as connection:
        return connection.cursor().execute(statement).fetchone()[0]


def drop_check_database() -> None:
    """Remove the scratch database, so a rerun starts from nothing."""
    with server_connection() as connection:
        connection.cursor().execute(
            f"""
            IF DB_ID('{CHECK_DATABASE_NAME}') IS NOT NULL
            BEGIN
                ALTER DATABASE [{CHECK_DATABASE_NAME}] SET SINGLE_USER WITH ROLLBACK IMMEDIATE;
                DROP DATABASE [{CHECK_DATABASE_NAME}];
            END
            """
        )


def main() -> int:
    configure_logging()
    print(f"Checking the audit trail on the scratch database {CHECK_DATABASE_NAME}.\n")
    drop_check_database()
    build_previous_version_database()

    try:
        check_upgrade_from_previous_version()
        check("running the schema again changes nothing", initialise_database() is False)
        seed_activity()
        check_recorded_content()
        check_filters()
        print()
        check_append_only()
        check_tamper_evidence()
        check_export()
    finally:
        drop_check_database()

    if _failures:
        print(f"\n{len(_failures)} checks failed:")
        for description in _failures:
            print(f"  - {description}")
        return 1

    print("\nEvery check passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
