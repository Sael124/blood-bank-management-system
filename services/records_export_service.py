"""Complete electronic copies of every stored record.

The filtered audit CSV is a copy of the trail the operator is looking at.
21 CFR 11.10(b) also asks for a copy of the records themselves: donors, units,
dispenses and the whole trail, in a portable format. XML is one of the formats
the FDA guidance names, and it is what this service produces.

The copy is built from a single read-only connection so the four sections
describe the same instant. The export itself is then written to the trail,
after the document is closed, so the copy is of the records as they were
and not of the fact that they were copied.
"""

from __future__ import annotations

from datetime import datetime

import pyodbc

from app_logging.activity_log import ActivityAction, record_standalone
from core.models import ActivityOutcome, AuditEntity, AuditOperation
from core.records_copy import RecordsSnapshot, build_records_xml
from data import repositories
from data.connection import read_only_connection, wrap_driver_error


def collect_snapshot() -> RecordsSnapshot:
    """Read every stored record into one snapshot."""
    try:
        with read_only_connection() as connection:
            cursor = connection.cursor()
            return RecordsSnapshot(
                generated_at=datetime.now().replace(microsecond=0),
                donors=repositories.all_donors(cursor),
                blood_units=repositories.all_blood_units(cursor),
                dispenses=repositories.all_dispenses(cursor),
                activity=repositories.all_activity_log_in_order(cursor),
            )
    except pyodbc.Error as error:
        raise wrap_driver_error(error) from error


def export_xml() -> tuple[str, RecordsSnapshot]:
    """Produce the complete XML copy and record that it was taken.

    Returns:
        The XML document and the snapshot it was built from.
    """
    snapshot = collect_snapshot()
    document = build_records_xml(snapshot)

    record_standalone(
        ActivityAction.RECORDS_EXPORTED,
        ActivityOutcome.SUCCESS,
        (
            f"יוצא עותק מלא של רשומות המערכת בפורמט XML: "
            f"{len(snapshot.donors)} תורמים, "
            f"{len(snapshot.blood_units)} מנות, "
            f"{len(snapshot.dispenses)} ניפוקים, "
            f"{len(snapshot.activity)} רשומות תיעוד."
        ),
        entity=AuditEntity.RECORDS,
        operation=AuditOperation.READ,
        reason="עותק אלקטרוני מלא לפי 21 CFR 11.10(b).",
    )
    return document, snapshot
