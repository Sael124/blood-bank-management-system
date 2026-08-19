"""Tests for the dispensing strategy."""

from __future__ import annotations

import itertools

import pytest

from core.allocation import (
    plan_emergency_dispense,
    plan_routine_dispense,
    rank_donor_candidates,
)
from core.blood_types import UNIVERSAL_DONOR, BloodType
from core.compatibility import is_compatible

A_POS = BloodType.A_POSITIVE
A_NEG = BloodType.A_NEGATIVE
B_POS = BloodType.B_POSITIVE
B_NEG = BloodType.B_NEGATIVE
AB_POS = BloodType.AB_POSITIVE
AB_NEG = BloodType.AB_NEGATIVE
O_POS = BloodType.O_POSITIVE
O_NEG = BloodType.O_NEGATIVE


def stock(**units: int) -> dict[BloodType, int]:
    """Build an inventory from keyword arguments such as stock(A_POSITIVE=3)."""
    inventory = {blood_type: 0 for blood_type in BloodType}
    for name, count in units.items():
        inventory[BloodType[name]] = count
    return inventory


def test_exact_match_is_used_before_any_substitute():
    plan = plan_routine_dispense(A_POS, 2, stock(A_POSITIVE=5, O_POSITIVE=5))
    assert [(a.blood_type, a.units) for a in plan.allocations] == [(A_POS, 2)]
    assert plan.is_fully_fulfilled
    assert not plan.uses_substitutes


def test_substitute_prefers_the_more_common_blood_type():
    # A+ is unavailable, so the choice is between O+ (32%) and A- (4%).
    plan = plan_routine_dispense(A_POS, 2, stock(O_POSITIVE=5, A_NEGATIVE=5))
    assert plan.allocations[0].blood_type is O_POS
    assert plan.uses_substitutes


def test_o_negative_is_kept_for_last_even_when_it_is_not_the_rarest():
    # An AB- recipient may receive A- (4%), O- (3%), B- (2%) and AB- (1%).
    # Prevalence alone would place O- second; the reserve rule pushes it last.
    order = rank_donor_candidates(
        AB_NEG, stock(A_NEGATIVE=1, B_NEGATIVE=1, AB_NEGATIVE=1, O_NEGATIVE=1)
    )
    assert order[-1] is UNIVERSAL_DONOR
    assert order == (AB_NEG, A_NEG, B_NEG, O_NEG)


def test_exact_match_wins_even_when_it_is_the_universal_donor():
    order = rank_donor_candidates(O_NEG, stock(O_NEGATIVE=4))
    assert order == (O_NEG,)


def test_universal_donor_is_used_only_after_every_other_option():
    plan = plan_routine_dispense(A_POS, 10, stock(A_POSITIVE=2, O_POSITIVE=3, A_NEGATIVE=1, O_NEGATIVE=9))
    supplied = [(a.blood_type, a.units) for a in plan.allocations]
    assert supplied == [(A_POS, 2), (O_POS, 3), (A_NEG, 1), (O_NEG, 4)]
    assert plan.uses_universal_donor_reserve


def test_partial_supply_is_reported_and_never_exceeds_stock():
    plan = plan_routine_dispense(B_NEG, 5, stock(B_NEGATIVE=2))
    assert plan.units_supplied == 2
    assert plan.missing_units == 3
    assert not plan.is_fully_fulfilled


def test_plan_is_empty_when_no_compatible_unit_exists():
    # An O- patient can only receive O-, and there is none in stock.
    plan = plan_routine_dispense(O_NEG, 1, stock(A_POSITIVE=10, O_POSITIVE=10))
    assert plan.is_empty
    assert plan.units_supplied == 0


def test_incompatible_stock_is_never_allocated():
    for recipient in BloodType:
        plan = plan_routine_dispense(
            recipient, 8, {blood_type: 1 for blood_type in BloodType}
        )
        for allocation in plan.allocations:
            assert is_compatible(allocation.blood_type, recipient)


def test_allocation_never_takes_more_than_the_available_units():
    inventory = stock(A_POSITIVE=1, O_POSITIVE=2, A_NEGATIVE=1, O_NEGATIVE=1)
    plan = plan_routine_dispense(A_POS, 50, inventory)
    for allocation in plan.allocations:
        assert allocation.units <= inventory[allocation.blood_type]
    assert plan.units_supplied == 5


def test_requesting_zero_or_negative_units_is_a_programming_error():
    for invalid_amount in (0, -1):
        with pytest.raises(ValueError):
            plan_routine_dispense(A_POS, invalid_amount, stock(A_POSITIVE=1))


def test_emergency_dispense_releases_the_entire_o_negative_stock():
    plan = plan_emergency_dispense(stock(O_NEGATIVE=7, A_POSITIVE=50))
    assert [(a.blood_type, a.units) for a in plan.allocations] == [(O_NEG, 7)]
    assert plan.is_fully_fulfilled


def test_emergency_dispense_is_empty_without_o_negative_units():
    plan = plan_emergency_dispense(stock(A_POSITIVE=50, O_POSITIVE=50))
    assert plan.is_empty


def test_only_available_types_are_offered_as_candidates():
    for recipient, available in itertools.product(BloodType, BloodType):
        candidates = rank_donor_candidates(recipient, {available: 3})
        assert all(candidate is available for candidate in candidates)
