"""
Tests for the daemon's vakanties auto-refresh trigger.

Only the date/state guard logic is exercised here — the actual fetch
and write paths live in vakanties_fetcher (covered by its own tests)
and the daemon's main loop (integration territory). The interesting
question this file answers is: 'given a date and a state file, do we
attempt a refresh?' That decision is the entire correctness of the
periodic refresh, so it gets pinned down with explicit cases.

Trigger rule under test: refresh if last_success_at is more than
VAKANTIES_REFRESH_INTERVAL_DAYS ago (or never / corrupt).
"""

from datetime import date, timedelta

import schoolbelldaemon as daemon


# --- 'Should we refresh?' decision matrix -----------------------------------


def test_refresh_runs_with_no_prior_state():
    # Fresh install: state file empty (or missing). Should refresh.
    assert daemon._should_refresh_vakanties_today(date(2026, 8, 1), {}) is True


def test_refresh_runs_when_state_is_corrupt():
    # If somebody edited the state file to nonsense, treat it as
    # 'never refreshed' and try anyway. Better to attempt than to
    # silently never refresh again.
    state = {"last_success_at": "not-a-date"}
    assert daemon._should_refresh_vakanties_today(date(2026, 8, 1), state) is True


def test_refresh_skipped_when_last_success_is_recent():
    # Yesterday's refresh succeeded. Today is way too early for the
    # next one — the daemon shouldn't hammer rijksoverheid every poll.
    today = date(2026, 8, 1)
    last = today - timedelta(days=1)
    state = {"last_success_at": f"{last.isoformat()}T03:14:00+00:00"}
    assert daemon._should_refresh_vakanties_today(today, state) is False


def test_refresh_skipped_just_under_interval():
    # Exactly 29 days ago. Still under the 30-day threshold.
    today = date(2026, 9, 15)
    last = today - timedelta(days=29)
    state = {"last_success_at": f"{last.isoformat()}T03:14:00+00:00"}
    assert daemon._should_refresh_vakanties_today(today, state) is False


def test_refresh_runs_at_interval_boundary():
    # Exactly 30 days ago. Boundary is inclusive — refresh today.
    today = date(2026, 9, 15)
    last = today - timedelta(days=daemon.VAKANTIES_REFRESH_INTERVAL_DAYS)
    state = {"last_success_at": f"{last.isoformat()}T03:14:00+00:00"}
    assert daemon._should_refresh_vakanties_today(today, state) is True


def test_refresh_runs_when_long_overdue():
    # Six months since last refresh. Definitely refresh.
    today = date(2026, 12, 1)
    last = today - timedelta(days=180)
    state = {"last_success_at": f"{last.isoformat()}T03:14:00+00:00"}
    assert daemon._should_refresh_vakanties_today(today, state) is True


def test_refresh_picks_up_new_schooljaar_after_august():
    # The schooljaar boundary used to be a special case in the
    # trigger. Now it's not — but the daemon should still notice
    # the new schooljaar within a month of August 1. This test
    # documents that property: refresh on Aug 5 if last success
    # was Jul 1 (35 days back), regardless of any date logic.
    today = date(2026, 8, 5)
    last = date(2026, 7, 1)
    state = {"last_success_at": f"{last.isoformat()}T03:14:00+00:00"}
    assert daemon._should_refresh_vakanties_today(today, state) is True


# --- State file IO ----------------------------------------------------------


def test_refresh_state_save_load_roundtrip(tmp_path, monkeypatch):
    # Verify the on-disk format actually roundtrips cleanly.
    monkeypatch.setattr(
        daemon,
        "VAKANTIES_FETCH_STATE_PATH",
        str(tmp_path / "state.json"),
    )
    monkeypatch.setattr(daemon, "DATA_DIR", str(tmp_path))

    daemon._save_vakanties_fetch_state({
        "last_success_at": "2026-08-01T03:14:00+00:00",
        "last_success_schooljaar": "2026-2027",
    })

    loaded = daemon._load_vakanties_fetch_state()
    assert loaded["last_success_at"] == "2026-08-01T03:14:00+00:00"
    assert loaded["last_success_schooljaar"] == "2026-2027"


def test_refresh_state_load_returns_empty_for_missing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(
        daemon,
        "VAKANTIES_FETCH_STATE_PATH",
        str(tmp_path / "does-not-exist.json"),
    )
    assert daemon._load_vakanties_fetch_state() == {}


def test_refresh_state_load_returns_empty_for_corrupt_json(tmp_path, monkeypatch):
    p = tmp_path / "state.json"
    p.write_text("{this is not json")
    monkeypatch.setattr(daemon, "VAKANTIES_FETCH_STATE_PATH", str(p))
    # Corrupt file shouldn't crash the daemon — fall back to empty
    # state, which causes the 'should refresh?' check to behave as
    # if no prior attempts happened.
    assert daemon._load_vakanties_fetch_state() == {}
