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
    DAGPLANNING_SILENT_FORM_VALUE,
    NAME_RE,
    effective_rooster_for_date,
    effectieve_rooster_naam_for_date,
    iso_week_key,
    iso_weeks_with_weekday_in_range,
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


def test_name_re_rejects_silence_sentinel():
    # Critical invariant: the agenda dropdown sentinel for 'silence
    # override' (currently '!off') must NOT be a valid rooster name.
    # If it were, a user could create a rooster called '!off', which
    # would appear in the agenda dropdown — and selecting it would
    # be misinterpreted as the silence sentinel. The '!' character
    # is outside [A-Za-z0-9 _-] so NAME_RE rejects it.
    #
    # If anyone ever changes either NAME_RE or the sentinel, this
    # test catches the regression before it ships.
    assert NAME_RE.match(DAGPLANNING_SILENT_FORM_VALUE) is None


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
    # Legacy / backwards-compat: an empty string in dagplanning is
    # treated as 'no override' and falls through to standaardweek.
    # This is preserved on upgrade so older dagplanning.json files
    # don't suddenly silence days that used to ring.
    dagplanning = {"2026-04-20": ""}
    standaardweek = {"Mon": "gewone_week"}

    assert effectieve_rooster_naam_for_date(
        date(2026, 4, 20), dagplanning, standaardweek,
    ) == "gewone_week"


def test_effectief_rooster_explicit_silence_override():
    # The fix for the agenda bug: a None value in dagplanning means
    # 'explicit silence override' and must beat the standaardweek.
    # This is what the agenda's '— geen bel —' option saves.
    dagplanning = {"2026-04-20": None}
    standaardweek = {"Mon": "gewone_week"}

    assert effectieve_rooster_naam_for_date(
        date(2026, 4, 20), dagplanning, standaardweek,
    ) == ""


def test_effectief_rooster_silence_override_with_no_standaardweek():
    # Symmetry check: silence override returns "" regardless of
    # standaardweek state. Useful so the daemon (which keys on
    # rooster_naam being truthy) sees the same 'no schedule' result.
    assert effectieve_rooster_naam_for_date(
        date(2026, 4, 20), {"2026-04-20": None}, {},
    ) == ""


# ---------------------------------------------------------------------------
# effective_rooster_for_date: tuple version (name, bron)
# Used by the API and compute_upcoming so the response can tell the user
# *why* a given day is silent or active.
# ---------------------------------------------------------------------------


def test_effective_rooster_for_date_dagplanning_override_returns_dagplanning_bron():
    name, bron = effective_rooster_for_date(
        date(2026, 4, 20),
        {"2026-04-20": "feestdag"},
        {"Mon": "gewone_week"},
    )
    assert name == "feestdag"
    assert bron == "dagplanning"


def test_effective_rooster_for_date_silence_override_keeps_dagplanning_bron():
    # A silence override is still 'from dagplanning' — the user made an
    # explicit choice, even if the choice is 'no bell'. The empty name
    # signals silence; the bron tells the API caller why.
    name, bron = effective_rooster_for_date(
        date(2026, 4, 20),
        {"2026-04-20": None},
        {"Mon": "gewone_week"},
    )
    assert name == ""
    assert bron == "dagplanning"


def test_effective_rooster_for_date_no_override_returns_standaardweek_bron():
    name, bron = effective_rooster_for_date(
        date(2026, 4, 20),
        {},
        {"Mon": "gewone_week"},
    )
    assert name == "gewone_week"
    assert bron == "standaardweek"


def test_effective_rooster_for_date_legacy_empty_string_falls_through():
    # Legacy behavior: an empty string in dagplanning is treated as 'no
    # override'. bron must report standaardweek to match — otherwise
    # the API would say bron='dagplanning' but actually be using the
    # standaardweek answer, which is misleading.
    name, bron = effective_rooster_for_date(
        date(2026, 4, 20),
        {"2026-04-20": ""},
        {"Mon": "gewone_week"},
    )
    assert name == "gewone_week"
    assert bron == "standaardweek"


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
# iso_weeks_with_weekday_in_range: expand a date range to the ISO weeks
# that contain at least one school day (Mon-Fri) within the range.
# Used by the school-holiday import to know which weeks to mark 'off'.
# ---------------------------------------------------------------------------


def test_iso_weeks_with_weekday_single_full_week():
    # Mon 2026-04-20 .. Sun 2026-04-26 is exactly ISO week 17 of 2026.
    # All five weekdays (Mon-Fri) are in range -> week 17.
    weeks = iso_weeks_with_weekday_in_range(date(2026, 4, 20), date(2026, 4, 26))
    assert weeks == {"2026-W17"}


def test_iso_weeks_with_weekday_skips_weekend_overshoot():
    # The bug we are fixing: a vacation that starts on a Saturday and
    # ends on a Sunday a week later (Sat 2026-10-10 .. Sun 2026-10-18)
    # only impacts ONE school week, not two.
    # Old behavior counted week 41 too (because Sat Oct 10 lives there)
    # — but the school days of week 41 (Mon-Fri Oct 5-9) are NOT in the
    # vacation. The school days that ARE in the vacation (Mon-Fri Oct
    # 12-16) all sit in week 42. So week 42 is the only result.
    weeks = iso_weeks_with_weekday_in_range(date(2026, 10, 10), date(2026, 10, 18))
    assert weeks == {"2026-W42"}


def test_iso_weeks_with_weekday_weekend_only_range_is_empty():
    # If the vacation falls entirely on a weekend (a single Saturday,
    # or Sat-Sun together), no school days are affected and no weeks
    # need to be marked.
    assert iso_weeks_with_weekday_in_range(date(2026, 10, 10), date(2026, 10, 11)) == set()
    assert iso_weeks_with_weekday_in_range(date(2026, 10, 10), date(2026, 10, 10)) == set()


def test_iso_weeks_with_weekday_two_full_weeks():
    # Mon..Sun, then Mon..Sun: a clean two-week vacation. Both weeks
    # contain weekdays in the vacation -> {17, 18}.
    weeks = iso_weeks_with_weekday_in_range(date(2026, 4, 20), date(2026, 5, 3))
    assert weeks == {"2026-W17", "2026-W18"}


def test_iso_weeks_with_weekday_single_weekday():
    # Wed 2026-04-22 is a single weekday -> its week.
    weeks = iso_weeks_with_weekday_in_range(date(2026, 4, 22), date(2026, 4, 22))
    assert weeks == {"2026-W17"}


def test_iso_weeks_with_weekday_end_before_start_returns_empty():
    # Defensive: if the user mistakenly enters eind < start, we
    # return nothing rather than wrap around or silently swap.
    assert iso_weeks_with_weekday_in_range(date(2026, 4, 26), date(2026, 4, 20)) == set()


def test_iso_weeks_with_weekday_crosses_iso_year_boundary():
    # 2025-12-29 is already ISO week 1 of 2026 (>=4 days in new year).
    # A vacation Mon Dec 22 .. Sun Jan 4 covers weekdays in both
    # ISO years and needs two different ISO years in the result.
    weeks = iso_weeks_with_weekday_in_range(date(2025, 12, 22), date(2026, 1, 4))
    assert weeks == {"2025-W52", "2026-W01"}


def test_iso_weeks_with_weekday_long_vacation_skips_first_week_when_starts_on_saturday():
    # Realistic: Sat 2026-07-04 .. Sun 2026-08-16 (zomer Noord).
    # Sat Jul 4 is in week 27, but no weekdays of week 27 are in the
    # vacation (week 27 ends Sun Jul 5). The first weekday in vacation
    # is Mon Jul 6 -> week 28. The last weekday is Fri Aug 14 -> week 33.
    # Inclusive: weeks 28, 29, 30, 31, 32, 33 -> 6 weeks total.
    weeks = iso_weeks_with_weekday_in_range(date(2026, 7, 4), date(2026, 8, 16))
    assert "2026-W27" not in weeks  # the bug we fixed
    assert "2026-W28" in weeks
    assert "2026-W33" in weeks
    assert len(weeks) == 6


def test_iso_weeks_with_weekday_friday_to_monday_covers_two_weeks():
    # Fri 2026-04-24 to Mon 2026-04-27: weekdays in two different ISO
    # weeks (Fri in 17, Mon in 18). Saturday/Sunday between them are
    # ignored, but each weekday end pulls in its own week.
    weeks = iso_weeks_with_weekday_in_range(date(2026, 4, 24), date(2026, 4, 27))
    assert weeks == {"2026-W17", "2026-W18"}


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
