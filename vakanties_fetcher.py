#!/usr/bin/env python3
"""
Fetch and parse Dutch school vacation data from rijksoverheid.nl.

The official source publishes one HTML page per schooljaar, each with
a table of vacation periods × the three regions (Noord, Midden, Zuid).
This module turns that HTML into the nested JSON format the
'Vakanties importeren' button on the Agenda already understands:

    {
      "schooljaar": "2025-2026",
      "regios": {
        "Noord": [
          {"naam": "Herfstvakantie", "start": "2025-10-18", "eind": "2025-10-26"},
          ...
        ],
        "Midden": [...],
        "Zuid":  [...]
      }
    }

Used by:
    - the daemon, for the August 1 yearly auto-refresh
    - a 'Vakanties verversen' button in the agenda UI
    - this file's own __main__ entry point as a CLI for testing

Run from the command line for a dry-run:
    python3 vakanties_fetcher.py 2025-2026 --dry-run

Or to actually overwrite data/vakanties.json:
    python3 vakanties_fetcher.py 2025-2026 --output data/vakanties.json

Network notes: the parser expects the raw server-side HTML response
from rijksoverheid.nl (no JavaScript rendering). As of writing this,
the date tables are server-rendered, so requests.get() is enough.
If they ever switch to client-side rendering, this module will start
failing the validation check (no regions parsed) and the daemon will
log the failure and leave the existing vakanties.json untouched.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

import requests
from bs4 import BeautifulSoup


# -- Constants -----------------------------------------------------------------

RIJKSOVERHEID_BASE = (
    "https://www.rijksoverheid.nl/onderwerpen/schoolvakanties"
    "/overzicht-schoolvakanties-per-schooljaar"
)

# Map Dutch month names (lowercase, exactly as they appear in the HTML)
# to month numbers. We intentionally use the long names — rijksoverheid
# spells them out in full ("oktober", not "okt").
DUTCH_MONTHS = {
    "januari": 1, "februari": 2, "maart": 3, "april": 4,
    "mei": 5, "juni": 6, "juli": 7, "augustus": 8,
    "september": 9, "oktober": 10, "november": 11, "december": 12,
}

REGIONS = ("Noord", "Midden", "Zuid")

# t/m may render as 't/m' (after BeautifulSoup decodes &#x2F;), 't.m.' or
# rarely with NBSP whitespace. This pattern matches all of them.
TM_SEPARATOR = re.compile(r"\s+t[./]m\.?\s+", re.IGNORECASE)

# A single date inside a range cell. Year is optional because the left
# side of '18 oktober t/m 26 oktober 2025' has no year — we fill it in
# from the right side later.
DATE_PATTERN = re.compile(
    r"^\s*(?P<day>\d{1,2})\s+(?P<month>[a-zA-Z]+)(?:\s+(?P<year>\d{4}))?\s*$"
)


# -- Data containers -----------------------------------------------------------

@dataclass
class Vakantie:
    """One vacation period for one region."""
    naam: str
    start: date
    eind: date

    def to_json_obj(self) -> dict:
        return {
            "naam": self.naam,
            "start": self.start.isoformat(),
            "eind": self.eind.isoformat(),
        }


@dataclass
class VakantiesResult:
    """Complete parsed result for one schooljaar."""
    schooljaar: str
    by_region: dict[str, list[Vakantie]] = field(default_factory=dict)

    def to_schooljaar_block(self, fetched_at: str) -> dict:
        """Serialize this single year as a sub-object inside the
        combined multi-year payload (see combined_payload below)."""
        return {
            "fetched_at": fetched_at,
            "regios": {
                region: [v.to_json_obj() for v in self.by_region.get(region, [])]
                for region in REGIONS
            },
        }


# -- Multi-year combined payload ----------------------------------------------
#
# The on-disk format for data/vakanties.json holds multiple schooljaren in
# one file:
#
#   {
#     "_source": "rijksoverheid.nl",
#     "_fetched_at": "<ISO timestamp of last refresh attempt>",
#     "schooljaren": {
#       "2025-2026": { "fetched_at": "...", "regios": {Noord: [...], ...} },
#       "2026-2027": { ... },
#       ...
#     }
#   }
#
# Why a single file with all years instead of one file per year:
#   - One atomic write.
#   - The agenda's import button can apply ALL future years in one click.
#   - The Voorkeuren status panel can list 'opgeslagen schooljaren' from
#     a single source of truth.
#
# An older single-schooljaar payload (from when the fetcher only handled
# one year) is automatically lifted into this shape — see migrate_legacy_format.


def combined_payload(
    successes: dict[str, "VakantiesResult"],
    *,
    previous: Optional[dict] = None,
    fetched_at: Optional[str] = None,
) -> dict:
    """Build the multi-year on-disk payload.

    `successes`: schooljaar -> freshly-parsed VakantiesResult.
    `previous`:  the existing data/vakanties.json contents, if any.
                 School years present in `previous` but not in `successes`
                 are preserved in the output — that's what makes a
                 partial fetch failure non-destructive (e.g. if 4 of 5
                 schooljaren refreshed cleanly and 1 returned 404, the
                 unhealthy year keeps its previously-good data instead
                 of being dropped).
    `fetched_at`: ISO timestamp; defaults to now-UTC.
    """
    if fetched_at is None:
        fetched_at = datetime.now(timezone.utc).isoformat()

    out = {
        "_source": "rijksoverheid.nl",
        "_fetched_at": fetched_at,
        "schooljaren": {},
    }

    if previous and isinstance(previous.get("schooljaren"), dict):
        for sj, block in previous["schooljaren"].items():
            if isinstance(block, dict):
                out["schooljaren"][sj] = block

    for sj, result in successes.items():
        out["schooljaren"][sj] = result.to_schooljaar_block(fetched_at)

    return out


def migrate_legacy_format(data: dict) -> dict:
    """Lift an older single-schooljaar payload to the multi-year shape.

    Old format (pre-multi-year):
        {"schooljaar": "2025-2026", "regios": {...}, "_fetched_at": "..."}

    New format:
        {"schooljaren": {"2025-2026": {"fetched_at": "...", "regios": {...}}}}

    Already-multi-year payloads pass through unchanged.
    Anything we don't recognize becomes an empty multi-year payload —
    callers should treat that as 'no data yet'.
    """
    if not isinstance(data, dict):
        return {"schooljaren": {}}
    if isinstance(data.get("schooljaren"), dict):
        return data  # already multi-year
    if "schooljaar" in data and isinstance(data.get("regios"), dict):
        fetched_at = data.get("_fetched_at", "")
        return {
            "_source": data.get("_source", "rijksoverheid.nl"),
            "_fetched_at": fetched_at,
            "schooljaren": {
                data["schooljaar"]: {
                    "fetched_at": fetched_at,
                    "regios": data["regios"],
                }
            },
        }
    return {"schooljaren": {}}


def schooljaren_to_fetch(today: date, count: int = 5) -> list[str]:
    """Return the schooljaren to refresh: current + (count-1) ahead.

    Rijksoverheid publishes ~5 schooljaren ahead, so fetching the
    current year plus the next four gives us the entire useful
    horizon. Using more than 5 risks 404s on years rijksoverheid
    hasn't published yet (which we'd handle as failures, but they'd
    fill the events log with noise).
    """
    base = target_schooljaar(today)
    base_start_year = int(base.split("-")[0])
    return [f"{base_start_year + i}-{base_start_year + i + 1}" for i in range(count)]


# -- URL / schooljaar helpers --------------------------------------------------

def url_for_schooljaar(schooljaar: str) -> str:
    """Construct the rijksoverheid URL for a given schooljaar.

    The URL pattern is deterministic — we don't have to scrape the index
    page to find the link. If rijksoverheid ever changes the slug, this
    constant breaks loudly (404) and we adjust here.
    """
    if not re.match(r"^\d{4}-\d{4}$", schooljaar):
        raise ValueError(f"Invalid schooljaar format: {schooljaar!r}, expected 'YYYY-YYYY'")
    return f"{RIJKSOVERHEID_BASE}/overzicht-schoolvakanties-{schooljaar}"


def target_schooljaar(today: date) -> str:
    """Determine which schooljaar 'today' falls into.

    Dutch school years run roughly August–July. From August 1 onwards,
    the new schooljaar is active; before then we're in the old one.
    On August 1, this returns the *new* schooljaar — exactly what the
    daemon's yearly refresh wants to fetch.
    """
    if today.month >= 8:
        return f"{today.year}-{today.year + 1}"
    return f"{today.year - 1}-{today.year}"


# -- Date parsing --------------------------------------------------------------

def _parse_dutch_date(s: str, fallback_year: Optional[int] = None) -> date:
    """Parse a single Dutch date like '18 oktober 2025' or '18 oktober'.

    If the year is missing, fallback_year is required. Raises ValueError
    on anything we can't parse.
    """
    m = DATE_PATTERN.match(s)
    if not m:
        raise ValueError(f"Cannot parse Dutch date: {s!r}")
    day = int(m.group("day"))
    month_name = m.group("month").lower()
    if month_name not in DUTCH_MONTHS:
        raise ValueError(f"Unknown Dutch month {month_name!r} in {s!r}")
    month = DUTCH_MONTHS[month_name]
    year_str = m.group("year")
    if year_str is not None:
        year = int(year_str)
    elif fallback_year is not None:
        year = fallback_year
    else:
        raise ValueError(f"Date {s!r} has no year and no fallback was provided")
    return date(year, month, day)


def parse_date_range(text: str) -> tuple[date, date]:
    """Parse a cell like '18 oktober t/m 26 oktober 2025'.

    Year handling:
      - If only the right side has a year, the left side inherits it.
        ('18 oktober t/m 26 oktober 2025' -> both in 2025.)
      - If both sides have years, they're both used as-is.
        ('20 december 2025 t/m 4 januari 2026' -> Dec 20 2025 and Jan 4 2026.)
      - If only the left has a year, the right inherits it. (Unlikely
        but supported for symmetry.)
      - If neither has a year, ValueError. The caller wouldn't know
        what to do with that.
    """
    parts = TM_SEPARATOR.split(text.strip())
    if len(parts) != 2:
        raise ValueError(f"Cannot split on 't/m': {text!r}")

    left_raw, right_raw = parts[0].strip(), parts[1].strip()

    # Pre-detect explicit years to decide fallback direction.
    left_has_year = bool(re.search(r"\b\d{4}\b", left_raw))
    right_has_year = bool(re.search(r"\b\d{4}\b", right_raw))

    if not left_has_year and not right_has_year:
        raise ValueError(f"Date range has no year on either side: {text!r}")

    # Parse whichever side has its own year first (or both); use that
    # as the fallback for the other side.
    if right_has_year:
        right_date = _parse_dutch_date(right_raw)
        left_date = _parse_dutch_date(left_raw, fallback_year=right_date.year)
    else:
        # right has no year, left does
        left_date = _parse_dutch_date(left_raw)
        right_date = _parse_dutch_date(right_raw, fallback_year=left_date.year)

    if left_date > right_date:
        raise ValueError(
            f"Date range starts after it ends: {left_date.isoformat()} > "
            f"{right_date.isoformat()} (from {text!r})"
        )
    return left_date, right_date


# -- HTML parsing --------------------------------------------------------------

def parse_schooljaar_html(html: str, schooljaar: str) -> VakantiesResult:
    """Extract vacation data from a rijksoverheid schooljaar page.

    Expects the page structure as observed in 2025-2026:
      <table>
        <thead>
          <tr><th></th><th>Regio Noord</th><th>Regio Midden</th><th>Regio Zuid</th></tr>
        </thead>
        <tbody>
          <tr>
            <th><p>Herfstvakantie</p></th>
            <td>11 oktober t/m 19 oktober 2025</td>
            <td>18 oktober t/m 26 oktober 2025</td>
            <td>...</td>
          </tr>
          ...
        </tbody>
      </table>

    The column→region mapping is read from <thead> rather than being
    hardcoded, so a column reorder on rijksoverheid's side wouldn't
    silently mis-map regions.

    If the page contains multiple tables (e.g. basisscholen vs
    voortgezet), we use the first one. That matches the typical school
    bell use case (basisonderwijs).
    """
    soup = BeautifulSoup(html, "html.parser")

    tables = soup.find_all("table")
    if not tables:
        raise ValueError("No <table> elements found on the page")

    table = tables[0]

    # --- Header row → region column index mapping
    thead = table.find("thead")
    if not thead:
        raise ValueError("First table has no <thead>")
    header_row = thead.find("tr")
    if not header_row:
        raise ValueError("<thead> has no <tr>")
    header_cells = header_row.find_all(["th", "td"])

    region_to_col: dict[str, int] = {}
    for col_idx, cell in enumerate(header_cells):
        text = cell.get_text(" ", strip=True).lower()
        for region in REGIONS:
            if region.lower() in text:
                region_to_col[region] = col_idx
                break

    missing = set(REGIONS) - set(region_to_col)
    if missing:
        raise ValueError(
            f"Header is missing one or more regions: {sorted(missing)}. "
            f"Got headers: {[c.get_text(' ', strip=True) for c in header_cells]!r}"
        )

    # --- Body rows → vacations per region
    tbody = table.find("tbody")
    if not tbody:
        raise ValueError("First table has no <tbody>")

    result = VakantiesResult(schooljaar=schooljaar)
    for region in REGIONS:
        result.by_region[region] = []

    for row in tbody.find_all("tr"):
        cells = row.find_all(["th", "td"])
        if not cells:
            continue
        # The vacation name is in the first cell (a <th scope="row">
        # in our example, but we don't insist on the tag — just on
        # being first).
        naam = cells[0].get_text(" ", strip=True)
        if not naam:
            continue

        for region, col_idx in region_to_col.items():
            if col_idx >= len(cells):
                # Row has fewer columns than the header — shouldn't
                # happen, but skip rather than raise.
                continue
            cell_text = cells[col_idx].get_text(" ", strip=True)
            if not cell_text:
                continue
            try:
                start, eind = parse_date_range(cell_text)
            except ValueError as e:
                # One bad cell shouldn't kill the whole import. Skip
                # this entry for this region; validation will catch it
                # later if too many are missing.
                print(
                    f"[WARN] Could not parse date for {region}/{naam}: "
                    f"{cell_text!r}: {e}",
                    file=sys.stderr,
                )
                continue
            result.by_region[region].append(
                Vakantie(naam=naam, start=start, eind=eind)
            )

    return result


# -- Validation ----------------------------------------------------------------

def validate_result(result: VakantiesResult, expect_schooljaar: Optional[str] = None) -> tuple[bool, str]:
    """Sanity-check parsed data before persisting it.

    Why this exists: scraping is fragile. If rijksoverheid changes the
    page layout in a way that breaks parsing (e.g. shuffles columns,
    drops rows), we'd rather refuse to overwrite a perfectly fine
    existing vakanties.json than corrupt it.

    Reasons to reject:
      - Less than 3 regions (one or more failed to parse)
      - Any region with fewer than 4 vacations (Dutch schooljaren
        normally have 5 — herfst/kerst/voorjaar/mei/zomer; we accept
        4 for some flexibility)
      - Dates outside the expected schooljaar's calendar years
        (catches accidental cross-year-page parsing)
    """
    if expect_schooljaar and result.schooljaar != expect_schooljaar:
        return False, (
            f"Schooljaar mismatch: result is {result.schooljaar}, "
            f"expected {expect_schooljaar}"
        )

    for region in REGIONS:
        if region not in result.by_region:
            return False, f"Missing region: {region}"
        if len(result.by_region[region]) < 4:
            return False, (
                f"Region {region} has only {len(result.by_region[region])} "
                f"vacations parsed (expected 4 or more)"
            )

    # Date sanity: every parsed date should fall within the calendar
    # years that the schooljaar spans (e.g. 2025-2026 → years 2025 and
    # 2026). Anything else is almost certainly a parser bug.
    try:
        start_year = int(result.schooljaar.split("-")[0])
    except (ValueError, IndexError):
        return False, f"Invalid schooljaar in result: {result.schooljaar!r}"
    allowed_years = {start_year, start_year + 1}

    for region, vakanties in result.by_region.items():
        for v in vakanties:
            if v.start.year not in allowed_years or v.eind.year not in allowed_years:
                return False, (
                    f"{region}/{v.naam}: dates {v.start} .. {v.eind} are outside "
                    f"the expected calendar years {sorted(allowed_years)}"
                )

    return True, "OK"


# -- HTTP fetch ----------------------------------------------------------------

def fetch_html(url: str, timeout: float = 30.0) -> str:
    """Fetch a page over HTTPS. Lets requests' exceptions bubble up.

    User-Agent is set explicitly. Some government sites filter out the
    default 'python-requests/X.Y' UA; setting a real-looking one
    avoids that and is also polite (admins can grep for 'schoolbell'
    in their access logs to attribute traffic).
    """
    headers = {
        "User-Agent": (
            "ivko-schoolbell/1.0 "
            "(+https://github.com/lazonder/schoolbell; periodic vakantie sync)"
        ),
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "nl,en;q=0.5",
    }
    r = requests.get(url, headers=headers, timeout=timeout)
    r.raise_for_status()
    # rijksoverheid serves UTF-8 with the charset in headers; requests
    # normally figures this out. Fall back explicitly just in case.
    if r.encoding is None or r.encoding.lower() == "iso-8859-1":
        r.encoding = "utf-8"
    return r.text


# -- High-level orchestration --------------------------------------------------

def fetch_and_parse(schooljaar: str, *, timeout: float = 30.0) -> VakantiesResult:
    """Fetch + parse + validate. Raises on any failure.

    Returns a VakantiesResult that's ready to be serialized to JSON.
    The caller is expected to do the JSON write atomically (see
    write_atomically).
    """
    url = url_for_schooljaar(schooljaar)
    html = fetch_html(url, timeout=timeout)
    result = parse_schooljaar_html(html, schooljaar)
    ok, msg = validate_result(result, expect_schooljaar=schooljaar)
    if not ok:
        raise ValueError(f"Validation failed for {schooljaar}: {msg}")
    return result


def fetch_and_parse_multi(
    schooljaren: list[str],
    *,
    timeout: float = 30.0,
) -> tuple[dict[str, VakantiesResult], list[tuple[str, str]]]:
    """Fetch and parse a list of schooljaren independently.

    Returns:
      successes -- {schooljaar: VakantiesResult} for the years that
                   fetched, parsed, and validated cleanly.
      failures  -- [(schooljaar, error_message), ...] for the rest.

    Independent failure handling on purpose: if rijksoverheid hasn't
    published 2030-2031 yet, we still want the 4 years that DID work.
    The caller (daemon / route) decides how to merge with prior data —
    typically via combined_payload(..., previous=prior_data) so the
    failed years keep their previously-good values.
    """
    successes: dict[str, VakantiesResult] = {}
    failures: list[tuple[str, str]] = []
    for sj in schooljaren:
        try:
            successes[sj] = fetch_and_parse(sj, timeout=timeout)
        except Exception as e:
            failures.append((sj, str(e)))
    return successes, failures


def write_atomically(path: str | os.PathLike, data: dict) -> None:
    """Write JSON to `path` via tmp-file + os.replace.

    Same pattern as save_json in webinterface.py. Lives here too so
    the daemon (which doesn't import webinterface) can use it without
    a circular-import dance.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, p)


# -- CLI -----------------------------------------------------------------------

def _cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Fetch and parse Dutch school vacation data from "
            "rijksoverheid.nl. By default fetches the current schooljaar "
            "plus 4 ahead (the typical horizon rijksoverheid publishes). "
            "Useful for testing the parser before letting the daemon "
            "auto-refresh."
        ),
    )
    parser.add_argument(
        "schooljaar",
        nargs="?",
        help=(
            "Single school year as 'YYYY-YYYY' (e.g. '2025-2026'). "
            "If omitted, fetches the default 5-year window starting "
            "from today's current schooljaar. Useful when you only "
            "want to test one year's parser output."
        ),
    )
    parser.add_argument(
        "--output", "-o",
        help="Path to write the JSON. If omitted, prints to stdout (dry-run).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Force stdout output even if --output is set. Useful for diffing.",
    )
    parser.add_argument(
        "--years",
        type=int,
        default=5,
        help=(
            "How many schooljaren to fetch (default 5). Ignored if "
            "an explicit schooljaar argument is given."
        ),
    )
    parser.add_argument(
        "--merge-with",
        help=(
            "Path to an existing vakanties.json. Successful fetches "
            "overwrite that year's entry; failed fetches keep the "
            "previous data for that year (matches daemon behavior)."
        ),
    )
    args = parser.parse_args(argv)

    if args.schooljaar:
        targets = [args.schooljaar]
    else:
        targets = schooljaren_to_fetch(date.today(), count=args.years)

    successes, failures = fetch_and_parse_multi(targets)

    for sj, err in failures:
        print(f"[FAIL] {sj}: {err}", file=sys.stderr)
    for sj in successes:
        print(f"[ OK ] {sj}", file=sys.stderr)

    if not successes:
        print("No school years fetched successfully", file=sys.stderr)
        return 2

    previous = None
    if args.merge_with:
        try:
            with open(args.merge_with, "r", encoding="utf-8") as f:
                previous = migrate_legacy_format(json.load(f))
        except (FileNotFoundError, json.JSONDecodeError) as e:
            print(f"[WARN] --merge-with: {e}", file=sys.stderr)

    payload = combined_payload(successes, previous=previous)

    if args.output and not args.dry_run:
        write_atomically(args.output, payload)
        print(f"Wrote {args.output}", file=sys.stderr)
    else:
        json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")

    return 0 if not failures else 4


if __name__ == "__main__":
    raise SystemExit(_cli())
