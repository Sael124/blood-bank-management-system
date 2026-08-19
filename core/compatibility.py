"""ABO/Rh transfusion compatibility rules.

A mistake here is fatal for the patient, so the table is transcribed verbatim
from the assignment, the reverse direction is derived rather than retyped, and
both directions are asserted to agree in the unit tests.
"""

from __future__ import annotations

from .blood_types import BloodType

#: For each recipient blood type, the donor blood types that may be transfused
#: into that recipient. This is the direction that matters when dispensing.
CAN_RECEIVE_FROM: dict[BloodType, frozenset[BloodType]] = {
    BloodType.A_POSITIVE: frozenset(
        {BloodType.A_POSITIVE, BloodType.A_NEGATIVE, BloodType.O_POSITIVE, BloodType.O_NEGATIVE}
    ),
    BloodType.O_POSITIVE: frozenset({BloodType.O_POSITIVE, BloodType.O_NEGATIVE}),
    BloodType.B_POSITIVE: frozenset(
        {BloodType.B_POSITIVE, BloodType.B_NEGATIVE, BloodType.O_POSITIVE, BloodType.O_NEGATIVE}
    ),
    BloodType.AB_POSITIVE: frozenset(BloodType),
    BloodType.A_NEGATIVE: frozenset({BloodType.A_NEGATIVE, BloodType.O_NEGATIVE}),
    BloodType.O_NEGATIVE: frozenset({BloodType.O_NEGATIVE}),
    BloodType.B_NEGATIVE: frozenset({BloodType.B_NEGATIVE, BloodType.O_NEGATIVE}),
    BloodType.AB_NEGATIVE: frozenset(
        {BloodType.AB_NEGATIVE, BloodType.A_NEGATIVE, BloodType.B_NEGATIVE, BloodType.O_NEGATIVE}
    ),
}


def _derive_can_donate_to() -> dict[BloodType, frozenset[BloodType]]:
    """Invert CAN_RECEIVE_FROM so the two tables can never drift apart."""
    inverted: dict[BloodType, set[BloodType]] = {blood_type: set() for blood_type in BloodType}
    for recipient, donors in CAN_RECEIVE_FROM.items():
        for donor in donors:
            inverted[donor].add(recipient)
    return {donor: frozenset(recipients) for donor, recipients in inverted.items()}


#: For each donor blood type, the recipients it may be transfused into.
CAN_DONATE_TO: dict[BloodType, frozenset[BloodType]] = _derive_can_donate_to()


def is_compatible(donor: BloodType, recipient: BloodType) -> bool:
    """Return True when a unit from `donor` may be transfused into `recipient`."""
    return donor in CAN_RECEIVE_FROM[recipient]


def compatible_donors_for(recipient: BloodType) -> frozenset[BloodType]:
    """Return every donor blood type that is safe for this recipient."""
    return CAN_RECEIVE_FROM[recipient]
