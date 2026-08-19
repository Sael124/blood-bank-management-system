"""Tests for the demonstration seeding helpers.

The seeding tool is not part of the clinical flow, but a broken distribution or
an invalid identity number would silently produce a demo bank that the real
validation layer rejects, so both are pinned here.
"""

from __future__ import annotations

import pytest

from core.blood_types import DISPLAY_ORDER, BloodType
from core.validation import validate_donor_id
from tools.seed_demo_data import israeli_id_from_prefix, units_per_blood_type


@pytest.mark.parametrize("total_units", [1, 7, 40, 100, 137, 999])
def test_distribution_uses_every_requested_unit(total_units: int) -> None:
    assert sum(units_per_blood_type(total_units).values()) == total_units


def test_distribution_follows_the_population_shares() -> None:
    """A round hundred maps one unit per percent, which is easy to verify by eye."""
    assert units_per_blood_type(100) == {
        BloodType.A_POSITIVE: 34,
        BloodType.O_POSITIVE: 32,
        BloodType.B_POSITIVE: 17,
        BloodType.AB_POSITIVE: 7,
        BloodType.A_NEGATIVE: 4,
        BloodType.O_NEGATIVE: 3,
        BloodType.B_NEGATIVE: 2,
        BloodType.AB_NEGATIVE: 1,
    }


def test_common_types_never_get_less_than_rare_types() -> None:
    counts = units_per_blood_type(50)
    ordered = [counts[blood_type] for blood_type in DISPLAY_ORDER]
    assert ordered == sorted(ordered, reverse=True)


@pytest.mark.parametrize("total_units", [0, -5])
def test_distribution_rejects_a_non_positive_total(total_units: int) -> None:
    with pytest.raises(ValueError):
        units_per_blood_type(total_units)


#: The tool numbers its donors from ten million upwards, so an all zero identity
#: number - which the validation layer refuses - can never be generated.
@pytest.mark.parametrize(
    "prefix", [10_000_000, 10_000_001, 10_003_042, 10_007_999, 99_999_999]
)
def test_generated_identity_numbers_pass_the_real_validation(prefix: int) -> None:
    """The seeded donors must survive the same checksum as a typed donor."""
    assert validate_donor_id(israeli_id_from_prefix(prefix))
