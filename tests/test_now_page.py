"""
Tests for the public /now page and its companion /api/now.

The page is intentionally unauthed (a TV in the staff room shouldn't
need a login), so the routes themselves are simple. The interesting
logic is in next_bell_for_now() — picking the next upcoming bell from
today's effective rooster, respecting week-off overrides, and
computing seconds_until without timezone confusion.
"""

import json
from datetime import date, datetime

import pytest

import webinterface
from tests._helpers import csrf_from_html  # noqa: F401  (kept for parity)


# ---------------------------------------------------------------------------
# Helper: seed a simple rooster on the test client's tmp dir
# ---------------------------------------------------------------------------


def _seed_rooster(rooster_name="Standaard", momenten=None, weekday_for_today=None):
    """Write rooster + standaardweek JSON files so next_bell_for_now()
    has something to read.

    `weekday_for_today` defaults to today's weekday key (Mon/Tue/...).
    Passing it explicitly is useful when a test wants today's effective
    rooster to be empty: just point standaardweek's entry for today's
    weekday at a different name than what's in the roosters file.
    """
    momenten = momenten or [
        {"tijd": "08:30", "naam": "Eerste bel", "bestand": "bel.mp3"},
        {"tijd": "10:00", "naam": "Kleine pauze", "bestand": "bel.mp3"},
        {"tijd": "14:00", "naam": "Einde", "bestand": "bel.mp3"},
    ]
    if weekday_for_today is None:
        weekday_for_today = webinterface.weekday_key(date.today())

    with open(webinterface.ROOSTERS_PATH, "w") as f:
        json.dump({rooster_name: momenten}, f)

    week = {k: "" for k, _ in webinterface.WEEKDAYS}
    week[weekday_for_today] = rooster_name
    with open(webinterface.STANDAARDWEEK_PATH, "w") as f:
        json.dump(week, f)


# ---------------------------------------------------------------------------
# Routes — auth + shape
# ---------------------------------------------------------------------------


def test_now_page_does_not_require_login(client):
    # The whole point of /now is unauthed access for a shared screen.
    # If a future change adds @ui_login_required by mistake, this
    # test catches it.
    r = client.get("/now")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    # Pin a couple of identifying strings so a typo'd template path
    # (rendering some other page with a 200) shows up.
    assert "Volgende bel" in body or "Geen bel" in body
    assert "/api/now" in body


def test_api_now_does_not_require_login(client):
    r = client.get("/api/now")
    assert r.status_code == 200
    body = r.get_json()
    # Always present — keeps the JS dumb. 'bell' is null when there's
    # nothing upcoming today.
    assert "now" in body
    assert "bell" in body


def test_api_now_returns_null_bell_when_no_rooster(client):
    # No data files seeded → today has no rooster → no bell.
    r = client.get("/api/now")
    body = r.get_json()
    assert body["bell"] is None


def test_api_now_returns_bell_when_data_seeded(client):
    _seed_rooster()
    r = client.get("/api/now")
    body = r.get_json()
    # Whether 'bell' is None depends on the wall clock at test time
    # (after 14:00 there's nothing left). What we can pin is shape:
    # if a bell is returned, it has all the expected keys.
    if body["bell"] is not None:
        for key in ("naam", "tijd", "bestand", "seconds_until", "datum"):
            assert key in body["bell"], f"missing key: {key}"


# ---------------------------------------------------------------------------
# next_bell_for_now() — unit tests with a fixed `now`
# ---------------------------------------------------------------------------


@pytest.fixture
def seeded(client):
    """Reuse the route fixture for its tmp-dir setup, then seed rooster
    files. Yields the rooster moments so tests can refer to them."""
    momenten = [
        {"tijd": "08:30", "naam": "Eerste bel", "bestand": "bel.mp3"},
        {"tijd": "10:00", "naam": "Kleine pauze", "bestand": "bel.mp3"},
        {"tijd": "14:00", "naam": "Einde", "bestand": "bel.mp3"},
    ]
    _seed_rooster(momenten=momenten)
    return momenten


def test_next_bell_picks_first_upcoming(seeded):
    # 09:00 → next is the 10:00 break.
    fake_now = datetime.combine(date.today(), datetime.strptime("09:00", "%H:%M").time())
    nxt = webinterface.next_bell_for_now(fake_now)
    assert nxt is not None
    assert nxt["tijd"] == "10:00"
    assert nxt["naam"] == "Kleine pauze"


def test_next_bell_skips_malformed_moments(client):
    # A hand-edited roosters.json with a missing or garbage tijd must
    # not crash the public /now page: malformed moments are skipped,
    # valid ones still surface. Missing naam/bestand fall back to "".
    _seed_rooster(momenten=[
        {"naam": "Geen tijd", "bestand": "bel.mp3"},              # tijd missing
        {"tijd": "zzz", "naam": "Rommel", "bestand": "bel.mp3"},  # garbage tijd
        {"tijd": "10:00"},                                        # naam+bestand missing
    ])
    fake_now = datetime.combine(date.today(), datetime.strptime("09:00", "%H:%M").time())
    nxt = webinterface.next_bell_for_now(fake_now)
    assert nxt is not None
    assert nxt["tijd"] == "10:00"
    assert nxt["naam"] == ""
    assert nxt["bestand"] == ""


def test_next_bell_returns_none_after_last_bell(seeded):
    # 15:00 → past the 14:00 final bell, no more today.
    fake_now = datetime.combine(date.today(), datetime.strptime("15:00", "%H:%M").time())
    assert webinterface.next_bell_for_now(fake_now) is None


def test_next_bell_returns_first_when_called_before_school(seeded):
    # 06:00 — well before any bell — returns the 08:30 first bell.
    fake_now = datetime.combine(date.today(), datetime.strptime("06:00", "%H:%M").time())
    nxt = webinterface.next_bell_for_now(fake_now)
    assert nxt is not None
    assert nxt["tijd"] == "08:30"


def test_next_bell_seconds_until_correct(seeded):
    # 09:59:00 → 10:00 bell is 60s away. Pin the math because it's
    # easy to off-by-one when stripping seconds.
    fake_now = datetime.combine(date.today(), datetime.strptime("09:59:00", "%H:%M:%S").time())
    nxt = webinterface.next_bell_for_now(fake_now)
    assert nxt is not None
    assert nxt["seconds_until"] == 60


def test_next_bell_returns_none_when_week_is_off(client):
    # Mark this week as 'uit' (vacation/closed). Even with a rooster
    # filled in, no bells should ring.
    _seed_rooster()
    wk_key = webinterface.iso_week_key(date.today())
    with open(webinterface.WEEKDISABLE_PATH, "w") as f:
        json.dump({wk_key: True}, f)

    fake_now = datetime.combine(date.today(), datetime.strptime("09:00", "%H:%M").time())
    assert webinterface.next_bell_for_now(fake_now) is None


def test_next_bell_returns_none_when_no_standaardweek_for_today(client):
    # Seed a rooster, but point today's weekday at a non-existent
    # rooster name (simulating the "today is silent" case).
    today_key = webinterface.weekday_key(date.today())
    week = {k: "" for k, _ in webinterface.WEEKDAYS}
    week[today_key] = ""  # explicitly empty
    with open(webinterface.STANDAARDWEEK_PATH, "w") as f:
        json.dump(week, f)

    fake_now = datetime.combine(date.today(), datetime.strptime("09:00", "%H:%M").time())
    assert webinterface.next_bell_for_now(fake_now) is None
