"""Flask application factory.

The web layer only translates HTTP requests into service calls and service
results into Hebrew screens. It contains no clinical rule and no SQL.
"""

from __future__ import annotations

import logging
from datetime import date, datetime

from flask import Flask, flash, redirect, render_template, url_for

from config import get_config
from core.errors import BloodBankError

from . import labels
from .routes import blueprint
from .security import register_csrf_protection

logger = logging.getLogger(__name__)

#: Forms in this application are tiny; anything larger is not a real submission.
_MAX_REQUEST_BYTES = 64 * 1024

#: Every code column the templates display, and the table that translates it.
_LABEL_FILTERS = {
    "action_label": labels.ACTION_LABELS,
    "outcome_label": labels.OUTCOME_LABELS,
    "mode_label": labels.MODE_LABELS,
    "status_label": labels.STATUS_LABELS,
    "entity_label": labels.ENTITY_LABELS,
    "operation_label": labels.OPERATION_LABELS,
}


def _format_datetime(value: datetime | None) -> str:
    return value.strftime("%d/%m/%Y %H:%M:%S") if value else ""


def _format_date(value: date | None) -> str:
    return value.strftime("%d/%m/%Y") if value else ""


def create_app() -> Flask:
    """Build the configured Flask application."""
    web_config = get_config().web

    application = Flask(__name__)
    application.config.update(
        SECRET_KEY=web_config.secret_key,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        MAX_CONTENT_LENGTH=_MAX_REQUEST_BYTES,
        TEMPLATES_AUTO_RELOAD=web_config.debug,
    )

    if web_config.secret_key_was_generated:
        logger.warning(
            "BECS_SECRET_KEY is not set, using a random key for this run. "
            "Sessions will be invalidated whenever the application restarts."
        )

    application.jinja_env.filters["datetime"] = _format_datetime
    application.jinja_env.filters["date"] = _format_date
    for filter_name, label_table in _LABEL_FILTERS.items():
        # The table is bound as a default argument: a closure over the loop
        # variable would leave every filter pointing at the last table.
        application.jinja_env.filters[filter_name] = (
            lambda value, table=label_table: labels.label(table, value)
        )

    register_csrf_protection(application)
    application.register_blueprint(blueprint)
    _register_error_handlers(application)
    return application


def _register_error_handlers(application: Flask) -> None:
    """Turn failures into readable Hebrew screens instead of stack traces."""

    @application.errorhandler(BloodBankError)
    def _handle_domain_error(error: BloodBankError):
        flash(str(error), "error")
        return redirect(url_for("becs.home"))

    @application.errorhandler(400)
    def _handle_bad_request(error):
        message = getattr(error, "description", "") or "הבקשה אינה תקינה."
        return render_template("error.html", status_code=400, message=message), 400

    @application.errorhandler(404)
    def _handle_not_found(error):
        return (
            render_template("error.html", status_code=404, message="הדף המבוקש אינו קיים."),
            404,
        )

    @application.errorhandler(500)
    def _handle_internal_error(error):
        logger.exception("Unhandled error while serving a request")
        return (
            render_template(
                "error.html",
                status_code=500,
                message="אירעה שגיאה בלתי צפויה. הפעולה לא בוצעה. פרטים נרשמו בקובץ הלוג.",
            ),
            500,
        )
