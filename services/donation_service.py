"""Intake of donated blood units."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

import pyodbc

from app_logging.activity_log import ActivityAction, record, record_standalone
from core import validation
from core.blood_types import BloodType
from core.errors import DataIntegrityError, ValidationError
from core.models import ActivityOutcome, Donor
from data import repositories
from data.connection import transaction, wrap_driver_error

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DonationReceipt:
    """Confirmation returned to the operator after a successful intake."""

    unit_id: int
    donor: Donor
    donation_date: date
    is_returning_donor: bool


def register_donation(
    raw_blood_type: str | None,
    raw_donation_date: str | None,
    raw_donor_id: str | None,
    raw_full_name: str | None,
) -> DonationReceipt:
    """Validate and store one donated unit of whole blood.

    Args:
        raw_blood_type: Blood type as submitted by the form.
        raw_donation_date: Donation date as submitted by the form.
        raw_donor_id: Israeli identity number of the donor.
        raw_full_name: Full name of the donor.

    Returns:
        A receipt containing the identifier of the stored unit.

    Raises:
        ValidationError: The input was rejected; nothing was stored.
        DataIntegrityError: The donor is already registered with a different
            blood type, which must be resolved by a human before storing.
        DatabaseUnavailableError: SQL Server could not complete the operation.
    """
    try:
        blood_type = validation.validate_blood_type(raw_blood_type)
        donation_date = validation.validate_donation_date(raw_donation_date)
        donor_id = validation.validate_donor_id(raw_donor_id)
        full_name = validation.validate_full_name(raw_full_name)
    except ValidationError as error:
        record_standalone(
            ActivityAction.DONATION_INTAKE,
            ActivityOutcome.REJECTED,
            f"קליטת תרומה נדחתה. שדה: {error.field or 'לא ידוע'}. סיבה: {error}",
        )
        raise

    donor = Donor(donor_id=donor_id, full_name=full_name, blood_type=blood_type)

    try:
        return _store_donation(donor, donation_date)
    except DataIntegrityError as error:
        record_standalone(
            ActivityAction.DONATION_INTAKE,
            ActivityOutcome.REJECTED,
            f"קליטת תרומה נדחתה עבור ת\"ז {donor_id}: {error}",
        )
        raise
    except pyodbc.Error as error:
        raise wrap_driver_error(error) from error


def _store_donation(donor: Donor, donation_date: date) -> DonationReceipt:
    """Persist the donor, the unit and the audit line in one transaction."""
    with transaction() as connection:
        cursor = connection.cursor()
        existing_donor = repositories.find_donor(cursor, donor.donor_id)
        is_returning_donor = existing_donor is not None

        if existing_donor is None:
            repositories.insert_donor(cursor, donor)
        else:
            _reject_blood_type_conflict(existing_donor, donor.blood_type)
            if existing_donor.full_name != donor.full_name:
                repositories.update_donor_name(cursor, donor.donor_id, donor.full_name)

        unit_id = repositories.insert_blood_unit(
            cursor, donor.donor_id, donor.blood_type, donation_date
        )

        record(
            cursor,
            ActivityAction.DONATION_INTAKE,
            ActivityOutcome.SUCCESS,
            (
                f"נקלטה מנת דם #{unit_id} מסוג {donor.blood_type} "
                f"מתורם ת\"ז {donor.donor_id} ({donor.full_name}), "
                f"תאריך תרומה {donation_date.isoformat()}."
            ),
        )

    logger.info("Stored blood unit %s of type %s", unit_id, donor.blood_type)
    return DonationReceipt(
        unit_id=unit_id,
        donor=donor,
        donation_date=donation_date,
        is_returning_donor=is_returning_donor,
    )


def _reject_blood_type_conflict(existing_donor: Donor, submitted_type: BloodType) -> None:
    """Stop an intake that contradicts the donor's recorded blood type.

    A person's blood type never changes, so a mismatch means either the identity
    number or the blood type was typed wrong. Storing the unit anyway would put a
    mislabelled bag on the shelf, which is exactly the failure that kills a
    patient later on.
    """
    if existing_donor.blood_type is submitted_type:
        return
    raise DataIntegrityError(
        f"התורם עם תעודת זהות זו רשום במערכת עם סוג דם {existing_donor.blood_type}, "
        f"אך הוזן סוג דם {submitted_type}. התרומה לא נקלטה. "
        "יש לבדוק את מספר תעודת הזהות ואת סוג הדם לפני ניסיון נוסף."
    )
