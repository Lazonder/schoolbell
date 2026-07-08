"""Tests for the @tab_required decorator and the root redirect.

Coverage matrix (the decorator's behaviour, pinned by these tests):

                 | HTML route            | API route (/api/*)
    -------------|-----------------------|-------------------------
    Anonymous    | 302 to /login?next=.. | 401 auth_required
    No tab       | 403                   | 403 tab_required
    Has tab      | 200                   | 200
    Admin (["*"])| 200                   | 200

Also: the bare ``/`` redirect sends each user to the first tab they
can see — admins to /agenda, restricted users to wherever their first
allowed tab lives, users without any tab back to /login.
"""

from core import users as core_users
from tests._helpers import csrf_from_html


# ---- Helpers --------------------------------------------------------


def _login(client, username: str, password: str):
    """Post to /login. Returns the redirect response so callers can
    assert on it. Keeps each test focused on its actual behaviour."""
    r = client.get("/login")
    csrf = csrf_from_html(r.get_data(as_text=True))
    return client.post(
        "/login",
        data={"_csrf": csrf, "username": username, "password": password},
        follow_redirects=False,
    )


# ---- HTML route gating ---------------------------------------------


def test_html_route_anonymous_redirects_to_login(client):
    """Anonymous user hitting an HTML tab route lands on /login."""
    r = client.get("/agenda", follow_redirects=False)
    assert r.status_code in (301, 302, 303)
    assert "/login" in r.headers["Location"]
    # The next= query param preserves the original target so the user
    # bounces back to /agenda after a successful login.
    assert "next=" in r.headers["Location"]


def test_html_route_without_tab_returns_403(client):
    """A logged-in user without the tab gets a 403 page."""
    core_users.create_user("anna", "passw0rd!", "gebruiker", ["agenda"])
    assert _login(client, "anna", "passw0rd!").status_code in (302, 303)
    r = client.get("/roosters", follow_redirects=False)
    assert r.status_code == 403


def test_html_route_with_tab_returns_200(client):
    """A logged-in user with the tab can render the page."""
    core_users.create_user("anna", "passw0rd!", "gebruiker", ["roosters"])
    assert _login(client, "anna", "passw0rd!").status_code in (302, 303)
    r = client.get("/roosters")
    assert r.status_code == 200


def test_admin_passes_every_tab(logged_in_client):
    """Admins (tabs == ["*"]) can hit every tab without explicit grant."""
    for path in ("/agenda", "/roosters", "/standaardweek",
                 "/geluiden", "/logs", "/settings"):
        r = logged_in_client.get(path)
        assert r.status_code == 200, f"admin blocked from {path}"


# ---- API route gating ----------------------------------------------


def test_api_route_anonymous_returns_401_json(client):
    """An anonymous fetch() to an /api/ tab route gets JSON 401, not
    a silent redirect to /login (which fetch() would follow and the
    page's JS would never see the auth failure)."""
    r = client.get("/api/settings")
    assert r.status_code == 401
    assert r.get_json() == {"error": "auth_required"}


def test_api_route_without_tab_returns_403_json(client):
    core_users.create_user("anna", "passw0rd!", "gebruiker", ["agenda"])
    assert _login(client, "anna", "passw0rd!").status_code in (302, 303)
    r = client.get("/api/settings")
    assert r.status_code == 403
    assert r.get_json() == {"error": "tab_required"}


def test_api_route_with_tab_returns_200(client):
    core_users.create_user(
        "anna", "passw0rd!", "gebruiker", ["settings"]
    )
    assert _login(client, "anna", "passw0rd!").status_code in (302, 303)
    r = client.get("/api/settings")
    assert r.status_code == 200
    # Body shape is just a sanity check — Settings.load() returns a
    # dataclass dump. We only care that we got past the gate.
    body = r.get_json()
    assert isinstance(body, dict)
    assert "taal" in body


# ---- standaardweek has its own tab key ------------------------------


def test_standaardweek_is_separate_from_roosters(client):
    """A user with ONLY 'roosters' can't reach /standaardweek.

    Pin the design choice that /standaardweek is a separate tab key
    rather than rolled into /roosters. If a future refactor merges
    them, this test fails and forces a deliberate decision.
    """
    core_users.create_user("anna", "passw0rd!", "gebruiker", ["roosters"])
    assert _login(client, "anna", "passw0rd!").status_code in (302, 303)
    assert client.get("/roosters").status_code == 200
    assert client.get("/standaardweek").status_code == 403


# ---- Root redirect ('/') sends each user to their landing page -----


def test_root_anonymous_redirects_to_login(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code in (301, 302, 303)
    assert "/login" in r.headers["Location"]


def test_root_admin_redirects_to_agenda(logged_in_client):
    r = logged_in_client.get("/", follow_redirects=False)
    assert r.status_code in (302, 303)
    # Admins land on /agenda — the first entry in TAB_ORDER.
    assert r.headers["Location"].endswith("/agenda")


def test_root_redirects_to_first_accessible_tab(client):
    """A user with limited tabs lands on their FIRST tab in nav order.

    "geluiden" comes after "agenda"/"roosters"/"standaardweek" in
    TAB_ORDER, so a geluiden-only user gets /geluiden as their
    landing page — not /agenda (which they don't have access to).
    """
    core_users.create_user("anna", "passw0rd!", "gebruiker", ["geluiden"])
    assert _login(client, "anna", "passw0rd!").status_code in (302, 303)
    r = client.get("/", follow_redirects=False)
    assert r.status_code in (302, 303)
    assert r.headers["Location"].endswith("/geluiden")


def test_login_lands_restricted_user_on_their_own_tab(client):
    """After login (no explicit ?next=), users go to their first tab.

    Pre-fix, the login route hard-coded a redirect to /roosters,
    which 403'd any user without the "roosters" tab — exactly the
    case multi-user is meant to support. The fix sends every user
    through monitoring.home, which then picks the right tab.

    The test follows the post-login redirects to confirm the user
    actually lands on a 200 page rather than the 403 they would
    have hit before.
    """
    core_users.create_user("anna", "passw0rd!", "gebruiker", ["geluiden"])
    r = client.get("/login")
    csrf = csrf_from_html(r.get_data(as_text=True))
    r = client.post(
        "/login",
        data={"_csrf": csrf, "username": "anna", "password": "passw0rd!"},
        follow_redirects=True,
    )
    assert r.status_code == 200
    # Final hop should be /geluiden — anna's only tab.
    assert r.request.path == "/geluiden"


def test_root_logs_out_user_without_any_tab(client):
    """An empty tabs list shouldn't trap the user in a 403 loop.

    Edge case for misconfigured accounts: an admin created a user
    and then explicitly granted them no tabs. The bare-site route
    clears their session and sends them to /login so they can at
    least leave gracefully. (It used to redirect via GET /logout,
    but that route is POST-only now.)
    """
    core_users.create_user("ghost", "passw0rd!", "gebruiker", [])
    assert _login(client, "ghost", "passw0rd!").status_code in (302, 303)
    r = client.get("/", follow_redirects=False)
    assert r.status_code in (302, 303)
    assert r.headers["Location"].endswith("/login")
    # Session really is gone: the next request is anonymous.
    with client.session_transaction() as sess:
        assert "user" not in sess


# ---- mag_tab in the rendered template ------------------------------


def test_nav_only_shows_accessible_tabs_for_restricted_user(client):
    """A restricted user only sees their own tabs in the nav.

    This is a UI affordance, not a security boundary: the gating is
    server-side (above tests). But it matters for usability — a
    "geluiden"-only user shouldn't see Agenda / Roosters links that
    they couldn't click anyway.
    """
    core_users.create_user("anna", "passw0rd!", "gebruiker", ["geluiden"])
    assert _login(client, "anna", "passw0rd!").status_code in (302, 303)
    r = client.get("/geluiden")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    # The link they HAVE access to should be there.
    assert "/geluiden" in html
    # Links they DON'T have access to should not. Checking the URL
    # rather than the label is more robust against translation
    # changes ("Voorkeuren" might be "Settings" in another locale).
    assert "/agenda" not in html
    assert "/roosters" not in html
    assert "/standaardweek" not in html
    assert "/settings" not in html


def test_nav_shows_everything_for_admin(logged_in_client):
    """An admin sees every nav link, regardless of TAB_ORDER changes."""
    r = logged_in_client.get("/agenda")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    for path in ("/agenda", "/roosters", "/standaardweek",
                 "/geluiden", "/logs", "/settings"):
        assert path in html, f"admin nav missing {path}"
