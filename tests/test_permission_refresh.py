"""Tests for _refresh_user_permissions: session syncs with users.json.

rol/tabs used to be cached in the session at login, so permission
changes (or account deletion!) only took effect when the user logged
back in. The before_request hook now re-reads the user record on
every request. These tests pin the three behaviors that matter:
revoked tab → immediate 403, granted tab → immediate access,
deleted user → immediate logout.
"""

from core import users as core_users
from tests._helpers import csrf_from_html


def _login_as(client, username, password):
    r = client.get("/login")
    csrf = csrf_from_html(r.get_data(as_text=True))
    r = client.post(
        "/login",
        data={"_csrf": csrf, "username": username, "password": password},
        follow_redirects=False,
    )
    assert r.status_code in (302, 303)


def test_revoked_tab_applies_on_next_request(client):
    core_users.create_user("anna", "passw0rd!", "gebruiker", ["agenda", "geluiden"])
    _login_as(client, "anna", "passw0rd!")
    assert client.get("/geluiden").status_code == 200

    # Admin takes the tab away — no re-login involved.
    core_users.update_user("anna", tabs=["agenda"])
    assert client.get("/geluiden").status_code == 403
    assert client.get("/agenda").status_code == 200


def test_granted_tab_applies_on_next_request(client):
    core_users.create_user("anna", "passw0rd!", "gebruiker", ["agenda"])
    _login_as(client, "anna", "passw0rd!")
    assert client.get("/geluiden").status_code == 403

    core_users.update_user("anna", tabs=["agenda", "geluiden"])
    assert client.get("/geluiden").status_code == 200


def test_deleted_user_is_logged_out_immediately(client):
    core_users.create_user("anna", "passw0rd!", "gebruiker", ["agenda"])
    _login_as(client, "anna", "passw0rd!")
    assert client.get("/agenda").status_code == 200

    core_users.delete_user("anna")
    # The still-valid session cookie must no longer grant anything:
    # the hook clears the session and the decorator redirects to
    # /login as if the request were anonymous.
    r = client.get("/agenda", follow_redirects=False)
    assert r.status_code in (302, 303)
    assert "/login" in r.headers["Location"]
    with client.session_transaction() as sess:
        assert "user" not in sess


def test_promotion_to_admin_applies_on_next_request(client):
    core_users.create_user("anna", "passw0rd!", "gebruiker", ["agenda"])
    _login_as(client, "anna", "passw0rd!")
    assert client.get("/gebruikers").status_code == 403

    core_users.update_user("anna", rol="admin")
    assert client.get("/gebruikers").status_code == 200
