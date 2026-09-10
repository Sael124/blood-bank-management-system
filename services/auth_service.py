"""Sign-in, user administration and the default accounts.

Passwords are stored as salted PBKDF2 hashes through Werkzeug, never in plain
text. The three demo accounts are created only when the table is empty, so an
administrator who later deletes them is not surprised to find them back after
the next restart.
"""

from __future__ import annotations

from dataclasses import dataclass

import pyodbc
from werkzeug.security import check_password_hash, generate_password_hash

from app_logging.activity_log import ActivityAction, record_standalone
from core import validation
from core.errors import AccessDeniedError, AuthenticationError, ValidationError
from core.models import ActivityOutcome, AuditEntity, AuditOperation, UserAccount
from core.roles import Role
from data import repositories
from data.connection import read_only_connection, transaction, wrap_driver_error

#: Demo accounts for the academic assignment. The passwords meet the validator
#: (letter + digit, eight characters) and are printed on the login screen so a
#: marker can exercise every role without a private briefing.
DEMO_ACCOUNTS: tuple[tuple[str, str, Role, str], ...] = (
    ("admin", "Admin123!", Role.ADMIN, "מנהל מערכת"),
    ("operator", "Operator123!", Role.OPERATOR, "עובד בנק הדם"),
    ("researcher", "Research123!", Role.RESEARCHER, "סטודנט מחקר"),
)


@dataclass(frozen=True)
class SystemMetadata:
    """Counts and account list: data about the records, not the records."""

    donor_count: int
    unit_count: int
    dispense_count: int
    activity_count: int
    user_count: int
    users: list[UserAccount]


def _hash_password(password: str) -> str:
    return generate_password_hash(password, method="pbkdf2:sha256")


def ensure_seed_users() -> None:
    """Create the three demo accounts the first time the table is empty."""
    try:
        with transaction() as connection:
            cursor = connection.cursor()
            if repositories.count_users(cursor) > 0:
                return
            for username, password, role, display_name in DEMO_ACCOUNTS:
                repositories.insert_user(
                    cursor,
                    username=username,
                    password_hash=_hash_password(password),
                    role=role,
                    display_name=display_name,
                    created_by="system",
                )
    except pyodbc.Error as error:
        raise wrap_driver_error(error) from error


def authenticate(raw_username: str | None, raw_password: str | None) -> UserAccount:
    """Return the active account when the password matches.

    A miss always looks the same: the caller is not told whether the username
    exists, because that distinction is how an outsider enumerates accounts.
    """
    try:
        username = validation.validate_username(raw_username)
        password = raw_password or ""
        if not password:
            raise ValidationError("חובה להזין סיסמה.", field="password")
    except ValidationError as error:
        record_standalone(
            ActivityAction.LOGIN_FAILURE,
            ActivityOutcome.REJECTED,
            "ניסיון התחברות נדחה: הקלט אינו תקין.",
            entity=AuditEntity.USER,
            operation=AuditOperation.NONE,
            reason=str(error),
        )
        raise AuthenticationError("שם המשתמש או הסיסמה אינם נכונים.") from error

    try:
        with read_only_connection() as connection:
            cursor = connection.cursor()
            account = repositories.find_user(cursor, username)
            password_hash = repositories.find_password_hash(cursor, username)
    except pyodbc.Error as error:
        raise wrap_driver_error(error) from error

    accepted = (
        account is not None
        and account.is_active
        and password_hash is not None
        and check_password_hash(password_hash, password)
    )
    if not accepted:
        record_standalone(
            ActivityAction.LOGIN_FAILURE,
            ActivityOutcome.REJECTED,
            "ניסיון התחברות נדחה.",
            entity=AuditEntity.USER,
            operation=AuditOperation.NONE,
            entity_id=username,
            actor=username,
        )
        raise AuthenticationError("שם המשתמש או הסיסמה אינם נכונים.")

    record_standalone(
        ActivityAction.LOGIN_SUCCESS,
        ActivityOutcome.SUCCESS,
        f"משתמש {account.username} התחבר למערכת.",
        entity=AuditEntity.USER,
        operation=AuditOperation.READ,
        entity_id=account.username,
        actor=account.username,
        new_value=account.role.value,
    )
    return account


def record_logout(username: str) -> None:
    record_standalone(
        ActivityAction.LOGOUT,
        ActivityOutcome.SUCCESS,
        f"משתמש {username} התנתק מהמערכת.",
        entity=AuditEntity.USER,
        operation=AuditOperation.READ,
        entity_id=username,
        actor=username,
    )


def list_users() -> list[UserAccount]:
    try:
        with read_only_connection() as connection:
            return repositories.list_users(connection.cursor())
    except pyodbc.Error as error:
        raise wrap_driver_error(error) from error


def create_user(
    raw_username: str | None,
    raw_password: str | None,
    raw_display_name: str | None,
    raw_role: str | None,
    created_by: str,
) -> UserAccount:
    username = validation.validate_username(raw_username)
    password = validation.validate_password(raw_password)
    display_name = validation.validate_display_name(raw_display_name)
    role = validation.validate_role(raw_role)

    try:
        with transaction() as connection:
            cursor = connection.cursor()
            if repositories.find_user(cursor, username) is not None:
                raise ValidationError("שם המשתמש כבר קיים במערכת.", field="username")
            repositories.insert_user(
                cursor,
                username=username,
                password_hash=_hash_password(password),
                role=role,
                display_name=display_name,
                created_by=created_by,
            )
            _audit_user_created(cursor, username, role, created_by)
    except pyodbc.Error as error:
        raise wrap_driver_error(error) from error

    return UserAccount(
        username=username,
        role=role,
        display_name=display_name,
        is_active=True,
        created_by=created_by,
    )


def _audit_user_created(cursor, username: str, role: Role, created_by: str) -> None:
    from app_logging.activity_log import record as audit_record

    audit_record(
        cursor,
        ActivityAction.USER_CREATED,
        ActivityOutcome.SUCCESS,
        f"נוצר משתמש {username} בתפקיד {role.value}.",
        entity=AuditEntity.USER,
        operation=AuditOperation.CREATE,
        entity_id=username,
        new_value=role.value,
        actor=created_by,
    )


def set_active(username: str, is_active: bool, actor: str) -> UserAccount:
    username = validation.validate_username(username)
    try:
        with transaction() as connection:
            cursor = connection.cursor()
            account = repositories.find_user(cursor, username)
            if account is None:
                raise ValidationError("המשתמש אינו קיים.", field="username")
            if account.username == actor and not is_active:
                raise AccessDeniedError("לא ניתן לכבות את החשבון שבו מתבצעת הפעולה.")
            if (
                account.role is Role.ADMIN
                and account.is_active
                and not is_active
                and repositories.count_active_admins(cursor) <= 1
            ):
                raise AccessDeniedError("לא ניתן לכבות את מנהל המערכת האחרון הפעיל.")
            repositories.set_user_active(cursor, username, is_active)
            from app_logging.activity_log import record as audit_record

            action = ActivityAction.USER_ACTIVATED if is_active else ActivityAction.USER_DEACTIVATED
            audit_record(
                cursor,
                action,
                ActivityOutcome.SUCCESS,
                f"עודכן מצב המשתמש {username}: {'פעיל' if is_active else 'כבוי'}.",
                entity=AuditEntity.USER,
                operation=AuditOperation.UPDATE,
                entity_id=username,
                old_value="active" if account.is_active else "inactive",
                new_value="active" if is_active else "inactive",
                actor=actor,
            )
            return UserAccount(
                username=account.username,
                role=account.role,
                display_name=account.display_name,
                is_active=is_active,
                created_at=account.created_at,
                created_by=account.created_by,
            )
    except pyodbc.Error as error:
        raise wrap_driver_error(error) from error


def get_metadata() -> SystemMetadata:
    try:
        with read_only_connection() as connection:
            cursor = connection.cursor()
            return SystemMetadata(
                donor_count=repositories.count_donors(cursor),
                unit_count=repositories.count_blood_units(cursor),
                dispense_count=repositories.count_dispenses(cursor),
                activity_count=repositories.count_activity_log(cursor),
                user_count=repositories.count_users(cursor),
                users=repositories.list_users(cursor),
            )
    except pyodbc.Error as error:
        raise wrap_driver_error(error) from error
