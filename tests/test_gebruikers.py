"""Route-level tests for the user-management UI (step 4).

The user store itself is fully covered by test_users.py. These tests
focus on the HTTP layer: admin gating, form parsing, and the
flash-error path when :mod:`core.users` raises ValueError.
"""

from core import users as core_users
from tests._helpers import csrf_from_html


def _login(client, username, password):
    """Same helper as in test_tab_required.py — kept small and local."""
    r = client.get("/login")
    csrf = csrf_from_html(r.get_data(as_text=True))
    return client.post(
        "/login",
        data={"_csrf": csrf, "username": username, "password": password},
        follow_redirects=False,
    )


# ---- admin_page_required gating ------------------------------------


def test_lijst_redirects_anonymous_to_login(client):
    r = client.get("/gebruikers", follow_redirects=False)
    assert r.status_code in (301, 302, 303)
    assert "/login" in r.headers["Location"]
    # next= so the user bounces back here after signing in.
    assert "next=" in r.headers["Location"]


def test_lijst_forbidden_for_non_admin(client):
    """A regular gebruiker, even one with all the regular tabs, gets
    403 on /gebruikers. User-management is strictly admin-only."""
    core_users.create_user(
        "anna", "passw0rd!", "gebruiker",
        ["agenda", "roosters", "standaardweek", "geluiden",
         "logs", "settings"],
    )
    assert _login(client, "anna", "passw0rd!").status_code in (302, 303)
    r = client.get("/gebruikers")
    assert r.status_code == 403


def test_lijst_admin_sees_all_users(logged_in_client):
    """Admin sees the management page and finds every user listed."""
    core_users.create_user("anna", "passw0rd!", "gebruiker", ["agenda"])
    core_users.create_user("bob", "passw0rd!", "admin", ["*"])
    r = logged_in_client.get("/gebruikers")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    for name in ("admin", "anna", "bob"):
        assert name in html, f"missing {name} in users page"


# ---- Create user via POST -----------------------------------------


def test_nieuw_creates_user(logged_in_client, csrf_token):
    r = logged_in_client.post(
        "/gebruikers/nieuw",
        data={
            "_csrf": csrf_token,
            "username": "carla",
            "password": "passw0rd!",
            "rol": "gebruiker",
            "tabs": ["agenda", "geluiden"],
        },
        follow_redirects=False,
    )
    assert r.status_code in (302, 303)
    user = core_users.get_user("carla")
    assert user is not None
    assert user["rol"] == "gebruiker"
    assert user["tabs"] == ["agenda", "geluiden"]


def test_nieuw_normalises_admin_tabs(logged_in_client, csrf_token):
    """Admins get tabs=['*'] regardless of which checkboxes were
    ticked — same contract as core.users.create_user."""
    r = logged_in_client.post(
        "/gebruikers/nieuw",
        data={
            "_csrf": csrf_token,
            "username": "diana",
            "password": "passw0rd!",
            "rol": "admin",
            "tabs": ["agenda"],  # ignored, admin always gets ["*"]
        },
        follow_redirects=False,
    )
    assert r.status_code in (302, 303)
    assert core_users.get_user("diana")["tabs"] == ["*"]


def test_nieuw_invalid_username_flashes_error(logged_in_client, csrf_token):
    """A validation error from create_user becomes a flash message
    rather than a 500. The user isn't created."""
    r = logged_in_client.post(
        "/gebruikers/nieuw",
        data={
            "_csrf": csrf_token,
            "username": "Bad Name!",
            "password": "passw0rd!",
            "rol": "gebruiker",
            "tabs": ["agenda"],
        },
        follow_redirects=True,
    )
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "Fout" in html or "fout" in html
    # User not created.
    assert core_users.get_user("Bad Name!") is None
    assert core_users.get_user("bad name!") is None


# ---- Wijzig (update role/tabs) ------------------------------------


def test_wijzig_changes_tabs(logged_in_client, csrf_token):
    core_users.create_user("eva", "passw0rd!", "gebruiker", ["agenda"])
    r = logged_in_client.post(
        "/gebruikers/eva/wijzig",
        data={
            "_csrf": csrf_token,
            "rol": "gebruiker",
            "tabs": ["geluiden", "logs"],
        },
        follow_redirects=False,
    )
    assert r.status_code in (302, 303)
    assert core_users.get_user("eva")["tabs"] == ["geluiden", "logs"]


def test_wijzig_promote_to_admin(logged_in_client, csrf_token):
    core_users.create_user("frank", "passw0rd!", "gebruiker", ["agenda"])
    r = logged_in_client.post(
        "/gebruikers/frank/wijzig",
        data={
            "_csrf": csrf_token,
            "rol": "admin",
            "tabs": ["agenda"],  # ignored after promotion
        },
        follow_redirects=False,
    )
    assert r.status_code in (302, 303)
    user = core_users.get_user("frank")
    assert user["rol"] == "admin"
    assert user["tabs"] == ["*"]


def test_wijzig_demote_last_admin_blocked(logged_in_client, csrf_token):
    """Trying to demote the lone admin (the one the fixture logged
    in as) flashes an error, leaves the user untouched."""
    r = logged_in_client.post(
        "/gebruikers/admin/wijzig",
        data={
            "_csrf": csrf_token,
            "rol": "gebruiker",
            "tabs": ["agenda"],
        },
        follow_redirects=True,
    )
    assert r.status_code == 200
    assert "Fout" in r.get_data(as_text=True)
    assert core_users.get_user("admin")["rol"] == "admin"


# ---- Wachtwoord reset ---------------------------------------------


def test_wachtwoord_resets(logged_in_client, csrf_token):
    core_users.create_user("greta", "old-pass!", "gebruiker", ["agenda"])
    r = logged_in_client.post(
        "/gebruikers/greta/wachtwoord",
        data={"_csrf": csrf_token, "password": "brand-new!"},
        follow_redirects=False,
    )
    assert r.status_code in (302, 303)
    assert core_users.verify_user("greta", "old-pass!") is None
    assert core_users.verify_user("greta", "brand-new!") is not None


def test_wachtwoord_too_short_flashes_error(logged_in_client, csrf_token):
    core_users.create_user("hans", "old-pass!", "gebruiker", ["agenda"])
    r = logged_in_client.post(
        "/gebruikers/hans/wachtwoord",
        data={"_csrf": csrf_token, "password": "short"},
        follow_redirects=True,
    )
    assert r.status_code == 200
    assert "Fout" in r.get_data(as_text=True)
    # Old password still works — validation rejected the change.
    assert core_users.verify_user("hans", "old-pass!") is not None


# ---- Verwijder ----------------------------------------------------


def test_verwijder_removes_user(logged_in_client, csrf_token):
    core_users.create_user("ian", "passw0rd!", "gebruiker", ["agenda"])
    r = logged_in_client.post(
        "/gebruikers/ian/verwijder",
        data={"_csrf": csrf_token},
        follow_redirects=False,
    )
    assert r.status_code in (302, 303)
    assert core_users.get_user("ian") is None


def test_verwijder_last_admin_blocked(logged_in_client, csrf_token):
    """Deleting the only admin (the one logged in) is refused — the
    store's last-admin protection surfaces as a flash error."""
    r = logged_in_client.post(
        "/gebruikers/admin/verwijder",
        data={"_csrf": csrf_token},
        follow_redirects=True,
    )
    assert r.status_code == 200
    assert "Fout" in r.get_data(as_text=True)
    # Still here.
    assert core_users.get_user("admin") is not None


# ---- Nav-link visibility ------------------------------------------


def test_nav_shows_gebruikers_link_for_admin(logged_in_client):
    """Admin sees the 'Gebruikers' nav-link."""
    r = logged_in_client.get("/agenda")
    assert r.status_code == 200
    assert "/gebruikers" in r.get_data(as_text=True)


def test_nav_hides_gebruikers_link_for_non_admin(client):
    """Non-admin doesn't see the 'Gebruikers' link.

    Cosmetic — the route is also admin-gated server-side — but
    important for UX: a regular user shouldn't see a tab they can't
    open.
    """
    core_users.create_user(
        "jane", "passw0rd!", "gebruiker", ["agenda"]
    )
    assert _login(client, "jane", "passw0rd!").status_code in (302, 303)
    r = client.get("/agenda")
    assert r.status_code == 200
    assert "/gebruikers" not in r.get_data(as_text=True)
