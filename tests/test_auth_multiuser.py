"""Route-level tests for the multi-user auth wiring (step 2).

These tests pin the contract introduced by step 2 of the multi-user
plan:

* The env-var admin is migrated into ``users.json`` on first request
  via the ``before_request`` bootstrap hook.
* Logging in populates ``session["rol"]`` and ``session["tabs"]``,
  not just ``session["user"]``.
* ``ui_logged_in`` accepts any user, not only the legacy admin.

Step 3 will add tab-level access checks; those tests live elsewhere.
"""

import json

from core import users as core_users
from tests._helpers import TEST_PASSWORD, csrf_from_html


# ---- Bootstrap from env vars ---------------------------------------


def test_bootstrap_creates_admin_on_first_request(client, tmp_path):
    """First request to any route seeds users.json from the env admin.

    The conftest sets SCHOOLBELL_WEB_USER=admin and a pwhash for
    TEST_PASSWORD. After hitting /login (a public route), users.json
    should exist with one admin record.
    """
    # Sanity: users.json doesn't exist yet (fresh tmp dir).
    users_path = core_users.USERS_PATH
    # Drive any request to fire the before_request hook.
    client.get("/login")
    # Now the store should be populated.
    with open(users_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert "admin" in data
    assert data["admin"]["rol"] == "admin"
    assert data["admin"]["tabs"] == ["*"]


def test_bootstrap_is_idempotent(client):
    """Hitting requests twice doesn't duplicate or rewrite the admin."""
    client.get("/login")
    users_path = core_users.USERS_PATH
    with open(users_path, "r", encoding="utf-8") as f:
        first = json.load(f)
    client.get("/login")
    with open(users_path, "r", encoding="utf-8") as f:
        second = json.load(f)
    # Same dict, byte-for-byte. If bootstrap rewrote anything (e.g.
    # bumped aangemaakt on every call) the timestamps would differ.
    assert first == second


def test_bootstrap_skips_when_store_already_populated(client):
    """A pre-existing user store wins over the env admin.

    Use case: admin promoted themselves, renamed the env user but
    left the old admin in users.json. Bootstrap must not insert a
    second admin from the env.
    """
    # Pre-seed: write one regular admin under a different name.
    core_users.create_user("kees", "passw0rd!", "admin", ["*"])
    initial = core_users.load_users()
    # Now drive a request. Env admin is "admin", which is NOT yet in
    # the store, but bootstrap should still skip because the store
    # is non-empty.
    client.get("/login")
    assert core_users.load_users() == initial
    assert "admin" not in core_users.load_users()


# ---- Login populates the session ----------------------------------


def test_login_sets_user_rol_tabs(client):
    """A successful login stores user + rol + tabs in the session."""
    r = client.get("/login")
    csrf = csrf_from_html(r.get_data(as_text=True))
    r = client.post(
        "/login",
        data={"_csrf": csrf, "username": "admin", "password": TEST_PASSWORD},
        follow_redirects=False,
    )
    assert r.status_code in (302, 303), r.get_data(as_text=True)
    with client.session_transaction() as sess:
        assert sess["user"] == "admin"
        assert sess["rol"] == "admin"
        assert sess["tabs"] == ["*"]


def test_login_lowercases_username(client):
    """Typing "Admin" matches the stored "admin" record.

    The lowercase normalization is in both create_user and the login
    route. This test pins the route-level half: without it, the
    POST would fail and the user would see "Onjuiste inloggegevens".
    """
    r = client.get("/login")
    csrf = csrf_from_html(r.get_data(as_text=True))
    r = client.post(
        "/login",
        data={"_csrf": csrf, "username": "ADMIN", "password": TEST_PASSWORD},
        follow_redirects=False,
    )
    assert r.status_code in (302, 303)
    with client.session_transaction() as sess:
        # Stored as lowercase, not the typed-in form.
        assert sess["user"] == "admin"


def test_login_with_wrong_password_does_not_create_session(client):
    r = client.get("/login")
    csrf = csrf_from_html(r.get_data(as_text=True))
    r = client.post(
        "/login",
        data={"_csrf": csrf, "username": "admin", "password": "wrong"},
        follow_redirects=False,
    )
    # No redirect to a protected page, the login form re-renders.
    assert r.status_code == 200
    with client.session_transaction() as sess:
        assert "user" not in sess
        assert "rol" not in sess
        assert "tabs" not in sess


# ---- Non-admin users can also log in -------------------------------


def test_gebruiker_can_log_in_and_session_reflects_tabs(client):
    """A non-admin user logs in and gets their restricted tab list."""
    # Pre-seed a regular user. The bootstrap from env runs on the
    # first request, but it skips because the store is non-empty
    # by the time we get to /login.
    core_users.create_user(
        "anna", "passw0rd!", "gebruiker", ["agenda", "roosters"]
    )

    r = client.get("/login")
    csrf = csrf_from_html(r.get_data(as_text=True))
    r = client.post(
        "/login",
        data={"_csrf": csrf, "username": "anna", "password": "passw0rd!"},
        follow_redirects=False,
    )
    assert r.status_code in (302, 303)
    with client.session_transaction() as sess:
        assert sess["user"] == "anna"
        assert sess["rol"] == "gebruiker"
        assert sess["tabs"] == ["agenda", "roosters"]


# ---- Settings API gating -------------------------------------------


def test_settings_api_blocks_user_without_settings_tab(client):
    """A gebruiker without the "settings" tab cannot reach /api/settings.

    Step 3 swapped @require_admin for @tab_required("settings") on the
    settings endpoints. Result: an admin still passes (their tabs
    list is ["*"]), but a regular user only reaches the API if they
    were explicitly granted the "settings" tab.
    """
    # Create a regular user without "settings" and log them in.
    core_users.create_user("anna", "passw0rd!", "gebruiker", ["agenda"])
    r = client.get("/login")
    csrf = csrf_from_html(r.get_data(as_text=True))
    r = client.post(
        "/login",
        data={"_csrf": csrf, "username": "anna", "password": "passw0rd!"},
        follow_redirects=False,
    )
    assert r.status_code in (302, 303)

    # GET (no CSRF needed) is easier to assert on. The body shape
    # is part of the tab_required contract: fetch() callers can show
    # a specific error message based on this string.
    r = client.get("/api/settings")
    assert r.status_code == 403
    assert r.get_json() == {"error": "tab_required"}


# ---- Logout ----------------------------------------------------------


def test_logout_clears_role_and_tabs(logged_in_client, csrf_token):
    # POST /logout is CSRF-protected like every other POST; pass the
    # token alongside the session cookie that logged_in_client
    # already carries.
    r = logged_in_client.post("/logout", data={"_csrf": csrf_token})
    assert r.status_code in (302, 303)
    with logged_in_client.session_transaction() as sess:
        assert "user" not in sess
        assert "rol" not in sess
        assert "tabs" not in sess
