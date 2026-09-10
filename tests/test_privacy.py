"""De-identification of donor fields for roles that must not see PHI."""

from __future__ import annotations

from datetime import date, datetime

from core.blood_types import BloodType
from core.models import ActivityLogEntry, ActivityOutcome, DonationRecord, UnitStatus
from core.privacy import REDACTED_NAME, redact_activity, redact_donation, redact_identity_numbers

_RECORDED = datetime(2026, 9, 10, 10, 0, 0)


def _donation() -> DonationRecord:
    return DonationRecord(
        unit_id=17,
        donor_id="123456782",
        donor_name="ישראל ישראלי",
        blood_type=BloodType.O_NEGATIVE,
        donation_date=date(2026, 9, 1),
        recorded_at=_RECORDED,
        status=UnitStatus.IN_STOCK,
    )


def test_redact_donation_keeps_the_unit_and_drops_the_person():
    redacted = redact_donation(_donation())
    assert redacted.unit_id == 17
    assert redacted.blood_type is BloodType.O_NEGATIVE
    assert redacted.donor_name == REDACTED_NAME
    assert redacted.donor_id == ""


def test_identity_numbers_are_stripped_from_free_text():
    text = "תורם 123456782 נקלט, מנה 42 נשארה במלאי."
    assert "123456782" not in redact_identity_numbers(text)
    assert "42" in redact_identity_numbers(text)


def test_eight_digit_numbers_are_not_treated_as_identity_numbers():
    assert redact_identity_numbers("מנה 12345678") == "מנה 12345678"


def test_intake_audit_line_does_not_leak_a_donor_name():
    entry = ActivityLogEntry(
        log_id=1,
        created_at=_RECORDED,
        action="DONATION_INTAKE",
        outcome=ActivityOutcome.SUCCESS,
        details="נקלטה תרומה מתורם ישראל ישראלי, ת״ז 123456782.",
        actor="operator",
        entity_type="BLOOD_UNIT",
        entity_id="17",
        old_value="",
        new_value="ישראל ישראלי",
    )
    redacted = redact_activity(entry)
    assert "ישראל" not in redacted.details
    assert "123456782" not in redacted.details
    assert redacted.new_value == ""
    assert redacted.actor == "operator"


def test_dispense_audit_line_only_loses_embedded_identity_numbers():
    entry = ActivityLogEntry(
        log_id=2,
        created_at=_RECORDED,
        action="ROUTINE_DISPENSE",
        outcome=ActivityOutcome.SUCCESS,
        details="נופקו 2 מנות ליעד מחלקה פנימית. אזכור ת״ז 123456782.",
        actor="operator",
        entity_type="DISPENSE",
        entity_id="9",
    )
    redacted = redact_activity(entry)
    assert "מחלקה פנימית" in redacted.details
    assert "123456782" not in redacted.details
