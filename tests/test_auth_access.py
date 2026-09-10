"""Login gate and role screens, without touching SQL Server.

The clinical services talk to the database. These tests only ask Flask whether
a signed-in role is allowed to open a page, so they stay green in CI.
"""

from __future__ import annotations

import pytest

from web import create_app


@pytest.fixture
def client():
    application = create_app()
    application.config["TESTING"] = True
    with application.test_client() as test_client:
        yield test_client


def _sign_in(client, username: str, role: str, display_name: str = "בודק") -> None:
    with client.session_transaction() as session:
        session["auth_username"] = username
        session["auth_role"] = role
        session["auth_display_name"] = display_name
        session["_csrf_token"] = "test-csrf-token"


def test_login_page_is_public(client):
    response = client.get("/login")
    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert "admin" in page
    assert "researcher" in page


def test_anonymous_visitor_is_sent_to_login(client):
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_state_changing_login_without_csrf_is_rejected(client):
    response = client.post(
        "/login",
        data={"username": "admin", "password": "Admin123!"},
        follow_redirects=False,
    )
    assert response.status_code == 400


def test_researcher_cannot_open_intake_or_the_full_audit(client):
    _sign_in(client, "researcher", "RESEARCHER")
    assert client.get("/donations").status_code == 403
    assert client.get("/audit").status_code == 403
    assert client.get("/dispense/routine").status_code == 403
    assert client.get("/users").status_code == 403
    assert client.get("/metadata").status_code == 403


def test_researcher_home_hides_clinical_actions(client):
    _sign_in(client, "researcher", "RESEARCHER")
    page = client.get("/").get_data(as_text=True)
    assert "קליטת תרומה" not in page
    assert "ניפוק בשגרה" not in page
    assert "ניהול משתמשים" not in page
    assert "מלאי ולוג פעולות" in page


def test_operator_may_open_intake_but_not_user_admin(client):
    _sign_in(client, "operator", "OPERATOR")
    assert client.get("/donations").status_code == 200
    assert client.get("/users").status_code == 403
    assert client.get("/metadata").status_code == 403
    page = client.get("/").get_data(as_text=True)
    assert "קליטת תרומה" in page
    assert "ניהול משתמשים" not in page


def test_admin_home_exposes_user_and_metadata_actions(client):
    _sign_in(client, "admin", "ADMIN")
    page = client.get("/").get_data(as_text=True)
    assert "קליטת תרומה" in page
    assert "ניהול משתמשים" in page
    assert "מטא-דאטה" in page
