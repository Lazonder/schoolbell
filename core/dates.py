"""Date and weekday helpers used by the schedule logic.

These functions only do calculations on date and datetime objects.
They do not read or write any files, so you can call them from
tests with a fixed date and check the result directly. Keeping
"pure" code (no side effects) in its own file makes it easier to
read and test.
"""

from datetime import date, datetime, time, timedelta


# A list of (key, label) for the seven days of the week.
# - The "key" (e.g. "Mon") is what we use inside JSON files and code.
# - The "label" (e.g. "Maandag") is what we show to the user.
# Two strings per day so we never have to translate between them.
WEEKDAYS = [
    ("Mon", "Maandag"),
    ("Tue", "Dinsdag"),
    ("Wed", "Woensdag"),
    ("Thu", "Donderdag"),
    ("Fri", "Vrijdag"),
    ("Sat", "Zaterdag"),
    ("Sun", "Zondag"),
]


def weekday_key(d: date) -> str:
    """Turn a date into one of the seven ``WEEKDAYS`` keys.

    Example: ``weekday_key(date(2026, 5, 11))`` returns ``"Mon"`` because
    May 11 2026 is a Monday. ``date.weekday()`` returns 0..6 with
    Monday = 0, so we can use it directly as a list index.
    """
    return ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][d.weekday()]


def effective_rooster_for_date(d: date, dagplanning: dict, standaardweek: dict) -> tuple[str, str]:
    """Resolve which rooster applies on date d, plus where the answer came from.

    Returns (rooster_name, bron):
      - rooster_name: "" means no schedule (silence override OR no
        standaardweek entry for this weekday).
      - bron: "dagplanning" if there was an override on this date
        (whether to a rooster or to silence), else "standaardweek".

    Three cases for dagplanning entries:
      - non-empty string -> override to that rooster
      - None (JSON null) -> explicit silence override (no bell)
      - "" or key missing -> no override, fall back to standaardweek

    The empty-string case is kept as 'no override' for backwards
    compatibility: older dagplanning.json files (or manual edits) may
    contain "" — treating that as silence would change behavior on
    upgrade. Explicit silence is signalled by None / null only.

    This is the single source of truth for 'which schedule applies on
    a given date' — every callsite (agenda render, agenda save, the
    /api/effectief-rooster endpoint, compute_upcoming) must go through
    this function so they can't drift apart on edge cases.
    """
    d_iso = d.isoformat()
    if d_iso in dagplanning:
        v = dagplanning[d_iso]
        if v is None:
            return ("", "dagplanning")  # explicit silence override
        if v:
            return (v, "dagplanning")
        # empty string falls through to standaardweek (legacy)
    name = (standaardweek or {}).get(weekday_key(d), "") or ""
    return (name, "standaardweek")


def effectieve_rooster_naam_for_date(d: date, dagplanning: dict, standaardweek: dict) -> str:
    """Backwards-compat wrapper that returns only the rooster name.

    Kept because tests already reference this name; new code should
    prefer effective_rooster_for_date when the bron is also useful.
    """
    return effective_rooster_for_date(d, dagplanning, standaardweek)[0]


def iso_week_key(d: date) -> str:
    """Turn a date into an ISO week key like ``"2026-W19"``.

    ISO weeks start on Monday and the first week of a year is the
    one that contains the first Thursday. This means Dec 31 of one
    year can sit in week 1 of the next year — that is normal, not
    a bug. We use this string as the key in ``weken_uit.json``
    (which weeks the bell is turned off).
    """
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def iso_weeks_with_weekday_in_range(start: date, end: date) -> set[str]:
    """All ISO week keys (YYYY-Www) that contain at least one weekday
    (Mon-Fri) inside the inclusive range start..end.

    Returns an empty set if end < start. Weekend-only ranges return
    an empty set.

    Why weekday-only and not 'any day in range': vacations like
    'Sat 2026-10-10 t/m Sun 2026-10-18' overlap two ISO weeks (41
    and 42), but the school days within the vacation only fall in
    week 42 (Mon-Fri Oct 12-16). Marking week 41 as 'Bel uit' would
    silence Mon-Fri Oct 5-9, which are normal school days outside
    the vacation. So we only count weeks that contain a school
    day belonging to the vacation.

    Assumes the bell rings Mon-Fri. Schools that configure a
    Saturday rooster would get a slight under-mark — out of scope
    for now; a per-rooster weekday set is much more code than
    this saves.

    Iterates day-by-day rather than week-by-week so partial-week
    edge cases (vacation Mon-Wed, vacation crossing the ISO year
    boundary where Dec 31 is in week 1 of the next year, etc.)
    all fall out correctly without special-casing. A 1-2 week
    vacation = at most ~14 iterations — cheap.
    """
    weeks: set[str] = set()
    if end < start:
        return weeks
    d = start
    while d <= end:
        if d.weekday() < 5:  # 0=Monday, 4=Friday; 5=Sat, 6=Sun
            weeks.add(iso_week_key(d))
        d += timedelta(days=1)
    return weeks


def prune_past_dates(dagplanning: dict, today: date) -> dict:
    """Return a copy of dagplanning without entries whose date is before today.

    The agenda accumulates per-date overrides. Once a date has passed,
    its entry is dead weight: the bell already rang (or didn't), and
    events.jsonl is the historical record we actually want to keep.
    Without this prune, dagplanning.json grows unbounded — every
    holiday, every snow day, forever.

    Today is kept (the bell may still ring later today). Entries with
    a date string that doesn't parse as ISO YYYY-MM-DD are also kept,
    so we don't silently drop unexpected data; if there's garbage in
    the file it's better to surface it than to delete it.
    """
    keep = {}
    for k, v in dagplanning.items():
        try:
            entry_date = date.fromisoformat(k)
        except (TypeError, ValueError):
            keep[k] = v
            continue
        if entry_date >= today:
            keep[k] = v
    return keep


def _next_local_midnight(now: datetime) -> datetime:
    """Return midnight at the start of the next day, in local time.

    Used to tell the API client when its cached schedule will become
    stale: the rooster can flip at midnight (different day, different
    standaardweek slot), so cache until then and check again.
    """
    tomorrow = now.date() + timedelta(days=1)
    return datetime.combine(tomorrow, time(0, 0, 0))
