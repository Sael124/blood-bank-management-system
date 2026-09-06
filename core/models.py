"""Domain entities shared by every layer of the application."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum

from .blood_types import BloodType


class UnitStatus(Enum):
    """Lifecycle of a single blood unit inside the bank."""

    IN_STOCK = "IN_STOCK"
    DISPENSED = "DISPENSED"

    def __str__(self) -> str:
        return self.value


class DispenseMode(Enum):
    """Why units left the bank, which changes the rules that were applied."""

    ROUTINE = "ROUTINE"
    EMERGENCY = "EMERGENCY"

    def __str__(self) -> str:
        return self.value


class ActivityOutcome(Enum):
    """Result recorded for every operator action in the audit log."""

    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"
    REJECTED = "REJECTED"
    FAILURE = "FAILURE"

    def __str__(self) -> str:
        return self.value


class AuditEntity(Enum):
    """The kind of record an audited action acted upon.

    21 CFR 11.10(e) asks the audit trail to identify the record that was
    created, modified or deleted, not merely the action that was taken, so the
    entity and its identifier are stored as separate columns rather than being
    buried inside a free text description.
    """

    DONOR = "DONOR"
    BLOOD_UNIT = "BLOOD_UNIT"
    DISPENSE = "DISPENSE"
    DATABASE = "DATABASE"
    AUDIT_TRAIL = "AUDIT_TRAIL"

    def __str__(self) -> str:
        return self.value


class AuditOperation(Enum):
    """What the action did to the record it touched.

    NONE covers actions that changed nothing, such as an input that was refused
    before it reached the database. Those are audited too, because an attempt to
    release blood is itself clinical information.
    """

    CREATE = "CREATE"
    UPDATE = "UPDATE"
    DELETE = "DELETE"
    READ = "READ"
    NONE = "NONE"

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class Donor:
    """A person who donated blood. The identity number is the primary key."""

    donor_id: str
    full_name: str
    blood_type: BloodType


@dataclass(frozen=True)
class BloodUnit:
    """One traceable unit of whole blood on the shelf or already dispensed."""

    unit_id: int
    blood_type: BloodType
    donation_date: date
    donor_id: str
    status: UnitStatus


@dataclass(frozen=True)
class InventoryRow:
    """One line of the inventory report."""

    blood_type: BloodType
    units_in_stock: int
    population_share_percent: float
    is_universal_donor: bool


@dataclass(frozen=True)
class DonationRecord:
    """A donation as stored, used for the recent donations report."""

    unit_id: int
    donor_id: str
    donor_name: str
    blood_type: BloodType
    donation_date: date
    recorded_at: datetime
    status: UnitStatus


@dataclass(frozen=True)
class DispenseRecord:
    """A dispense event as stored, used for the recent dispenses report."""

    dispense_id: int
    mode: DispenseMode
    requested_blood_type: BloodType | None
    units_requested: int
    units_supplied: int
    destination: str
    created_at: datetime
    supplied_breakdown: str


@dataclass(frozen=True)
class ActivityLogEntry:
    """A single audit trail line, as stored and as shown on screen.

    Everything after `actor` was added for 21 CFR Part 11 and defaults to an
    empty value, so lines written by the earlier version of the system - which
    had none of these columns - still load and display correctly.

    Attributes:
        created_at: Workstation local time, kept for continuity with the
            original log screen.
        created_at_utc: The same instant in UTC. Stored alongside the local time
            because a log that spans a daylight saving change is otherwise
            ambiguous about the true sequence of events.
        utc_offset: The offset that connects the two, as "+03:00".
        previous_hash: Hash of the preceding line, forming the tamper evident
            chain.
        record_hash: Hash of this line's own content plus `previous_hash`.
    """

    log_id: int
    created_at: datetime
    action: str
    outcome: ActivityOutcome
    details: str
    actor: str
    entity_type: str = ""
    entity_id: str = ""
    operation: str = ""
    old_value: str = ""
    new_value: str = ""
    reason: str = ""
    created_at_utc: datetime | None = None
    utc_offset: str = ""
    source_host: str = ""
    previous_hash: str = ""
    record_hash: str = ""

    @property
    def is_hash_protected(self) -> bool:
        """Whether this line takes part in the tamper evident hash chain."""
        return bool(self.record_hash)
