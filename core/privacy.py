"""De-identification of donor fields for roles that must not see PHI.

HIPAA covers information that identifies the patient. The same inventory counts
and blood types, without names or identity numbers, are not restricted in the
same way. This module is the single place that strips those identifiers, so a
template cannot forget one column.
"""

from __future__ import annotations

import re
from dataclasses import replace

from core.models import ActivityLogEntry, DonationRecord

#: Shown in place of a donor's legal name.
REDACTED_NAME = "מוסתר"

#: Israeli identity numbers are nine digits. Replacing every run of nine digits
#: is what stops a name-free sentence from still identifying the person.
_IDENTITY_NUMBER = re.compile(r"\d{9}")

_PHI_ACTIONS = frozenset(
    {
        "DONATION_INTAKE",
        "DONOR_REGISTERED",
        "DONOR_NAME_UPDATED",
    }
)


def redact_identity_numbers(text: str) -> str:
    """Replace every Israeli identity number in free text with a placeholder."""
    return _IDENTITY_NUMBER.sub("*********", text or "")


def redact_donation(record: DonationRecord) -> DonationRecord:
    """Keep the unit, drop the person."""
    return replace(record, donor_id="", donor_name=REDACTED_NAME)


def redact_activity(entry: ActivityLogEntry) -> ActivityLogEntry:
    """Keep the fact that an action happened, drop anything that names a donor.

    Unit identifiers and staff usernames stay: they do not identify the patient.
    Names, identity numbers and the before/after values of a name change go.
    """
    if entry.entity_type == "DONOR" or entry.action in _PHI_ACTIONS:
        return replace(
            entry,
            details="פרטים מזהים של תורם הוסתרו בהתאם ל-HIPAA.",
            old_value="",
            new_value="",
            entity_id="",
            reason="",
        )
    return replace(
        entry,
        details=redact_identity_numbers(entry.details),
        old_value=redact_identity_numbers(entry.old_value),
        new_value=redact_identity_numbers(entry.new_value),
        entity_id=redact_identity_numbers(entry.entity_id),
        reason=redact_identity_numbers(entry.reason),
    )


def redact_donations(records: list[DonationRecord]) -> list[DonationRecord]:
    return [redact_donation(record) for record in records]


def redact_activities(entries: list[ActivityLogEntry]) -> list[ActivityLogEntry]:
    return [redact_activity(entry) for entry in entries]
