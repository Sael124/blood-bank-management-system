"""Tests for the tamper evidence of the audit trail.

The value of a hash chain is entirely in what it refuses to accept, so these
tests are written as attacks: edit a line, remove a line, move a line, cut the
end off the log, slip in a line that carries no hash. Every one of them has to
be reported, and an untouched log has to be reported as intact.

The chain is pure logic, so all of this is checked without a database.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta

import pytest

from core.audit_chain import (
    GENESIS_HASH,
    AuditRecordContent,
    ChainFailure,
    canonical_payload,
    compute_record_hash,
    content_of,
    format_utc_offset,
    verify_chain,
)
from core.models import ActivityLogEntry, ActivityOutcome

_LOCAL = datetime(2026, 3, 14, 9, 30, 0)
_UTC = datetime(2026, 3, 14, 7, 30, 0)


def make_content(**overrides) -> AuditRecordContent:
    """Build a representative audit line, with any field replaced."""
    content = AuditRecordContent(
        created_at=_LOCAL,
        created_at_utc=_UTC,
        utc_offset="+02:00",
        actor="operator",
        action="DONATION_INTAKE",
        outcome="SUCCESS",
        entity_type="BLOOD_UNIT",
        entity_id="41",
        operation="CREATE",
        old_value="",
        new_value="A+",
        reason="",
        details="נקלטה מנת דם #41",
        source_host="WARD-PC-2",
    )
    return dataclasses.replace(content, **overrides)


def build_chain(count: int) -> tuple[list[ActivityLogEntry], str]:
    """Build a correctly chained log of `count` lines, and its head hash."""
    entries: list[ActivityLogEntry] = []
    previous_hash = GENESIS_HASH

    for index in range(count):
        content = make_content(
            created_at=_LOCAL + timedelta(minutes=index),
            created_at_utc=_UTC + timedelta(minutes=index),
            entity_id=str(index),
            details=f"פעולה מספר {index}",
        )
        record_hash = compute_record_hash(previous_hash, content)
        entries.append(
            ActivityLogEntry(
                log_id=index + 1,
                created_at=content.created_at,
                action=content.action,
                outcome=ActivityOutcome(content.outcome),
                details=content.details,
                actor=content.actor,
                entity_type=content.entity_type,
                entity_id=content.entity_id,
                operation=content.operation,
                old_value=content.old_value,
                new_value=content.new_value,
                reason=content.reason,
                created_at_utc=content.created_at_utc,
                utc_offset=content.utc_offset,
                source_host=content.source_host,
                previous_hash=previous_hash,
                record_hash=record_hash,
            )
        )
        previous_hash = record_hash

    return entries, previous_hash


# --------------------------------------------------------------------------- #
# The hashed payload
# --------------------------------------------------------------------------- #

def test_the_hash_is_reproducible_from_the_same_content():
    content = make_content()
    assert compute_record_hash(GENESIS_HASH, content) == compute_record_hash(
        GENESIS_HASH, content
    )


def test_every_protected_field_changes_the_hash():
    """No protected field may be editable without the hash noticing."""
    baseline = compute_record_hash(GENESIS_HASH, make_content())
    alternatives = {
        "created_at": _LOCAL + timedelta(seconds=1),
        "created_at_utc": _UTC + timedelta(seconds=1),
        "utc_offset": "+03:00",
        "actor": "someone-else",
        "action": "ROUTINE_DISPENSE",
        "outcome": "REJECTED",
        "entity_type": "DONOR",
        "entity_id": "42",
        "operation": "UPDATE",
        "old_value": "O-",
        "new_value": "O-",
        "reason": "כי",
        "details": "משהו אחר",
        "source_host": "OTHER-PC",
    }
    assert set(alternatives) == {
        field.name for field in dataclasses.fields(AuditRecordContent)
    }, "a protected field was added without a test that it is hashed"

    for field, value in alternatives.items():
        altered = compute_record_hash(GENESIS_HASH, make_content(**{field: value}))
        assert altered != baseline, f"changing {field} did not change the hash"


def test_the_same_content_hashes_differently_after_a_different_line():
    content = make_content()
    first = compute_record_hash(GENESIS_HASH, content)
    assert compute_record_hash(first, content) != first


def test_text_cannot_be_moved_across_a_field_boundary():
    """Length prefixes are what stop one field from imitating two.

    Without them, "abc" followed by "" and "ab" followed by "c" would serialise
    to the same bytes, and two different audit lines would share a hash.
    """
    moved_left = make_content(old_value="abc", new_value="")
    moved_right = make_content(old_value="ab", new_value="c")

    assert canonical_payload(moved_left) != canonical_payload(moved_right)
    assert compute_record_hash(GENESIS_HASH, moved_left) != compute_record_hash(
        GENESIS_HASH, moved_right
    )


@pytest.mark.parametrize(
    ("offset", "expected"),
    [
        (timedelta(hours=3), "+03:00"),
        (timedelta(hours=2), "+02:00"),
        (timedelta(0), "+00:00"),
        (timedelta(hours=-5), "-05:00"),
        (timedelta(hours=5, minutes=30), "+05:30"),
        (timedelta(hours=-3, minutes=-30), "-03:30"),
        (None, ""),
    ],
)
def test_utc_offsets_are_rendered_unambiguously(offset, expected):
    assert format_utc_offset(offset) == expected


# --------------------------------------------------------------------------- #
# Verification
# --------------------------------------------------------------------------- #

def test_an_untouched_chain_is_intact():
    entries, head = build_chain(5)
    report = verify_chain(entries, head)

    assert report.is_intact
    assert report.failure is ChainFailure.NONE
    assert report.total_records == 5
    assert report.protected_records == 5
    assert not report.has_unprotected_records


def test_an_empty_log_is_intact():
    assert verify_chain([], GENESIS_HASH).is_intact


def test_an_edited_line_is_reported():
    entries, head = build_chain(4)
    entries[2] = dataclasses.replace(entries[2], details="הוחלף בדיעבד")

    report = verify_chain(entries, head)

    assert report.failure is ChainFailure.CONTENT_ALTERED
    assert report.broken_at_log_id == entries[2].log_id


def test_an_edited_operator_name_is_reported():
    """Rewriting who acted is the edit an audit trail exists to catch."""
    entries, head = build_chain(3)
    entries[1] = dataclasses.replace(entries[1], actor="somebody-else")

    assert verify_chain(entries, head).failure is ChainFailure.CONTENT_ALTERED


def test_a_removed_line_is_reported():
    entries, head = build_chain(5)
    del entries[2]

    report = verify_chain(entries, head)

    assert report.failure is ChainFailure.LINK_BROKEN
    # The break shows up on the line that pointed at the one now missing.
    assert report.broken_at_log_id == 4


def test_reordered_lines_are_reported():
    entries, head = build_chain(4)
    entries[1], entries[2] = entries[2], entries[1]

    assert verify_chain(entries, head).failure is ChainFailure.LINK_BROKEN


def test_lines_deleted_from_the_end_are_reported():
    """The chain alone cannot see this, which is why the head is stored apart."""
    entries, head = build_chain(5)
    truncated = entries[:3]

    report = verify_chain(truncated, head)

    assert report.failure is ChainFailure.HEAD_MISMATCH


def test_a_whole_log_replaced_by_a_consistent_forgery_is_reported():
    """Rewriting every hash still leaves the separately stored head unmatched."""
    _, real_head = build_chain(5)
    forged_entries, _ = build_chain(3)

    assert verify_chain(forged_entries, real_head).failure is ChainFailure.HEAD_MISMATCH


def test_lines_written_before_hashing_are_reported_as_unprotected_not_broken():
    """A database upgraded in place keeps its old lines, which have no hash."""
    entries, head = build_chain(3)
    legacy = [
        ActivityLogEntry(
            log_id=-2,
            created_at=_LOCAL - timedelta(days=1),
            action="DONATION_INTAKE",
            outcome=ActivityOutcome.SUCCESS,
            details="שורה מהגרסה הקודמת",
            actor="operator",
        )
    ]

    report = verify_chain(legacy + entries, head)

    assert report.is_intact
    assert report.unprotected_records == 1
    assert report.protected_records == 3
    assert report.has_unprotected_records


def test_an_unprotected_line_among_protected_ones_is_reported():
    """A line inserted outside the application carries no hash to check."""
    entries, head = build_chain(4)
    entries[2] = dataclasses.replace(entries[2], previous_hash="", record_hash="")

    report = verify_chain(entries, head)

    assert report.failure is ChainFailure.HASH_MISSING
    assert report.broken_at_log_id == entries[2].log_id


def test_content_of_reproduces_the_hash_of_a_stored_line():
    """Reading a line back must yield exactly the content that was hashed."""
    entries, _ = build_chain(1)
    entry = entries[0]

    assert compute_record_hash(entry.previous_hash, content_of(entry)) == entry.record_hash
