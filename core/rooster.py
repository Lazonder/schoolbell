"""Helpers that work on rooster data: time strings and moments.

A "rooster" is a list of moments. A moment is a small dict like::

    {"tijd": "10:00", "naam": "Pauze", "bestand": "bel.mp3"}

This file does not read or write files. It just cleans up and
sorts data that someone hands it. Pure functions, easy to test.
"""

import re

from core.dates import WEEKDAYS


# A name must be 1 to 35 characters long.
# Allowed: letters (A-Z, a-z), digits (0-9), space, underscore, dash.
# Anything else (slashes, quotes, emoji, ...) is rejected, so the
# name is always safe to put in a URL or a filename.
NAME_RE = re.compile(r"^[A-Za-z0-9 _-]{1,35}$")

# Time pattern for a 24-hour clock. Matches "HH:MM" and "HH:MM:SS".
# Hours: 00..23, minutes/seconds: 00..59. We keep ``re.match`` later
# strict by anchoring with ^ and $.
TIME_RE = re.compile(r"^(?:[01]?\d|2[0-3]):[0-5]\d(?::[0-5]\d)?$")

# Form-level marker sent by the agenda dropdown when the user picks
# '— geen bel —' for a date. Stored as JSON null in dagplanning to mean
# 'explicit silence override on this date'. The '!' prefix is outside
# NAME_RE so it can never collide with a real rooster name.
DAGPLANNING_SILENT_FORM_VALUE = "!off"


def normalize_time(t: str) -> str:
    """
    Accepts '8:05', '08:05', '11:30:00', etc.
    Always returns 'HH:MM', or '' if the time is invalid.
    """
    t = (t or "").strip()
    if not TIME_RE.match(t):
        return ""
    parts = t.split(":")
    if len(parts) < 2:
        return ""
    hh, mm = parts[0], parts[1]
    # prevent odd things like '007:03'
    try:
        hh_int = int(hh)
        mm_int = int(mm)
    except ValueError:
        return ""
    if not (0 <= hh_int <= 23 and 0 <= mm_int <= 59):
        return ""
    return f"{hh_int:02d}:{mm_int:02d}"


def normalize_and_sort_moments(moments):
    cleaned = []
    for m in moments:
        tijd_norm = normalize_time(m.get("tijd") or "")
        naam = (m.get("naam") or "").strip()
        bestand = (m.get("bestand") or "").strip()
        if not tijd_norm:
            continue
        if not naam:
            continue
        if not bestand:
            continue
        out = {"tijd": tijd_norm, "naam": naam, "bestand": bestand}

        # Optional warning bell: rings warn_min minutes before the
        # main moment with warn_bestand. Both fields must be valid
        # and non-empty for a warning to actually fire. Anything
        # else is treated as "no warning". Keeps roosters.json
        # forward- and backward-compatible: legacy moments without
        # the keys load fine and simply don't get a warning.
        warn_min = m.get("warn_min")
        warn_bestand = (m.get("warn_bestand") or "").strip()
        try:
            warn_min_int = int(warn_min) if warn_min is not None else 0
        except (TypeError, ValueError):
            warn_min_int = 0
        if 1 <= warn_min_int <= 60 and warn_bestand:
            out["warn_min"] = warn_min_int
            out["warn_bestand"] = warn_bestand
        # If only one of the two is set, the warning is silently
        # dropped. No half-configured state survives normalization.

        cleaned.append(out)
    cleaned.sort(key=lambda x: x["tijd"])
    return cleaned


def default_roosters_obj():
    """Empty rooster dictionary used when ``roosters.json`` is missing."""
    return {}


def default_dagplanning_obj():
    """Empty dagplanning dictionary used when the file is missing."""
    return {}


def default_standaardweek_obj():
    """Default standaardweek with no rooster picked for any weekday.

    Shape: ``{"Mon": "", "Tue": "", ..., "Sun": ""}``. The keys come
    from ``WEEKDAYS`` so the order matches what the UI expects.
    """
    return {k: "" for k, _ in WEEKDAYS}


def default_weken_uit_obj():
    """Empty 'weeks off' dictionary.

    Shape when filled: ``{"2025-W34": True, "2025-W35": True, ...}``.
    A True value means: do not ring any bell during that whole week.
    """
    return {}
