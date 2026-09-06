"""Tests for the complete electronic copy of stored records.

21 CFR 11.10(b) asks for an accurate and complete copy. These tests therefore
check three things that a broken exporter would get wrong: every section is
present even when it is empty, a value that contains XML markup survives as
text instead of becoming a new element, and the stored codes stay in English
rather than being translated for the screen.
"""

from __future__ import annotations

from datetime import date, datetime
from xml.etree.ElementTree import fromstring

from core.models import ActivityLogEntry, ActivityOutcome
from core.records_copy import (
    RECORDS_COPY_VERSION,
    BloodUnitCopy,
    DispenseCopy,
    DonorCopy,
    RecordsSnapshot,
    build_records_xml,
)


def make_donor(**overrides) -> DonorCopy:
    donor = DonorCopy(
        donor_id="123456782",
        full_name="יעל כהן",
        blood_type="A+",
        registered_at=datetime(2026, 3, 14, 9, 30, 0),
    )
    return DonorCopy(**{**donor.__dict__, **overrides})


def make_unit(**overrides) -> BloodUnitCopy:
    unit = BloodUnitCopy(
        unit_id=41,
        donor_id="123456782",
        blood_type="A+",
        donation_date=date(2026, 3, 10),
        status="IN_STOCK",
        recorded_at=datetime(2026, 3, 14, 9, 31, 0),
        dispense_id=None,
    )
    return BloodUnitCopy(**{**unit.__dict__, **overrides})


def make_dispense(**overrides) -> DispenseCopy:
    dispense = DispenseCopy(
        dispense_id=7,
        mode="ROUTINE",
        requested_blood_type="AB-",
        units_requested=4,
        units_supplied=3,
        destination="חדרי ניתוח וטראומה",
        supplied_breakdown="A-×2, B-×1",
        created_at=datetime(2026, 3, 14, 10, 0, 0),
    )
    return DispenseCopy(**{**dispense.__dict__, **overrides})


def make_activity(**overrides) -> ActivityLogEntry:
    defaults = {
        "log_id": 1,
        "created_at": datetime(2026, 3, 14, 9, 30, 0),
        "action": "DONATION_INTAKE",
        "outcome": ActivityOutcome.SUCCESS,
        "details": "נקלטה מנת דם #41",
        "actor": "operator",
        "entity_type": "BLOOD_UNIT",
        "entity_id": "41",
        "operation": "CREATE",
        "old_value": "",
        "new_value": "A+",
        "reason": "",
        "created_at_utc": datetime(2026, 3, 14, 7, 30, 0),
        "utc_offset": "+02:00",
        "source_host": "WARD-PC-2",
        "previous_hash": "a" * 64,
        "record_hash": "b" * 64,
    }
    return ActivityLogEntry(**{**defaults, **overrides})


def make_snapshot(**overrides) -> RecordsSnapshot:
    snapshot = RecordsSnapshot(
        generated_at=datetime(2026, 3, 14, 11, 0, 0),
        donors=[make_donor()],
        blood_units=[make_unit()],
        dispenses=[make_dispense()],
        activity=[make_activity()],
    )
    return RecordsSnapshot(**{**snapshot.__dict__, **overrides})


def parse(document: str):
    assert document.startswith('<?xml version="1.0" encoding="UTF-8"?>')
    return fromstring(document)


def test_empty_snapshot_still_contains_every_section():
    """A bank with no data must still produce a complete, valid document."""
    root = parse(build_records_xml(make_snapshot(donors=[], blood_units=[], dispenses=[], activity=[])))

    assert root.tag == "becs-records"
    assert root.get("version") == RECORDS_COPY_VERSION
    assert root.get("total-records") == "0"
    assert root.find("donors").get("count") == "0"
    assert root.find("blood-units").get("count") == "0"
    assert root.find("dispenses").get("count") == "0"
    assert root.find("activity-log").get("count") == "0"
    assert root.find("donors/donor") is None
    assert root.find("activity-log/activity") is None


def test_a_full_snapshot_preserves_every_stored_field():
    root = parse(build_records_xml(make_snapshot()))

    assert root.get("total-records") == "4"

    donor = root.find("donors/donor")
    assert donor.findtext("donor-id") == "123456782"
    assert donor.findtext("full-name") == "יעל כהן"
    assert donor.findtext("blood-type") == "A+"
    assert donor.findtext("registered-at") == "2026-03-14 09:30:00"

    unit = root.find("blood-units/blood-unit")
    assert unit.findtext("unit-id") == "41"
    assert unit.findtext("status") == "IN_STOCK"
    assert unit.findtext("dispense-id") == ""

    dispense = root.find("dispenses/dispense")
    assert dispense.findtext("mode") == "ROUTINE"
    assert dispense.findtext("units-supplied") == "3"
    assert dispense.findtext("destination") == "חדרי ניתוח וטראומה"

    activity = root.find("activity-log/activity")
    assert activity.findtext("action") == "DONATION_INTAKE"
    assert activity.findtext("outcome") == "SUCCESS"
    assert activity.findtext("record-hash") == "b" * 64


def test_markup_in_a_name_is_escaped_and_stays_one_field():
    """A name containing < or & must not invent extra XML elements."""
    document = build_records_xml(
        make_snapshot(donors=[make_donor(full_name="אבי <כהן> & שות'")])
    )
    root = parse(document)

    assert root.findtext("donors/donor/full-name") == "אבי <כהן> & שות'"
    assert "<כהן>" not in document
    assert "&amp;" in document


def test_a_line_break_in_audit_details_survives_as_one_element():
    details = "שורה ראשונה\nשורה שנייה"
    root = parse(build_records_xml(make_snapshot(activity=[make_activity(details=details)])))

    assert root.findtext("activity-log/activity/details") == details


def test_dispensed_unit_keeps_its_dispense_link():
    root = parse(build_records_xml(make_snapshot(blood_units=[make_unit(status="DISPENSED", dispense_id=7)])))

    unit = root.find("blood-units/blood-unit")
    assert unit.findtext("status") == "DISPENSED"
    assert unit.findtext("dispense-id") == "7"


def test_stored_codes_are_not_translated():
    """The copy is of the record, not of the Hebrew screen."""
    document = build_records_xml(make_snapshot())

    assert "קליטת תרומה" not in document
    assert "DONATION_INTAKE" in document
    assert "IN_STOCK" in document
    assert "ROUTINE" in document
