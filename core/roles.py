"""Access roles for HIPAA minimum-necessary access.

The three roles are the ones the assignment requires. Everything a screen is
allowed to show is derived from these helpers, so a new endpoint cannot invent
its own idea of who may see a donor's name.
"""

from __future__ import annotations

from enum import Enum


class Role(Enum):
    """The three user types the system recognises."""

    ADMIN = "ADMIN"
    OPERATOR = "OPERATOR"
    RESEARCHER = "RESEARCHER"

    def __str__(self) -> str:
        return self.value


def parse_role(raw_value: str) -> Role:
    """Return the Role for a stored code, or raise ValueError."""
    return Role(raw_value)


def can_view_phi(role: Role) -> bool:
    """Whether this role may see a donor's name or identity number.

    A research student works on de-identified aggregates. Name and ID are
    protected health information the moment they identify the person, so they
    are withheld from that role.
    """
    return role in {Role.ADMIN, Role.OPERATOR}


def can_handle_units(role: Role) -> bool:
    """Whether this role may take donations in or dispense units out."""
    return role in {Role.ADMIN, Role.OPERATOR}


def can_view_audit(role: Role) -> bool:
    """Whether this role may open the Part 11 audit trail.

    The trail names donors, so a research student is kept to the de-identified
    inventory instead.
    """
    return role in {Role.ADMIN, Role.OPERATOR}


def can_export_records(role: Role) -> bool:
    """Whether this role may take a complete copy of every stored record."""
    return role is Role.ADMIN


def can_manage_users(role: Role) -> bool:
    """Whether this role may create and disable other accounts."""
    return role is Role.ADMIN


def can_view_metadata(role: Role) -> bool:
    """Whether this role may inspect system metadata rather than clinical rows."""
    return role is Role.ADMIN
