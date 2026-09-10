"""Tests for input validation, including the Israeli identity check digit."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from core.blood_types import BloodType
from core.errors import ValidationError
from core.roles import Role
from core.validation import (
    DEFAULT_DESTINATION,
    MAX_UNITS_PER_REQUEST,
    validate_blood_type,
    validate_destination,
    validate_display_name,
    validate_donation_date,
    validate_donor_id,
    validate_full_name,
    validate_password,
    validate_role,
    validate_unit_count,
    validate_username,
)

TODAY = date(2026, 8, 19)


# ------------------------------------------------------------------ blood type

@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("A+", BloodType.A_POSITIVE),
        (" o- ", BloodType.O_NEGATIVE),
        ("ab+", BloodType.AB_POSITIVE),
    ],
)
def test_blood_type_accepts_case_and_whitespace_variations(raw, expected):
    assert validate_blood_type(raw) is expected


@pytest.mark.parametrize("raw", ["", None, "C+", "A", "AB", "O+-", "A++"])
def test_blood_type_rejects_anything_that_is_not_one_of_the_eight_types(raw):
    with pytest.raises(ValidationError):
        validate_blood_type(raw)


# -------------------------------------------------------------------- donor id

def test_donor_id_accepts_a_number_with_a_correct_check_digit():
    assert validate_donor_id("123456782") == "123456782"


def test_donor_id_is_padded_to_nine_digits():
    assert validate_donor_id("18") == "000000018"


def test_donor_id_ignores_separators():
    assert validate_donor_id(" 12345678-2 ") == "123456782"


def test_donor_id_rejects_a_wrong_check_digit():
    # A single mistyped digit must not silently attach a unit to another person.
    with pytest.raises(ValidationError):
        validate_donor_id("123456789")


@pytest.mark.parametrize("raw", ["", None, "abcdefghi", "12345678a", "1234567890", "000000000"])
def test_donor_id_rejects_malformed_input(raw):
    with pytest.raises(ValidationError):
        validate_donor_id(raw)


# ------------------------------------------------------------------- full name

def test_full_name_collapses_repeated_whitespace():
    assert validate_full_name("  ישראל   ישראלי  ") == "ישראל ישראלי"


def test_full_name_accepts_hebrew_and_latin_letters():
    assert validate_full_name("Sara Cohen-Levi") == "Sara Cohen-Levi"


@pytest.mark.parametrize(
    "raw",
    ["", None, "א", "דוד 2", "<script>alert(1)</script>", "Robert'); DROP TABLE donors;--"],
)
def test_full_name_rejects_digits_and_markup(raw):
    with pytest.raises(ValidationError):
        validate_full_name(raw)


# --------------------------------------------------------------- donation date

@pytest.mark.parametrize("raw", ["2026-08-19", "19/08/2026", "19.08.2026"])
def test_donation_date_accepts_the_supported_formats(raw):
    assert validate_donation_date(raw, today=TODAY) == TODAY


def test_donation_date_rejects_a_future_date():
    tomorrow = (TODAY + timedelta(days=1)).isoformat()
    with pytest.raises(ValidationError):
        validate_donation_date(tomorrow, today=TODAY)


def test_donation_date_rejects_an_implausible_year():
    with pytest.raises(ValidationError):
        validate_donation_date("1998-01-01", today=TODAY)


@pytest.mark.parametrize("raw", ["", None, "not a date", "2026-02-30", "32/01/2026"])
def test_donation_date_rejects_malformed_input(raw):
    with pytest.raises(ValidationError):
        validate_donation_date(raw, today=TODAY)


# ------------------------------------------------------------------ unit count

@pytest.mark.parametrize(("raw", "expected"), [("1", 1), (" 12 ", 12), (7, 7)])
def test_unit_count_accepts_positive_whole_numbers(raw, expected):
    assert validate_unit_count(raw) == expected


@pytest.mark.parametrize("raw", ["", None, "0", "-3", "2.5", "many", str(MAX_UNITS_PER_REQUEST + 1)])
def test_unit_count_rejects_invalid_amounts(raw):
    with pytest.raises(ValidationError):
        validate_unit_count(raw)


# ----------------------------------------------------------------- destination

def test_destination_falls_back_to_a_generic_value_when_left_empty():
    assert validate_destination("   ") == DEFAULT_DESTINATION


def test_destination_allows_a_ward_name_with_digits():
    assert validate_destination("חדר ניתוח 3") == "חדר ניתוח 3"


def test_destination_rejects_markup_characters():
    with pytest.raises(ValidationError):
        validate_destination("<b>trauma</b>")


# ------------------------------------------------------------------- username

def test_username_is_normalised_to_lowercase():
    assert validate_username("Admin") == "admin"


@pytest.mark.parametrize("raw", ["", None, "ab", "1admin", "ad min", "user-name"])
def test_username_rejects_malformed_input(raw):
    with pytest.raises(ValidationError):
        validate_username(raw)


# ------------------------------------------------------------------- password

def test_password_accepts_a_mixed_letter_and_digit_secret():
    assert validate_password("Admin123!") == "Admin123!"


@pytest.mark.parametrize("raw", ["", None, "short1", "nodigitshere", "12345678"])
def test_password_rejects_weak_secrets(raw):
    with pytest.raises(ValidationError):
        validate_password(raw)


# -------------------------------------------------------------- display name

def test_display_name_collapses_whitespace():
    assert validate_display_name("  עובד   בנק  ") == "עובד בנק"


def test_display_name_rejects_digits():
    with pytest.raises(ValidationError):
        validate_display_name("עובד 2")


# ---------------------------------------------------------------------- role

def test_role_accepts_the_three_defined_roles():
    assert validate_role("researcher") is Role.RESEARCHER
    assert validate_role("ADMIN") is Role.ADMIN


def test_role_rejects_an_unknown_code():
    with pytest.raises(ValidationError):
        validate_role("GUEST")

