"""Tests for reviewing and copying the audit trail.

Two things are checked here. The filter has to refuse anything it did not
define itself, because it is the one screen whose input reaches a database
query. And the exported copy has to be faithful: 21 CFR 11.10(b) asks for an
accurate and complete copy, so a detail that happens to contain a comma or a
line break must survive the export as one field instead of quietly splitting a
row and shifting every column after it.
"""

from __future__ import annotations

import csv
import io
from datetime import date, datetime

import pytest

from core.errors import ValidationError
from core.models import ActivityLogEntry, ActivityOutcome
from services.audit_service import (
    AUDIT_ACTIONS,
    AUDIT_OUTCOMES,
    AuditQuery,
    parse_query,
    to_csv,
)


def make_entry(log_id: int = 1, **overrides) -> ActivityLogEntry:
    """Build a fully populated audit line, with any field replaced."""
    defaults = {
        "log_id": log_id,
        "created_at": datetime(2026, 3, 14, 9, 30, 0),
        "action": "DONOR_NAME_UPDATED",
        "outcome": ActivityOutcome.SUCCESS,
        "details": "עודכן שמו של התורם",
        "actor": "operator",
        "entity_type": "DONOR",
        "entity_id": "123456782",
        "operation": "UPDATE",
        "old_value": "יעל כהן",
        "new_value": "יעל לוי",
        "reason": "השם שהוזן שונה מהשם הרשום",
        "created_at_utc": datetime(2026, 3, 14, 7, 30, 0),
        "utc_offset": "+02:00",
        "source_host": "WARD-PC-2",
        "previous_hash": "a" * 64,
        "record_hash": "b" * 64,
    }
    return ActivityLogEntry(**{**defaults, **overrides})


def read_csv(document: str) -> list[list[str]]:
    return list(csv.reader(io.StringIO(document, newline="")))


# --------------------------------------------------------------------------- #
# The filter
# --------------------------------------------------------------------------- #

def test_an_empty_filter_selects_everything():
    query = parse_query({})

    assert query == AuditQuery()
    assert not query.is_filtered
    assert query.describe() == "ללא סינון"


def test_a_complete_filter_is_parsed():
    query = parse_query(
        {
            "date_from": "2026-01-01",
            "date_to": "2026-01-31",
            "actor": "  ward\\operator ",
            "action": "DONATION_INTAKE",
            "outcome": "REJECTED",
        }
    )

    assert query.date_from == date(2026, 1, 1)
    assert query.date_to == date(2026, 1, 31)
    assert query.actor == "ward\\operator"
    assert query.action == "DONATION_INTAKE"
    assert query.outcome == "REJECTED"
    assert query.is_filtered


def test_a_code_is_accepted_whatever_case_it_arrives_in():
    assert parse_query({"outcome": "success"}).outcome == "SUCCESS"


@pytest.mark.parametrize("action", AUDIT_ACTIONS)
def test_every_action_the_system_can_record_is_filterable(action):
    """A new action must become searchable by being added, not by being listed."""
    assert parse_query({"action": action}).action == action


@pytest.mark.parametrize("outcome", AUDIT_OUTCOMES)
def test_every_outcome_is_filterable(outcome):
    assert parse_query({"outcome": outcome}).outcome == outcome


@pytest.mark.parametrize(
    ("raw_values", "field"),
    [
        ({"action": "DROP TABLE"}, "action"),
        ({"action": "DONATION_INTAKE'"}, "action"),
        ({"outcome": "MAYBE"}, "outcome"),
        ({"date_from": "14/13/2026"}, "date_from"),
        ({"date_to": "yesterday"}, "date_to"),
        ({"actor": "%"}, "actor"),
        ({"actor": "operator' OR 1=1--"}, "actor"),
        ({"actor": "<script>"}, "actor"),
        ({"actor": "x" * 61}, "actor"),
    ],
)
def test_an_unrecognised_filter_value_is_refused(raw_values, field):
    with pytest.raises(ValidationError) as raised:
        parse_query(raw_values)

    # The screen marks the offending input, so the error has to name it.
    assert raised.value.field == field


def test_a_range_that_ends_before_it_starts_is_refused():
    """Such a range matches nothing, which reads as "nothing ever happened"."""
    with pytest.raises(ValidationError) as raised:
        parse_query({"date_from": "2026-02-01", "date_to": "2026-01-01"})

    assert raised.value.field == "date_to"


def test_a_single_day_is_a_valid_range():
    query = parse_query({"date_from": "2026-01-31", "date_to": "2026-01-31"})

    assert query.date_from == query.date_to


def test_the_filter_describes_itself_for_the_audit_line():
    described = parse_query({"date_from": "2026-01-01", "actor": "operator"}).describe()

    assert "2026-01-01" in described
    assert "operator" in described


# --------------------------------------------------------------------------- #
# The exported copy
# --------------------------------------------------------------------------- #

def test_the_export_has_a_header_and_one_row_per_line():
    rows = read_csv(to_csv([make_entry(1), make_entry(2)]))

    assert len(rows) == 3
    assert rows[0][0] == "מספר רשומה"
    assert [row[0] for row in rows[1:]] == ["1", "2"]


def test_the_export_of_an_empty_trail_is_the_header_alone():
    assert len(read_csv(to_csv([]))) == 1


def test_the_export_carries_every_column_of_the_record():
    header, row = read_csv(to_csv([make_entry()]))

    assert len(row) == len(header)
    for value in (
        "operator",
        "WARD-PC-2",
        "DONOR_NAME_UPDATED",
        "SUCCESS",
        "DONOR",
        "123456782",
        "UPDATE",
        "יעל כהן",
        "יעל לוי",
        "+02:00",
        "b" * 64,
    ):
        assert value in row, f"{value} is missing from the exported copy"


def test_both_timestamps_are_exported():
    _, row = read_csv(to_csv([make_entry()]))

    assert "2026-03-14 09:30:00" in row
    assert "2026-03-14 07:30:00" in row


@pytest.mark.parametrize(
    "hostile_detail",
    [
        "פרטים, עם פסיק",
        'פרטים עם "מרכאות"',
        "פרטים\nעם שורה חדשה",
        "פרטים\r\nעם סוף שורה של חלונות",
        'הכול ביחד: , " \n',
    ],
)
def test_punctuation_in_a_detail_does_not_break_the_copy(hostile_detail):
    """A row must stay one row, whatever the operator typed into it."""
    rows = read_csv(to_csv([make_entry(details=hostile_detail), make_entry(log_id=2)]))

    assert len(rows) == 3
    assert rows[1][-3] == hostile_detail
    assert rows[2][0] == "2"


def test_a_line_from_before_the_upgrade_is_still_exportable():
    """Lines with none of the Part 11 columns must not break the export."""
    legacy = ActivityLogEntry(
        log_id=7,
        created_at=datetime(2025, 12, 1, 8, 0, 0),
        action="DONATION_INTAKE",
        outcome=ActivityOutcome.SUCCESS,
        details="שורה מהגרסה הקודמת",
        actor="operator",
    )

    header, row = read_csv(to_csv([legacy]))

    assert len(row) == len(header)
    assert row[0] == "7"
