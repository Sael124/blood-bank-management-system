"""Tamper evidence for the audit trail, as a hash chain.

21 CFR 11.10(e) requires an audit trail that does not obscure previously
recorded information, and 11.10(c) requires records to stay retrievable and
trustworthy for as long as they are retained. A database trigger already refuses
every UPDATE and DELETE on the log, but a trigger only protects the log while it
is enforced: anyone with administrative rights can disable it, edit rows and
enable it again, leaving no trace.

So each line also carries the hash of its own content together with the hash of
the line before it. Editing a line changes its hash, removing a line breaks the
link in the line that followed it, and removing the newest lines no longer
matches the head hash stored separately. None of these can be repaired without
recomputing every hash from the edited point onwards, which is exactly the
evidence an inspection looks for.

This module lives in the pure layer on purpose: no database, no clock, no
configuration. That is what makes the chain exhaustively testable, and it is
also what lets the verifier be trusted, since it cannot be influenced by the
data it is checking. It is also why both the writer and the schema can depend on
it without the two depending on each other.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum

from .models import ActivityLogEntry

#: The `previous_hash` of the very first line in the chain. A fixed, obviously
#: artificial value, so a missing link can never be mistaken for the start.
GENESIS_HASH = "0" * 64

#: Included in every hashed payload. If the payload layout ever changes, this
#: changes with it, so old and new lines can never be confused for one another.
_PAYLOAD_VERSION = "BECS-AUDIT-1"

#: Length of a hexadecimal SHA-256 digest, and of the hash columns.
HASH_LENGTH = 64

# Maximum length of each stored audit field. This module owns them because it
# defines what an audit record is: the schema builds its columns from them and
# the writer truncates to them, so a value can never be longer than the column
# that has to hold it.
AUDIT_DETAILS_LENGTH = 1000
AUDIT_VALUE_LENGTH = 400
AUDIT_REASON_LENGTH = 300
AUDIT_ENTITY_ID_LENGTH = 40
AUDIT_ACTOR_LENGTH = 60
AUDIT_HOST_LENGTH = 80


@dataclass(frozen=True)
class AuditRecordContent:
    """Exactly the audit fields that the hash protects.

    Anything not listed here is not covered by the chain, which is why the
    identity of the operator, the timestamps and the before and after values are
    all part of it.
    """

    created_at: datetime
    created_at_utc: datetime
    utc_offset: str
    actor: str
    action: str
    outcome: str
    entity_type: str
    entity_id: str
    operation: str
    old_value: str
    new_value: str
    reason: str
    details: str
    source_host: str


class ChainFailure(Enum):
    """Why verification failed, as a code the web layer turns into Hebrew."""

    NONE = "NONE"

    #: A line's stored hash does not match its own content: the line was edited.
    CONTENT_ALTERED = "CONTENT_ALTERED"

    #: A line does not point at the line before it: a line was removed or
    #: inserted in the middle of the chain.
    LINK_BROKEN = "LINK_BROKEN"

    #: The newest line does not match the separately stored head hash: lines
    #: were removed from the end of the log.
    HEAD_MISMATCH = "HEAD_MISMATCH"

    #: An unprotected line appears after protected ones, which can only happen
    #: if a line was replaced by one written outside the application.
    HASH_MISSING = "HASH_MISSING"

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class AuditChainReport:
    """The result of verifying the audit trail end to end."""

    total_records: int
    protected_records: int
    unprotected_records: int
    failure: ChainFailure = ChainFailure.NONE
    broken_at_log_id: int | None = None

    @property
    def is_intact(self) -> bool:
        return self.failure is ChainFailure.NONE

    @property
    def has_unprotected_records(self) -> bool:
        """True when the log predates hashing and is only partly protected."""
        return self.unprotected_records > 0


def format_utc_offset(offset: timedelta | None) -> str:
    """Render a UTC offset as "+03:00", the form stored beside every timestamp.

    The FDA guidance on Part 11 asks that time stamps be implemented with a
    clear understanding of the time zone they refer to. Storing the offset
    explicitly means a line read years later is unambiguous even if the
    workstation has since moved country or the clocks have changed.
    """
    if offset is None:
        return ""
    total_minutes = round(offset.total_seconds() / 60)
    sign = "-" if total_minutes < 0 else "+"
    hours, minutes = divmod(abs(total_minutes), 60)
    return f"{sign}{hours:02d}:{minutes:02d}"


def _timestamp_field(value: datetime | None) -> str:
    """Render a timestamp to the second, matching the precision it is stored at.

    The database columns are DATETIME2(0), so hashing a value that still carried
    microseconds would produce a hash that can never be reproduced from the
    stored row.
    """
    if value is None:
        return ""
    return value.replace(microsecond=0).isoformat()


def canonical_payload(content: AuditRecordContent) -> str:
    """Serialise the protected fields into one unambiguous string.

    Every field is prefixed with its length instead of being joined by a
    separator. A separator would let an operator move text across the field
    boundary - putting "2024-01-01|admin" in a free text field, for instance -
    and produce two different records with the same hash. Length prefixes make
    that impossible, because the field boundaries are part of the payload.
    """
    fields = (
        _timestamp_field(content.created_at),
        _timestamp_field(content.created_at_utc),
        content.utc_offset,
        content.actor,
        content.action,
        content.outcome,
        content.entity_type,
        content.entity_id,
        content.operation,
        content.old_value,
        content.new_value,
        content.reason,
        content.details,
        content.source_host,
    )
    return "".join([f"{_PAYLOAD_VERSION}:"] + [f"{len(field)}:{field}" for field in fields])


def compute_record_hash(previous_hash: str, content: AuditRecordContent) -> str:
    """Return the hexadecimal SHA-256 of one audit line inside the chain."""
    payload = f"{len(previous_hash)}:{previous_hash}{canonical_payload(content)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def content_of(entry: ActivityLogEntry) -> AuditRecordContent:
    """Rebuild the protected content of a stored line, so it can be re-hashed.

    This is the single place that maps a stored row onto the hashed payload; the
    writer builds `AuditRecordContent` directly from the same field names.
    """
    return AuditRecordContent(
        created_at=entry.created_at,
        created_at_utc=entry.created_at_utc,
        utc_offset=entry.utc_offset,
        actor=entry.actor,
        action=entry.action,
        outcome=entry.outcome.value,
        entity_type=entry.entity_type,
        entity_id=entry.entity_id,
        operation=entry.operation,
        old_value=entry.old_value,
        new_value=entry.new_value,
        reason=entry.reason,
        details=entry.details,
        source_host=entry.source_host,
    )


def verify_chain(
    entries: list[ActivityLogEntry], stored_head_hash: str
) -> AuditChainReport:
    """Recompute the whole chain and report the first inconsistency found.

    Args:
        entries: Every audit line, in ascending log_id order. Ascending order
            matters: the chain is only meaningful in the direction it was
            written.
        stored_head_hash: The head hash held outside the log table. Comparing
            against it is what detects lines deleted from the end, which the
            chain alone cannot see.

    Returns:
        A report naming the first broken line, or an intact report.
    """
    unprotected = 0
    protected = 0
    expected_previous = GENESIS_HASH
    last_record_hash = GENESIS_HASH

    for entry in entries:
        if not entry.is_hash_protected:
            # Lines written before hashing existed are legitimately unprotected,
            # but only while no protected line has been seen yet.
            if protected > 0:
                return AuditChainReport(
                    total_records=len(entries),
                    protected_records=protected,
                    unprotected_records=unprotected + 1,
                    failure=ChainFailure.HASH_MISSING,
                    broken_at_log_id=entry.log_id,
                )
            unprotected += 1
            continue

        if entry.previous_hash != expected_previous:
            return AuditChainReport(
                total_records=len(entries),
                protected_records=protected,
                unprotected_records=unprotected,
                failure=ChainFailure.LINK_BROKEN,
                broken_at_log_id=entry.log_id,
            )

        recomputed = compute_record_hash(entry.previous_hash, content_of(entry))
        if recomputed != entry.record_hash:
            return AuditChainReport(
                total_records=len(entries),
                protected_records=protected,
                unprotected_records=unprotected,
                failure=ChainFailure.CONTENT_ALTERED,
                broken_at_log_id=entry.log_id,
            )

        protected += 1
        expected_previous = entry.record_hash
        last_record_hash = entry.record_hash

    if last_record_hash != stored_head_hash:
        return AuditChainReport(
            total_records=len(entries),
            protected_records=protected,
            unprotected_records=unprotected,
            failure=ChainFailure.HEAD_MISMATCH,
            broken_at_log_id=entries[-1].log_id if entries else None,
        )

    return AuditChainReport(
        total_records=len(entries),
        protected_records=protected,
        unprotected_records=unprotected,
    )
