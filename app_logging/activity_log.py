"""The clinical audit trail.

Regulated blood establishment software must be able to answer, after the fact,
who performed which action and what the system decided. Every intake, every
dispense and every rejected attempt is therefore recorded.

Two ways to record exist on purpose:

* `record` joins an existing transaction, so a successful dispense and its audit
  line are committed together or not at all.
* `record_standalone` opens its own short transaction, for events that happen
  outside any transaction such as rejected input.
"""

from __future__ import annotations

import getpass
import logging
from enum import Enum

import pyodbc

from core.models import ActivityOutcome
from data import repositories
from data.connection import transaction

logger = logging.getLogger(__name__)

#: Length of the details column in the database.
_MAX_DETAILS_LENGTH = 1000


class ActivityAction(Enum):
    """The auditable actions the system can perform."""

    DONATION_INTAKE = "DONATION_INTAKE"
    ROUTINE_DISPENSE = "ROUTINE_DISPENSE"
    EMERGENCY_DISPENSE = "EMERGENCY_DISPENSE"
    DATABASE_INITIALISED = "DATABASE_INITIALISED"

    def __str__(self) -> str:
        return self.value


def current_operator() -> str:
    """Identify the operator by the Windows account running the workstation.

    The assignment does not require user accounts, so the operating system user
    is the honest answer to "who did this" without inventing a login screen.
    """
    try:
        return getpass.getuser()[:60]
    except OSError:
        return "unknown"


def record(
    cursor: pyodbc.Cursor,
    action: ActivityAction,
    outcome: ActivityOutcome,
    details: str,
    actor: str | None = None,
) -> None:
    """Append an audit line inside the caller's transaction."""
    repositories.insert_activity_log(
        cursor,
        actor=actor or current_operator(),
        action=action.value,
        outcome=outcome,
        details=details[:_MAX_DETAILS_LENGTH],
    )


def record_standalone(
    action: ActivityAction,
    outcome: ActivityOutcome,
    details: str,
    actor: str | None = None,
) -> None:
    """Append an audit line in its own transaction, never raising to the caller.

    A failure to write the audit line must not replace the original error the
    operator is being shown, so it is reported to the technical log instead.
    """
    try:
        with transaction() as connection:
            record(connection.cursor(), action, outcome, details, actor)
    except Exception as error:  # noqa: BLE001 - auditing must never mask the real failure
        logger.error("Could not write audit entry %s/%s: %s", action, outcome, error)
