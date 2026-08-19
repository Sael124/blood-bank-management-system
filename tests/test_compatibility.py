"""Tests for the transfusion compatibility table.

The table is the one thing in this system that can kill a patient, so it is
verified against an independent rule rather than against a copy of itself: a
transfusion is safe exactly when the donor carries no antigen that the recipient
lacks.
"""

from __future__ import annotations

import itertools

from core.blood_types import POPULATION_SHARE_PERCENT, UNIVERSAL_DONOR, BloodType
from core.compatibility import CAN_DONATE_TO, CAN_RECEIVE_FROM, is_compatible

_ABO_ANTIGENS = {"A": {"A"}, "B": {"B"}, "AB": {"A", "B"}, "O": set()}


def antigens_of(blood_type: BloodType) -> set[str]:
    """Return the antigens present on the red cells of this blood type."""
    label = blood_type.value
    antigens = set(_ABO_ANTIGENS[label[:-1]])
    if label.endswith("+"):
        antigens.add("D")
    return antigens


def test_compatibility_matches_the_antigen_rule():
    for donor, recipient in itertools.product(BloodType, BloodType):
        expected = antigens_of(donor) <= antigens_of(recipient)
        assert is_compatible(donor, recipient) is expected, f"{donor} -> {recipient}"


def test_every_blood_type_can_receive_from_itself():
    for blood_type in BloodType:
        assert is_compatible(blood_type, blood_type)


def test_o_negative_is_the_universal_donor():
    assert set(CAN_DONATE_TO[UNIVERSAL_DONOR]) == set(BloodType)


def test_ab_positive_is_the_universal_recipient():
    assert set(CAN_RECEIVE_FROM[BloodType.AB_POSITIVE]) == set(BloodType)


def test_o_negative_can_only_receive_o_negative():
    assert set(CAN_RECEIVE_FROM[BloodType.O_NEGATIVE]) == {BloodType.O_NEGATIVE}


def test_rh_negative_recipients_never_receive_rh_positive_blood():
    for recipient in BloodType:
        if recipient.value.endswith("-"):
            for donor in CAN_RECEIVE_FROM[recipient]:
                assert donor.value.endswith("-"), f"{donor} must not reach {recipient}"


def test_the_two_direction_tables_agree():
    for donor, recipient in itertools.product(BloodType, BloodType):
        assert (donor in CAN_RECEIVE_FROM[recipient]) == (recipient in CAN_DONATE_TO[donor])


def test_population_distribution_covers_all_types_and_sums_to_one_hundred():
    assert set(POPULATION_SHARE_PERCENT) == set(BloodType)
    assert round(sum(POPULATION_SHARE_PERCENT.values()), 6) == 100.0
