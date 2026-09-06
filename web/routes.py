"""HTTP endpoints for the operator screens of the system."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import date, datetime

from flask import (
    Blueprint,
    Response,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)

from core.blood_types import DISPLAY_ORDER, UNIVERSAL_DONOR
from core.errors import BloodBankError
from core.validation import DEFAULT_DESTINATION, MAX_UNITS_PER_REQUEST
from services import (
    audit_service,
    dispense_service,
    donation_service,
    inventory_service,
    records_export_service,
)

from .labels import CHAIN_FAILURE_LABELS, label

logger = logging.getLogger(__name__)

blueprint = Blueprint("becs", __name__)

_INTAKE_FIELDS = ("blood_type", "donation_date", "donor_id", "full_name")
_DISPENSE_FIELDS = ("blood_type", "units", "destination")
_AUDIT_FILTER_FIELDS = ("date_from", "date_to", "actor", "action", "outcome")


def _submitted(
    fields: tuple[str, ...], source: Mapping[str, str] | None = None
) -> dict[str, str]:
    """Collect the named fields, trimmed, so they can be redisplayed.

    Reads the submitted form by default, and the query string when a screen
    carries its state in the address instead - as the audit filter does, so that
    a filtered view can be bookmarked and reopened.
    """
    values = request.form if source is None else source
    return {field: (values.get(field) or "").strip() for field in fields}


def _error_field(error: BloodBankError) -> str:
    """Name of the input to highlight, when the error points at one."""
    return getattr(error, "field", "") or ""


# --------------------------------------------------------------------------- #
# Screen 1: intake of donations
# --------------------------------------------------------------------------- #

def _render_intake(form: dict[str, str], invalid_field: str = "", status: int = 200):
    return (
        render_template(
            "intake.html",
            form=form,
            invalid_field=invalid_field,
            blood_types=DISPLAY_ORDER,
            today=date.today().isoformat(),
        ),
        status,
    )


@blueprint.get("/")
def home():
    """The single entry point to every screen.

    Inner screens deliberately offer no navigation of their own, so the operator
    always passes through here when switching task. That makes it impossible to
    drift from an intake form into a dispense form by accident.
    """
    return render_template("home.html")


@blueprint.get("/donations")
def donation_intake():
    return _render_intake(
        {"blood_type": "", "donation_date": date.today().isoformat(), "donor_id": "", "full_name": ""}
    )


@blueprint.post("/donations")
def submit_donation():
    form = _submitted(_INTAKE_FIELDS)
    try:
        receipt = donation_service.register_donation(
            raw_blood_type=form["blood_type"],
            raw_donation_date=form["donation_date"],
            raw_donor_id=form["donor_id"],
            raw_full_name=form["full_name"],
        )
    except BloodBankError as error:
        flash(str(error), "error")
        return _render_intake(form, _error_field(error), status=400)

    flash(
        f"מנת הדם נקלטה בהצלחה. מספר מנה: {receipt.unit_id}, סוג דם: {receipt.donor.blood_type}, "
        f"תורם: {receipt.donor.full_name}.",
        "success",
    )
    if receipt.is_returning_donor:
        flash("התורם מזוהה כתורם חוזר שכבר רשום במערכת.", "info")
    # Redirect after POST so a refresh cannot record the same donation twice.
    return redirect(url_for("becs.donation_intake"))


# --------------------------------------------------------------------------- #
# Screen 2: routine dispensing
# --------------------------------------------------------------------------- #

def _render_routine(form: dict[str, str], invalid_field: str = "", status: int = 200):
    try:
        stock = dispense_service.get_stock_snapshot()
    except BloodBankError as error:
        flash(str(error), "error")
        stock = None
    return (
        render_template(
            "routine.html",
            form=form,
            invalid_field=invalid_field,
            blood_types=DISPLAY_ORDER,
            stock=stock,
            max_units=MAX_UNITS_PER_REQUEST,
            universal_donor=UNIVERSAL_DONOR,
        ),
        status,
    )


@blueprint.get("/dispense/routine")
def routine_dispense_form():
    return _render_routine({"blood_type": "", "units": "1", "destination": DEFAULT_DESTINATION})


@blueprint.post("/dispense/routine/preview")
def routine_dispense_preview():
    form = _submitted(_DISPENSE_FIELDS)
    try:
        preview = dispense_service.preview_routine_dispense(
            raw_blood_type=form["blood_type"],
            raw_units=form["units"],
            raw_destination=form["destination"],
        )
    except BloodBankError as error:
        flash(str(error), "error")
        return _render_routine(form, _error_field(error), status=400)

    return render_template(
        "routine_preview.html",
        preview=preview,
        plan=preview.plan,
        form=form,
        universal_donor=UNIVERSAL_DONOR,
    )


@blueprint.post("/dispense/routine/confirm")
def routine_dispense_confirm():
    form = _submitted(_DISPENSE_FIELDS)
    try:
        outcome = dispense_service.confirm_routine_dispense(
            raw_blood_type=form["blood_type"],
            raw_units=form["units"],
            raw_destination=form["destination"],
        )
    except BloodBankError as error:
        flash(str(error), "error")
        return _render_routine(form, _error_field(error), status=400)

    _flash_dispense_outcome(outcome)
    return redirect(url_for("becs.routine_dispense_form"))


def _flash_dispense_outcome(outcome) -> None:
    """Report the result, keeping every clinical caveat visible to the operator."""
    plan = outcome.plan
    flash(
        f"נופקו {plan.units_supplied} מנות ליעד {outcome.destination}. "
        f"פירוט: {dispense_service.describe_plan(plan)}. מספר ניפוק: {outcome.dispense_id}.",
        "success",
    )
    if plan.uses_substitutes:
        substitutes = ", ".join(str(blood_type) for blood_type in plan.substitute_types)
        flash(
            f"שימו לב: נופקו מנות מסוג חלופי תואם ({substitutes}) במקום {plan.requested_blood_type}.",
            "warning",
        )
    if plan.uses_universal_donor_reserve:
        flash(
            f"אזהרה: נופקו מנות מרזרבת {UNIVERSAL_DONOR} המיועדת למצבי אר\"ן. יש להשלים את המלאי.",
            "warning",
        )
    if outcome.is_partial:
        flash(
            f"הבקשה סופקה חלקית: חסרות {plan.missing_units} מנות מתוך {plan.units_requested} שהתבקשו.",
            "warning",
        )


# --------------------------------------------------------------------------- #
# Screen 3: mass casualty dispensing
# --------------------------------------------------------------------------- #

def _render_emergency(status: int = 200):
    try:
        stock = dispense_service.get_stock_snapshot()
        available_units = stock.get(UNIVERSAL_DONOR, 0)
    except BloodBankError as error:
        flash(str(error), "error")
        available_units = None
    return (
        render_template(
            "emergency.html",
            available_units=available_units,
            universal_donor=UNIVERSAL_DONOR,
            default_destination=DEFAULT_DESTINATION,
        ),
        status,
    )


@blueprint.get("/dispense/emergency")
def emergency_dispense_form():
    return _render_emergency()


@blueprint.post("/dispense/emergency")
def emergency_dispense():
    try:
        outcome = dispense_service.dispense_emergency_supply(
            raw_destination=(request.form.get("destination") or "").strip()
        )
    except BloodBankError as error:
        flash(str(error), "error")
        return _render_emergency(status=400)

    flash(
        f"ניפוק אר\"ן בוצע: נופקו {outcome.plan.units_supplied} מנות מסוג {UNIVERSAL_DONOR} "
        f"ליעד {outcome.destination}. מספר ניפוק: {outcome.dispense_id}.",
        "success",
    )
    flash(
        f"כל מלאי {UNIVERSAL_DONOR} נופק. יש להפעיל מיד נוהל השלמת מלאי.",
        "warning",
    )
    return redirect(url_for("becs.emergency_dispense_form"))


# --------------------------------------------------------------------------- #
# Screen 4: inventory and audit log
# --------------------------------------------------------------------------- #

#: Rows shown before a report has to be expanded. Ten fits on screen without
#: scrolling, which keeps the newest entries readable at a glance.
TABLE_PREVIEW_ROWS = 10


@blueprint.get("/inventory")
def inventory():
    try:
        overview = inventory_service.get_overview()
    except BloodBankError as error:
        flash(str(error), "error")
        overview = None
    return render_template(
        "inventory.html",
        overview=overview,
        universal_donor=UNIVERSAL_DONOR,
        preview_rows=TABLE_PREVIEW_ROWS,
        activity_limit=inventory_service.ACTIVITY_LOG_LIMIT,
        records_limit=inventory_service.RECENT_RECORDS_LIMIT,
    )


@blueprint.post("/records/export")
def export_records():
    """Download every stored record as the electronic copy required by 11.10(b)."""
    try:
        document, snapshot = records_export_service.export_xml()
    except BloodBankError as error:
        flash(str(error), "error")
        return redirect(url_for("becs.inventory"))

    logger.info("Exported a complete records copy of %s rows", snapshot.total_records)
    filename = f"becs-records-{datetime.now():%Y%m%d-%H%M%S}.xml"
    return Response(
        document,
        mimetype="application/xml",
        headers={
            "Content-Type": "application/xml; charset=utf-8",
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


# --------------------------------------------------------------------------- #
# Screen 5: the audit trail
# --------------------------------------------------------------------------- #

def _render_audit(
    form: dict[str, str],
    entries: list | None,
    invalid_field: str = "",
    status: int = 200,
):
    return (
        render_template(
            "audit.html",
            form=form,
            entries=entries,
            invalid_field=invalid_field,
            actions=audit_service.AUDIT_ACTIONS,
            outcomes=audit_service.AUDIT_OUTCOMES,
            page_limit=audit_service.AUDIT_PAGE_LIMIT,
        ),
        status,
    )


def _back_to_audit(form: dict[str, str]):
    """Return to the trail with the filter the operator was working with.

    Empty values are dropped so the address stays readable and a bookmark of it
    keeps meaning the same thing.
    """
    active_filter = {field: value for field, value in form.items() if value}
    return redirect(url_for("becs.audit_trail", **active_filter))


@blueprint.get("/audit")
def audit_trail():
    """The full audit trail, filtered by whatever the address asks for."""
    form = _submitted(_AUDIT_FILTER_FIELDS, request.args)
    try:
        query = audit_service.parse_query(form)
        entries = audit_service.search(query)
    except BloodBankError as error:
        flash(str(error), "error")
        return _render_audit(form, None, _error_field(error), status=400)

    return _render_audit(form, entries)


@blueprint.post("/audit/export")
def audit_trail_export():
    """Download the filtered trail as the electronic copy required by 11.10(b)."""
    form = _submitted(_AUDIT_FILTER_FIELDS)
    try:
        query = audit_service.parse_query(form)
        document, row_count = audit_service.export_csv(query)
    except BloodBankError as error:
        flash(str(error), "error")
        return _back_to_audit(form)

    logger.info("Exported %s audit records", row_count)
    filename = f"becs-audit-trail-{datetime.now():%Y%m%d-%H%M%S}.csv"
    return Response(
        # Excel reads a CSV as the local ANSI code page unless it finds a byte
        # order mark, which would turn every Hebrew detail into mojibake.
        "\ufeff" + document,
        mimetype="text/csv",
        headers={
            "Content-Type": "text/csv; charset=utf-8",
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


@blueprint.post("/audit/verify")
def audit_trail_verify():
    """Recompute the hash chain and report whether the trail was tampered with."""
    form = _submitted(_AUDIT_FILTER_FIELDS)
    try:
        report = audit_service.verify_integrity()
    except BloodBankError as error:
        flash(str(error), "error")
        return _back_to_audit(form)

    if report.is_intact:
        flash(
            f"בדיקת השלמות הסתיימה בהצלחה. {report.protected_records} רשומות "
            f"מתוך {report.total_records} נבדקו ונמצאו ללא שינוי.",
            "success",
        )
    else:
        flash(
            f"אזהרה: יומן התיעוד אינו שלם. {label(CHAIN_FAILURE_LABELS, report.failure)}. "
            f"אי-ההתאמה זוהתה ברשומה מספר {report.broken_at_log_id}. "
            "יש לדווח למנהל המערכת ולשמור גיבוי של בסיס הנתונים.",
            "error",
        )
    if report.has_unprotected_records:
        flash(
            f"{report.unprotected_records} רשומות נכתבו לפני הפעלת מנגנון האימות "
            "ולכן אינן מוגנות בשרשרת הגיבוב.",
            "info",
        )
    return _back_to_audit(form)
