"""
Tests for the LAN-access toggle (Settings.lan_toegang).

Three layers under test:

1. webinterface.lan_toegang_filter — the before_request hook that
   refuses non-loopback clients when the setting is off.
2. webinterface._client_is_loopback — the address check, including
   the X-Forwarded-For spoofing defence (both the ProxyFix-resolved
   address AND the direct TCP peer must be loopback).
3. The lockout guard in blueprints.settings.api_settings_post —
   switching LAN access off is refused when the request itself comes
   from the network, because saving it would cut that admin off on
   their very next request.

The Flask test client connects as 127.0.0.1 by default; a remote
client is simulated with environ_overrides={"REMOTE_ADDR": ...}, and
the nginx-proxy situation with an X-Forwarded-For header on top.
"""

import json

import pytest

import settings_store
from blueprints.settings import _apply_settings_payload, _coerce_bool
from settings_store import Settings


LAN_IP = "192.168.1.50"


@pytest.fixture(autouse=True)
def temp_config(tmp_path, monkeypatch):
    """Point CONFIG_PATH at a fresh temp file for every test.

    Without this, Settings.load() inside the before_request hook
    would read the developer's real config.json — and the disable-
    tests below would then *write* to it via the API.
    """
    cfg = tmp_path / "config.json"
    monkeypatch.setattr(settings_store, "CONFIG_PATH", cfg)
    yield cfg


def _save_lan_toegang(value: bool):
    s = Settings()
    s.lan_toegang = value
    s.save()


# ---------------------------------------------------------------------------
# Default behavior: LAN access on
# ---------------------------------------------------------------------------


def test_default_is_lan_toegang_on():
    # Backwards compatibility: existing installs are reachable over
    # the LAN today, so an upgrade must not lock their admins out.
    assert Settings().lan_toegang is True


def test_lan_client_allowed_by_default(client):
    r = client.get("/login", environ_overrides={"REMOTE_ADDR": LAN_IP})
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# Toggle off: loopback only
# ---------------------------------------------------------------------------


def test_lan_client_refused_when_off(client):
    _save_lan_toegang(False)
    r = client.get("/login", environ_overrides={"REMOTE_ADDR": LAN_IP})
    assert r.status_code == 403


def test_loopback_client_still_allowed_when_off(client):
    _save_lan_toegang(False)
    r = client.get("/login")  # test client peer is 127.0.0.1
    assert r.status_code == 200


def test_proxied_lan_client_refused_when_off(client):
    # Normal install.sh setup: nginx (peer 127.0.0.1) forwards for a
    # LAN visitor via X-Forwarded-For. ProxyFix resolves remote_addr
    # to the visitor's IP; the filter must refuse it.
    _save_lan_toegang(False)
    r = client.get(
        "/login",
        environ_overrides={"REMOTE_ADDR": "127.0.0.1"},
        headers={"X-Forwarded-For": LAN_IP},
    )
    assert r.status_code == 403


def test_spoofed_xff_from_lan_peer_refused_when_off(client):
    # No nginx in front (e.g. gunicorn bound to the network directly):
    # a LAN client forges X-Forwarded-For: 127.0.0.1. ProxyFix then
    # reports a loopback remote_addr, but the direct TCP peer betrays
    # the client. Must still be refused.
    _save_lan_toegang(False)
    r = client.get(
        "/login",
        environ_overrides={"REMOTE_ADDR": LAN_IP},
        headers={"X-Forwarded-For": "127.0.0.1"},
    )
    assert r.status_code == 403


def test_daemon_api_via_loopback_unaffected(client, monkeypatch):
    # The daemon polls /api/effectief-rooster over 127.0.0.1 with
    # Basic Auth. The filter must never stand in its way, or the bell
    # stops following schedule changes the moment LAN access is off.
    _save_lan_toegang(False)
    r = client.get("/api/effectief-rooster")
    # 401 (Basic Auth challenge), NOT 403: the request got past the
    # LAN filter and reached the endpoint's own authentication.
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# Lockout guard on POST /api/settings
# ---------------------------------------------------------------------------


def _post_settings(client, csrf, payload, **kwargs):
    return client.post(
        "/api/settings",
        data=json.dumps(payload),
        content_type="application/json",
        headers={"X-CSRF-Token": csrf, **kwargs.pop("headers", {})},
        **kwargs,
    )


def test_disable_from_loopback_succeeds(logged_in_client, csrf_token):
    r = _post_settings(logged_in_client, csrf_token, {"lan_toegang": False})
    assert r.status_code == 200
    assert Settings.load().lan_toegang is False


def test_disable_from_lan_refused(logged_in_client, csrf_token):
    r = _post_settings(
        logged_in_client,
        csrf_token,
        {"lan_toegang": False},
        environ_overrides={"REMOTE_ADDR": LAN_IP},
    )
    assert r.status_code == 400
    # And nothing was saved: the network stays reachable.
    assert Settings.load().lan_toegang is True


def test_enable_from_lan_allowed(logged_in_client, csrf_token):
    # Turning LAN access ON from the network is a no-op risk-wise
    # (it can only ever widen access for the requester), so the
    # guard must not block it.
    r = _post_settings(
        logged_in_client,
        csrf_token,
        {"lan_toegang": True},
        environ_overrides={"REMOTE_ADDR": LAN_IP},
    )
    assert r.status_code == 200
    assert Settings.load().lan_toegang is True


# ---------------------------------------------------------------------------
# Payload parsing
# ---------------------------------------------------------------------------


def test_apply_payload_accepts_bool_and_string_forms():
    s = Settings()
    _apply_settings_payload(s, {"lan_toegang": False})
    assert s.lan_toegang is False
    _apply_settings_payload(s, {"lan_toegang": "on"})
    assert s.lan_toegang is True
    _apply_settings_payload(s, {"lan_toegang": "false"})
    assert s.lan_toegang is False


def test_coerce_bool_common_forms():
    assert _coerce_bool(True) is True
    assert _coerce_bool("1") is True
    assert _coerce_bool("on") is True
    assert _coerce_bool("TRUE") is True
    assert _coerce_bool(False) is False
    assert _coerce_bool("0") is False
    assert _coerce_bool("off") is False
    assert _coerce_bool(0) is False
