"""Read only reporting: inventory, recent activity and the audit trail."""

from __future__ import annotations

from dataclasses import dataclass

import pyodbc

from core.blood_types import UNIVERSAL_DONOR
from core.models import ActivityLogEntry, DispenseRecord, DonationRecord, InventoryRow
from core.privacy import redact_activities, redact_donations
from data import repositories
from data.connection import read_only_connection, wrap_driver_error

#: Upper bound on the rows each report fetches. The screen shows a short preview
#: and expands on demand, so these caps only guard against an unbounded query on
#: a bank that has been running for months.
ACTIVITY_LOG_LIMIT = 200
RECENT_RECORDS_LIMIT = 200


@dataclass(frozen=True)
class InventoryOverview:
    """Everything the inventory and log screen needs, fetched in one round trip."""

    rows: list[InventoryRow]
    donations: list[DonationRecord]
    dispenses: list[DispenseRecord]
    activity: list[ActivityLogEntry]

    @property
    def total_units(self) -> int:
        return sum(row.units_in_stock for row in self.rows)

    @property
    def universal_donor_units(self) -> int:
        """Size of the emergency reserve, the number that matters most."""
        return next(
            (row.units_in_stock for row in self.rows if row.blood_type is UNIVERSAL_DONOR),
            0,
        )

    @property
    def empty_blood_types(self) -> list[InventoryRow]:
        return [row for row in self.rows if row.units_in_stock == 0]


def get_inventory_rows() -> list[InventoryRow]:
    """Return stock per blood type, most common type first."""
    try:
        with read_only_connection() as connection:
            return repositories.inventory_report(connection.cursor())
    except pyodbc.Error as error:
        raise wrap_driver_error(error) from error


def get_overview(*, hide_phi: bool = False) -> InventoryOverview:
    """Collect the whole reporting screen using a single database connection.

    Args:
        hide_phi: When True, donor names and identity numbers are stripped so a
            research student sees only de-identified aggregates.
    """
    try:
        with read_only_connection() as connection:
            cursor = connection.cursor()
            overview = InventoryOverview(
                rows=repositories.inventory_report(cursor),
                donations=repositories.recent_donations(cursor, RECENT_RECORDS_LIMIT),
                dispenses=repositories.recent_dispenses(cursor, RECENT_RECORDS_LIMIT),
                activity=repositories.recent_activity_log(cursor, ACTIVITY_LOG_LIMIT),
            )
    except pyodbc.Error as error:
        raise wrap_driver_error(error) from error

    if hide_phi:
        return InventoryOverview(
            rows=overview.rows,
            donations=redact_donations(overview.donations),
            dispenses=overview.dispenses,
            activity=redact_activities(overview.activity),
        )
    return overview
