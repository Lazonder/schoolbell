"""Blueprint for the Agenda page and the vakanties workflow.

The agenda is where the admin overrides the standaardweek for
specific dates ('today is a study day', 'this Friday is silent',
etc.) and marks whole weeks as 'off' for school holidays. Two
extra POST-only routes around it pull holiday data from
rijksoverheid.nl and apply it.

  GET/POST /agenda                    — render + bulk save
  POST     /agenda/import-vakanties   — turn vakanties.json into
                                        weken_uit entries
  POST     /agenda/refresh-vakanties  — scrape rijksoverheid.nl

Important: ``_load_vakanties_file`` and the import/refresh handlers
are heavy paths. They pull in beautifulsoup4 and may do a network
call. Imports are lazy (inside the function body) so the agenda
render itself stays cheap.
"""

import os
from datetime import date, timedelta
from typing import Optional

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_babel import format_date, gettext as _

import webinterface as wi
from core.dates import iso_weeks_with_weekday_in_range, prune_past_dates
from core.rooster import (
    DAGPLANNING_SILENT_FORM_VALUE,
    default_dagplanning_obj,
    default_roosters_obj,
    default_standaardweek_obj,
    default_weken_uit_obj,
)
from settings_store import Settings


agenda_bp = Blueprint("agenda", __name__)


def _load_vakanties_file() -> tuple[Optional[dict], Optional[str]]:
    """Read and migrate data/vakanties.json. Returns (data, error_msg).

    On a missing file, returns (None, None) — the caller decides whether
    that's an error in their context (import: yes; status display: no).
    On parse failure, returns (None, error message). On success,
    returns the migrated multi-year dict and None.
    """
    if not os.path.exists(wi.VAKANTIES_PATH):
        return None, None
    try:
        import json
        with open(wi.VAKANTIES_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception as e:
        # JSONDecodeError shows as 'JSONDecodeError: ...' which is
        # readable enough for an admin flash; OSError catches
        # permission and disk errors uniformly.
        import json as _json
        if isinstance(e, _json.JSONDecodeError):
            return None, f"vakantiebestand is geen geldige JSON: {e}"
        return None, f"vakantiebestand kon niet worden gelezen: {e}"

    # Lazy import to keep webinterface free of beautifulsoup4 unless
    # somebody actually touches the vakanties path.
    import vakanties_fetcher
    return vakanties_fetcher.migrate_legacy_format(raw), None


# -- Agenda (per-date override of standaardweek) --
@agenda_bp.route("/agenda", methods=["GET", "POST"])
@wi.ui_login_required
def agenda():
    wi.ensure_dirs()
    roosters = wi.load_json(wi.ROOSTERS_PATH, default_roosters_obj())
    dagplanning = wi.load_json(wi.DAGPLANNING_PATH, default_dagplanning_obj())
    standaardweek = wi.load_json(wi.STANDAARDWEEK_PATH, default_standaardweek_obj())
    weken_uit = wi.load_json(wi.WEEKDISABLE_PATH, default_weken_uit_obj())

    # Dropdown options as (value, label) tuples. Three categories:
    #   ""    -> follow standaardweek (drops any existing override)
    #   !off  -> explicit silence override (saved as null in dagplanning)
    #   <r>   -> override to rooster <r>
    opties = (
        [("", "— volg standaardweek —"),
         (DAGPLANNING_SILENT_FORM_VALUE, "— geen bel —")]
        + [(r, r) for r in roosters.keys()]
    )

    # --- POST: save ---
    if request.method == "POST" and request.form.get("_action") == "bulk_save":
        # Two files are written here: dagplanning.json and weken_uit.json.
        # We acquire both locks. The order (dagplanning first, weken_uit
        # second) is fixed across all writers, so there's no risk of
        # deadlock between concurrent requests. Currently this is the
        # only multi-file write path. If more are added, keep the same
        # alphabetical-by-path lock order.
        with wi.locked_json(wi.DAGPLANNING_PATH, default_dagplanning_obj()) as (dag_state, save_dag), \
             wi.locked_json(wi.WEEKDISABLE_PATH, default_weken_uit_obj()) as (_wk_state, save_wk):

            updated_dagplanning = dag_state.copy()
            for key in request.form.keys():
                if key.startswith("day[") and key.endswith("]"):
                    datum = key[4:-1]
                    waarde = (request.form.get(key) or "").strip()
                    if waarde == "":
                        # 'follow standaardweek' — drop any existing override
                        updated_dagplanning.pop(datum, None)
                    elif waarde == DAGPLANNING_SILENT_FORM_VALUE:
                        # Explicit silence override — store as JSON null.
                        # api_effectief_rooster handles null correctly
                        # (returns empty momenten list -> daemon silent).
                        updated_dagplanning[datum] = None
                    elif waarde in roosters:
                        updated_dagplanning[datum] = waarde
                    else:
                        flash(_("Ongeldig rooster voor %(datum)s: '%(waarde)s' bestaat niet. Overgeslagen.", datum=datum, waarde=waarde))

            # Update weeks off
            today = date.today()
            first_monday = today - timedelta(days=today.weekday())
            weeks_list = [first_monday + timedelta(weeks=i) for i in range(52)]

            new_weken_uit = {}
            for wk_start in weeks_list:
                y, w, _w = wk_start.isocalendar()
                wk_key = f"{y}-W{w:02d}"
                if f"week_off[{wk_key}]" in request.form:
                    new_weken_uit[wk_key] = True

            # Drop dagplanning entries from the past so the file doesn't
            # grow unbounded. Done at save time (rather than via a cron)
            # because save is the natural moment for cleanup. The user
            # just made an explicit edit, so doing housekeeping here is
            # the least surprising moment to lose stale data.
            updated_dagplanning = prune_past_dates(updated_dagplanning, today)

            save_dag(updated_dagplanning)
            save_wk(new_weken_uit)

        # Logging + feedback
        dagen_keys = sorted(updated_dagplanning.keys())
        weken_keys = sorted(new_weken_uit.keys())
        wi.log_event("ui", {
            "action": "save_agenda",
            "dagen_count": len(dagen_keys),
            "dagen_first": dagen_keys[0] if dagen_keys else "",
            "dagen_last": dagen_keys[-1] if dagen_keys else "",
            "weken_uit_count": len(weken_keys),
        })
        flash(_("Agenda opgeslagen."))
        return redirect(url_for("agenda.agenda"))

    # --- GET: render data for template ---
    today = date.today()
    first_monday = today - timedelta(days=today.weekday())
    weeks_list = [first_monday + timedelta(weeks=i) for i in range(52)]

    weekday_key_map = ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"]

    def selected_for_date(d: date) -> str:
        """Which dropdown option should be marked 'selected' for date d?

        Mirrors the three-option dropdown:
          - explicit silence override (None in dagplanning) -> "!off"
          - rooster override                                 -> rooster name
          - no override -> show the standaardweek default for that weekday
                           (so the user sees what *will* play if they don't
                            change anything; keeps the UI honest about
                            current effective behavior)

        Note: this does its own lookup (not via the shared helper)
        because it needs to distinguish 'silence override' from 'no
        override', which the helper collapses into '' for both. The
        legacy "" case still falls through to the standaardweek
        default below, matching the helper.
        """
        d_iso = d.isoformat()
        if d_iso in dagplanning:
            v = dagplanning[d_iso]
            if v is None:
                return DAGPLANNING_SILENT_FORM_VALUE
            if v:
                return v
            # empty string in dagplanning -> treat as no override (legacy)
        std_key = weekday_key_map[d.weekday()]
        return standaardweek.get(std_key, "")

    weeks = []
    for wk_start in weeks_list:
        y, w, _w = wk_start.isocalendar()
        wk_key = f"{y}-W{w:02d}"
        off = bool(weken_uit.get(wk_key, False))
        days = [wk_start + timedelta(days=i) for i in range(5)]  # Ma..Vr
        # Locale-aware date format: NL renders '10-05-2026', EN renders
        # '5/10/26', DE '10.05.2026', FR '10/05/2026'. All from one
        # call. format='short' picks the locale's compact convention.
        # The locale itself comes from Flask-Babel's per-request
        # selector (Settings.taal -> select_locale()).
        weeks.append({
            "key": wk_key,
            "off": off,
            "range": f"{format_date(days[0], format='short')} .. {format_date(days[-1], format='short')}",
            "days": [
                {
                    "iso": d.isoformat(),
                    "selected": selected_for_date(d),
                } for d in days
            ]
        })

    s = Settings.load()
    return render_template(
        "agenda.html",
        tab="agenda",
        csrf_token=wi.get_csrf_token(),
        weeks=weeks,
        opties=opties,
        vakanties_path_exists=os.path.exists(wi.VAKANTIES_PATH),
        vakantieregio=s.vakantieregio,
        vakanties_scrape_enabled=s.vakanties_scrape_enabled,
    )


@agenda_bp.route("/agenda/import-vakanties", methods=["POST"])
@wi.ui_login_required
def import_vakanties():
    """Import all stored school holidays from data/vakanties.json into
    weken_uit.

    The vakanties file is maintained by the daemon (or the manual
    refresh button) and contains 1..5 schooljaren of vacation data,
    each with three regions. This handler iterates ALL stored
    schooljaren, picks the region currently configured in Voorkeuren
    (Settings.vakantieregio), expands every {start, eind} period to
    the overlapping ISO weeks, and marks them as 'off'.

    Merge semantics: existing weken_uit entries are kept. The button
    only ADDS to the 'off' set; it never unmarks a week. Idempotent
    (safe to run more than once with the same result) and safe to
    mix with manual 'Bel uit' checkboxes.
    """
    # Master-switch enforcement (see refresh_vakanties for rationale).
    if not Settings.load().vakanties_scrape_enabled:
        flash(_("Vakantie-scraping is uitgeschakeld in Voorkeuren."))
        return redirect(url_for("agenda.agenda"))
    wi.ensure_dirs()

    data, err = _load_vakanties_file()
    if err is not None:
        flash(_("Importeren mislukt: %(err)s", err=err))
        return redirect(url_for("agenda.agenda"))
    if data is None:
        flash(_(
            "Geen vakantiebestand gevonden (%(path)s). "
            "Klik 'Verversen van rijksoverheid.nl' om het op te halen.",
            path=wi.VAKANTIES_PATH,
        ))
        return redirect(url_for("agenda.agenda"))

    schooljaren = data.get("schooljaren", {})
    if not isinstance(schooljaren, dict) or not schooljaren:
        flash(_(
            "Vakantiebestand bevat geen 'schooljaren'. Klik "
            "'Verversen van rijksoverheid.nl' om opnieuw op te halen."
        ))
        return redirect(url_for("agenda.agenda"))

    settings = Settings.load()
    regio = settings.vakantieregio

    new_weeks: set[str] = set()
    skipped: list[str] = []
    schooljaren_processed: list[str] = []
    schooljaren_zonder_regio: list[str] = []

    for sj_key, sj_block in sorted(schooljaren.items()):
        if not isinstance(sj_block, dict):
            skipped.append(f"{sj_key}: schooljaar-blok is geen object")
            continue
        regios_block = sj_block.get("regios", {})
        if not isinstance(regios_block, dict) or regio not in regios_block:
            schooljaren_zonder_regio.append(sj_key)
            continue
        vakanties_lijst = regios_block[regio]
        if not isinstance(vakanties_lijst, list):
            skipped.append(f"{sj_key}/{regio}: regios-entry is geen lijst")
            continue

        schooljaren_processed.append(sj_key)
        for v in vakanties_lijst:
            if not isinstance(v, dict):
                skipped.append(f"{sj_key}: ongeldige entry (geen object)")
                continue
            try:
                start = date.fromisoformat(v["start"])
                eind = date.fromisoformat(v["eind"])
            except (KeyError, TypeError, ValueError) as e:
                skipped.append(f"{sj_key}/{v.get('naam', '?')}: {e}")
                continue
            if eind < start:
                skipped.append(f"{sj_key}/{v.get('naam', '?')}: eind < start")
                continue
            new_weeks |= iso_weeks_with_weekday_in_range(start, eind)

    if not new_weeks:
        # Be specific about why we found nothing: missing region in all
        # years vs garbage entries vs empty file.
        if schooljaren_zonder_regio and not schooljaren_processed:
            schooljaren_lijst = ", ".join(sorted(schooljaren.keys()))
            flash(_(
                "Geen schooljaren in het bestand bevatten regio '%(regio)s'. "
                "Aanwezige schooljaren: %(schooljaren)s.",
                regio=regio,
                schooljaren=schooljaren_lijst,
            ))
        else:
            flash(_(
                "Geen weken om te markeren voor regio %(regio)s. "
                "Controleer het vakantiebestand (%(count)s ongeldige entries).",
                regio=regio,
                count=len(skipped),
            ))
        return redirect(url_for("agenda.agenda"))

    # Merge into existing weken_uit under the file lock so a concurrent
    # agenda-save doesn't lose either the manual edits or the import.
    with wi.locked_json(wi.WEEKDISABLE_PATH, default_weken_uit_obj()) as (state, save):
        for wk in new_weeks:
            state[wk] = True
        save(state)

    wi.log_event("ui", {
        "action": "import_vakanties",
        "regio": regio,
        "schooljaren": schooljaren_processed,
        "weken_count": len(new_weeks),
        "skipped_count": len(skipped),
    })

    schooljaren_lijst = ", ".join(schooljaren_processed)
    msg = _(
        "%(weken)s week(weken) gemarkeerd als 'Bel uit' (regio %(regio)s, "
        "uit %(aantal)s schooljaar/jaren: %(schooljaren)s).",
        weken=len(new_weeks),
        regio=regio,
        aantal=len(schooljaren_processed),
        schooljaren=schooljaren_lijst,
    )
    if skipped:
        voorb = "; ".join(skipped[:3])
        meer = "" if len(skipped) <= 3 else _(" en %(count)s meer", count=len(skipped) - 3)
        msg += _(" Overgeslagen: %(voorb)s%(meer)s.", voorb=voorb, meer=meer)
    flash(msg)
    return redirect(url_for("agenda.agenda"))


@agenda_bp.route("/agenda/refresh-vakanties", methods=["POST"])
@wi.ui_login_required
def refresh_vakanties():
    """Fetch the latest vakanties from rijksoverheid.nl and overwrite
    data/vakanties.json.

    Manual trigger for the same logic the daemon will run on August 1.
    Useful (a) to seed the file the first time, (b) to verify the
    scraper still works against the live page when something seems
    off, (c) to pick up corrections rijksoverheid publishes mid-year.

    The fetcher itself does the parse+validate; if either step fails,
    we leave the existing vakanties.json untouched. The user gets a
    clear flash and the events log records what went wrong.

    The fetcher pulls the current schooljaar plus 4 ahead (~5 years).
    Failures per-year are independent: if one year is unavailable
    (e.g. rijksoverheid hasn't published it yet), the others still
    land. Years that previously succeeded but failed now keep their
    last-good data — see vakanties_fetcher.combined_payload.
    """
    # Server-side honour of the master switch. The UI hides the
    # button when scraping is disabled, but a stale page or a curl
    # bypass shouldn't be able to trigger a refresh either.
    if not Settings.load().vakanties_scrape_enabled:
        flash(_("Vakantie-scraping is uitgeschakeld in Voorkeuren."))
        return redirect(url_for("agenda.agenda"))
    # Lazy import: vakanties_fetcher pulls in beautifulsoup4 and makes
    # a network call. The agenda render path doesn't need either, so
    # keeping the import inside the handler keeps startup cheaper
    # for the 99% of requests that don't refresh.
    import vakanties_fetcher

    today = date.today()
    targets = vakanties_fetcher.schooljaren_to_fetch(today)

    # Read the existing file (if any) so we can preserve last-good
    # data for any year that fails this round.
    previous, prev_err = _load_vakanties_file()
    if prev_err:
        # Treat parse-error of existing file as 'no prior data' rather
        # than aborting. The refresh's whole point is to write a
        # clean file. But surface it so the admin knows the old file
        # was bad.
        flash(_("Bestaand vakantiebestand kon niet gelezen worden (%(err)s); wordt overschreven.", err=prev_err))
        previous = None

    successes, failures = vakanties_fetcher.fetch_and_parse_multi(targets)

    if not successes:
        # Total failure: don't touch the existing file. Tell the admin
        # what happened so they can debug (network? parser? page?).
        first_err = failures[0][1] if failures else "unknown"
        wi.log_event("ui", {
            "action": "refresh_vakanties_error",
            "targets": targets,
            "failures": [{"schooljaar": s, "error": e} for s, e in failures],
        })
        flash(_(
            "Verversen mislukt voor alle %(n)d schooljaren. Eerste fout: %(err)s",
            n=len(targets), err=first_err,
        ))
        return redirect(url_for("agenda.agenda"))

    payload = vakanties_fetcher.combined_payload(successes, previous=previous)
    vakanties_fetcher.write_atomically(wi.VAKANTIES_PATH, payload)

    wi.log_event("ui", {
        "action": "refresh_vakanties_ok",
        "schooljaren_ok": list(successes.keys()),
        "schooljaren_failed": [{"schooljaar": s, "error": e} for s, e in failures],
    })

    msg = (
        f"{len(successes)} schooljaar/jaren opgehaald van rijksoverheid.nl: "
        f"{', '.join(sorted(successes.keys()))}. "
        f"Klik 'Vakanties importeren' om ze toe te passen."
    )
    if failures:
        # Mention failures but don't bury the success.
        msg += (
            f" {len(failures)} mislukt: "
            f"{', '.join(s for s, _ in failures)}."
        )
    flash(msg)
    return redirect(url_for("agenda.agenda"))
