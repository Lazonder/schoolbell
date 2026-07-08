"""Tests for the defensive HTTP headers added in webinterface._security_headers.

Every response — authed page, public page, JSON API, even errors —
must carry the three headers. A couple of representative routes are
sampled rather than the full route table; the after_request hook is
route-agnostic, so if these pass the rest follows.
"""

EXPECTED = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "SAMEORIGIN",
    "Referrer-Policy": "same-origin",
}


def _assert_headers(resp):
    for name, value in EXPECTED.items():
        assert resp.headers.get(name) == value, f"{name} missing/wrong"


def test_headers_on_public_login_page(client):
    _assert_headers(client.get("/login"))


def test_headers_on_public_now_page(client):
    _assert_headers(client.get("/now"))


def test_headers_on_json_api(client):
    _assert_headers(client.get("/api/now"))


def test_headers_on_redirect_response(client):
    # Anonymous hit on a protected page → 302 to /login. Redirects
    # must carry the headers too.
    _assert_headers(client.get("/roosters", follow_redirects=False))
