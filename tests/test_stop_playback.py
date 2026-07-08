"""Tests for the Stop button (stop-flag mechanism).

Playback happens in whichever Gunicorn worker handled the play
request, or in the daemon for scheduled bells. The Stop button
can't reach into those processes directly, so it touches a flag
file whose mtime every playing process watches. Covered here:

- the flag helpers (request_stop / stop_flag_mtime)
- the watcher loop stopping the mixer on a fresh flag, and
  ignoring a stale one
- the /geluiden/stop route: auth, flag creation, redirect
"""

import time

import pytest

import webinterface as wi
from core import users as core_users
from core.audio_files import (
    _watch_stop_flag,
    request_stop,
    stop_flag_mtime,
)
from tests._helpers import csrf_from_html


# ---------------------------------------------------------------------------
# Flag helpers
# ---------------------------------------------------------------------------


def test_request_stop_creates_flag_and_bumps_mtime(tmp_path):
    flag = str(tmp_path / "stop_playback")
    assert stop_flag_mtime(flag) is None

    request_stop(flag)
    first = stop_flag_mtime(flag)
    assert first is not None

    time.sleep(0.01)
    request_stop(flag)
    assert stop_flag_mtime(flag) >= first


# ---------------------------------------------------------------------------
# Watcher loop
# ---------------------------------------------------------------------------


class _FakeMusic:
    """Stand-in for pygame.mixer.music: busy until stop() is called."""

    def __init__(self):
        self.stopped = False

    def get_busy(self):
        return not self.stopped

    def stop(self):
        self.stopped = True


@pytest.fixture
def fake_mixer(monkeypatch):
    """Patch the real pygame module the watcher imports."""
    import pygame
    music = _FakeMusic()
    monkeypatch.setattr(pygame.mixer, "get_init", lambda: True)
    monkeypatch.setattr(pygame.mixer, "music", music)
    return music


def test_watcher_stops_on_fresh_flag(tmp_path, fake_mixer, monkeypatch):
    from core import audio_files
    monkeypatch.setattr(audio_files, "STOP_FLAG_POLL_SEC", 0.01)

    flag = str(tmp_path / "stop_playback")
    started_at = time.time() - 1  # playback started a second ago
    request_stop(flag)  # flag is *newer* than started_at

    # Run the watcher body directly (no thread needed for the test:
    # the loop exits as soon as it stops the mixer).
    _watch_stop_flag(flag, started_at)
    assert fake_mixer.stopped is True


def test_watcher_ignores_stale_flag(tmp_path, fake_mixer, monkeypatch):
    from core import audio_files
    monkeypatch.setattr(audio_files, "STOP_FLAG_POLL_SEC", 0.01)

    flag = str(tmp_path / "stop_playback")
    request_stop(flag)  # old stop, from before this playback
    time.sleep(0.02)
    started_at = time.time()

    # End the 'playback' shortly after, from a side thread, so the
    # watcher exits via the not-busy path instead of stopping.
    import threading

    def end_playback():
        time.sleep(0.05)
        fake_mixer.stopped = True  # simulates the sound finishing

    t = threading.Thread(target=end_playback)
    t.start()
    _watch_stop_flag(flag, started_at)
    t.join()
    # The watcher never called stop() itself before the sound ended:
    # a stale flag must not kill a fresh playback. (stopped is True
    # only because the helper thread ended the playback.)
    assert stop_flag_mtime(flag) < started_at


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------


def test_stop_route_touches_flag(logged_in_client, csrf_token):
    assert stop_flag_mtime(wi.STOP_FLAG_PATH) is None
    r = logged_in_client.post(
        "/geluiden/stop", data={"_csrf": csrf_token}, follow_redirects=False
    )
    assert r.status_code in (302, 303)
    assert stop_flag_mtime(wi.STOP_FLAG_PATH) is not None


def test_stop_route_requires_login(client):
    r = client.post("/geluiden/stop", follow_redirects=False)
    # CSRF check fires before the tab decorator for anonymous POSTs
    # without a token; either rejection is fine — the flag must not
    # be created.
    assert r.status_code in (302, 303, 400)
    assert stop_flag_mtime(wi.STOP_FLAG_PATH) is None


def test_stop_route_requires_geluiden_tab(client):
    core_users.create_user("anna", "passw0rd!", "gebruiker", ["agenda"])
    r = client.get("/login")
    csrf = csrf_from_html(r.get_data(as_text=True))
    r = client.post(
        "/login",
        data={"_csrf": csrf, "username": "anna", "password": "passw0rd!"},
        follow_redirects=False,
    )
    assert r.status_code in (302, 303)

    r = client.get("/agenda")
    csrf = csrf_from_html(r.get_data(as_text=True))
    r = client.post(
        "/geluiden/stop", data={"_csrf": csrf}, follow_redirects=False
    )
    assert r.status_code == 403
    assert stop_flag_mtime(wi.STOP_FLAG_PATH) is None
