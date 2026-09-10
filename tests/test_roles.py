"""Permission matrix for the three HIPAA roles."""

from __future__ import annotations

import pytest

from core.roles import (
    Role,
    can_export_records,
    can_handle_units,
    can_manage_users,
    can_view_audit,
    can_view_metadata,
    can_view_phi,
    parse_role,
)


@pytest.mark.parametrize(
    ("role", "phi", "units", "audit", "export", "users", "metadata"),
    [
        (Role.ADMIN, True, True, True, True, True, True),
        (Role.OPERATOR, True, True, True, False, False, False),
        (Role.RESEARCHER, False, False, False, False, False, False),
    ],
)
def test_role_permissions(role, phi, units, audit, export, users, metadata):
    assert can_view_phi(role) is phi
    assert can_handle_units(role) is units
    assert can_view_audit(role) is audit
    assert can_export_records(role) is export
    assert can_manage_users(role) is users
    assert can_view_metadata(role) is metadata


def test_parse_role_accepts_stored_codes():
    assert parse_role("RESEARCHER") is Role.RESEARCHER


def test_parse_role_rejects_unknown_codes():
    with pytest.raises(ValueError):
        parse_role("GUEST")
