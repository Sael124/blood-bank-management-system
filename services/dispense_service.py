"""Dispensing blood units, in routine and in a mass casualty event.

Routine dispensing is a two step flow on purpose:

1. `preview_routine_dispense` computes a recommendation and shows it to the
   operator, including any substitute type that would be used.
2. `confirm_routine_dispense` recomputes the very same plan against a locked
   inventory and only then hands the units out.

The plan is never trusted from the browser: it is recalculated server side, so a
tampered form cannot force an incompatible transfusion, and a stock change
between the two steps cannot produce an over-dispense.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import pyodbc

from app_logging.activity_log import ActivityAction, record, record_standalone
from core import validation
from core.allocation import DispensePlan, plan_emergency_dispense, plan_routine_dispense
from core.blood_types import UNIVERSAL_DONOR, BloodType
from core.errors import ConcurrentUpdateError, OutOfStockError, ValidationError
from core.models import ActivityOutcome, AuditEntity, AuditOperation, DispenseMode
from data import repositories
from data.connection import read_only_connection, transaction, wrap_driver_error

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DispensePreview:
    """The recommendation shown to the operator before the units leave the bank."""

    plan: DispensePlan
    destination: str
    stock: dict[BloodType, int]


@dataclass(frozen=True)
class DispenseOutcome:
    """What actually happened after the operator approved the dispense."""

    dispense_id: int
    mode: DispenseMode
    plan: DispensePlan
    destination: str

    @property
    def is_partial(self) -> bool:
        return not self.plan.is_fully_fulfilled


def describe_plan(plan: DispensePlan) -> str:
    """Render the allocation as compact Hebrew text, e.g. "A+ 2 מנות, O+ 1 מנות"."""
    if plan.is_empty:
        return "לא נופקו מנות"
    return ", ".join(
        f"{allocation.blood_type} × {allocation.units}" for allocation in plan.allocations
    )


def get_stock_snapshot() -> dict[BloodType, int]:
    """Read the current inventory without locking anything."""
    try:
        with read_only_connection() as connection:
            return repositories.stock_by_blood_type(connection.cursor())
    except pyodbc.Error as error:
        raise wrap_driver_error(error) from error


def _validate_routine_request(
    raw_blood_type: str | None,
    raw_units: str | None,
    raw_destination: str | None,
) -> tuple[BloodType, int, str]:
    """Validate a routine request, auditing the rejection before re-raising.

    Shared by the preview and the approval steps, so a tampered approval that
    skips the preview is validated and audited exactly the same way.
    """
    try:
        return (
            validation.validate_blood_type(raw_blood_type),
            validation.validate_unit_count(raw_units),
            validation.validate_destination(raw_destination),
        )
    except ValidationError as error:
        record_standalone(
            ActivityAction.ROUTINE_DISPENSE,
            ActivityOutcome.REJECTED,
            f"בקשת ניפוק בשגרה נדחתה בשלב אימות הקלט. שדה: {error.field or 'לא ידוע'}.",
            entity=AuditEntity.DISPENSE,
            operation=AuditOperation.NONE,
            reason=str(error),
        )
        raise


def preview_routine_dispense(
    raw_blood_type: str | None,
    raw_units: str | None,
    raw_destination: str | None,
) -> DispensePreview:
    """Validate a routine request and return the recommended allocation.

    Nothing is dispensed and nothing is modified by this call.

    Raises:
        ValidationError: The request was rejected.
        OutOfStockError: No compatible unit exists for this patient at all.
    """
    blood_type, units_requested, destination = _validate_routine_request(
        raw_blood_type, raw_units, raw_destination
    )

    stock = get_stock_snapshot()
    plan = plan_routine_dispense(blood_type, units_requested, stock)

    if plan.is_empty:
        message = (
            f"אין במלאי אף מנה תואמת עבור סוג דם {blood_type}. "
            "לא ניתן לנפק, ויש לפנות להשלמת מלאי."
        )
        record_standalone(
            ActivityAction.ROUTINE_DISPENSE,
            ActivityOutcome.REJECTED,
            f"בקשת ניפוק ל-{destination}: {units_requested} מנות מסוג {blood_type}.",
            entity=AuditEntity.DISPENSE,
            operation=AuditOperation.NONE,
            reason=message,
        )
        raise OutOfStockError(message)

    return DispensePreview(plan=plan, destination=destination, stock=stock)


def confirm_routine_dispense(
    raw_blood_type: str | None,
    raw_units: str | None,
    raw_destination: str | None,
) -> DispenseOutcome:
    """Hand out units for an approved routine request, atomically.

    Raises:
        ValidationError: The request was rejected.
        OutOfStockError: The inventory ran out between preview and approval.
        ConcurrentUpdateError: Another operator took the same units; nothing was
            dispensed and the request should be repeated.
    """
    blood_type, units_requested, destination = _validate_routine_request(
        raw_blood_type, raw_units, raw_destination
    )

    try:
        return _execute_dispense(
            mode=DispenseMode.ROUTINE,
            requested_blood_type=blood_type,
            units_requested=units_requested,
            destination=destination,
        )
    except pyodbc.Error as error:
        raise wrap_driver_error(error) from error


def dispense_emergency_supply(raw_destination: str | None = None) -> DispenseOutcome:
    """Release every available O- unit for a mass casualty event.

    Only O- is released because the wounded arrive before anyone can type their
    blood, and O- is the one type that is safe for every possible recipient.

    Raises:
        OutOfStockError: There is no O- unit left on the shelf.
    """
    destination = validation.validate_destination(raw_destination)
    try:
        return _execute_dispense(
            mode=DispenseMode.EMERGENCY,
            requested_blood_type=UNIVERSAL_DONOR,
            units_requested=None,
            destination=destination,
        )
    except pyodbc.Error as error:
        raise wrap_driver_error(error) from error


def _execute_dispense(
    mode: DispenseMode,
    requested_blood_type: BloodType,
    units_requested: int | None,
    destination: str,
) -> DispenseOutcome:
    """Shared transaction for both dispensing modes.

    The audit entry for a refused request is committed before the exception is
    raised, otherwise the rollback would erase the evidence that the request ever
    reached the system.
    """
    shortage_message: str | None = None
    outcome: DispenseOutcome | None = None

    with transaction() as connection:
        cursor = connection.cursor()
        stock = repositories.stock_by_blood_type(cursor)

        if mode is DispenseMode.EMERGENCY:
            plan = plan_emergency_dispense(stock)
        else:
            plan = plan_routine_dispense(requested_blood_type, units_requested or 0, stock)

        if plan.is_empty:
            shortage_message = _empty_plan_message(mode, requested_blood_type)
            record(
                cursor,
                _action_for(mode),
                ActivityOutcome.REJECTED,
                f"בקשת ניפוק ל-{destination} נדחתה ולא נופקה אף מנה.",
                entity=AuditEntity.DISPENSE,
                operation=AuditOperation.NONE,
                reason=shortage_message,
            )
        else:
            outcome = _commit_plan(cursor, mode, plan, destination)

    if shortage_message is not None:
        raise OutOfStockError(shortage_message)

    assert outcome is not None  # one of the two branches always runs
    logger.info(
        "Dispense %s (%s) supplied %s units to %s",
        outcome.dispense_id,
        mode,
        outcome.plan.units_supplied,
        destination,
    )
    return outcome


def _commit_plan(
    cursor: pyodbc.Cursor,
    mode: DispenseMode,
    plan: DispensePlan,
    destination: str,
) -> DispenseOutcome:
    """Reserve the planned units, mark them dispensed and write the audit line."""
    breakdown = describe_plan(plan)
    dispense_id = repositories.insert_dispense(
        cursor,
        mode=mode,
        requested_blood_type=plan.requested_blood_type,
        units_requested=plan.units_requested,
        units_supplied=plan.units_supplied,
        destination=destination,
        supplied_breakdown=breakdown,
    )

    for allocation in plan.allocations:
        unit_ids = repositories.lock_units_for_dispense(
            cursor, allocation.blood_type, allocation.units
        )
        updated_rows = repositories.attach_units_to_dispense(cursor, unit_ids, dispense_id)
        if len(unit_ids) != allocation.units or updated_rows != allocation.units:
            # Raising rolls back the whole transaction, so no unit leaves the bank.
            raise ConcurrentUpdateError(
                f"המלאי של סוג דם {allocation.blood_type} השתנה במהלך הניפוק ולכן לא נופקה אף מנה. "
                "נא לרענן את המסך ולנסות שוב."
            )

    outcome_status = (
        ActivityOutcome.SUCCESS if plan.is_fully_fulfilled else ActivityOutcome.PARTIAL
    )
    record(
        cursor,
        _action_for(mode),
        outcome_status,
        _audit_details(mode, plan, destination, dispense_id),
        entity=AuditEntity.DISPENSE,
        operation=AuditOperation.CREATE,
        entity_id=dispense_id,
        new_value=f"{breakdown} → {destination}",
    )
    return DispenseOutcome(
        dispense_id=dispense_id, mode=mode, plan=plan, destination=destination
    )


def _action_for(mode: DispenseMode) -> ActivityAction:
    return (
        ActivityAction.EMERGENCY_DISPENSE
        if mode is DispenseMode.EMERGENCY
        else ActivityAction.ROUTINE_DISPENSE
    )


def _empty_plan_message(mode: DispenseMode, requested_blood_type: BloodType) -> str:
    if mode is DispenseMode.EMERGENCY:
        return (
            f"אין במלאי אף מנה מסוג {UNIVERSAL_DONOR}, ולכן לא ניתן לנפק דם במצב אר\"ן. "
            "יש להפעיל מיד נוהל השלמת מלאי חירום."
        )
    return (
        f"אין במלאי אף מנה תואמת עבור סוג דם {requested_blood_type}, ולכן לא נופקה אף מנה."
    )


def _audit_details(
    mode: DispenseMode, plan: DispensePlan, destination: str, dispense_id: int
) -> str:
    """Compose the audit line, spelling out any substitution that was applied."""
    if mode is DispenseMode.EMERGENCY:
        return (
            f"ניפוק חירום אר\"ן #{dispense_id} ל-{destination}: "
            f"נופקו {plan.units_supplied} מנות מסוג {UNIVERSAL_DONOR} (כל המלאי שהיה זמין)."
        )

    parts = [
        f"ניפוק בשגרה #{dispense_id} ל-{destination}: "
        f"התבקשו {plan.units_requested} מנות מסוג {plan.requested_blood_type}, "
        f"נופקו {plan.units_supplied} מנות ({describe_plan(plan)})."
    ]
    if plan.uses_substitutes:
        substitutes = ", ".join(str(blood_type) for blood_type in plan.substitute_types)
        parts.append(f"נעשה שימוש בסוג חלופי תואם: {substitutes}.")
    if plan.uses_universal_donor_reserve:
        parts.append(
            f"שימו לב: נופקו מנות מרזרבת {UNIVERSAL_DONOR} המיועדת למצבי אר\"ן."
        )
    if not plan.is_fully_fulfilled:
        parts.append(f"חסר במלאי: {plan.missing_units} מנות.")
    return " ".join(parts)
