"""
Tests for the /healthz endpoint.

The route delegates each individual check to other code paths
(filesystem checks via os.* directly, settings via Settings.load,
daemon liveness via get_daemon_heartbeat). What this file pins
down is the *response shape* and the *200 vs 503 decision*: every
check key is always present, the status string is consistent
with the HTTP code, and a single failing check tips the whole
response into 503.
"""

import os

import pytest

import webinterface


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Flask test client with state dirs pointed at a tmp tree.

    /healthz writes a probe file and lists the audio dir, so we
    redirect both DATA_DIR and AUDIO_DIR away from the real install
    to avoid leaving probes in the user's data folder during a test.
    """
    monkeypatch.setattr(webinterface, "DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(webinterface, "AUDIO_DIR", str(tmp_path / "audio"))
    monkeypatch.setattr(
        webinterface,
        "DAEMON_HEARTBEAT_PATH",
        str(tmp_path / "data" / "daemon_heartbeat.json"),
    )
    os.makedirs(str(tmp_path / "data"))
    os.makedirs(str(tmp_path / "audio"))
    return webinterface.app.test_client()


def test_healthz_without_heartbeat_returns_503(client):
    # No heartbeat file -> daemon_alive=false -> overall degraded.
    # The other three checks should all be OK in this fixture.
    r = client.get("/healthz")
    assert r.status_code == 503
    body = r.get_json()
    assert body["status"] == "degraded"
    checks = body["checks"]
    assert checks["data_dir_writable"] is True
    assert checks["audio_dir_readable"] is True
    assert checks["settings_loadable"] is True
    assert checks["daemon_alive"] is False


def test_healthz_with_fresh_heartbeat_returns_200(client, tmp_path):
    from datetime import datetime, timezone
    import json
    hb = tmp_path / "data" / "daemon_heartbeat.json"
    hb.write_text(json.dumps({"last_poll_at": datetime.now(timezone.utc).isoformat()}))

    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.get_json()
    assert body["status"] == "ok"
    assert body["checks"]["daemon_alive"] is True


def test_healthz_does_not_require_login(client):
    # Monitoring probes don't carry session cookies. Make sure the
    # endpoint is reachable without authentication. (A bug in the
    # past was that adding @ui_login_required to a similar admin
    # route would have killed external uptime checks.)
    r = client.get("/healthz")
    # Any of 200/503 means the route ran. A 302 redirect to /login
    # would mean we accidentally locked it down.
    assert r.status_code in (200, 503), f"unexpected status {r.status_code}"


def test_healthz_reports_audio_dir_failure(client, tmp_path, monkeypatch):
    # Point AUDIO_DIR somewhere os.listdir will fail. The healthz
    # response should still come back 503 (rather than 500) and the
    # audio_dir check key should be present and false — a monitoring
    # tool can then alert on the specific failure.
    monkeypatch.setattr(webinterface, "AUDIO_DIR", str(tmp_path / "does-not-exist"))
    r = client.get("/healthz")
    assert r.status_code == 503
    body = r.get_json()
    assert body["checks"]["audio_dir_readable"] is False
    assert "audio_dir_error" in body["checks"]


def test_healthz_error_leaks_no_paths(client, tmp_path, monkeypatch):
    # /healthz is reachable without login, so a failed check must not
    # echo raw exception text (which contains filesystem paths) to
    # the caller. Only the exception *type* may appear; the full
    # message goes to the server log. Pins the CodeQL finding
    # 'Information exposure through an exception'.
    monkeypatch.setattr(webinterface, "AUDIO_DIR", str(tmp_path / "does-not-exist"))
    r = client.get("/healthz")
    body = r.get_json()
    err = body["checks"]["audio_dir_error"]
    assert err == "FileNotFoundError"
    assert "/" not in err and str(tmp_path) not in err


def test_healthz_response_has_all_check_keys(client):
    # Pin the documented contract: every named check is present in
    # the response so a monitoring config can pick a specific key
    # to alert on, even when the overall status is 'ok'.
    r = client.get("/healthz")
    body = r.get_json()
    for key in (
        "data_dir_writable",
        "audio_dir_readable",
        "settings_loadable",
        "daemon_alive",
        "daemon_last_poll_at",
        "daemon_age_seconds",
        "daemon_threshold_seconds",
    ):
        assert key in body["checks"], f"missing check key: {key}"
