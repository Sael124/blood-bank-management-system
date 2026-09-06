"""All SQL used by the application.

Every query is parameterised, so operator input is never concatenated into SQL
and injection is structurally impossible. Each function receives an open cursor
so that the caller controls the transaction boundary.
"""

from __future__ import annotations

from datetime import date

import pyodbc

from core.audit_chain import AuditRecordContent
from core.blood_types import BloodType, parse_blood_type, population_share
from core.errors import DataIntegrityError
from core.models import (
    ActivityLogEntry,
    ActivityOutcome,
    DispenseMode,
    DispenseRecord,
    Donor,
    DonationRecord,
    InventoryRow,
    UnitStatus,
)
from core.blood_types import DISPLAY_ORDER, UNIVERSAL_DONOR
from core.records_copy import BloodUnitCopy, DispenseCopy, DonorCopy


# --------------------------------------------------------------------------- #
# Donors
# --------------------------------------------------------------------------- #

def find_donor(cursor: pyodbc.Cursor, donor_id: str) -> Donor | None:
    """Return the stored donor, or None when this identity number is new."""
    cursor.execute(
        "SELECT donor_id, full_name, blood_type FROM dbo.donors WHERE donor_id = ?",
        donor_id,
    )
    row = cursor.fetchone()
    if row is None:
        return None
    return Donor(
        donor_id=row.donor_id.strip(),
        full_name=row.full_name,
        blood_type=parse_blood_type(row.blood_type),
    )


def insert_donor(cursor: pyodbc.Cursor, donor: Donor) -> None:
    """Store a first time donor."""
    cursor.execute(
        "INSERT INTO dbo.donors (donor_id, full_name, blood_type) VALUES (?, ?, ?)",
        donor.donor_id,
        donor.full_name,
        donor.blood_type.value,
    )


def update_donor_name(cursor: pyodbc.Cursor, donor_id: str, full_name: str) -> None:
    """Refresh a returning donor's name (people legally change names)."""
    cursor.execute(
        "UPDATE dbo.donors SET full_name = ? WHERE donor_id = ?",
        full_name,
        donor_id,
    )


# --------------------------------------------------------------------------- #
# Blood units
# --------------------------------------------------------------------------- #

def insert_blood_unit(
    cursor: pyodbc.Cursor, donor_id: str, blood_type: BloodType, donation_date: date
) -> int:
    """Store one donated unit as available stock and return its identifier."""
    cursor.execute(
        # OUTPUT returns the generated key as the statement's own result set,
        # which keeps the insert and the key retrieval in one atomic statement.
        """
        INSERT INTO dbo.blood_units (donor_id, blood_type, donation_date, status)
        OUTPUT INSERTED.unit_id
        VALUES (?, ?, ?, ?)
        """,
        donor_id,
        blood_type.value,
        donation_date,
        UnitStatus.IN_STOCK.value,
    )
    return int(cursor.fetchone()[0])


def stock_by_blood_type(cursor: pyodbc.Cursor) -> dict[BloodType, int]:
    """Return the number of available units for each of the eight blood types."""
    counts: dict[BloodType, int] = {blood_type: 0 for blood_type in BloodType}
    cursor.execute(
        """
        SELECT blood_type, COUNT(*) AS units
        FROM dbo.blood_units
        WHERE status = ?
        GROUP BY blood_type
        """,
        UnitStatus.IN_STOCK.value,
    )
    for row in cursor.fetchall():
        counts[parse_blood_type(row.blood_type)] = int(row.units)
    return counts


def inventory_report(cursor: pyodbc.Cursor) -> list[InventoryRow]:
    """Return the inventory ordered from the most common blood type downwards."""
    counts = stock_by_blood_type(cursor)
    return [
        InventoryRow(
            blood_type=blood_type,
            units_in_stock=counts[blood_type],
            population_share_percent=population_share(blood_type),
            is_universal_donor=blood_type is UNIVERSAL_DONOR,
        )
        for blood_type in DISPLAY_ORDER
    ]


def lock_units_for_dispense(
    cursor: pyodbc.Cursor, blood_type: BloodType, units: int
) -> list[int]:
    """Reserve the oldest available units of one type inside the transaction.

    UPDLOCK holds the rows until the transaction ends, so two operators
    dispensing at the same moment cannot hand out the same physical unit. The
    oldest donation is taken first, which is standard blood bank practice.
    """
    if units <= 0:
        return []
    cursor.execute(
        """
        SELECT TOP (?) unit_id
        FROM dbo.blood_units WITH (UPDLOCK, ROWLOCK)
        WHERE status = ? AND blood_type = ?
        ORDER BY donation_date ASC, unit_id ASC
        """,
        units,
        UnitStatus.IN_STOCK.value,
        blood_type.value,
    )
    return [int(row.unit_id) for row in cursor.fetchall()]


def attach_units_to_dispense(
    cursor: pyodbc.Cursor, unit_ids: list[int], dispense_id: int
) -> int:
    """Mark the reserved units as dispensed and return how many rows changed.

    The status check in the WHERE clause is a second safety net: a unit that was
    taken by another transaction in the meantime will simply not be updated, and
    the caller detects the mismatch through the returned count.
    """
    if not unit_ids:
        return 0
    placeholders = ", ".join("?" for _ in unit_ids)
    cursor.execute(
        f"""
        UPDATE dbo.blood_units
        SET status = ?, dispense_id = ?
        WHERE unit_id IN ({placeholders}) AND status = ?
        """,
        UnitStatus.DISPENSED.value,
        dispense_id,
        *unit_ids,
        UnitStatus.IN_STOCK.value,
    )
    return cursor.rowcount


# --------------------------------------------------------------------------- #
# Dispenses
# --------------------------------------------------------------------------- #

def insert_dispense(
    cursor: pyodbc.Cursor,
    mode: DispenseMode,
    requested_blood_type: BloodType | None,
    units_requested: int,
    units_supplied: int,
    destination: str,
    supplied_breakdown: str,
) -> int:
    """Store the dispense event header and return its identifier."""
    cursor.execute(
        """
        INSERT INTO dbo.dispenses (
            mode, requested_blood_type, units_requested,
            units_supplied, destination, supplied_breakdown
        )
        OUTPUT INSERTED.dispense_id
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        mode.value,
        requested_blood_type.value if requested_blood_type else None,
        units_requested,
        units_supplied,
        destination,
        supplied_breakdown,
    )
    return int(cursor.fetchone()[0])


# --------------------------------------------------------------------------- #
# Reports
# --------------------------------------------------------------------------- #

def recent_donations(cursor: pyodbc.Cursor, limit: int = 50) -> list[DonationRecord]:
    """Return the latest intake records together with the donor's name."""
    cursor.execute(
        """
        SELECT TOP (?)
            unit.unit_id, unit.donor_id, donor.full_name, unit.blood_type,
            unit.donation_date, unit.recorded_at, unit.status
        FROM dbo.blood_units AS unit
        INNER JOIN dbo.donors AS donor ON donor.donor_id = unit.donor_id
        ORDER BY unit.unit_id DESC
        """,
        limit,
    )
    return [
        DonationRecord(
            unit_id=int(row.unit_id),
            donor_id=row.donor_id.strip(),
            donor_name=row.full_name,
            blood_type=parse_blood_type(row.blood_type),
            donation_date=row.donation_date,
            recorded_at=row.recorded_at,
            status=UnitStatus(row.status),
        )
        for row in cursor.fetchall()
    ]


def recent_dispenses(cursor: pyodbc.Cursor, limit: int = 50) -> list[DispenseRecord]:
    """Return the latest dispense events, newest first."""
    cursor.execute(
        """
        SELECT TOP (?)
            dispense_id, mode, requested_blood_type, units_requested,
            units_supplied, destination, created_at, supplied_breakdown
        FROM dbo.dispenses
        ORDER BY dispense_id DESC
        """,
        limit,
    )
    return [
        DispenseRecord(
            dispense_id=int(row.dispense_id),
            mode=DispenseMode(row.mode),
            requested_blood_type=(
                parse_blood_type(row.requested_blood_type) if row.requested_blood_type else None
            ),
            units_requested=int(row.units_requested),
            units_supplied=int(row.units_supplied),
            destination=row.destination,
            created_at=row.created_at,
            supplied_breakdown=row.supplied_breakdown,
        )
        for row in cursor.fetchall()
    ]


# --------------------------------------------------------------------------- #
# Complete copies of records
# --------------------------------------------------------------------------- #

def all_donors(cursor: pyodbc.Cursor) -> list[DonorCopy]:
    """Return every donor, oldest registration first."""
    cursor.execute(
        """
        SELECT donor_id, full_name, blood_type, registered_at
        FROM dbo.donors
        ORDER BY registered_at ASC, donor_id ASC
        """
    )
    return [
        DonorCopy(
            donor_id=row.donor_id.strip(),
            full_name=row.full_name,
            blood_type=row.blood_type,
            registered_at=row.registered_at,
        )
        for row in cursor.fetchall()
    ]


def all_blood_units(cursor: pyodbc.Cursor) -> list[BloodUnitCopy]:
    """Return every blood unit, including those already dispensed."""
    cursor.execute(
        """
        SELECT unit_id, donor_id, blood_type, donation_date, status,
               recorded_at, dispense_id
        FROM dbo.blood_units
        ORDER BY unit_id ASC
        """
    )
    return [
        BloodUnitCopy(
            unit_id=int(row.unit_id),
            donor_id=row.donor_id.strip(),
            blood_type=row.blood_type,
            donation_date=row.donation_date,
            status=row.status,
            recorded_at=row.recorded_at,
            dispense_id=int(row.dispense_id) if row.dispense_id is not None else None,
        )
        for row in cursor.fetchall()
    ]


def all_dispenses(cursor: pyodbc.Cursor) -> list[DispenseCopy]:
    """Return every dispense event, oldest first."""
    cursor.execute(
        """
        SELECT dispense_id, mode, requested_blood_type, units_requested,
               units_supplied, destination, supplied_breakdown, created_at
        FROM dbo.dispenses
        ORDER BY dispense_id ASC
        """
    )
    return [
        DispenseCopy(
            dispense_id=int(row.dispense_id),
            mode=row.mode,
            requested_blood_type=row.requested_blood_type or "",
            units_requested=int(row.units_requested),
            units_supplied=int(row.units_supplied),
            destination=row.destination,
            supplied_breakdown=row.supplied_breakdown,
            created_at=row.created_at,
        )
        for row in cursor.fetchall()
    ]


# --------------------------------------------------------------------------- #
# Audit log
# --------------------------------------------------------------------------- #

#: Every audit query selects the same columns in the same order, so the row to
#: entry mapping below has exactly one definition to stay in step with.
_ACTIVITY_LOG_COLUMNS = """
    log_id, created_at, actor, action, outcome, details,
    entity_type, entity_id, operation, old_value, new_value, reason,
    created_at_utc, utc_offset, source_host, previous_hash, record_hash
"""


def _text(value: str | None) -> str:
    """Read a nullable text column as a string.

    Audit lines written before the Part 11 upgrade have no value in the new
    columns, and the empty string is what the hash chain treats as "absent".
    """
    return value or ""


def _to_activity_log_entry(row: pyodbc.Row) -> ActivityLogEntry:
    return ActivityLogEntry(
        log_id=int(row.log_id),
        created_at=row.created_at,
        action=row.action,
        outcome=ActivityOutcome(row.outcome),
        details=row.details,
        actor=row.actor,
        entity_type=_text(row.entity_type),
        entity_id=_text(row.entity_id),
        operation=_text(row.operation),
        old_value=_text(row.old_value),
        new_value=_text(row.new_value),
        reason=_text(row.reason),
        created_at_utc=row.created_at_utc,
        utc_offset=_text(row.utc_offset),
        source_host=_text(row.source_host),
        previous_hash=_text(row.previous_hash),
        record_hash=_text(row.record_hash),
    )


def lock_audit_chain_head(cursor: pyodbc.Cursor) -> str:
    """Return the current head of the hash chain, locked until the commit.

    The update lock is what serialises audit writers. Two operators acting at
    the same instant would otherwise read the same head and produce two lines
    claiming the same predecessor, which would fork the chain and make it
    unverifiable. Blocking the second writer for the few milliseconds the first
    one needs is a trade the audit trail is worth.
    """
    cursor.execute(
        """
        SELECT head_hash
        FROM dbo.audit_chain_head WITH (UPDLOCK, ROWLOCK)
        WHERE chain_id = 1
        """
    )
    row = cursor.fetchone()
    if row is None:
        raise DataIntegrityError(
            "שורת הבקרה של שרשרת התיעוד חסרה בבסיס הנתונים. "
            "יש להפעיל את המערכת מחדש כדי לאתחל אותה לפני ביצוע פעולות."
        )
    return row.head_hash


def set_audit_chain_head(cursor: pyodbc.Cursor, head_hash: str) -> None:
    """Advance the head of the hash chain to the line just written."""
    cursor.execute(
        """
        UPDATE dbo.audit_chain_head
        SET head_hash = ?, updated_at = SYSDATETIME()
        WHERE chain_id = 1
        """,
        head_hash,
    )


def read_audit_chain_head(cursor: pyodbc.Cursor) -> str:
    """Return the head of the hash chain without locking, for verification."""
    cursor.execute("SELECT head_hash FROM dbo.audit_chain_head WHERE chain_id = 1")
    row = cursor.fetchone()
    return row.head_hash if row is not None else ""


def insert_activity_log(
    cursor: pyodbc.Cursor,
    content: AuditRecordContent,
    previous_hash: str,
    record_hash: str,
) -> None:
    """Append one line to the audit trail, hashes included.

    The timestamps are supplied by the caller rather than by a column default,
    because the hash is computed over the values that are about to be stored: a
    value generated by the server afterwards could never be reproduced.
    """
    cursor.execute(
        """
        INSERT INTO dbo.activity_log (
            created_at, actor, action, outcome, details,
            entity_type, entity_id, operation, old_value, new_value, reason,
            created_at_utc, utc_offset, source_host, previous_hash, record_hash
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        content.created_at,
        content.actor,
        content.action,
        content.outcome,
        content.details,
        content.entity_type,
        content.entity_id,
        content.operation,
        content.old_value,
        content.new_value,
        content.reason,
        content.created_at_utc,
        content.utc_offset,
        content.source_host,
        previous_hash,
        record_hash,
    )


def recent_activity_log(cursor: pyodbc.Cursor, limit: int = 100) -> list[ActivityLogEntry]:
    """Return the newest audit trail lines."""
    cursor.execute(
        f"""
        SELECT TOP (?) {_ACTIVITY_LOG_COLUMNS}
        FROM dbo.activity_log
        ORDER BY log_id DESC
        """,
        limit,
    )
    return [_to_activity_log_entry(row) for row in cursor.fetchall()]


def _like_pattern(value: str) -> str:
    """Turn operator text into a LIKE pattern that matches it literally.

    Without escaping, a "%" typed into the search box would match everything and
    a "[" would be read as a character class, so the filter would quietly answer
    a different question than the one that was asked.
    """
    escaped = (
        value.replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
        .replace("[", "\\[")
    )
    return f"%{escaped}%"


def search_activity_log(
    cursor: pyodbc.Cursor,
    limit: int,
    date_from: date | None = None,
    date_to: date | None = None,
    actor: str = "",
    action: str = "",
    outcome: str = "",
) -> list[ActivityLogEntry]:
    """Return audit lines matching the filter, newest first.

    Every condition is a bound parameter; only the fixed condition text is
    assembled here, so no operator input reaches the statement itself.
    """
    conditions: list[str] = []
    parameters: list[object] = [limit]

    if date_from is not None:
        conditions.append("created_at >= ?")
        parameters.append(date_from)
    if date_to is not None:
        # The column holds a time as well, so an inclusive "up to" has to reach
        # the start of the following day rather than midnight of this one.
        conditions.append("created_at < DATEADD(day, 1, ?)")
        parameters.append(date_to)
    if actor:
        conditions.append("actor LIKE ? ESCAPE '\\'")
        parameters.append(_like_pattern(actor))
    if action:
        conditions.append("action = ?")
        parameters.append(action)
    if outcome:
        conditions.append("outcome = ?")
        parameters.append(outcome)

    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    cursor.execute(
        f"""
        SELECT TOP (?) {_ACTIVITY_LOG_COLUMNS}
        FROM dbo.activity_log
        {where_clause}
        ORDER BY log_id DESC
        """,
        *parameters,
    )
    return [_to_activity_log_entry(row) for row in cursor.fetchall()]


def all_activity_log_in_order(cursor: pyodbc.Cursor) -> list[ActivityLogEntry]:
    """Return the entire audit trail in writing order, for chain verification.

    The whole trail is read because a chain can only be verified from its start:
    checking a window of it would leave the rest unexamined, which is where an
    edit would be hidden.
    """
    cursor.execute(
        f"""
        SELECT {_ACTIVITY_LOG_COLUMNS}
        FROM dbo.activity_log
        ORDER BY log_id ASC
        """
    )
    return [_to_activity_log_entry(row) for row in cursor.fetchall()]
