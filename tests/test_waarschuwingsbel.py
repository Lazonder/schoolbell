"""
Tests for the optional warning-bell feature.

Two halves:
- webinterface side: normalize_and_sort_moments preserves valid
  warn_min/warn_bestand, drops half-configured/invalid combos, and
  stays forward-compatible with legacy roosters.json files.
- daemon side: apply_day_schedule adds a second scheduled job at
  (tijd - warn_min) when both fields are set; _subtract_minutes
  returns None when the warning would land before midnight.
"""

import json

import pytest

import webinterface
import schoolbelldaemon as daemon


# ---------------------------------------------------------------------------
# webinterface.normalize_and_sort_moments
# ---------------------------------------------------------------------------


def test_normalize_preserves_valid_warning():
    out = webinterface.normalize_and_sort_moments([
        {"tijd": "10:00", "naam": "Pauze", "bestand": "bel.mp3",
         "warn_min": 5, "warn_bestand": "ping.mp3"},
    ])
    assert out == [
        {"tijd": "10:00", "naam": "Pauze", "bestand": "bel.mp3",
         "warn_min": 5, "warn_bestand": "ping.mp3"},
    ]


def test_normalize_drops_warning_with_zero_minutes():
    # 0 means "no warning" — even if warn_bestand is filled, the
    # combination is dropped so it doesn't accidentally ring at the
    # same time as the main bell.
    out = webinterface.normalize_and_sort_moments([
        {"tijd": "10:00", "naam": "Pauze", "bestand": "bel.mp3",
         "warn_min": 0, "warn_bestand": "ping.mp3"},
    ])
    assert "warn_min" not in out[0]
    assert "warn_bestand" not in out[0]


def test_normalize_drops_warning_without_bestand():
    # Half-configured: warn_min set but no audio file. Drop it
    # rather than save the bad state. Keeps the daemon simple.
    out = webinterface.normalize_and_sort_moments([
        {"tijd": "10:00", "naam": "Pauze", "bestand": "bel.mp3",
         "warn_min": 5, "warn_bestand": ""},
    ])
    assert "warn_min" not in out[0]


def test_normalize_drops_warning_above_60_minutes():
    # Cap is 60 — anyone wanting to "warn 2 hours earlier" probably
    # wants a separate moment instead. The bound is enforced both
    # in the form handler and here.
    out = webinterface.normalize_and_sort_moments([
        {"tijd": "10:00", "naam": "Pauze", "bestand": "bel.mp3",
         "warn_min": 90, "warn_bestand": "ping.mp3"},
    ])
    assert "warn_min" not in out[0]


def test_normalize_handles_legacy_moment_without_warning_keys():
    # Forward-compat: existing roosters.json rows from before this
    # feature must round-trip unchanged (apart from tijd
    # normalization that already happened).
    out = webinterface.normalize_and_sort_moments([
        {"tijd": "10:00", "naam": "Pauze", "bestand": "bel.mp3"},
    ])
    assert out == [
        {"tijd": "10:00", "naam": "Pauze", "bestand": "bel.mp3"},
    ]
    assert "warn_min" not in out[0]


def test_normalize_coerces_string_warn_min():
    # The form posts strings; the dataclass-style normalizer must
    # cope. "5" → 5 should pass; "abc" → silently no-warning.
    out = webinterface.normalize_and_sort_moments([
        {"tijd": "10:00", "naam": "Pauze", "bestand": "bel.mp3",
         "warn_min": "5", "warn_bestand": "ping.mp3"},
    ])
    assert out[0]["warn_min"] == 5

    out2 = webinterface.normalize_and_sort_moments([
        {"tijd": "10:00", "naam": "Pauze", "bestand": "bel.mp3",
         "warn_min": "abc", "warn_bestand": "ping.mp3"},
    ])
    assert "warn_min" not in out2[0]


# ---------------------------------------------------------------------------
# add_moment route — form handling end-to-end
# ---------------------------------------------------------------------------


def _seed_rooster(rooster="Standaard"):
    with open(webinterface.ROOSTERS_PATH, "w") as f:
        json.dump({rooster: []}, f)


def test_add_moment_with_warning_persists_both_fields(logged_in_client, csrf_token):
    _seed_rooster()
    r = logged_in_client.post(
        "/roosters/Standaard/add-moment",
        data={
            "_csrf": csrf_token,
            "tijd": "10:00",
            "naam": "Pauze",
            "bestand": "bel.mp3",
            "warn_min": "3",
            "warn_bestand": "ping.mp3",
        },
        follow_redirects=False,
    )
    assert r.status_code in (302, 303)
    with open(webinterface.ROOSTERS_PATH) as f:
        data = json.load(f)
    moment = data["Standaard"][0]
    assert moment["warn_min"] == 3
    assert moment["warn_bestand"] == "ping.mp3"


def test_add_moment_without_warning_omits_warn_keys(logged_in_client, csrf_token):
    # The common case — no warning configured. Empty warn_min and
    # warn_bestand must round-trip as a clean moment with no extra
    # keys, so existing tooling and the daemon's old code path
    # don't see surprise fields.
    _seed_rooster()
    r = logged_in_client.post(
        "/roosters/Standaard/add-moment",
        data={
            "_csrf": csrf_token,
            "tijd": "10:00",
            "naam": "Pauze",
            "bestand": "bel.mp3",
            "warn_min": "",
            "warn_bestand": "",
        },
        follow_redirects=False,
    )
    assert r.status_code in (302, 303)
    with open(webinterface.ROOSTERS_PATH) as f:
        data = json.load(f)
    moment = data["Standaard"][0]
    assert "warn_min" not in moment
    assert "warn_bestand" not in moment


def test_add_moment_warn_min_out_of_range_flashes(logged_in_client, csrf_token):
    # 61 is over the documented cap. Handler should flash, redirect,
    # and NOT save the moment.
    _seed_rooster()
    r = logged_in_client.post(
        "/roosters/Standaard/add-moment",
        data={
            "_csrf": csrf_token,
            "tijd": "10:00",
            "naam": "Pauze",
            "bestand": "bel.mp3",
            "warn_min": "61",
            "warn_bestand": "ping.mp3",
        },
        follow_redirects=False,
    )
    assert r.status_code in (302, 303)
    with open(webinterface.ROOSTERS_PATH) as f:
        data = json.load(f)
    assert data["Standaard"] == []  # nothing saved


# ---------------------------------------------------------------------------
# daemon — schedule planning math
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tijd, minutes, expected",
    [
        ("10:00", 5, "09:55"),
        ("10:30", 30, "10:00"),
        ("00:30", 60, None),     # would be -30 — skip
        ("00:01", 1, "00:00"),   # exactly midnight is fine
        ("12:00", 0, "12:00"),   # zero minutes returns same time
    ],
)
def test_subtract_minutes(tijd, minutes, expected):
    assert daemon._subtract_minutes(tijd, minutes) == expected


def test_subtract_minutes_handles_garbage():
    assert daemon._subtract_minutes("not-a-time", 5) is None
    assert daemon._subtract_minutes(None, 5) is None


def test_apply_day_schedule_plans_main_and_warning(monkeypatch):
    # Capture every plan_job_at call without touching the real
    # `schedule` library, so the test stays focused on the
    # main-vs-warning fan-out.
    plans: list[tuple] = []
    monkeypatch.setattr(
        daemon, "plan_job_at",
        lambda hhmm, audio_file, label="": plans.append((hhmm, audio_file, label)),
    )
    monkeypatch.setattr(daemon, "cancel_all_jobs", lambda: None)

    daemon.apply_day_schedule([
        {"tijd": "10:00", "naam": "Pauze", "bestand": "bel.mp3",
         "warn_min": 5, "warn_bestand": "ping.mp3"},
    ])

    # Two calls: the warning at 09:55 and the main bell at 10:00.
    assert len(plans) == 2
    times = sorted(p[0] for p in plans)
    assert times == ["09:55", "10:00"]
    main = next(p for p in plans if p[0] == "10:00")
    warn = next(p for p in plans if p[0] == "09:55")
    assert main[1] == "bel.mp3"
    assert warn[1] == "ping.mp3"
    assert "waarschuwing" in warn[2].lower()


def test_apply_day_schedule_skips_midnight_crossing_warning(monkeypatch):
    plans: list[tuple] = []
    monkeypatch.setattr(
        daemon, "plan_job_at",
        lambda hhmm, audio_file, label="": plans.append((hhmm, audio_file, label)),
    )
    monkeypatch.setattr(daemon, "cancel_all_jobs", lambda: None)

    # 60-min warning before 00:30 would land at 23:30 the day before —
    # `schedule` would ring it the wrong day. Skip silently (just a
    # WARN log) and still plan the main bell.
    daemon.apply_day_schedule([
        {"tijd": "00:30", "naam": "Vroeg", "bestand": "bel.mp3",
         "warn_min": 60, "warn_bestand": "ping.mp3"},
    ])
    assert len(plans) == 1
    assert plans[0][0] == "00:30"


def test_apply_day_schedule_handles_moment_without_warning(monkeypatch):
    # Legacy moment shape (no warn_* keys). Must plan the main bell
    # only, no extra job, no errors.
    plans: list[tuple] = []
    monkeypatch.setattr(
        daemon, "plan_job_at",
        lambda hhmm, audio_file, label="": plans.append((hhmm, audio_file, label)),
    )
    monkeypatch.setattr(daemon, "cancel_all_jobs", lambda: None)

    daemon.apply_day_schedule([
        {"tijd": "10:00", "naam": "Pauze", "bestand": "bel.mp3"},
    ])
    assert len(plans) == 1
    assert plans[0][0] == "10:00"
