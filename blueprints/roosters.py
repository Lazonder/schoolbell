"""Blueprint for the Roosters and Standaardweek pages.

A "rooster" is a named list of belmomenten (a sounds + times list)
that gets activated for a particular weekday by the standaardweek,
or for a single date by the agenda. This file holds:

  GET   /roosters                              — overview page
  POST  /roosters/add                          — create a new rooster
  POST  /roosters/<r>/delete                   — delete a rooster
  POST  /roosters/<r>/add-moment               — add a moment row
  POST  /roosters/<r>/edit-moment/<index>      — replace a moment row
  POST  /roosters/<r>/delete-moment/<index>    — remove a moment row
  GET   /standaardweek                         — assign a rooster per
                                                  weekday
  POST  /standaardweek                         — save those choices

The standaardweek lives in a separate file but is logically part
of "rooster management". The user moves between the two pages
constantly while setting up a school week. Keeping them in one
blueprint reflects that workflow.
"""

import os

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_babel import gettext as _

import webinterface as wi
from core.audio_files import safe_audio_path
from core.rooster import (
    NAME_RE,
    default_dagplanning_obj,
    default_roosters_obj,
    default_standaardweek_obj,
    normalize_and_sort_moments,
    normalize_time,
)
from core.dates import WEEKDAYS


roosters_bp = Blueprint("roosters", __name__)


def _bestand_bestaat(name: str) -> bool:
    """True when ``name`` is a safe audio filename that exists in AUDIO_DIR.

    The upload/play/delete handlers already validate filenames via
    safe_audio_path, but the moment forms accepted any string for
    ``bestand``/``warn_bestand``. The dropdown in the UI only offers
    real files, so a mismatch here means either a hand-crafted POST
    or a file that was deleted between page render and submit. The
    hand-crafted case matters for safety: the daemon glues this
    value onto the audio folder's path, and a name like ``../../x``
    (path traversal: each ``..`` means "one folder up") would point
    outside that folder. Both cases should be rejected with a clear
    message instead of saved as a bell that can never ring.
    """
    p = safe_audio_path(name, wi.AUDIO_DIR)
    return p is not None and os.path.isfile(p)


# -- Roosters --
@roosters_bp.route("/roosters", methods=["GET"])
@wi.tab_required("roosters")
def roosters():
    """Show the roosters overview page.

    Lists all existing roosters with their bell moments. If the URL
    contains ``?edit_r=`` and ``?edit_i=``, the matching moment row
    is shown as an editable form instead of read-only text.
    """
    wi.ensure_dirs()
    roosters = wi.load_json(wi.ROOSTERS_PATH, default_roosters_obj())
    geluiden = wi.list_audio()

    # Inline-edit mode: the ✎ button on a row links back here with
    # ?edit_r=<naam>&edit_i=<index>. We pass these straight to the
    # template, which then renders that one row as a form instead of
    # the usual read-only cells. We don't validate the values here;
    # the template just compares against the loop variables, and a
    # bogus combination simply means no row matches → no edit form.
    edit_r = request.args.get("edit_r") or ""
    try:
        edit_i = int(request.args.get("edit_i", ""))
    except (TypeError, ValueError):
        edit_i = -1

    return render_template(
        "roosters.html",
        tab="roosters",
        csrf_token=wi.get_csrf_token(),
        roosters=roosters,
        geluiden=geluiden,
        edit_r=edit_r,
        edit_i=edit_i,
    )


@roosters_bp.route("/roosters/add", methods=["POST"])
@wi.tab_required("roosters")
def add_rooster():
    """Create a new rooster with the given name.

    Validates the name against the allowed character set. If the
    checkbox 'kopieer van eerste' is checked, the new rooster starts
    with a copy of the first existing rooster's moments instead of
    an empty list.
    """
    wi.ensure_dirs()
    naam = (request.form.get("naam") or "").strip()
    if not naam:
        flash(_("Naam van rooster is verplicht."))
        return redirect(url_for("roosters.roosters"))
    # Validate against NAME_RE so the rooster name can be used safely
    # everywhere it ends up (dropdown values, JSON keys, log lines).
    # Without this, a user could create '!off' which collides with
    # the silence marker in the agenda dropdown. The rooster would
    # appear as an option but selecting it would be misinterpreted as
    # an explicit silence override. The regex also blocks weirdness
    # like '../', '<script>', newlines, etc.
    if not NAME_RE.match(naam):
        flash(_("Ongeldige naam. Gebruik 1–35 tekens: letters, cijfers, spatie, _ of -."))
        return redirect(url_for("roosters.roosters"))

    with wi.locked_json(wi.ROOSTERS_PATH, default_roosters_obj()) as (roosters, save):
        if naam in roosters:
            flash(_("Er bestaat al een rooster met deze naam."))
            return redirect(url_for("roosters.roosters"))

        kopieer = "kopieer_van_eerste" in request.form
        if kopieer and roosters:
            first_name = next(iter(roosters.keys()))
            roosters[naam] = normalize_and_sort_moments(roosters[first_name])
        else:
            roosters[naam] = []

        save(roosters)

    wi.log_event("ui", {"action": "add_rooster", "rooster": naam})
    flash(_("Rooster '%(naam)s' aangemaakt.", naam=naam))
    return redirect(url_for("roosters.roosters"))


@roosters_bp.route("/roosters/<rooster>/delete", methods=["POST"])
@wi.tab_required("roosters")
def delete_rooster(rooster):
    """Delete a rooster, but only if nothing else still uses it.

    Before deleting, checks whether the rooster appears in the
    standaardweek or in any day override in the agenda. If it does,
    the deletion is blocked and the admin sees a message explaining
    which places still reference it.
    """
    wi.ensure_dirs()
    with wi.locked_json(wi.ROOSTERS_PATH, default_roosters_obj()) as (roosters, save):
        if rooster not in roosters:
            flash(_("Onbekend rooster."))
            return redirect(url_for("roosters.roosters"))

        # Before deleting: check if the rooster is still used somewhere.
        # Without this check, references in standaardweek.json and
        # dagplanning.json would point to a deleted rooster. In the UI
        # you'd still see the name, but no bell would ring (silent bug).
        # We deliberately choose block-and-warn instead of cascading
        # delete: you don't want a click in the Roosters screen to
        # silently remove days from the agenda. The user must first
        # manually remove those references in Standaardweek and Agenda,
        # then retry. These two reads don't need their own lock:
        # save_json is atomic, so a reader sees either the old or the
        # new file, never partial. The check is best-effort against
        # very recent writes, not a guarantee.
        stdweek = wi.load_json(wi.STANDAARDWEEK_PATH, default_standaardweek_obj())
        dagplanning = wi.load_json(wi.DAGPLANNING_PATH, default_dagplanning_obj())

        gebruikt_in_stdweek = [dag for dag, r in stdweek.items() if r == rooster]
        gebruikt_in_dagplanning = sorted(d for d, r in dagplanning.items() if r == rooster)

        if gebruikt_in_stdweek or gebruikt_in_dagplanning:
            delen = []
            if gebruikt_in_stdweek:
                delen.append(_("Standaardweek (%(dagen)s)", dagen=", ".join(gebruikt_in_stdweek)))
            if gebruikt_in_dagplanning:
                voorb = ", ".join(gebruikt_in_dagplanning[:3])
                meer = "" if len(gebruikt_in_dagplanning) <= 3 else _(" en %(n)d meer", n=len(gebruikt_in_dagplanning) - 3)
                delen.append(_("Agenda (%(voorb)s%(meer)s)", voorb=voorb, meer=meer))
            flash(_(
                "Rooster '%(rooster)s' is nog in gebruik bij: %(delen)s. "
                "Haal deze verwijzingen eerst weg voordat je het rooster verwijdert.",
                rooster=rooster, delen="; ".join(delen),
            ))
            return redirect(url_for("roosters.roosters"))

        del roosters[rooster]
        save(roosters)

    wi.log_event("ui", {"action": "delete_rooster", "rooster": rooster})
    flash(_("Rooster '%(rooster)s' verwijderd.", rooster=rooster))
    return redirect(url_for("roosters.roosters"))


@roosters_bp.route("/roosters/<rooster>/add-moment", methods=["POST"])
@wi.tab_required("roosters")
def add_moment(rooster):
    """Add a new bell moment to a rooster.

    Reads the time, name, and audio file from the form. Optionally
    also reads a warning bell (a sound that plays N minutes before
    the main bell). Validates all fields before saving.
    """
    wi.ensure_dirs()

    # Validate form input outside the lock. No need to block other
    # writers while we check for empty fields.
    tijd_raw = request.form.get("tijd", "")
    tijd = normalize_time(tijd_raw)
    naam = (request.form.get("naam") or "").strip()
    bestand = (request.form.get("bestand") or "").strip()

    if not tijd:
        flash(_("Tijd moet in formaat UU:MM (bijv. 8:05 of 08:05)."))
        return redirect(url_for("roosters.roosters"))
    if not naam:
        flash(_("Naam is verplicht."))
        return redirect(url_for("roosters.roosters"))
    if not bestand:
        flash(_("Kies een geluidsbestand."))
        return redirect(url_for("roosters.roosters"))
    if not _bestand_bestaat(bestand):
        flash(_("Geluidsbestand '%(bestand)s' bestaat niet.", bestand=bestand))
        return redirect(url_for("roosters.roosters"))

    # Optional warning fields. Empty/0 -> no warning. The form sends
    # both, the user just leaves them at defaults if they don't want
    # a warning. We validate ranges here so the user gets a flash
    # message. normalize_and_sort_moments() also defends downstream.
    warn_min_raw = (request.form.get("warn_min") or "").strip()
    warn_bestand = (request.form.get("warn_bestand") or "").strip()
    warn_min: int = 0
    if warn_min_raw:
        try:
            warn_min = int(warn_min_raw)
        except ValueError:
            flash(_("Waarschuwing: minuten moeten een getal zijn."))
            return redirect(url_for("roosters.roosters"))
        if not (0 <= warn_min <= 60):
            flash(_("Waarschuwing: minuten moeten tussen 0 en 60 liggen."))
            return redirect(url_for("roosters.roosters"))
    if warn_min > 0 and not warn_bestand:
        flash(_("Kies een geluid voor de waarschuwingsbel, of zet 'minuten eerder' op 0."))
        return redirect(url_for("roosters.roosters"))
    if warn_min > 0 and not _bestand_bestaat(warn_bestand):
        flash(_("Geluidsbestand '%(bestand)s' bestaat niet.", bestand=warn_bestand))
        return redirect(url_for("roosters.roosters"))

    new_moment = {"tijd": tijd, "naam": naam, "bestand": bestand}
    if warn_min > 0 and warn_bestand:
        new_moment["warn_min"] = warn_min
        new_moment["warn_bestand"] = warn_bestand

    with wi.locked_json(wi.ROOSTERS_PATH, default_roosters_obj()) as (roosters, save):
        if rooster not in roosters:
            flash(_("Onbekend rooster."))
            return redirect(url_for("roosters.roosters"))

        roosters[rooster].append(new_moment)
        roosters[rooster] = normalize_and_sort_moments(roosters[rooster])
        save(roosters)

    wi.log_event(
        "ui",
        {
            "action": "add_moment",
            "rooster": rooster,
            "tijd": tijd,
            "naam": naam,
            "bestand": bestand,
            "warn_min": warn_min if warn_min > 0 else None,
            "warn_bestand": warn_bestand if warn_min > 0 else None,
        },
    )
    flash(_("Moment toegevoegd aan '%(rooster)s'.", rooster=rooster))
    return redirect(url_for("roosters.roosters"))


@roosters_bp.route("/roosters/<rooster>/edit-moment/<int:index>", methods=["POST"])
@wi.tab_required("roosters")
def edit_moment(rooster, index):
    """Replace one moment in <rooster> by the values from the form.

    The form fields are the same as in ``add_moment``: ``tijd``, ``naam``,
    ``bestand`` and the optional ``warn_min`` / ``warn_bestand`` pair.
    Validation is identical too — anything caught here flashes a message
    and bounces back to the rooster page (re-opened in edit mode so the
    user keeps their context). After saving we re-normalize and re-sort,
    so a changed ``tijd`` may shuffle the moment to a different index.
    """
    wi.ensure_dirs()

    # Same validation as add_moment. Done outside the lock so other
    # writers aren't blocked while we sanity-check the form.
    tijd_raw = request.form.get("tijd", "")
    tijd = normalize_time(tijd_raw)
    naam = (request.form.get("naam") or "").strip()
    bestand = (request.form.get("bestand") or "").strip()

    # On any validation error we redirect back into edit mode for this
    # row, so the user doesn't lose what they were doing.
    edit_redirect = redirect(
        url_for("roosters.roosters", edit_r=rooster, edit_i=index) + "#edit"
    )

    if not tijd:
        flash(_("Tijd moet in formaat UU:MM (bijv. 8:05 of 08:05)."))
        return edit_redirect
    if not naam:
        flash(_("Naam is verplicht."))
        return edit_redirect
    if not bestand:
        flash(_("Kies een geluidsbestand."))
        return edit_redirect
    if not _bestand_bestaat(bestand):
        flash(_("Geluidsbestand '%(bestand)s' bestaat niet.", bestand=bestand))
        return edit_redirect

    warn_min_raw = (request.form.get("warn_min") or "").strip()
    warn_bestand = (request.form.get("warn_bestand") or "").strip()
    warn_min: int = 0
    if warn_min_raw:
        try:
            warn_min = int(warn_min_raw)
        except ValueError:
            flash(_("Waarschuwing: minuten moeten een getal zijn."))
            return edit_redirect
        if not (0 <= warn_min <= 60):
            flash(_("Waarschuwing: minuten moeten tussen 0 en 60 liggen."))
            return edit_redirect
    if warn_min > 0 and not warn_bestand:
        flash(_("Kies een geluid voor de waarschuwingsbel, of zet 'minuten eerder' op 0."))
        return edit_redirect
    if warn_min > 0 and not _bestand_bestaat(warn_bestand):
        flash(_("Geluidsbestand '%(bestand)s' bestaat niet.", bestand=warn_bestand))
        return edit_redirect

    new_moment = {"tijd": tijd, "naam": naam, "bestand": bestand}
    if warn_min > 0 and warn_bestand:
        new_moment["warn_min"] = warn_min
        new_moment["warn_bestand"] = warn_bestand

    with wi.locked_json(wi.ROOSTERS_PATH, default_roosters_obj()) as (roosters, save):
        if rooster not in roosters:
            flash(_("Onbekend rooster."))
            return redirect(url_for("roosters.roosters"))
        moments = roosters[rooster]
        if not (0 <= index < len(moments)):
            flash(_("Onbekende regel."))
            return redirect(url_for("roosters.roosters"))

        old_moment = moments[index]
        moments[index] = new_moment
        roosters[rooster] = normalize_and_sort_moments(moments)
        save(roosters)

    wi.log_event(
        "ui",
        {
            "action": "edit_moment",
            "rooster": rooster,
            "oud_tijd": old_moment.get("tijd", ""),
            "oud_naam": old_moment.get("naam", ""),
            "tijd": tijd,
            "naam": naam,
            "bestand": bestand,
            "warn_min": warn_min if warn_min > 0 else None,
            "warn_bestand": warn_bestand if warn_min > 0 else None,
        },
    )
    flash(_("Moment '%(naam)s' bijgewerkt in '%(rooster)s'.", naam=naam, rooster=rooster))
    return redirect(url_for("roosters.roosters"))


@roosters_bp.route("/roosters/<rooster>/delete-moment/<int:index>", methods=["POST"])
@wi.tab_required("roosters")
def delete_moment(rooster, index):
    """Remove one bell moment from a rooster.

    ``index`` is the position of the moment in the list (0 = first).
    If the index is out of range, a warning is shown and nothing is deleted.
    """
    wi.ensure_dirs()
    with wi.locked_json(wi.ROOSTERS_PATH, default_roosters_obj()) as (roosters, save):
        if rooster not in roosters:
            flash(_("Onbekend rooster."))
            return redirect(url_for("roosters.roosters"))
        moments = roosters[rooster]
        if 0 <= index < len(moments):
            removed = moments.pop(index)
            roosters[rooster] = normalize_and_sort_moments(moments)
            save(roosters)
            wi.log_event("ui", {"action": "delete_moment", "rooster": rooster, "tijd": removed.get("tijd",""), "naam": removed.get("naam","")})
            flash(_("Moment '%(naam)s' verwijderd uit '%(rooster)s'.", naam=removed.get("naam",""), rooster=rooster))
        else:
            flash(_("Onbekende regel."))
    return redirect(url_for("roosters.roosters"))


# -- Standaardweek --
@roosters_bp.route("/standaardweek", methods=["GET", "POST"])
@wi.tab_required("standaardweek")
def standaardweek():
    """Show or save the standard week settings.

    On GET: render the page where the admin can choose which rooster
    plays on each weekday (Monday through Sunday).
    On POST: save the chosen roosters for each day, then redirect back.
    """
    wi.ensure_dirs()

    if request.method == "POST":
        # Read roosters without a lock: best-effort validation against
        # currently known roosters. The lock on standaardweek is what
        # protects us from concurrent saves of the standard week itself.
        roosters = wi.load_json(wi.ROOSTERS_PATH, default_roosters_obj())
        with wi.locked_json(wi.STANDAARDWEEK_PATH, default_standaardweek_obj()) as (std, save):
            for key, label in WEEKDAYS:
                keuze = (request.form.get(f"rooster_{key}") or "").strip()
                if keuze and keuze not in roosters:
                    flash(_("'%(keuze)s' bestaat niet als rooster; overslaan voor %(dag)s.", keuze=keuze, dag=label))
                    continue
                std[key] = keuze
            save(std)
            wi.log_event("ui", {"action": "save_standaardweek", "keuzes": std})
        flash(_("Standaardweek opgeslagen."))
        return redirect(url_for("roosters.standaardweek"))

    roosters = wi.load_json(wi.ROOSTERS_PATH, default_roosters_obj())
    std = wi.load_json(wi.STANDAARDWEEK_PATH, default_standaardweek_obj())
    opties = list(roosters.keys())

    return render_template(
        "standaardweek.html",
        tab="standaardweek",
        csrf_token=wi.get_csrf_token(),
        weekdagen=WEEKDAYS,
        huidige=std,
        opties=opties,
    )
