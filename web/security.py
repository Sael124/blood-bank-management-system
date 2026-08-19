"""Cross site request forgery protection for every form in the application.

A dispense is an irreversible action, so it must not be triggerable by a page
the operator merely visited. Each session holds a random token that must be sent
back with every state changing request.
"""

from __future__ import annotations

import hmac
import logging
import secrets

from flask import Flask, abort, request, session

logger = logging.getLogger(__name__)

CSRF_FIELD_NAME = "csrf_token"
_CSRF_SESSION_KEY = "_csrf_token"
_STATE_CHANGING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def issue_csrf_token() -> str:
    """Return the session token, creating it on first use."""
    if _CSRF_SESSION_KEY not in session:
        session[_CSRF_SESSION_KEY] = secrets.token_urlsafe(32)
    return session[_CSRF_SESSION_KEY]


def register_csrf_protection(application: Flask) -> None:
    """Reject state changing requests that do not carry a matching token."""

    @application.before_request
    def _verify_csrf_token() -> None:
        if request.method not in _STATE_CHANGING_METHODS:
            return
        expected = session.get(_CSRF_SESSION_KEY, "")
        provided = request.form.get(CSRF_FIELD_NAME, "")
        # hmac.compare_digest avoids leaking the token through timing.
        if not expected or not hmac.compare_digest(expected, provided):
            logger.warning("Rejected %s %s: invalid CSRF token", request.method, request.path)
            abort(400, description="הבקשה נדחתה מטעמי אבטחה. נא לרענן את הדף ולנסות שוב.")

    application.jinja_env.globals["csrf_token"] = issue_csrf_token
    application.jinja_env.globals["csrf_field_name"] = CSRF_FIELD_NAME
