"""Deciding which units to hand out for a request - the strategic core of BECS.

Dispensing policy, in priority order:

1. An exact type match is always preferred. It is the safest option and it does
   not consume any other type's stock.
2. Otherwise a compatible substitute is chosen, preferring the type that is most
   common in the population. Common types are the easiest to replenish, so
   spending them costs the bank the least.
3. O- is always the very last resort, even when it is not the rarest type on the
   shelf. It is the only type that can be transfused into an unidentified
   patient, which makes it the reserve that keeps a mass casualty event
   survivable. Sorting by prevalence alone would spend it too early: a recipient
   of AB- can receive A- (4%), O- (3%), B- (2%) and AB- (1%), and prevalence
   alone would reach for O- before B- and AB-.
"""

from __future__ import annotations

from dataclasses import dataclass

from .blood_types import UNIVERSAL_DONOR, BloodType, population_share
from .compatibility import compatible_donors_for

#: Priority tiers, lower is used first.
_TIER_EXACT_MATCH = 0
_TIER_COMPATIBLE_SUBSTITUTE = 1
_TIER_UNIVERSAL_DONOR_RESERVE = 2


@dataclass(frozen=True)
class Allocation:
    """A number of units taken from one specific blood type."""

    blood_type: BloodType
    units: int
    is_substitute: bool


@dataclass(frozen=True)
class DispensePlan:
    """What the bank is able to supply for a request, before it is committed.

    The plan is a recommendation: it is shown to the operator for approval and
    recomputed against a locked inventory at the moment of the actual dispense.
    """

    requested_blood_type: BloodType
    units_requested: int
    allocations: tuple[Allocation, ...]

    @property
    def units_supplied(self) -> int:
        return sum(allocation.units for allocation in self.allocations)

    @property
    def missing_units(self) -> int:
        return max(0, self.units_requested - self.units_supplied)

    @property
    def is_fully_fulfilled(self) -> bool:
        return self.missing_units == 0

    @property
    def is_empty(self) -> bool:
        return not self.allocations

    @property
    def uses_substitutes(self) -> bool:
        return any(allocation.is_substitute for allocation in self.allocations)

    @property
    def substitute_types(self) -> tuple[BloodType, ...]:
        return tuple(
            allocation.blood_type for allocation in self.allocations if allocation.is_substitute
        )

    @property
    def uses_universal_donor_reserve(self) -> bool:
        return any(
            allocation.blood_type is UNIVERSAL_DONOR and allocation.is_substitute
            for allocation in self.allocations
        )


def _priority_key(candidate: BloodType, recipient: BloodType, stock: dict[BloodType, int]):
    if candidate is recipient:
        tier = _TIER_EXACT_MATCH
    elif candidate is UNIVERSAL_DONOR:
        tier = _TIER_UNIVERSAL_DONOR_RESERVE
    else:
        tier = _TIER_COMPATIBLE_SUBSTITUTE
    # Negated so that "more common" and "more in stock" sort first, with the
    # label as a final tie-breaker to keep the order deterministic.
    return tier, -population_share(candidate), -stock.get(candidate, 0), candidate.value


def rank_donor_candidates(
    recipient: BloodType, stock: dict[BloodType, int]
) -> tuple[BloodType, ...]:
    """Return the compatible types that currently have stock, best choice first."""
    available = [
        candidate
        for candidate in compatible_donors_for(recipient)
        if stock.get(candidate, 0) > 0
    ]
    available.sort(key=lambda candidate: _priority_key(candidate, recipient, stock))
    return tuple(available)


def plan_routine_dispense(
    recipient: BloodType, units_requested: int, stock: dict[BloodType, int]
) -> DispensePlan:
    """Build the best plan for a routine request against the given inventory.

    Args:
        recipient: Blood type of the patient the units are requested for.
        units_requested: How many units the ward asked for (must be positive).
        stock: Units currently on the shelf per blood type.

    Returns:
        A plan that may be empty or partial when stock is insufficient. Reporting
        a shortage is the caller's responsibility; refusing to plan at all would
        hide the units that *are* available from the operator.
    """
    if units_requested <= 0:
        raise ValueError("units_requested must be a positive number")

    allocations: list[Allocation] = []
    remaining = units_requested

    for candidate in rank_donor_candidates(recipient, stock):
        if remaining == 0:
            break
        units_taken = min(remaining, stock.get(candidate, 0))
        if units_taken <= 0:
            continue
        allocations.append(
            Allocation(
                blood_type=candidate,
                units=units_taken,
                is_substitute=candidate is not recipient,
            )
        )
        remaining -= units_taken

    return DispensePlan(
        requested_blood_type=recipient,
        units_requested=units_requested,
        allocations=tuple(allocations),
    )


def plan_emergency_dispense(stock: dict[BloodType, int]) -> DispensePlan:
    """Plan a mass casualty dispense: every O- unit on the shelf.

    Only O- is dispensed because the wounded arrive untyped, and O- is the only
    type that is compatible with every possible recipient.
    """
    available_units = max(0, stock.get(UNIVERSAL_DONOR, 0))
    allocations = (
        (Allocation(blood_type=UNIVERSAL_DONOR, units=available_units, is_substitute=False),)
        if available_units > 0
        else ()
    )
    return DispensePlan(
        requested_blood_type=UNIVERSAL_DONOR,
        units_requested=available_units,
        allocations=allocations,
    )
