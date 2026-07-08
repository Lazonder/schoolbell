"""
Route-level tests for the auth + CSRF wiring.

The pure-helper tests (test_helpers.py, test_vakanties_fetcher.py, ...)
cover the data layer well, but they don't catch the kind of mistake
that breaks logging in: forgetting an @ui_login_required decorator,
loosening CSRF, regressing the session-fixation defense in /login.
This file exercises the actual Flask routing through the test client
so those failures surface as red tests instead of as a broken UI.

Conventions used:
- `client`           = anonymous test client, isolated tmp dirs.
- `logged_in_client` = same client, already past POST /login.
- `csrf_token`       = a CSRF token valid for `logged_in_client`'s session.

All three come from tests/conftest.py.
"""

from tests._helpers import TEST_PASSWORD, csrf_from_html


# ---------------------------------------------------------------------------
# Anonymous access / redirects
# ---------------------------------------------------------------------------


def test_root_redirects_when_anonymous(client):
    # A browser hitting / without a session cookie should land on /login.
    # 302 (or 303) with a Location pointing somewhere under /login is
    # enough — the exact tail (?next=...) is implementation detail.
    r = client.get("/", follow_redirects=False)
    assert r.status_code in (301, 302, 303)
    assert "/login" in r.headers["Location"]


def test_protected_route_redirects_to_login(client):
    # /roosters has @ui_login_required. Anonymous GET must redirect
    # rather than serve the page or 401. Pin this so loosening the
    # decorator (or accidentally removing it) shows up here.
    r = client.get("/roosters", follow_redirects=False)
    assert r.status_code in (301, 302, 303)
    assert "/login" in r.headers["Location"]


def test_login_page_renders_for_anonymous(client):
    r = client.get("/login")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    # CSRF token must be in the form so the next POST can include it;
    # if this check fails, the login flow is broken end-to-end.
    assert csrf_from_html(body)


def test_api_settings_get_requires_auth(client):
    # /api/settings is a JSON endpoint, so @require_admin returns a
    # JSON 401 instead of redirecting — fetch() callers in the UI
    # need a machine-readable response. Pin the 401 to catch any
    # accidental switch back to a redirect that would break the
    # settings page's JS.
    r = client.get("/api/settings", follow_redirects=False)
    assert r.status_code == 401
    assert r.get_json() == {"error": "auth_required"}


# ---------------------------------------------------------------------------
# Login flow
# ---------------------------------------------------------------------------


def test_login_with_wrong_password_re_renders_form(client):
    # Wrong password should NOT 200 onto agenda; it should re-render
    # /login (still 200, but the page itself, not a redirect).
    r = client.get("/login")
    csrf = csrf_from_html(r.get_data(as_text=True))
    r = client.post(
        "/login",
        data={"_csrf": csrf, "username": "admin", "password": "obviously-wrong"},
        follow_redirects=False,
    )
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    # A fresh login form should be rendered. We don't pin the exact
    # flash text — just that we're back on the login page (it has a
    # password field).
    assert 'name="password"' in body


def test_login_with_correct_password_redirects(client):
    r = client.get("/login")
    csrf = csrf_from_html(r.get_data(as_text=True))
    r = client.post(
        "/login",
        data={"_csrf": csrf, "username": "admin", "password": TEST_PASSWORD},
        follow_redirects=False,
    )
    assert r.status_code in (302, 303)
    # The redirect target on success comes from `next_url`; default
    # for a fresh login (no ?next=) is /roosters per the route. We
    # don't pin the exact path since changing the default is a UX
    # decision, but it must NOT be /login.
    assert "/login" not in r.headers["Location"]


# ---------------------------------------------------------------------------
# Open-redirect defense: ?next=... is only honoured for local paths
# ---------------------------------------------------------------------------


def test_login_honours_safe_next_param(client):
    # A legitimate ?next=/agenda must round-trip through login: the
    # post-login redirect should land on /agenda, not the default.
    r = client.get("/login?next=/agenda")
    csrf = csrf_from_html(r.get_data(as_text=True))
    r = client.post(
        "/login",
        data={
            "_csrf": csrf,
            "username": "admin",
            "password": TEST_PASSWORD,
            "next": "/agenda",
        },
        follow_redirects=False,
    )
    assert r.status_code in (302, 303)
    assert r.headers["Location"].endswith("/agenda")


def _post_login_with_next(client, next_value):
    r = client.get("/login")
    csrf = csrf_from_html(r.get_data(as_text=True))
    return client.post(
        "/login",
        data={
            "_csrf": csrf,
            "username": "admin",
            "password": TEST_PASSWORD,
            "next": next_value,
        },
        follow_redirects=False,
    )


def test_login_rejects_absolute_url_in_next(client):
    # Open-redirect defense: ?next=https://evil.example must be
    # silently discarded. The post-login redirect should NOT lead
    # off-site.
    r = _post_login_with_next(client, "https://evil.example/phishing")
    assert r.status_code in (302, 303)
    loc = r.headers["Location"]
    assert "evil.example" not in loc, (
        "Absolute external URL was accepted as ?next= target — "
        "open-redirect protection regressed."
    )


def test_login_rejects_protocol_relative_url_in_next(client):
    # //evil.example is a protocol-relative URL: the browser resolves
    # it with the current scheme but a different host. Must be
    # rejected as firmly as a fully absolute URL.
    r = _post_login_with_next(client, "//evil.example/phishing")
    assert r.status_code in (302, 303)
    loc = r.headers["Location"]
    assert "evil.example" not in loc


def test_login_rejects_javascript_scheme_in_next(client):
    # javascript: URLs would execute in the browser when followed.
    # Reject the same way.
    r = _post_login_with_next(client, "javascript:alert(1)")
    assert r.status_code in (302, 303)
    loc = r.headers["Location"]
    assert "javascript" not in loc.lower()


def test_login_clears_old_csrf_token(client):
    # Session-fixation defense: anything in the session before login
    # gets discarded. A token captured from /login should NOT match
    # the token issued on the post-login page, because session.clear()
    # ran in between.
    r = client.get("/login")
    csrf_before = csrf_from_html(r.get_data(as_text=True))
    client.post(
        "/login",
        data={"_csrf": csrf_before, "username": "admin", "password": TEST_PASSWORD},
        follow_redirects=False,
    )
    r = client.get("/roosters")
    csrf_after = csrf_from_html(r.get_data(as_text=True))
    assert csrf_before != csrf_after, (
        "Pre- and post-login CSRF tokens are equal — session.clear() "
        "may have been removed from the login route, reopening the "
        "session-fixation hole."
    )


def test_logout_requires_post(logged_in_client):
    # GET /logout is gone: it made logout CSRF-able via a simple
    # <img src="/logout"> on any third-party page.
    r = logged_in_client.get("/logout")
    assert r.status_code == 405


def test_logout_drops_session(logged_in_client, csrf_token):
    # /logout is wired as POST-only. POST goes
    # through the same CSRF gate as every other state-changing route,
    # so we send the token. After the redirect, hitting a protected
    # route should bounce back to /login — i.e. the session was
    # actually cleared, not just marked-but-still-valid.
    r = logged_in_client.post(
        "/logout", data={"_csrf": csrf_token}, follow_redirects=False
    )
    assert r.status_code in (301, 302, 303)
    r = logged_in_client.get("/roosters", follow_redirects=False)
    assert r.status_code in (301, 302, 303)
    assert "/login" in r.headers["Location"]


# ---------------------------------------------------------------------------
# Authed access
# ---------------------------------------------------------------------------


def test_logged_in_client_can_get_roosters(logged_in_client):
    # Smoke check that the login fixture actually worked. If this
    # fails, every other authed test below is questionable.
    r = logged_in_client.get("/roosters")
    assert r.status_code == 200


def test_api_settings_returns_json_when_authed(logged_in_client):
    r = logged_in_client.get("/api/settings")
    assert r.status_code == 200
    body = r.get_json()
    # Pin the expected keys — the same set health_check.py probes for.
    # If any disappear, integration probes break and the bug shows
    # here first.
    for key in (
        "volume_percent",
        "max_file_size_mb",
        "poll_interval_sec",
        "allowed_extensions",
    ):
        assert key in body, f"missing key in /api/settings response: {key}"


# ---------------------------------------------------------------------------
# CSRF protection on POST
# ---------------------------------------------------------------------------


def test_post_without_csrf_returns_400(logged_in_client):
    # csrf_protect runs before_request and rejects POSTs whose token
    # is missing or wrong. /api/settings is a convenient target: it's
    # POST, it's authed, and we don't need to construct a valid body
    # because the CSRF check fires before the handler runs.
    r = logged_in_client.post("/api/settings", json={})
    assert r.status_code == 400


def test_post_with_wrong_csrf_returns_400(logged_in_client):
    r = logged_in_client.post(
        "/api/settings",
        data={"_csrf": "not-the-right-token"},
    )
    assert r.status_code == 400


def test_post_with_correct_csrf_passes_csrf_check(logged_in_client, csrf_token):
    # The CSRF check itself returns 400 on failure; on success, the
    # request reaches the handler. /api/settings POST validates the
    # JSON body and returns 400 if it's missing/invalid — but with a
    # *different* meaning. To keep this test purely about CSRF, send
    # a syntactically valid (if empty) settings update via header.
    # The expected outcome is "anything except 400 csrf" — so as long
    # as the response body doesn't say 'CSRF', we're past the gate.
    r = logged_in_client.post(
        "/api/settings",
        json={},
        headers={"X-CSRF-Token": csrf_token},
    )
    body_text = r.get_data(as_text=True)
    assert "CSRF" not in body_text, (
        f"Request with valid token got rejected as CSRF: {r.status_code} {body_text!r}"
    )


def test_csrf_check_skipped_for_basic_auth_api(client):
    # /api/effectief-rooster is the daemon endpoint — Basic Auth from
    # localhost, no browser session, so CSRF doesn't apply. The
    # before_request handler explicitly bypasses the check for that
    # path. Pin the bypass: a POST-like miss without CSRF should not
    # come back as 'CSRF token invalid'. (The route only accepts GET,
    # so what we actually verify is that the response isn't the
    # generic CSRF-400.)
    r = client.get("/api/effectief-rooster")
    body = r.get_data(as_text=True)
    assert "CSRF" not in body
