"""All SQL used by the application.

Every query is parameterised, so operator input is never concatenated into SQL
and injection is structurally impossible. Each function receives an open cursor
so that the caller controls the transaction boundary.
"""

from __future__ import annotations

from datetime import date

import pyodbc

from core.blood_types import BloodType, parse_blood_type, population_share
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
# Audit log
# --------------------------------------------------------------------------- #

def insert_activity_log(
    cursor: pyodbc.Cursor,
    actor: str,
    action: str,
    outcome: ActivityOutcome,
    details: str,
) -> None:
    """Append one line to the audit trail."""
    cursor.execute(
        """
        INSERT INTO dbo.activity_log (actor, action, outcome, details)
        VALUES (?, ?, ?, ?)
        """,
        actor,
        action,
        outcome.value,
        details,
    )


def recent_activity_log(cursor: pyodbc.Cursor, limit: int = 100) -> list[ActivityLogEntry]:
    """Return the newest audit trail lines."""
    cursor.execute(
        """
        SELECT TOP (?) log_id, created_at, actor, action, outcome, details
        FROM dbo.activity_log
        ORDER BY log_id DESC
        """,
        limit,
    )
    return [
        ActivityLogEntry(
            log_id=int(row.log_id),
            created_at=row.created_at,
            action=row.action,
            outcome=ActivityOutcome(row.outcome),
            details=row.details,
            actor=row.actor,
        )
        for row in cursor.fetchall()
    ]
