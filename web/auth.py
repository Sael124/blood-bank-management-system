"""Session login and role checks for the Flask layer.

The clinical services never see Flask. This module is the only place that reads
the cookie, binds the signed-in username onto the audit trail, and turns a
missing or insufficient role into a Hebrew screen instead of a stack trace.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import TypeVar

from flask import Flask, flash, g, redirect, request, session, url_for

from app_logging.activity_log import bind_actor
from core.errors import AccessDeniedError
from core.models import UserAccount
from core.roles import (
    Role,
    can_export_records,
    can_handle_units,
    can_manage_users,
    can_view_audit,
    can_view_metadata,
    can_view_phi,
    parse_role,
)

_SESSION_USERNAME = "auth_username"
_SESSION_ROLE = "auth_role"
_SESSION_DISPLAY = "auth_display_name"

F = TypeVar("F", bound=Callable)


def register_authentication(application: Flask) -> None:
    """Require a signed-in user on every screen except login and static files."""

    @application.before_request
    def _require_signed_in_user():
        bind_actor(session.get(_SESSION_USERNAME))
        endpoint = request.endpoint or ""
        if endpoint in {"becs.login", "becs.submit_login"} or endpoint.startswith("static"):
            return None
        if current_user() is None:
            flash("יש להתחבר למערכת כדי להמשיך.", "info")
            return redirect(url_for("becs.login"))
        return None

    application.jinja_env.globals["current_user"] = current_user
    application.jinja_env.globals["can_view_phi"] = _flag(can_view_phi)
    application.jinja_env.globals["can_handle_units"] = _flag(can_handle_units)
    application.jinja_env.globals["can_view_audit"] = _flag(can_view_audit)
    application.jinja_env.globals["can_export_records"] = _flag(can_export_records)
    application.jinja_env.globals["can_manage_users"] = _flag(can_manage_users)
    application.jinja_env.globals["can_view_metadata"] = _flag(can_view_metadata)


def _flag(check):
    """Expose a role helper to templates as a zero-argument function."""

    def wrapped() -> bool:
        account = current_user()
        return account is not None and check(account.role)

    return wrapped


def current_user() -> UserAccount | None:
    """The account stored in this session, or None when nobody is signed in."""
    cached = getattr(g, "current_user", None)
    if cached is not None or getattr(g, "current_user_loaded", False):
        return cached
    username = session.get(_SESSION_USERNAME)
    role_value = session.get(_SESSION_ROLE)
    display_name = session.get(_SESSION_DISPLAY)
    if not username or not role_value:
        g.current_user = None
        g.current_user_loaded = True
        return None
    try:
        role = parse_role(role_value)
    except ValueError:
        g.current_user = None
        g.current_user_loaded = True
        return None
    account = UserAccount(
        username=username,
        role=role,
        display_name=display_name or username,
        is_active=True,
    )
    g.current_user = account
    g.current_user_loaded = True
    return account


def start_session(account: UserAccount) -> None:
    """Store the signed-in account and rotate the CSRF token with a new session."""
    session.clear()
    session[_SESSION_USERNAME] = account.username
    session[_SESSION_ROLE] = account.role.value
    session[_SESSION_DISPLAY] = account.display_name
    bind_actor(account.username)


def end_session() -> str:
    """Clear the session and return the username that was signed in, if any."""
    username = session.get(_SESSION_USERNAME) or ""
    session.clear()
    bind_actor(None)
    return username


def require_roles(*allowed: Role) -> Callable[[F], F]:
    """Refuse the request when the signed-in role is not one of `allowed`."""

    def decorator(view: F) -> F:
        @wraps(view)
        def wrapped(*args, **kwargs):
            account = current_user()
            if account is None or account.role not in allowed:
                raise AccessDeniedError("אין הרשאה לבצע פעולה זו.")
            return view(*args, **kwargs)

        return wrapped  # type: ignore[return-value]

    return decorator
