"""Accurate electronic copies of every stored record, as 21 CFR 11.10(b) asks.

The FDA guidance on Part 11 names XML as one of the portable formats an
inspector can be given. One document holds every donor, unit, dispense and
audit line, so the copy is complete rather than a screen-sized preview, and
the values stay exactly as they were stored - not translated for display.

This module is pure on purpose: no database, no clock, no HTTP. The writer
builds a snapshot; this file only serialises it. That is what makes the copy
testable, and it is also why a comma, a quote or a Hebrew letter cannot
silently break the document.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from xml.etree.ElementTree import Element, SubElement, indent, tostring

from .models import ActivityLogEntry

#: Written into every copy so an older document can never be mistaken for a
#: newer layout if the element names ever change.
RECORDS_COPY_VERSION = "BECS-RECORDS-1"


@dataclass(frozen=True)
class DonorCopy:
    """One donor row, as stored."""

    donor_id: str
    full_name: str
    blood_type: str
    registered_at: datetime | None


@dataclass(frozen=True)
class BloodUnitCopy:
    """One blood unit row, as stored, including the dispense it is attached to."""

    unit_id: int
    donor_id: str
    blood_type: str
    donation_date: date | None
    status: str
    recorded_at: datetime | None
    dispense_id: int | None


@dataclass(frozen=True)
class DispenseCopy:
    """One dispense event row, as stored."""

    dispense_id: int
    mode: str
    requested_blood_type: str
    units_requested: int
    units_supplied: int
    destination: str
    supplied_breakdown: str
    created_at: datetime | None


@dataclass(frozen=True)
class RecordsSnapshot:
    """Everything the complete copy has to hold."""

    generated_at: datetime
    donors: list[DonorCopy]
    blood_units: list[BloodUnitCopy]
    dispenses: list[DispenseCopy]
    activity: list[ActivityLogEntry]

    @property
    def total_records(self) -> int:
        return (
            len(self.donors)
            + len(self.blood_units)
            + len(self.dispenses)
            + len(self.activity)
        )


def _stamp(value: datetime | date | None) -> str:
    """Render a stored timestamp the same way the audit CSV does."""
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.replace(microsecond=0).isoformat(sep=" ")
    return value.isoformat()


def _add(parent: Element, tag: str, value: object) -> None:
    """Append one text element. None becomes empty, never the word 'None'."""
    child = SubElement(parent, tag)
    child.text = "" if value is None else str(value)


def _add_donor(parent: Element, donor: DonorCopy) -> None:
    node = SubElement(parent, "donor")
    _add(node, "donor-id", donor.donor_id)
    _add(node, "full-name", donor.full_name)
    _add(node, "blood-type", donor.blood_type)
    _add(node, "registered-at", _stamp(donor.registered_at))


def _add_unit(parent: Element, unit: BloodUnitCopy) -> None:
    node = SubElement(parent, "blood-unit")
    _add(node, "unit-id", unit.unit_id)
    _add(node, "donor-id", unit.donor_id)
    _add(node, "blood-type", unit.blood_type)
    _add(node, "donation-date", _stamp(unit.donation_date))
    _add(node, "status", unit.status)
    _add(node, "recorded-at", _stamp(unit.recorded_at))
    _add(node, "dispense-id", unit.dispense_id if unit.dispense_id is not None else "")


def _add_dispense(parent: Element, dispense: DispenseCopy) -> None:
    node = SubElement(parent, "dispense")
    _add(node, "dispense-id", dispense.dispense_id)
    _add(node, "mode", dispense.mode)
    _add(node, "requested-blood-type", dispense.requested_blood_type)
    _add(node, "units-requested", dispense.units_requested)
    _add(node, "units-supplied", dispense.units_supplied)
    _add(node, "destination", dispense.destination)
    _add(node, "supplied-breakdown", dispense.supplied_breakdown)
    _add(node, "created-at", _stamp(dispense.created_at))


def _add_activity(parent: Element, entry: ActivityLogEntry) -> None:
    node = SubElement(parent, "activity")
    _add(node, "log-id", entry.log_id)
    _add(node, "created-at", _stamp(entry.created_at))
    _add(node, "created-at-utc", _stamp(entry.created_at_utc))
    _add(node, "utc-offset", entry.utc_offset)
    _add(node, "actor", entry.actor)
    _add(node, "source-host", entry.source_host)
    _add(node, "action", entry.action)
    _add(node, "outcome", entry.outcome.value)
    _add(node, "entity-type", entry.entity_type)
    _add(node, "entity-id", entry.entity_id)
    _add(node, "operation", entry.operation)
    _add(node, "old-value", entry.old_value)
    _add(node, "new-value", entry.new_value)
    _add(node, "reason", entry.reason)
    _add(node, "details", entry.details)
    _add(node, "previous-hash", entry.previous_hash)
    _add(node, "record-hash", entry.record_hash)


def build_records_xml(snapshot: RecordsSnapshot) -> str:
    """Serialise the snapshot as a UTF-8 XML document.

    ElementTree escapes markup characters, so a donor name or an audit detail
    that happens to contain ``<`` or ``&`` cannot break the document or invent
    extra elements.
    """
    root = Element(
        "becs-records",
        {
            "version": RECORDS_COPY_VERSION,
            "generated-at": _stamp(snapshot.generated_at),
            "total-records": str(snapshot.total_records),
        },
    )

    donors = SubElement(root, "donors", {"count": str(len(snapshot.donors))})
    for donor in snapshot.donors:
        _add_donor(donors, donor)

    units = SubElement(root, "blood-units", {"count": str(len(snapshot.blood_units))})
    for unit in snapshot.blood_units:
        _add_unit(units, unit)

    dispenses = SubElement(root, "dispenses", {"count": str(len(snapshot.dispenses))})
    for dispense in snapshot.dispenses:
        _add_dispense(dispenses, dispense)

    activity = SubElement(root, "activity-log", {"count": str(len(snapshot.activity))})
    for entry in snapshot.activity:
        _add_activity(activity, entry)

    # Indent in place so an inspector can read the file without a dedicated
    # XML tool, which is what "human readable form" asks for.
    indent(root, space="  ")
    body = tostring(root, encoding="unicode")
    return f'<?xml version="1.0" encoding="UTF-8"?>\n{body}\n'
