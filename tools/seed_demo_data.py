"""Fill the bank with demonstration donations.

Useful for a live demo and for checking the dispensing strategy without typing
dozens of donations by hand.

Run from the project root:
    python tools/seed_demo_data.py          # the default number of units
    python tools/seed_demo_data.py 100      # an explicit number of units
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app_logging.app_logger import configure_logging  # noqa: E402
from core.blood_types import DISPLAY_ORDER, BloodType, population_share  # noqa: E402
from core.errors import BloodBankError  # noqa: E402
from data.schema import initialise_database  # noqa: E402
from services import donation_service  # noqa: E402

DEFAULT_TOTAL_UNITS = 100

#: A demo database, not a stress test: a higher number would take minutes and
#: bury the reporting screen without teaching anything new.
MAX_TOTAL_UNITS = 2_000

_FIRST_NAMES = ("יעל", "דוד", "נועה", "אבי", "רון", "מיכל", "תמר", "עומר", "שירה", "איתי")
_LAST_NAMES = ("כהן", "לוי", "מזרחי", "פרץ", "ביטון", "אברהם", "דהן", "אזולאי")

#: Keeps every generated identity number inside its own blood type block, so a
#: rerun maps a donor to the same blood type instead of colliding with itself.
_SERIAL_BASE = 10_000_000
_SERIALS_PER_BLOOD_TYPE = 1_000


def israeli_id_from_prefix(prefix: int) -> str:
    """Append the official check digit to an eight digit prefix."""
    body = str(prefix).zfill(8)
    total = 0
    for position, character in enumerate(body):
        product = int(character) * (1 if position % 2 == 0 else 2)
        total += product if product < 10 else product - 9
    return body + str((10 - total % 10) % 10)


def units_per_blood_type(total_units: int) -> dict[BloodType, int]:
    """Split the requested total across blood types by population share.

    Reuses the distribution already defined in the domain layer, so the demo
    inventory always mirrors the real mix the dispensing algorithm expects.
    """
    if total_units < 1:
        raise ValueError("מספר המנות לזריעה חייב להיות חיובי.")

    exact_shares = {
        blood_type: total_units * population_share(blood_type) / 100
        for blood_type in DISPLAY_ORDER
    }
    counts = {blood_type: int(share) for blood_type, share in exact_shares.items()}

    # Largest remainder method: the units lost to truncation go to the types that
    # were rounded down the most, so the total is exact and the mix stays faithful.
    missing_units = total_units - sum(counts.values())
    by_largest_remainder = sorted(
        DISPLAY_ORDER, key=lambda blood_type: exact_shares[blood_type] - counts[blood_type], reverse=True
    )
    for blood_type in by_largest_remainder[:missing_units]:
        counts[blood_type] += 1

    return counts


def _requested_total(arguments: list[str]) -> int:
    """Read the unit count from the command line, refusing unusable values."""
    if not arguments:
        return DEFAULT_TOTAL_UNITS

    try:
        total_units = int(arguments[0])
    except ValueError:
        raise SystemExit("שימוש: python tools/seed_demo_data.py [מספר מנות]") from None

    if not 1 <= total_units <= MAX_TOTAL_UNITS:
        raise SystemExit(f"מספר המנות חייב להיות בין 1 ל-{MAX_TOTAL_UNITS}.")
    return total_units


def seed(total_units: int = DEFAULT_TOTAL_UNITS) -> None:
    configure_logging()
    initialise_database()

    plan = units_per_blood_type(total_units)
    today = date.today()
    created = 0
    skipped = 0

    print(f"זריעת {total_units} מנות דם לפי שכיחות סוגי הדם באוכלוסייה:\n")

    for blood_type, units in plan.items():
        for unit_number in range(units):
            serial = (
                _SERIAL_BASE
                + DISPLAY_ORDER.index(blood_type) * _SERIALS_PER_BLOOD_TYPE
                + unit_number
            )
            donor_id = israeli_id_from_prefix(serial)
            full_name = (
                f"{_FIRST_NAMES[unit_number % len(_FIRST_NAMES)]} "
                f"{_LAST_NAMES[(DISPLAY_ORDER.index(blood_type) * 3 + unit_number) % len(_LAST_NAMES)]}"
            )
            try:
                donation_service.register_donation(
                    raw_blood_type=blood_type.value,
                    # Spreads the donations over recent days instead of stacking
                    # them all on today, so the reports look like real history.
                    raw_donation_date=(today - timedelta(days=unit_number % 30)).isoformat(),
                    raw_donor_id=donor_id,
                    raw_full_name=full_name,
                )
                created += 1
            except BloodBankError as error:
                # A rerun hits donors that already exist with another type.
                print(f"דילוג על {donor_id}: {error}")
                skipped += 1

        print(f"  {blood_type.value:>4}: {units} מנות")

    print(f"\nנוצרו {created} מנות דם. דילוגים: {skipped}.")


if __name__ == "__main__":
    seed(_requested_total(sys.argv[1:]))
