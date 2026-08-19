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
    """A single audit trail line, as shown in the activity log screen."""

    log_id: int
    created_at: datetime
    action: str
    outcome: ActivityOutcome
    details: str
    actor: str
