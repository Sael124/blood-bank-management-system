"""Intake of donated blood units."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

import pyodbc

from app_logging.activity_log import ActivityAction, record, record_standalone
from core import validation
from core.blood_types import BloodType
from core.errors import BloodTypeConflictError, DataIntegrityError, ValidationError
from core.models import ActivityOutcome, AuditEntity, AuditOperation, Donor
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
            f"ניסיון קליטת תרומה נדחה בשלב אימות הקלט. שדה: {error.field or 'לא ידוע'}.",
            entity=AuditEntity.BLOOD_UNIT,
            operation=AuditOperation.NONE,
            reason=str(error),
        )
        raise

    donor = Donor(donor_id=donor_id, full_name=full_name, blood_type=blood_type)

    try:
        return _store_donation(donor, donation_date)
    except BloodTypeConflictError as error:
        # The two blood types go into the value columns rather than only into the
        # sentence, so the contradiction can be read straight off the trail.
        record_standalone(
            ActivityAction.DONATION_INTAKE,
            ActivityOutcome.REJECTED,
            f"ניסיון קליטת תרומה נדחה: סוג הדם שהוזן סותר את סוג הדם הרשום לתורם ת\"ז {donor_id}.",
            entity=AuditEntity.DONOR,
            operation=AuditOperation.NONE,
            entity_id=donor_id,
            old_value=error.recorded_blood_type,
            new_value=error.submitted_blood_type,
            reason=str(error),
        )
        raise
    except DataIntegrityError as error:
        record_standalone(
            ActivityAction.DONATION_INTAKE,
            ActivityOutcome.REJECTED,
            f"ניסיון קליטת תרומה נדחה עבור ת\"ז {donor_id}.",
            entity=AuditEntity.DONOR,
            operation=AuditOperation.NONE,
            entity_id=donor_id,
            reason=str(error),
        )
        raise
    except pyodbc.Error as error:
        raise wrap_driver_error(error) from error


def _store_donation(donor: Donor, donation_date: date) -> DonationReceipt:
    """Persist the donor, the unit and their audit lines in one transaction.

    Each stored record gets its own audit line - the donor, a change to the
    donor, and the unit - because the trail has to be able to answer what
    happened to one specific record, not only what the operator was doing.
    """
    with transaction() as connection:
        cursor = connection.cursor()
        existing_donor = repositories.find_donor(cursor, donor.donor_id)
        is_returning_donor = existing_donor is not None

        if existing_donor is None:
            _register_new_donor(cursor, donor)
        else:
            _reject_blood_type_conflict(existing_donor, donor.blood_type)
            _apply_donor_name_change(cursor, existing_donor, donor.full_name)

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
            entity=AuditEntity.BLOOD_UNIT,
            operation=AuditOperation.CREATE,
            entity_id=unit_id,
            new_value=f"{donor.blood_type} · תורם {donor.donor_id} · {donation_date.isoformat()}",
        )

    logger.info("Stored blood unit %s of type %s", unit_id, donor.blood_type)
    return DonationReceipt(
        unit_id=unit_id,
        donor=donor,
        donation_date=donation_date,
        is_returning_donor=is_returning_donor,
    )


def _register_new_donor(cursor: pyodbc.Cursor, donor: Donor) -> None:
    """Store a first time donor and record the creation of that record."""
    repositories.insert_donor(cursor, donor)
    record(
        cursor,
        ActivityAction.DONOR_REGISTERED,
        ActivityOutcome.SUCCESS,
        f"נרשם תורם חדש: ת\"ז {donor.donor_id}, {donor.full_name}, סוג דם {donor.blood_type}.",
        entity=AuditEntity.DONOR,
        operation=AuditOperation.CREATE,
        entity_id=donor.donor_id,
        new_value=f"{donor.full_name} · {donor.blood_type}",
    )


def _apply_donor_name_change(
    cursor: pyodbc.Cursor, existing_donor: Donor, submitted_name: str
) -> None:
    """Refresh a returning donor's name, keeping the name it replaced.

    People legally change names, so the update itself is legitimate. What is not
    legitimate is losing the previous name: 21 CFR 11.10(e) requires that a
    change must not obscure what was recorded before it, and a unit released
    under the old name has to stay traceable to the person who gave it.
    """
    if existing_donor.full_name == submitted_name:
        return

    repositories.update_donor_name(cursor, existing_donor.donor_id, submitted_name)
    record(
        cursor,
        ActivityAction.DONOR_NAME_UPDATED,
        ActivityOutcome.SUCCESS,
        f"עודכן שמו של התורם ת\"ז {existing_donor.donor_id}.",
        entity=AuditEntity.DONOR,
        operation=AuditOperation.UPDATE,
        entity_id=existing_donor.donor_id,
        old_value=existing_donor.full_name,
        new_value=submitted_name,
        reason="השם שהוזן בקליטת התרומה שונה מהשם הרשום במערכת עבור תעודת זהות זו.",
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
    raise BloodTypeConflictError(
        f"התורם עם תעודת זהות זו רשום במערכת עם סוג דם {existing_donor.blood_type}, "
        f"אך הוזן סוג דם {submitted_type}. התרומה לא נקלטה. "
        "יש לבדוק את מספר תעודת הזהות ואת סוג הדם לפני ניסיון נוסף.",
        recorded_blood_type=str(existing_donor.blood_type),
        submitted_blood_type=str(submitted_type),
    )
