"""Input validation - the system's first line of defence.

Every value that arrives from the browser passes through this module before it
reaches the domain logic or the database. Because a wrong blood type or a wrong
donor identity can kill a patient, validation rejects anything suspicious rather
than trying to guess what the operator meant.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta

from .blood_types import BloodType, parse_blood_type
from .errors import ValidationError

#: A single request larger than this is almost certainly a typo. Israel's entire
#: daily national consumption is far below it, so the bound is safe.
MAX_UNITS_PER_REQUEST = 500

#: Israeli identity numbers are nine digits including the check digit.
ISRAELI_ID_LENGTH = 9

MIN_NAME_LENGTH = 2
MAX_NAME_LENGTH = 120

#: Hebrew or Latin letters only, plus the separators that appear in real names.
#: Digits and markup characters are rejected outright, which removes injection
#: payloads at the entry point instead of relying on escaping alone.
_NAME_PATTERN = re.compile(r"^[A-Za-z\u0590-\u05FF][A-Za-z\u0590-\u05FF ,.'\-]*$")

MAX_DESTINATION_LENGTH = 120
DEFAULT_DESTINATION = "חדרי ניתוח וטראומה"

#: A ward name may contain digits ("חדר ניתוח 3"), so it is validated separately
#: from a person's name, while still refusing markup characters.
_DESTINATION_PATTERN = re.compile(r"^[A-Za-z0-9\u0590-\u05FF ,.'\-/()]+$")

#: Donations older than this are treated as a data entry mistake.
_MAX_DONATION_AGE = timedelta(days=365 * 5)

_SUPPORTED_DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d.%m.%Y")


def validate_blood_type(raw_value: str | None) -> BloodType:
    """Return the BloodType for operator input, or raise ValidationError."""
    if not raw_value or not raw_value.strip():
        raise ValidationError("חובה לבחור סוג דם.", field="blood_type")
    try:
        return parse_blood_type(raw_value)
    except KeyError:
        supported = ", ".join(blood_type.value for blood_type in BloodType)
        raise ValidationError(
            f"סוג דם לא חוקי. הסוגים הנתמכים הם: {supported}.", field="blood_type"
        ) from None


def _has_valid_israeli_check_digit(digits: str) -> bool:
    """Verify the official Israeli identity number check digit.

    Each digit is multiplied alternately by 1 and 2; products of two digits are
    reduced by 9 (equivalent to summing their digits), and the total must be a
    multiple of ten.
    """
    total = 0
    for position, character in enumerate(digits):
        product = int(character) * (1 if position % 2 == 0 else 2)
        total += product if product < 10 else product - 9
    return total % 10 == 0


def validate_donor_id(raw_value: str | None) -> str:
    """Normalise and verify an Israeli identity number.

    Shorter inputs are zero padded on the left, which is how identity numbers are
    officially written. The check digit is verified so a mistyped digit cannot
    silently attach a donation to the wrong person.
    """
    if raw_value is None or not raw_value.strip():
        raise ValidationError("חובה להזין מספר תעודת זהות.", field="donor_id")

    candidate = raw_value.strip().replace("-", "").replace(" ", "")
    if not candidate.isdigit():
        raise ValidationError("מספר תעודת זהות יכול להכיל ספרות בלבד.", field="donor_id")
    if len(candidate) > ISRAELI_ID_LENGTH:
        raise ValidationError(
            f"מספר תעודת זהות אינו יכול להיות ארוך מ-{ISRAELI_ID_LENGTH} ספרות.",
            field="donor_id",
        )

    padded = candidate.zfill(ISRAELI_ID_LENGTH)
    if padded == "0" * ISRAELI_ID_LENGTH:
        raise ValidationError("מספר תעודת זהות אינו יכול להיות אפסים בלבד.", field="donor_id")
    if not _has_valid_israeli_check_digit(padded):
        raise ValidationError(
            "מספר תעודת הזהות אינו תקין - ספרת הביקורת אינה מתאימה. נא לבדוק את המספר.",
            field="donor_id",
        )
    return padded


def validate_full_name(raw_value: str | None) -> str:
    """Collapse whitespace and verify the name contains only name characters."""
    if raw_value is None or not raw_value.strip():
        raise ValidationError("חובה להזין שם מלא.", field="full_name")

    name = " ".join(raw_value.split())
    if len(name) < MIN_NAME_LENGTH:
        raise ValidationError(
            f"שם מלא חייב להכיל לפחות {MIN_NAME_LENGTH} תווים.", field="full_name"
        )
    if len(name) > MAX_NAME_LENGTH:
        raise ValidationError(
            f"שם מלא אינו יכול להיות ארוך מ-{MAX_NAME_LENGTH} תווים.", field="full_name"
        )
    if not _NAME_PATTERN.match(name):
        raise ValidationError(
            "שם מלא יכול להכיל אותיות בעברית או באנגלית, רווחים ומקפים בלבד.",
            field="full_name",
        )
    return name


def _parse_date(text: str) -> date | None:
    """Try every accepted date format and return the first successful parse."""
    for date_format in _SUPPORTED_DATE_FORMATS:
        try:
            return datetime.strptime(text, date_format).date()
        except ValueError:
            continue
    return None


def validate_donation_date(raw_value: str | None, today: date | None = None) -> date:
    """Parse a donation date and reject impossible values.

    A future date would mean a donation that has not happened yet, and a very old
    date is almost always a typo in the year.
    """
    if raw_value is None or not raw_value.strip():
        raise ValidationError("חובה להזין תאריך תרומה.", field="donation_date")

    reference_day = today or date.today()
    parsed = _parse_date(raw_value.strip())

    if parsed is None:
        raise ValidationError(
            "תאריך תרומה אינו בפורמט תקין. הפורמט הנדרש הוא YYYY-MM-DD.",
            field="donation_date",
        )
    if parsed > reference_day:
        raise ValidationError("תאריך התרומה אינו יכול להיות בעתיד.", field="donation_date")
    if parsed < reference_day - _MAX_DONATION_AGE:
        raise ValidationError(
            "תאריך התרומה רחוק מדי בעבר. נא לבדוק את השנה שהוזנה.", field="donation_date"
        )
    return parsed


def validate_destination(raw_value: str | None) -> str:
    """Validate the requesting ward, falling back to a generic destination."""
    if raw_value is None or not raw_value.strip():
        return DEFAULT_DESTINATION

    destination = " ".join(raw_value.split())
    if len(destination) > MAX_DESTINATION_LENGTH:
        raise ValidationError(
            f"שם היעד אינו יכול להיות ארוך מ-{MAX_DESTINATION_LENGTH} תווים.",
            field="destination",
        )
    if not _DESTINATION_PATTERN.match(destination):
        raise ValidationError("שם היעד מכיל תווים שאינם מורשים.", field="destination")
    return destination


def validate_unit_count(raw_value: str | int | None) -> int:
    """Parse the number of requested units and keep it inside sane bounds."""
    if raw_value is None or (isinstance(raw_value, str) and not raw_value.strip()):
        raise ValidationError("חובה להזין מספר מנות דם.", field="units")

    try:
        units = int(str(raw_value).strip())
    except ValueError:
        raise ValidationError("מספר מנות הדם חייב להיות מספר שלם.", field="units") from None

    if units <= 0:
        raise ValidationError("מספר מנות הדם חייב להיות גדול מאפס.", field="units")
    if units > MAX_UNITS_PER_REQUEST:
        raise ValidationError(
            f"לא ניתן לבקש יותר מ-{MAX_UNITS_PER_REQUEST} מנות בבקשה אחת.", field="units"
        )
    return units
