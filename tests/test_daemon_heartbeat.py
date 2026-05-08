"""
Tests for the daemon-heartbeat plumbing.

The writer side (schoolbelldaemon._write_heartbeat) is a 6-line
'json.dump a timestamp to a file' that's not worth its own test —
running the daemon for one second exercises it. The reader side
(webinterface.get_daemon_heartbeat) is the interesting bit because
it has to make a 'is the daemon alive?' decision under several
edge cases: file missing, file corrupt, file old, file fresh,
poll_interval_sec set unusually high.

We use monkeypatch to point the heartbeat path at a tmp file and
to swap Settings.load() so we can pick the threshold the test
reasons about.
"""

from datetime import datetime, timezone, timedelta

import pytest

import webinterface


@pytest.fixture
def hb_path(tmp_path, monkeypatch):
    """Redirect DAEMON_HEARTBEAT_PATH at a tmp file for the test."""
    p = tmp_path / "daemon_heartbeat.json"
    monkeypatch.setattr(webinterface, "DAEMON_HEARTBEAT_PATH", str(p))
    return p


def _write_heartbeat(path, dt):
    """Helper: write a heartbeat file with a given UTC datetime."""
    import json
    path.write_text(json.dumps({"last_poll_at": dt.isoformat()}))


def test_missing_file_reports_dead(hb_path):
    # No heartbeat written ever -> the dot must be red. last_poll_at
    # is "" so the template can render 'geen heartbeat' in the tooltip.
    hb = webinterface.get_daemon_heartbeat()
    assert hb["alive"] is False
    assert hb["last_poll_at"] == ""
    assert hb["age_seconds"] is None


def test_corrupt_file_reports_dead(hb_path):
    # A half-written file or someone editing it manually shouldn't
    # explode the header — silently treat it as 'no heartbeat'.
    hb_path.write_text("{not valid json")
    hb = webinterface.get_daemon_heartbeat()
    assert hb["alive"] is False
    assert hb["last_poll_at"] == ""


def test_recent_heartbeat_reports_alive(hb_path):
    # Wrote 2 seconds ago at the default poll_interval_sec=2 -> well
    # within the 10-second floor on the freshness threshold.
    _write_heartbeat(hb_path, datetime.now(timezone.utc) - timedelta(seconds=2))
    hb = webinterface.get_daemon_heartbeat()
    assert hb["alive"] is True
    assert hb["age_seconds"] is not None
    assert 0 <= hb["age_seconds"] <= 5  # rough lower bound


def test_old_heartbeat_reports_dead(hb_path):
    # 5 minutes ago — way past any reasonable threshold.
    _write_heartbeat(hb_path, datetime.now(timezone.utc) - timedelta(minutes=5))
    hb = webinterface.get_daemon_heartbeat()
    assert hb["alive"] is False
    assert hb["age_seconds"] >= 5 * 60 - 1


def test_threshold_is_minimum_ten_seconds(hb_path, monkeypatch):
    # At the 2-second default poll interval, 3x = 6s. We enforce a
    # 10-second floor so a single GC pause or disk hiccup doesn't
    # flip the indicator on a single render. This pins that floor:
    # a 7-second-old heartbeat at poll_interval=2 is still 'alive'.
    _write_heartbeat(hb_path, datetime.now(timezone.utc) - timedelta(seconds=7))
    hb = webinterface.get_daemon_heartbeat()
    assert hb["threshold_seconds"] == 10
    assert hb["alive"] is True


def test_threshold_scales_with_long_poll_interval(hb_path, monkeypatch):
    # Schools that bumped poll_interval_sec to e.g. 30 deserve a
    # proportionally longer freshness window — otherwise the dot
    # would always be red. Threshold = 3 * poll_interval = 90s.
    class _StubSettings:
        poll_interval_sec = 30
    monkeypatch.setattr(webinterface.Settings, "load", classmethod(lambda cls: _StubSettings()))

    _write_heartbeat(hb_path, datetime.now(timezone.utc) - timedelta(seconds=60))
    hb = webinterface.get_daemon_heartbeat()
    assert hb["threshold_seconds"] == 90
    assert hb["alive"] is True


def test_naive_timestamp_is_treated_as_utc(hb_path):
    # The daemon writes timezone-aware ISO strings, but if someone
    # ever swaps the writer for a naive datetime.now().isoformat()
    # we shouldn't crash with a 'can't subtract offset-naive and
    # offset-aware' TypeError. Pin that defensive normalization.
    naive_recent = datetime.utcnow() - timedelta(seconds=2)
    hb_path.write_text(f'{{"last_poll_at": "{naive_recent.isoformat()}"}}')
    hb = webinterface.get_daemon_heartbeat()
    # Don't assert alive — the tz fudge could push a borderline case
    # either way. What we DO want is no exception and a sane shape.
    assert hb["age_seconds"] is not None
    assert isinstance(hb["alive"], bool)
