"""The clinical audit trail.

Regulated blood establishment software must be able to answer, after the fact,
who performed which action, which record it touched and what the system decided.
Every intake, every dispense, every change to stored data and every rejected
attempt is therefore recorded.

What each line carries, and why:

* **who** - the operator, and the workstation the action came from.
* **when** - the workstation's local time and the same instant in UTC, together
  with the offset between them, so the true order of events survives a daylight
  saving change.
* **what** - the action, its outcome, the record it acted on, and for a change,
  the value before and the value after. 21 CFR 11.10(e) requires that a change
  must not obscure what was previously recorded, which is only possible if the
  old value is kept.
* **proof** - the hash of the line and of the line before it, so a later edit to
  the trail can be detected. See `core.audit_chain`.

Two ways to record exist on purpose:

* `record` joins an existing transaction, so a successful dispense and its audit
  line are committed together or not at all.
* `record_standalone` opens its own short transaction, for events that happen
  outside any transaction such as rejected input.
"""

from __future__ import annotations

import getpass
import logging
import socket
from contextvars import ContextVar
from datetime import datetime, timezone
from enum import Enum

import pyodbc

from core.audit_chain import (
    AUDIT_ACTOR_LENGTH,
    AUDIT_DETAILS_LENGTH,
    AUDIT_ENTITY_ID_LENGTH,
    AUDIT_HOST_LENGTH,
    AUDIT_REASON_LENGTH,
    AUDIT_VALUE_LENGTH,
    AuditRecordContent,
    compute_record_hash,
    format_utc_offset,
)
from core.models import ActivityOutcome, AuditEntity, AuditOperation
from data import repositories
from data.connection import transaction

logger = logging.getLogger(__name__)


class ActivityAction(Enum):
    """The auditable actions the system can perform."""

    DONATION_INTAKE = "DONATION_INTAKE"
    DONOR_REGISTERED = "DONOR_REGISTERED"
    DONOR_NAME_UPDATED = "DONOR_NAME_UPDATED"
    ROUTINE_DISPENSE = "ROUTINE_DISPENSE"
    EMERGENCY_DISPENSE = "EMERGENCY_DISPENSE"
    DATABASE_INITIALISED = "DATABASE_INITIALISED"
    AUDIT_TRAIL_EXPORTED = "AUDIT_TRAIL_EXPORTED"
    AUDIT_TRAIL_VERIFIED = "AUDIT_TRAIL_VERIFIED"
    RECORDS_EXPORTED = "RECORDS_EXPORTED"
    LOGIN_SUCCESS = "LOGIN_SUCCESS"
    LOGIN_FAILURE = "LOGIN_FAILURE"
    LOGOUT = "LOGOUT"
    USER_CREATED = "USER_CREATED"
    USER_ACTIVATED = "USER_ACTIVATED"
    USER_DEACTIVATED = "USER_DEACTIVATED"

    def __str__(self) -> str:
        return self.value


#: The signed-in username for this request. A context variable rather than a
#: function argument, so every existing service call records the HIPAA user
#: without each of them having to thread `actor` through by hand.
_session_actor: ContextVar[str | None] = ContextVar("session_actor", default=None)


def bind_actor(username: str | None) -> None:
    """Attach the signed-in username to subsequent audit lines on this request."""
    _session_actor.set(username)


def current_operator() -> str:
    """Identify who performed the action.

    The signed-in account is the honest Part 11 answer to "who did this". When
    nothing is signed in - startup, a rejected login - the workstation account
    is used instead of inventing a name.
    """
    bound = _session_actor.get()
    if bound:
        return bound[:AUDIT_ACTOR_LENGTH]
    try:
        return getpass.getuser()[:AUDIT_ACTOR_LENGTH]
    except OSError:
        return "unknown"


def current_host() -> str:
    """Name of the workstation the action was performed from."""
    try:
        return socket.gethostname()[:AUDIT_HOST_LENGTH]
    except OSError:
        return "unknown"


def _build_content(
    action: ActivityAction,
    outcome: ActivityOutcome,
    details: str,
    actor: str | None,
    entity: AuditEntity,
    entity_id: str,
    operation: AuditOperation,
    old_value: str,
    new_value: str,
    reason: str,
) -> AuditRecordContent:
    """Stamp an action with its identity and time, ready to be hashed and stored.

    The timestamp is taken once and truncated to whole seconds here, because the
    columns store seconds: hashing a more precise value than the database keeps
    would produce a hash that can never be reproduced from the stored row.
    """
    local_now = datetime.now().astimezone().replace(microsecond=0)
    return AuditRecordContent(
        created_at=local_now.replace(tzinfo=None),
        created_at_utc=local_now.astimezone(timezone.utc).replace(tzinfo=None),
        utc_offset=format_utc_offset(local_now.utcoffset()),
        actor=(actor or current_operator())[:AUDIT_ACTOR_LENGTH],
        action=action.value,
        outcome=outcome.value,
        entity_type=entity.value,
        entity_id=str(entity_id)[:AUDIT_ENTITY_ID_LENGTH],
        operation=operation.value,
        old_value=old_value[:AUDIT_VALUE_LENGTH],
        new_value=new_value[:AUDIT_VALUE_LENGTH],
        reason=reason[:AUDIT_REASON_LENGTH],
        details=details[:AUDIT_DETAILS_LENGTH],
        source_host=current_host(),
    )


def record(
    cursor: pyodbc.Cursor,
    action: ActivityAction,
    outcome: ActivityOutcome,
    details: str,
    *,
    entity: AuditEntity,
    operation: AuditOperation,
    entity_id: str | int = "",
    old_value: str = "",
    new_value: str = "",
    reason: str = "",
    actor: str | None = None,
) -> None:
    """Append an audit line inside the caller's transaction.

    `entity` and `operation` are required rather than defaulted, so that no
    action can be recorded without saying which record it affected and what it
    did to it. An action that changes nothing states so explicitly with
    `AuditOperation.NONE`.

    Args:
        cursor: Cursor of the transaction the audited work is running in, so the
            line and the work it describes are committed together.
        details: Human readable account of what happened, in Hebrew, as shown to
            the operator.
        entity_id: Identifier of the affected record, if there is one.
        old_value: The value before the change. Required by 11.10(e) for any
            modification, so that the change does not obscure what was there.
        reason: Why the action was taken, when that is not obvious from it.
    """
    content = _build_content(
        action, outcome, details, actor, entity, str(entity_id), operation, old_value, new_value, reason
    )
    previous_hash = repositories.lock_audit_chain_head(cursor)
    record_hash = compute_record_hash(previous_hash, content)
    repositories.insert_activity_log(cursor, content, previous_hash, record_hash)
    repositories.set_audit_chain_head(cursor, record_hash)


def record_standalone(
    action: ActivityAction,
    outcome: ActivityOutcome,
    details: str,
    *,
    entity: AuditEntity,
    operation: AuditOperation,
    entity_id: str | int = "",
    old_value: str = "",
    new_value: str = "",
    reason: str = "",
    actor: str | None = None,
) -> None:
    """Append an audit line in its own transaction, never raising to the caller.

    A failure to write the audit line must not replace the original error the
    operator is being shown, so it is reported to the technical log instead.
    """
    try:
        with transaction() as connection:
            record(
                connection.cursor(),
                action,
                outcome,
                details,
                entity=entity,
                operation=operation,
                entity_id=entity_id,
                old_value=old_value,
                new_value=new_value,
                reason=reason,
                actor=actor,
            )
    except Exception as error:  # noqa: BLE001 - auditing must never mask the real failure
        logger.error("Could not write audit entry %s/%s: %s", action, outcome, error)
