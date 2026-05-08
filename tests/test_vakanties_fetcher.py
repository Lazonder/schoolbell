"""
Tests for vakanties_fetcher: the parser, validator, URL builder, and
schooljaar helper. The HTML fixture is the actual table layout copied
from a rijksoverheid.nl page (schooljaar 2025-2026), so a real-world
regression in the upstream HTML structure will surface here as a
failed test rather than as a silent bad import.

Network is not exercised — fetch_html() is wrapped only when the
daemon or CLI runs. Tests pass without internet access.
"""

from datetime import date

import pytest

from vakanties_fetcher import (
    Vakantie,
    VakantiesResult,
    combined_payload,
    migrate_legacy_format,
    parse_date_range,
    parse_schooljaar_html,
    schooljaren_to_fetch,
    target_schooljaar,
    url_for_schooljaar,
    validate_result,
)


# Real HTML structure as observed on
# rijksoverheid.nl/onderwerpen/schoolvakanties/.../overzicht-schoolvakanties-2025-2026.
# Single table — basisscholen. The 't&#x2F;m' entity is what the page
# serves; BeautifulSoup decodes it to 't/m'.
SAMPLE_HTML_2025_2026 = """
<html><body>
<table>
 <caption></caption>
 <thead>
  <tr>
   <th scope="row"></th>
   <th scope="col">Regio Noord</th>
   <th scope="col">Regio Midden</th>
   <th scope="col">Regio Zuid</th>
  </tr>
 </thead>
 <tbody>
  <tr>
   <th scope="row"><p>Herfstvakantie</p></th>
   <td>18 oktober t&#x2F;m 26 oktober 2025</td>
   <td>18 oktober t&#x2F;m 26 oktober 2025</td>
   <td>11 oktober t&#x2F;m 19 oktober 2025</td>
  </tr>
  <tr>
   <th scope="row"><p>Kerstvakantie</p></th>
   <td>20 december 2025 t&#x2F;m 4 januari 2026</td>
   <td>20 december 2025 t&#x2F;m 4 januari 2026</td>
   <td>20 december 2025 t&#x2F;m 4 januari 2026</td>
  </tr>
  <tr>
   <th scope="row"><p>Voorjaarsvakantie</p></th>
   <td>21 februari t&#x2F;m 1 maart 2026</td>
   <td>14 februari t&#x2F;m 22 februari 2026</td>
   <td>14 februari t&#x2F;m 22 februari 2026</td>
  </tr>
  <tr>
   <th scope="row"><p>Meivakantie</p></th>
   <td>25 april t&#x2F;m 3 mei 2026</td>
   <td>25 april t&#x2F;m 3 mei 2026</td>
   <td>25 april t&#x2F;m 3 mei 2026</td>
  </tr>
  <tr>
   <th scope="row"><p>Zomervakantie</p></th>
   <td>4 juli t&#x2F;m 16 augustus 2026</td>
   <td>18 juli t&#x2F;m 30 augustus 2026</td>
   <td>11 juli t&#x2F;m 23 augustus 2026</td>
  </tr>
 </tbody>
</table>
</body></html>
"""


# ---------------------------------------------------------------------------
# parse_date_range
# ---------------------------------------------------------------------------


def test_parse_date_range_year_only_on_right():
    # Most common case in the source HTML: '18 oktober t/m 26 oktober 2025'.
    # Left side has no year; we infer it from the right side.
    start, eind = parse_date_range("18 oktober t/m 26 oktober 2025")
    assert start == date(2025, 10, 18)
    assert eind == date(2025, 10, 26)


def test_parse_date_range_year_on_both_sides_for_cross_year():
    # Kerstvakantie crosses the year boundary, so both sides need
    # explicit years. The parser must use each side's own year.
    start, eind = parse_date_range("20 december 2025 t/m 4 januari 2026")
    assert start == date(2025, 12, 20)
    assert eind == date(2026, 1, 4)


def test_parse_date_range_year_only_on_left():
    # Symmetry case: not seen in the wild, but the parser supports it.
    # If the only year is on the left, the right side inherits it.
    start, eind = parse_date_range("18 oktober 2025 t/m 26 oktober")
    assert start == date(2025, 10, 18)
    assert eind == date(2025, 10, 26)


def test_parse_date_range_html_entity_already_decoded():
    # In real use BeautifulSoup decodes &#x2F; to '/' before this
    # function sees it. But just in case some caller passes the raw
    # entity through, make sure the regex still matches the slash.
    # (The 't&#x2F;m' literal would not split — confirming we rely
    # on prior decoding.)
    start, eind = parse_date_range("11 oktober t/m 19 oktober 2025")
    assert (start, eind) == (date(2025, 10, 11), date(2025, 10, 19))


def test_parse_date_range_no_year_anywhere_raises():
    with pytest.raises(ValueError, match="no year"):
        parse_date_range("11 oktober t/m 19 oktober")


def test_parse_date_range_inverted_dates_raise():
    # Defensive: if the source ever ships a clearly-wrong range,
    # we raise rather than silently store a backwards period.
    with pytest.raises(ValueError, match="starts after"):
        parse_date_range("19 oktober t/m 11 oktober 2025")


def test_parse_date_range_unknown_month_raises():
    with pytest.raises(ValueError, match="Unknown Dutch month"):
        parse_date_range("11 oktoberen t/m 19 oktoberen 2025")


# ---------------------------------------------------------------------------
# parse_schooljaar_html — full table parse
# ---------------------------------------------------------------------------


def test_parse_schooljaar_html_returns_all_three_regions():
    result = parse_schooljaar_html(SAMPLE_HTML_2025_2026, "2025-2026")
    assert result.schooljaar == "2025-2026"
    assert set(result.by_region.keys()) == {"Noord", "Midden", "Zuid"}


def test_parse_schooljaar_html_each_region_has_five_vacations():
    result = parse_schooljaar_html(SAMPLE_HTML_2025_2026, "2025-2026")
    for region in ("Noord", "Midden", "Zuid"):
        assert len(result.by_region[region]) == 5, region


def test_parse_schooljaar_html_zuid_herfst_is_earlier_than_noord():
    # The actual official 2025-2026 data: Zuid has herfstvakantie a
    # week earlier than Noord/Midden. This was originally guessed wrong
    # in vakanties.example.json — pinning this test catches the same
    # mistake if the parser ever swaps the column→region mapping.
    result = parse_schooljaar_html(SAMPLE_HTML_2025_2026, "2025-2026")
    noord = next(v for v in result.by_region["Noord"] if v.naam == "Herfstvakantie")
    zuid  = next(v for v in result.by_region["Zuid"]  if v.naam == "Herfstvakantie")
    assert zuid.start == date(2025, 10, 11)
    assert noord.start == date(2025, 10, 18)


def test_parse_schooljaar_html_kerstvakantie_crosses_year():
    result = parse_schooljaar_html(SAMPLE_HTML_2025_2026, "2025-2026")
    for region in ("Noord", "Midden", "Zuid"):
        kerst = next(v for v in result.by_region[region] if v.naam == "Kerstvakantie")
        assert kerst.start == date(2025, 12, 20)
        assert kerst.eind == date(2026, 1, 4)


def test_parse_schooljaar_html_zomervakantie_staggered_by_region():
    # Summer staggering is the most useful per-region data — the bell
    # silence window is ~6 weeks long and starts/ends at different
    # times for each region.
    result = parse_schooljaar_html(SAMPLE_HTML_2025_2026, "2025-2026")
    starts = {
        region: next(v.start for v in result.by_region[region] if v.naam == "Zomervakantie")
        for region in ("Noord", "Midden", "Zuid")
    }
    assert starts["Noord"] == date(2026, 7, 4)
    assert starts["Zuid"]  == date(2026, 7, 11)
    assert starts["Midden"] == date(2026, 7, 18)


def test_parse_schooljaar_html_to_schooljaar_block_shape():
    # The block produced for the multi-year file should have a
    # 'fetched_at' string and the regios sub-object exactly as
    # import_vakanties consumes.
    result = parse_schooljaar_html(SAMPLE_HTML_2025_2026, "2025-2026")
    block = result.to_schooljaar_block(fetched_at="2026-05-08T03:00:00+00:00")
    assert block["fetched_at"] == "2026-05-08T03:00:00+00:00"
    assert set(block["regios"].keys()) == {"Noord", "Midden", "Zuid"}
    sample = block["regios"]["Noord"][0]
    assert set(sample.keys()) == {"naam", "start", "eind"}
    # ISO-string dates, matching the existing data/vakanties.json contract.
    assert sample["start"].count("-") == 2


def test_parse_schooljaar_html_no_table_raises():
    with pytest.raises(ValueError, match="No <table>"):
        parse_schooljaar_html("<html><body><p>nothing</p></body></html>", "2025-2026")


def test_parse_schooljaar_html_missing_region_in_header_raises():
    # If rijksoverheid drops a region (won't happen, but if the HTML
    # mutates we want to know loudly, not silently).
    bad_html = SAMPLE_HTML_2025_2026.replace("Regio Zuid", "Regio Boemerang")
    with pytest.raises(ValueError, match="missing one or more regions"):
        parse_schooljaar_html(bad_html, "2025-2026")


# ---------------------------------------------------------------------------
# validate_result
# ---------------------------------------------------------------------------


def test_validate_happy_path():
    result = parse_schooljaar_html(SAMPLE_HTML_2025_2026, "2025-2026")
    ok, msg = validate_result(result, expect_schooljaar="2025-2026")
    assert ok is True, msg


def test_validate_rejects_schooljaar_mismatch():
    result = parse_schooljaar_html(SAMPLE_HTML_2025_2026, "2025-2026")
    ok, msg = validate_result(result, expect_schooljaar="2026-2027")
    assert ok is False
    assert "Schooljaar mismatch" in msg


def test_validate_rejects_too_few_vacations():
    # Drop 3 of 5 entries from one region — should reject the whole
    # result, not silently accept a half-empty region.
    result = parse_schooljaar_html(SAMPLE_HTML_2025_2026, "2025-2026")
    result.by_region["Noord"] = result.by_region["Noord"][:2]
    ok, msg = validate_result(result)
    assert ok is False
    assert "Noord" in msg


# ---------------------------------------------------------------------------
# URL / schooljaar helpers
# ---------------------------------------------------------------------------


def test_url_for_schooljaar_concatenates_correctly():
    url = url_for_schooljaar("2025-2026")
    assert url.endswith("/overzicht-schoolvakanties-2025-2026")
    assert url.startswith("https://www.rijksoverheid.nl/")


def test_url_for_schooljaar_rejects_invalid_format():
    with pytest.raises(ValueError):
        url_for_schooljaar("2025/2026")


@pytest.mark.parametrize(
    "today, expected",
    [
        (date(2026, 1, 15), "2025-2026"),  # mid-January → still last year's schooljaar
        (date(2026, 7, 31), "2025-2026"),  # last day of July → still last year's
        (date(2026, 8, 1),  "2026-2027"),  # August 1 → flip to new schooljaar (the trigger day)
        (date(2026, 8, 15), "2026-2027"),  # mid-August → same
        (date(2026, 12, 31),"2026-2027"),  # year-end → still in this schooljaar
    ],
)
def test_target_schooljaar(today, expected):
    assert target_schooljaar(today) == expected


# ---------------------------------------------------------------------------
# schooljaren_to_fetch: rolling 5-year window
# ---------------------------------------------------------------------------


def test_schooljaren_to_fetch_default_count_is_five():
    today = date(2026, 4, 26)  # currently in 2025-2026
    sj = schooljaren_to_fetch(today)
    assert sj == ["2025-2026", "2026-2027", "2027-2028", "2028-2029", "2029-2030"]


def test_schooljaren_to_fetch_after_august_flips_window():
    today = date(2026, 8, 15)  # now in 2026-2027
    sj = schooljaren_to_fetch(today)
    assert sj == ["2026-2027", "2027-2028", "2028-2029", "2029-2030", "2030-2031"]


def test_schooljaren_to_fetch_custom_count():
    sj = schooljaren_to_fetch(date(2026, 4, 26), count=2)
    assert sj == ["2025-2026", "2026-2027"]


# ---------------------------------------------------------------------------
# combined_payload: multi-year on-disk shape, with partial-failure preserve
# ---------------------------------------------------------------------------


def _make_result(schooljaar: str, naam: str = "Stub") -> VakantiesResult:
    """Tiny VakantiesResult helper for payload tests."""
    r = VakantiesResult(schooljaar=schooljaar)
    for region in ("Noord", "Midden", "Zuid"):
        r.by_region[region] = [Vakantie(naam=naam, start=date(2025, 1, 1), eind=date(2025, 1, 7))]
    return r


def test_combined_payload_no_previous():
    successes = {"2025-2026": _make_result("2025-2026")}
    payload = combined_payload(successes, fetched_at="2026-05-08T00:00:00+00:00")
    assert payload["_source"] == "rijksoverheid.nl"
    assert payload["_fetched_at"] == "2026-05-08T00:00:00+00:00"
    assert set(payload["schooljaren"].keys()) == {"2025-2026"}
    block = payload["schooljaren"]["2025-2026"]
    assert block["fetched_at"] == "2026-05-08T00:00:00+00:00"
    assert set(block["regios"]) == {"Noord", "Midden", "Zuid"}


def test_combined_payload_preserves_previous_years_not_in_successes():
    # The partial-failure case: previous run had 5 years, this run only
    # got 3 of them. The 2 missing years must keep their old data so
    # an outage doesn't shrink our horizon.
    previous = {
        "schooljaren": {
            "2025-2026": {"fetched_at": "old", "regios": {"Noord": [], "Midden": [], "Zuid": []}},
            "2026-2027": {"fetched_at": "old", "regios": {"Noord": [], "Midden": [], "Zuid": []}},
            "2029-2030": {"fetched_at": "old", "regios": {"Noord": [], "Midden": [], "Zuid": []}},
        }
    }
    successes = {
        "2025-2026": _make_result("2025-2026"),
        "2026-2027": _make_result("2026-2027"),
    }
    payload = combined_payload(successes, previous=previous, fetched_at="new")

    # Both refreshed years got new data.
    assert payload["schooljaren"]["2025-2026"]["fetched_at"] == "new"
    assert payload["schooljaren"]["2026-2027"]["fetched_at"] == "new"
    # The not-refreshed year's data stayed.
    assert payload["schooljaren"]["2029-2030"]["fetched_at"] == "old"


def test_combined_payload_overwrites_previous_year_with_new_success():
    # Same year in both previous and successes -> success wins.
    previous = {"schooljaren": {"2025-2026": {"fetched_at": "old", "regios": {"Noord": []}}}}
    successes = {"2025-2026": _make_result("2025-2026", naam="Fresh")}
    payload = combined_payload(successes, previous=previous, fetched_at="new")
    assert payload["schooljaren"]["2025-2026"]["fetched_at"] == "new"
    # The new result populated the regios properly (3 regions, not 1).
    assert set(payload["schooljaren"]["2025-2026"]["regios"]) == {"Noord", "Midden", "Zuid"}


# ---------------------------------------------------------------------------
# migrate_legacy_format: lift old single-year payload to multi-year
# ---------------------------------------------------------------------------


def test_migrate_legacy_format_lifts_single_year():
    legacy = {
        "schooljaar": "2025-2026",
        "_source": "rijksoverheid.nl",
        "_fetched_at": "2026-04-26T15:00:00+00:00",
        "regios": {
            "Noord": [{"naam": "Herfstvakantie", "start": "2025-10-18", "eind": "2025-10-26"}],
            "Midden": [],
            "Zuid": [],
        },
    }
    new = migrate_legacy_format(legacy)
    assert "schooljaren" in new
    assert "2025-2026" in new["schooljaren"]
    assert new["schooljaren"]["2025-2026"]["regios"] == legacy["regios"]
    assert new["schooljaren"]["2025-2026"]["fetched_at"] == legacy["_fetched_at"]


def test_migrate_legacy_format_passes_through_already_multi_year():
    already = {"schooljaren": {"2025-2026": {"fetched_at": "x", "regios": {}}}}
    assert migrate_legacy_format(already) is already


def test_migrate_legacy_format_handles_garbage():
    # Anything we don't recognize becomes an empty multi-year payload —
    # callers can treat that as 'no data yet' uniformly.
    assert migrate_legacy_format({}) == {"schooljaren": {}}
    assert migrate_legacy_format({"unrelated": True}) == {"schooljaren": {}}
    assert migrate_legacy_format(None) == {"schooljaren": {}}
