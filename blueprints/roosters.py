"""Blueprint for the Roosters and Standaardweek pages.

A "rooster" is a named list of belmomenten (a sounds + times list)
that gets activated for a particular weekday by the standaardweek,
or for a single date by the agenda. This file holds:

  GET   /roosters                              — overview page
  POST  /roosters/add                          — create a new rooster
  POST  /roosters/<r>/delete                   — delete a rooster
  POST  /roosters/<r>/add-moment               — add a moment row
  POST  /roosters/<r>/delete-moment/<index>    — remove a moment row
  GET   /standaardweek                         — assign a rooster per
                                                  weekday
  POST  /standaardweek                         — save those choices

The standaardweek lives in a separate file but is logically part
of "rooster management". The user moves between the two pages
constantly while setting up a school week. Keeping them in one
blueprint reflects that workflow.
"""

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_babel import gettext as _

import webinterface as wi
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


# -- Roosters --
@roosters_bp.route("/roosters", methods=["GET"])
@wi.ui_login_required
def roosters():
    wi.ensure_dirs()
    roosters = wi.load_json(wi.ROOSTERS_PATH, default_roosters_obj())
    geluiden = wi.list_audio()
    return render_template(
        "roosters.html",
        tab="roosters",
        csrf_token=wi.get_csrf_token(),
        roosters=roosters,
        geluiden=geluiden,
    )


@roosters_bp.route("/roosters/add", methods=["POST"])
@wi.ui_login_required
def add_rooster():
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
@wi.ui_login_required
def delete_rooster(rooster):
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
@wi.ui_login_required
def add_moment(rooster):
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


@roosters_bp.route("/roosters/<rooster>/delete-moment/<int:index>", methods=["POST"])
@wi.ui_login_required
def delete_moment(rooster, index):
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
@wi.ui_login_required
def standaardweek():
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
