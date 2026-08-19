"""The eight non-rare ABO/Rh blood types and their prevalence in Israel."""

from __future__ import annotations

from enum import Enum


class BloodType(Enum):
    """An ABO/Rh blood type.

    The value is the clinical label ("A+", "O-", ...) so it can be stored in the
    database and rendered in the UI without any translation table.
    """

    A_POSITIVE = "A+"
    A_NEGATIVE = "A-"
    B_POSITIVE = "B+"
    B_NEGATIVE = "B-"
    AB_POSITIVE = "AB+"
    AB_NEGATIVE = "AB-"
    O_POSITIVE = "O+"
    O_NEGATIVE = "O-"

    def __str__(self) -> str:
        return self.value


#: The single blood type that any patient can safely receive, which is why it is
#: the only type dispensable in a mass casualty event where there is no time to
#: type the wounded. Its scarcity is what makes it a strategic reserve.
UNIVERSAL_DONOR = BloodType.O_NEGATIVE

#: Share of the Israeli population carrying each blood type, in percent.
#: Source: the ABO/Rh distribution table supplied with the assignment.
POPULATION_SHARE_PERCENT: dict[BloodType, float] = {
    BloodType.A_POSITIVE: 34.0,
    BloodType.O_POSITIVE: 32.0,
    BloodType.B_POSITIVE: 17.0,
    BloodType.AB_POSITIVE: 7.0,
    BloodType.A_NEGATIVE: 4.0,
    BloodType.O_NEGATIVE: 3.0,
    BloodType.B_NEGATIVE: 2.0,
    BloodType.AB_NEGATIVE: 1.0,
}

#: Display order: most common first, which is also the order used in the
#: inventory screen so the operator reads the important rows at the top.
DISPLAY_ORDER: tuple[BloodType, ...] = tuple(
    sorted(BloodType, key=lambda blood_type: -POPULATION_SHARE_PERCENT[blood_type])
)


def population_share(blood_type: BloodType) -> float:
    """Return how common the blood type is in the population, in percent."""
    return POPULATION_SHARE_PERCENT[blood_type]


def parse_blood_type(raw_value: str) -> BloodType:
    """Convert operator input such as "o-" or " AB+ " into a BloodType.

    Raises:
        KeyError: if the text is not one of the eight supported labels. Callers
            that face the operator should use core.validation instead, which
            turns this into a readable ValidationError.
    """
    normalised = raw_value.strip().upper().replace(" ", "")
    for blood_type in BloodType:
        if blood_type.value == normalised:
            return blood_type
    raise KeyError(raw_value)
