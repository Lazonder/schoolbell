"""Tests for the in-memory login rate limiter.

The throttle refuses further login attempts from an IP after
LOGIN_MAX_FAILURES failed tries within LOGIN_WINDOW_SEC. Keyed on
request.remote_addr; a successful login clears the counter.

State is module-global in blueprints.auth; the autouse fixture in
conftest resets it around every test, so each test builds its own
failure history.
"""

import time

from blueprints import auth as auth_bp
from tests._helpers import TEST_PASSWORD, csrf_from_html


def _attempt(client, password: str):
    """One POST to /login with a fresh CSRF token. Returns response."""
    r = client.get("/login")
    csrf = csrf_from_html(r.get_data(as_text=True))
    return client.post(
        "/login",
        data={"_csrf": csrf, "username": "admin", "password": password},
        follow_redirects=False,
    )


def test_throttle_kicks_in_after_max_failures(client):
    for _ in range(auth_bp.LOGIN_MAX_FAILURES):
        r = _attempt(client, "wrong-password")
        assert r.status_code == 200  # form re-rendered with flash

    # Attempt N+1 is refused with 429 — even with correct credentials.
    r = _attempt(client, TEST_PASSWORD)
    assert r.status_code == 429


def test_successful_login_clears_failure_count(client):
    # A few failures, then success: counter must reset so the next
    # failures start counting from zero again.
    for _ in range(3):
        _attempt(client, "wrong-password")
    r = _attempt(client, TEST_PASSWORD)
    assert r.status_code in (302, 303)
    assert "127.0.0.1" not in auth_bp._failed_logins


def test_throttle_expires_after_window(client, monkeypatch):
    for _ in range(auth_bp.LOGIN_MAX_FAILURES):
        _attempt(client, "wrong-password")
    assert _attempt(client, TEST_PASSWORD).status_code == 429

    # Slide the window: pretend LOGIN_WINDOW_SEC has passed by
    # shifting time.monotonic forward. Patched on the blueprint's
    # module so only the limiter sees the fake clock.
    real_monotonic = time.monotonic
    monkeypatch.setattr(
        auth_bp.time,
        "monotonic",
        lambda: real_monotonic() + auth_bp.LOGIN_WINDOW_SEC + 1,
    )
    r = _attempt(client, TEST_PASSWORD)
    assert r.status_code in (302, 303)
