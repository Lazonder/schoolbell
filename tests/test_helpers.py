"""
Unit tests for the pure helper functions from webinterface.py.

'Pure' = no side effects (no I/O, no global state), so we can safely
import and call them directly — no fixtures or mocking needed. This is
the most valuable set to lock down first against regressions: these
functions do the real work (time normalization, filename validation,
day-schedule selection), while the Flask routes on top are thin.

Common test shape (AAA):
  - Arrange: build up the input
  - Act:     call the function
  - Assert:  check the result

Run:
  pip install -r requirements-dev.txt
  pytest            # from the project root
  pytest -v         # verbose: show pass/fail per test
"""

from datetime import date

import pytest

# Importing this has the side effect of executing webinterface.py.
# That's OK: all side effects in that file (creating the Flask app,
# reading env vars) are idempotent and don't touch the filesystem.
from webinterface import (
    _env_bool,
    effectieve_rooster_naam_for_date,
    iso_week_key,
    normalize_and_sort_moments,
    normalize_time,
    prune_past_dates,
    safe_audio_filename,
    weekday_key,
)


# ---------------------------------------------------------------------------
# normalize_time: string -> "HH:MM" or ""
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("8:05", "08:05"),      # single-digit hour gets padded
        ("08:05", "08:05"),     # already correct, stays
        ("08:5", ""),           # minutes must always be 2 digits (regex)
        ("23:59", "23:59"),     # upper bound valid
        ("00:00", "00:00"),     # lower bound valid
        ("11:30:00", "11:30"),  # seconds get stripped
        ("  9:15  ", "09:15"),  # strip() works
    ],
)
def test_normalize_time_happy_path(raw, expected):
    assert normalize_time(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",         # empty
        "abc",      # not a time
        "24:00",    # hour out of range
        "12:60",    # minute out of range
        "007:03",   # three-digit hour: regex doesn't match
        "12",       # missing :mm
        ":30",      # missing hour
        None,       # None input gets coerced to "" by strip()
    ],
)
def test_normalize_time_invalid_returns_empty(raw):
    # normalize_time() deliberately returns "" instead of None, so that
    # callers in templates can do `if tijd:` without a type check.
    assert normalize_time(raw) == ""


# ---------------------------------------------------------------------------
# safe_audio_filename: prevents path traversal and junk in filenames
# ---------------------------------------------------------------------------


def test_safe_audio_filename_happy_path():
    assert safe_audio_filename("bel01", ".mp3") == "bel01.mp3"


def test_safe_audio_filename_strips_whitespace():
    assert safe_audio_filename("  intro  ", ".wav") == "intro.wav"


@pytest.mark.parametrize(
    "base",
    [
        "../etc/passwd",     # path-traversal attempt
        "bel/01",            # slash forbidden
        "bel\\01",           # backslash forbidden
        "bel.01",            # dot forbidden (would create a double extension)
        "",                  # empty
        "a" * 36,            # 1 too long (limit is 35)
        "bel@home",          # @ forbidden
        "héllo",             # accent forbidden (outside [A-Za-z0-9 _-])
    ],
)
def test_safe_audio_filename_rejects_invalid(base):
    # On an invalid name you get an empty string back. The caller is
    # expected to check for that and refuse.
    assert safe_audio_filename(base, ".mp3") == ""


# ---------------------------------------------------------------------------
# normalize_and_sort_moments: cleanup + sort of a list of bell moments
# ---------------------------------------------------------------------------


def test_normalize_and_sort_moments_drops_invalid_and_sorts():
    input_moments = [
        {"tijd": "10:05", "naam": "Pauze", "bestand": "pauze.mp3"},
        {"tijd": "bogus", "naam": "X", "bestand": "x.mp3"},          # invalid time
        {"tijd": "8:00", "naam": "Start", "bestand": "start.mp3"},    # not zero-padded
        {"tijd": "09:15", "naam": "", "bestand": "a.mp3"},            # empty name
        {"tijd": "09:20", "naam": "Z", "bestand": ""},                # empty file
    ]

    result = normalize_and_sort_moments(input_moments)

    # Only the two valid ones remain, sorted by time.
    assert result == [
        {"tijd": "08:00", "naam": "Start", "bestand": "start.mp3"},
        {"tijd": "10:05", "naam": "Pauze", "bestand": "pauze.mp3"},
    ]


def test_normalize_and_sort_moments_empty_list():
    assert normalize_and_sort_moments([]) == []


# ---------------------------------------------------------------------------
# weekday_key + effectieve_rooster_naam_for_date:
# the core of "which schedule applies on a given date?"
# ---------------------------------------------------------------------------


def test_weekday_key_mapping():
    # 2026-04-20 is a Monday, 04-26 a Sunday — handy check set.
    assert weekday_key(date(2026, 4, 20)) == "Mon"
    assert weekday_key(date(2026, 4, 21)) == "Tue"
    assert weekday_key(date(2026, 4, 22)) == "Wed"
    assert weekday_key(date(2026, 4, 23)) == "Thu"
    assert weekday_key(date(2026, 4, 24)) == "Fri"
    assert weekday_key(date(2026, 4, 25)) == "Sat"
    assert weekday_key(date(2026, 4, 26)) == "Sun"


def test_effectief_rooster_dagplanning_wint_van_standaardweek():
    # Day plan is a per-date override: if there's something for that day,
    # it takes precedence over the standard week.
    dagplanning = {"2026-04-20": "feestdag"}
    standaardweek = {"Mon": "gewone_week", "Tue": "gewone_week"}

    naam = effectieve_rooster_naam_for_date(
        date(2026, 4, 20), dagplanning, standaardweek,
    )
    assert naam == "feestdag"


def test_effectief_rooster_valt_terug_op_standaardweek():
    # No day-plan entry → use the weekday from the standard week.
    dagplanning = {}
    standaardweek = {"Mon": "gewone_week", "Tue": "andere_week"}

    assert effectieve_rooster_naam_for_date(
        date(2026, 4, 20), dagplanning, standaardweek,
    ) == "gewone_week"
    assert effectieve_rooster_naam_for_date(
        date(2026, 4, 21), dagplanning, standaardweek,
    ) == "andere_week"


def test_effectief_rooster_lege_state_geeft_lege_string():
    # Nothing known → "", not None. That keeps caller code simple.
    assert effectieve_rooster_naam_for_date(
        date(2026, 4, 20), {}, {},
    ) == ""


def test_effectief_rooster_dagplanning_met_lege_waarde_valt_terug():
    # Rule from the code: `if d_iso in dagplanning and dagplanning[d_iso]`
    # — an empty string in dagplanning does NOT count as an override.
    # Important, because the UI sometimes intentionally sets "" to clear
    # a day, and in those cases standaardweek must be the fallback.
    dagplanning = {"2026-04-20": ""}
    standaardweek = {"Mon": "gewone_week"}

    assert effectieve_rooster_naam_for_date(
        date(2026, 4, 20), dagplanning, standaardweek,
    ) == "gewone_week"


# ---------------------------------------------------------------------------
# iso_week_key: formats a date as "YYYY-Www"
# ---------------------------------------------------------------------------


def test_iso_week_key_format():
    # 2026-01-05 = Monday of ISO week 2. Handy edge: around the year
    # turn, the ISO week may carry a different year, which we want to
    # test correctly.
    assert iso_week_key(date(2026, 1, 5)) == "2026-W02"


def test_iso_week_key_rond_jaarwissel():
    # 2025-12-29 falls in ISO week 1 of 2026 (first week with 4+ days
    # in the new year). Those edge cases are regression-prone when
    # date code is refactored.
    assert iso_week_key(date(2025, 12, 29)) == "2026-W01"


# ---------------------------------------------------------------------------
# _env_bool: environment parsing with monkeypatch
# ---------------------------------------------------------------------------


def test_env_bool_default_bij_onzet(monkeypatch):
    # monkeypatch.delenv with raising=False does nothing if the var
    # isn't set, but ensures we have a clean state.
    monkeypatch.delenv("FAKE_FLAG", raising=False)

    assert _env_bool("FAKE_FLAG", default=True) is True
    assert _env_bool("FAKE_FLAG", default=False) is False


@pytest.mark.parametrize("value", ["0", "false", "no", "off", "FALSE", "Off", "", "  "])
def test_env_bool_false_varianten(monkeypatch, value):
    monkeypatch.setenv("FAKE_FLAG", value)
    assert _env_bool("FAKE_FLAG", default=True) is False


@pytest.mark.parametrize("value", ["1", "true", "yes", "on", "TRUE", "YES", "something"])
def test_env_bool_true_varianten(monkeypatch, value):
    monkeypatch.setenv("FAKE_FLAG", value)
    assert _env_bool("FAKE_FLAG", default=False) is True


# ---------------------------------------------------------------------------
# prune_past_dates: drops dagplanning entries from before today
# ---------------------------------------------------------------------------


def test_prune_past_dates_keeps_today_and_future():
    today = date(2026, 4, 26)
    dag = {
        "2026-04-25": "feestdag",   # yesterday → drop
        "2026-04-26": "feestdag",   # today → keep (bell may still ring)
        "2026-04-27": "lesvrij",    # tomorrow → keep
        "2027-01-01": "nieuwjaar",  # far future → keep
    }
    result = prune_past_dates(dag, today)
    assert result == {
        "2026-04-26": "feestdag",
        "2026-04-27": "lesvrij",
        "2027-01-01": "nieuwjaar",
    }


def test_prune_past_dates_empty_dict():
    assert prune_past_dates({}, date(2026, 4, 26)) == {}


def test_prune_past_dates_returns_a_copy():
    # The agenda route stores the result back; making sure we don't
    # alias the input dict avoids accidental in-place mutation later.
    dag = {"2026-04-26": "x"}
    result = prune_past_dates(dag, date(2026, 4, 26))
    result["2026-04-26"] = "MUTATED"
    assert dag == {"2026-04-26": "x"}


def test_prune_past_dates_keeps_unparseable_keys():
    # If a malformed key sneaks into the file, we keep it rather than
    # silently delete data we don't understand. Surfacing oddness is
    # better than swallowing it.
    today = date(2026, 4, 26)
    dag = {
        "2026-04-25": "drop_me",
        "not-a-date": "weird",
        "": "empty_key",
    }
    result = prune_past_dates(dag, today)
    assert result == {"not-a-date": "weird", "": "empty_key"}
