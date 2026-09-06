"""Reviewing, copying and verifying the audit trail.

21 CFR 11.10 asks for three things that reading the newest hundred lines off a
screen does not provide:

* **(b)** accurate and complete copies of the records, in a human readable and
  in an electronic form. The screen and its print layout are the readable copy;
  the CSV produced here is the electronic one, holding the values exactly as
  stored rather than as translated for display.
* **(c)** records that stay retrievable for as long as they are kept, which is
  what the filter is for: a trail of tens of thousands of lines is only
  retrievable if a specific day, operator or action can be asked for directly.
* **(e)** an audit trail that can be shown not to have been altered, which is
  what verification does.

Reviewing the trail is itself an auditable event, so an export and a
verification are each recorded in the trail they operated on.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date

import pyodbc

from app_logging.activity_log import ActivityAction, record_standalone
from core import validation
from core.audit_chain import AuditChainReport, verify_chain
from core.models import ActivityLogEntry, ActivityOutcome, AuditEntity, AuditOperation
from data import repositories
from data.connection import read_only_connection, wrap_driver_error

#: Upper bound on the lines one screen or one export may hold. A trail that has
#: been running for months does not fit in a browser, and an unbounded query
#: would take the database down with it; narrowing the filter is the answer.
AUDIT_PAGE_LIMIT = 500

#: The values a filter may hold, taken from the system's own definitions so a
#: new action becomes filterable the moment it is added. The tuples keep the
#: dropdowns in a stable order; the sets are what a submitted value is checked
#: against, because a request can arrive without ever loading the page.
AUDIT_ACTIONS: tuple[str, ...] = tuple(action.value for action in ActivityAction)
AUDIT_OUTCOMES: tuple[str, ...] = tuple(outcome.value for outcome in ActivityOutcome)
ALLOWED_ACTIONS = frozenset(AUDIT_ACTIONS)
ALLOWED_OUTCOMES = frozenset(AUDIT_OUTCOMES)

#: Hebrew column titles of the exported copy. The rows below carry the values as
#: they are stored, in English, so that the export is a faithful copy of the
#: record and not of its on screen translation.
_CSV_HEADERS = (
    "מספר רשומה",
    "זמן מקומי",
    "זמן UTC",
    "היסט מ-UTC",
    "מבצע",
    "עמדה",
    "פעולה",
    "תוצאה",
    "סוג רשומה",
    "מזהה רשומה",
    "סוג שינוי",
    "ערך קודם",
    "ערך חדש",
    "סיבה",
    "פרטים",
    "גיבוב קודם",
    "גיבוב הרשומה",
)


@dataclass(frozen=True)
class AuditQuery:
    """A validated filter over the audit trail."""

    date_from: date | None = None
    date_to: date | None = None
    actor: str = ""
    action: str = ""
    outcome: str = ""

    @property
    def is_filtered(self) -> bool:
        """Whether the operator narrowed the trail at all."""
        return any(
            (self.date_from, self.date_to, self.actor, self.action, self.outcome)
        )

    def describe(self) -> str:
        """Summarise the filter for the audit line that records the export."""
        parts: list[str] = []
        if self.date_from:
            parts.append(f"מתאריך {self.date_from.isoformat()}")
        if self.date_to:
            parts.append(f"עד תאריך {self.date_to.isoformat()}")
        if self.actor:
            parts.append(f"מבצע: {self.actor}")
        if self.action:
            parts.append(f"פעולה: {self.action}")
        if self.outcome:
            parts.append(f"תוצאה: {self.outcome}")
        return ", ".join(parts) if parts else "ללא סינון"


def parse_query(raw_values: Mapping[str, str]) -> AuditQuery:
    """Build a validated filter from the query string.

    Raises:
        ValidationError: A filter value was malformed or unknown; the field it
            points at is the one the screen marks.
    """
    date_from = validation.validate_optional_date(raw_values.get("date_from"), "date_from")
    date_to = validation.validate_optional_date(raw_values.get("date_to"), "date_to")
    validation.validate_date_range(date_from, date_to)

    return AuditQuery(
        date_from=date_from,
        date_to=date_to,
        actor=validation.validate_actor_search(raw_values.get("actor")),
        action=validation.validate_code(raw_values.get("action"), ALLOWED_ACTIONS, "action"),
        outcome=validation.validate_code(raw_values.get("outcome"), ALLOWED_OUTCOMES, "outcome"),
    )


def search(query: AuditQuery, limit: int = AUDIT_PAGE_LIMIT) -> list[ActivityLogEntry]:
    """Return the audit lines that match the filter, newest first."""
    try:
        with read_only_connection() as connection:
            return repositories.search_activity_log(
                connection.cursor(),
                limit=limit,
                date_from=query.date_from,
                date_to=query.date_to,
                actor=query.actor,
                action=query.action,
                outcome=query.outcome,
            )
    except pyodbc.Error as error:
        raise wrap_driver_error(error) from error


def to_csv(entries: list[ActivityLogEntry]) -> str:
    """Render audit lines as CSV, in the order they are displayed.

    The csv module does the quoting, so a detail containing a comma, a quote or
    a line break is exported as one field instead of silently splitting the row
    and corrupting the copy.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(_CSV_HEADERS)
    for entry in entries:
        writer.writerow(
            (
                entry.log_id,
                entry.created_at.isoformat(sep=" ") if entry.created_at else "",
                entry.created_at_utc.isoformat(sep=" ") if entry.created_at_utc else "",
                entry.utc_offset,
                entry.actor,
                entry.source_host,
                entry.action,
                entry.outcome.value,
                entry.entity_type,
                entry.entity_id,
                entry.operation,
                entry.old_value,
                entry.new_value,
                entry.reason,
                entry.details,
                entry.previous_hash,
                entry.record_hash,
            )
        )
    return buffer.getvalue()


def export_csv(query: AuditQuery) -> tuple[str, int]:
    """Produce the electronic copy of the filtered trail, and record that it was.

    Returns:
        The CSV text and the number of lines it holds.
    """
    entries = search(query)
    document = to_csv(entries)

    record_standalone(
        ActivityAction.AUDIT_TRAIL_EXPORTED,
        ActivityOutcome.SUCCESS,
        f"יוצא עותק של יומן התיעוד: {len(entries)} רשומות.",
        entity=AuditEntity.AUDIT_TRAIL,
        operation=AuditOperation.READ,
        reason=f"סינון: {query.describe()}",
    )
    return document, len(entries)


def verify_integrity() -> AuditChainReport:
    """Recompute the hash chain over the whole trail and record the result.

    The verification itself is audited whatever it finds, so that a failed check
    cannot be repeated quietly until it happens to pass.
    """
    try:
        with read_only_connection() as connection:
            cursor = connection.cursor()
            entries = repositories.all_activity_log_in_order(cursor)
            head_hash = repositories.read_audit_chain_head(cursor)
    except pyodbc.Error as error:
        raise wrap_driver_error(error) from error

    report = verify_chain(entries, head_hash)

    record_standalone(
        ActivityAction.AUDIT_TRAIL_VERIFIED,
        ActivityOutcome.SUCCESS if report.is_intact else ActivityOutcome.FAILURE,
        (
            f"נבדקה שלמות יומן התיעוד: {report.protected_records} רשומות מוגנות "
            f"מתוך {report.total_records}."
        ),
        entity=AuditEntity.AUDIT_TRAIL,
        operation=AuditOperation.READ,
        reason=(
            "השרשרת שלמה."
            if report.is_intact
            else f"נמצאה אי-התאמה ({report.failure}) ברשומה {report.broken_at_log_id}."
        ),
    )
    return report
