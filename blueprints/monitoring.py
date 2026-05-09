"""Blueprint for read-only and monitoring routes.

Six routes that don't change anything in the system:

  GET  /                       — homepage redirect to the agenda
  GET  /logs                   — admin Logboek page
  GET  /healthz                — JSON health probe (unauthed)
  GET  /now                    — public 'volgende bel' countdown page
  GET  /api/now                — JSON endpoint that /now refetches
  GET  /api/effectief-rooster  — what the daemon polls for the schedule

The four 'public' or machine-readable endpoints (/healthz, /now,
/api/now, /api/effectief-rooster) belong together because they each
serve clients that aren't a logged-in admin — a TV, a monitoring
agent, the daemon itself. Keeping them in one file makes it easy to
review what is exposed without authentication.
"""

import os
from datetime import date, datetime

from flask import Blueprint, jsonify, redirect, render_template, request, url_for

# Parent module: still owns the path constants, helpers (load_json,
# ensure_dirs, get_csrf_token, log helpers, get_daemon_heartbeat,
# next_bell_for_now), the auth object (HTTPBasicAuth) and the
# ui_login_required decorator. Later phases of issue #28 will pull
# these into core/, after which the wi.* indirection drops.
import webinterface as wi
from core.dates import (
    _next_local_midnight,
    effective_rooster_for_date,
    iso_week_key,
)
from core.rooster import (
    default_dagplanning_obj,
    default_roosters_obj,
    default_standaardweek_obj,
    default_weken_uit_obj,
)
from settings_store import Settings


# Blueprint name 'monitoring' becomes the prefix for url_for():
# url_for('monitoring.logs_page'), etc.
monitoring_bp = Blueprint("monitoring", __name__)


@monitoring_bp.route("/")
@wi.ui_login_required
def home():
    # Hitting the bare site goes to the agenda. The agenda lives
    # in webinterface.py for now (still on the main app, not a
    # blueprint), so we use its plain endpoint name.
    return redirect(url_for("agenda"))


@monitoring_bp.route("/logs", methods=["GET"])
@wi.ui_login_required
def logs_page():
    wi.ensure_dirs()
    upcoming = wi.compute_upcoming(20)
    evs = list(reversed(wi.read_events(200)))
    recent_bell = [e for e in evs if e.get("type") == "bell"][:20]
    recent_ui   = [e for e in evs if e.get("type") == "ui"][:20]
    return render_template(
        "logs.html",
        tab="logs",
        csrf_token=wi.get_csrf_token(),
        upcoming=upcoming,
        recent_bell=recent_bell,
        recent_ui=recent_ui,
    )


@monitoring_bp.route("/healthz", methods=["GET"])
def healthz():
    """Liveness/readiness probe.

    Returns 200 with a JSON status doc when the basic plumbing is
    OK, 503 when something is broken. Intentionally unauthenticated:
    monitoring agents (uptime checks, container probes, nagios-style
    probes) typically can't carry session cookies. The information
    leaked is minimal — error messages may include filesystem paths
    that are already implied by the project layout.

    Checks performed:
      - DATA_DIR exists and is writable (we briefly create + remove
        a probe file)
      - AUDIO_DIR exists and is readable
      - Settings can be loaded
      - Daemon heartbeat is fresh (delegates to get_daemon_heartbeat)

    'Degraded' (503) is returned on any failed check. The 'checks'
    map in the body always lists every check so a monitoring tool
    can show *which* part is unhealthy, not just that something is.
    """
    checks: dict = {}
    overall_ok = True

    # 1) Data dir writable. Touch + delete a probe file. Done before
    # ensure_dirs() so a permission-bug surface here rather than being
    # papered over by os.makedirs(exist_ok=True).
    try:
        os.makedirs(wi.DATA_DIR, exist_ok=True)
        probe = os.path.join(wi.DATA_DIR, ".healthz_probe")
        with open(probe, "w") as f:
            f.write("ok")
        os.remove(probe)
        checks["data_dir_writable"] = True
    except Exception as e:
        checks["data_dir_writable"] = False
        checks["data_dir_error"] = str(e)
        overall_ok = False

    # 2) Audio dir readable. Bell can't ring without it; the daemon
    # would log 'File not found' for every moment.
    try:
        os.listdir(wi.AUDIO_DIR)
        checks["audio_dir_readable"] = True
    except Exception as e:
        checks["audio_dir_readable"] = False
        checks["audio_dir_error"] = str(e)
        overall_ok = False

    # 3) Settings loadable. A corrupt config.json would crash routes
    # one by one; better to flag it here.
    try:
        Settings.load()
        checks["settings_loadable"] = True
    except Exception as e:
        checks["settings_loadable"] = False
        checks["settings_error"] = str(e)
        overall_ok = False

    # 4) Daemon heartbeat — the most operationally interesting signal.
    # If web is up but daemon is dead, no bells ring even though the
    # site looks healthy. Surface that loudly.
    hb = wi.get_daemon_heartbeat()
    checks["daemon_alive"] = hb["alive"]
    checks["daemon_last_poll_at"] = hb["last_poll_at"]
    checks["daemon_age_seconds"] = hb["age_seconds"]
    checks["daemon_threshold_seconds"] = hb["threshold_seconds"]
    if not hb["alive"]:
        overall_ok = False

    body = {
        "status": "ok" if overall_ok else "degraded",
        "checks": checks,
    }
    return jsonify(body), (200 if overall_ok else 503)


# --- /now: public read-only "next bell" page ---------------------------------
#
# Designed to live full-screen on a TV in the staff room. Deliberately
# unauthed so anyone in the building can glance at it; only static info
# is exposed (the next bell name + countdown), never the full schedule
# or any state-changing controls. The companion /api/now returns the
# same data as JSON so the page can refresh without a reload.

@monitoring_bp.route("/now", methods=["GET"])
def now_page():
    return render_template("now.html")


@monitoring_bp.route("/api/now", methods=["GET"])
def api_now():
    """JSON shape: see next_bell_for_now(). 'bell' is null when no
    upcoming bell today; the page treats that as 'geen bel meer
    vandaag'. Always 200 so the JS doesn't have to special-case
    network errors vs no-bell.
    """
    bell = wi.next_bell_for_now(datetime.now())
    return jsonify({
        "now": datetime.now().isoformat(timespec="seconds"),
        "bell": bell,
    }), 200


@monitoring_bp.route("/api/effectief-rooster", methods=["GET"])
@wi.auth.login_required
def api_effectief_rooster():
    """
    Return the effective schedule for a given day.
    Query params:
      - datum=YYYY-MM-DD (optional, default today)
      - empty_204=1       -> return 204 for empty schedule/week off
    """
    wi.ensure_dirs()

    datum_qs = (request.args.get("datum") or "").strip()
    empty_204 = (request.args.get("empty_204") == "1")

    if datum_qs:
        try:
            d = datetime.strptime(datum_qs, "%Y-%m-%d").date()
        except ValueError:
            return {"error": "Invalid date. Use YYYY-MM-DD."}, 400
    else:
        d = date.today()
    d_iso = d.isoformat()

    roosters = wi.load_json(wi.ROOSTERS_PATH, default_roosters_obj())
    dagplanning = wi.load_json(wi.DAGPLANNING_PATH, default_dagplanning_obj())
    standaardweek = wi.load_json(wi.STANDAARDWEEK_PATH, default_standaardweek_obj())
    weken_uit = wi.load_json(wi.WEEKDISABLE_PATH, default_weken_uit_obj())

    next_check_str = _next_local_midnight(datetime.now()).isoformat()
    headers = {"Cache-Control": "public, max-age=300",
               "X-Next-Check": next_check_str}

    wk_key = iso_week_key(d)
    if weken_uit.get(wk_key):
        if empty_204:
            return ("", 204, headers)
        return ({
            "datum": d_iso,
            "bron": "week-uit",
            "rooster_naam": "",
            "momenten": [],
            "next_check_suggested": next_check_str,
        }, 200, headers)

    # Route via the single helper. Without this, the API and the
    # in-process agenda render disagreed on the legacy "" case in
    # dagplanning (API silenced, agenda fell through). Going through
    # the helper makes both paths see the same answer.
    rooster_naam, bron = effective_rooster_for_date(d, dagplanning, standaardweek)

    momenten = []
    if rooster_naam and rooster_naam in roosters:
        momenten = roosters[rooster_naam]

    if not momenten:
        if empty_204:
            return ("", 204, headers)
        return ({
            "datum": d_iso,
            "bron": bron,
            "rooster_naam": rooster_naam,
            "momenten": [],
            "next_check_suggested": next_check_str,
        }, 200, headers)

    return ({
        "datum": d_iso,
        "bron": bron,
        "rooster_naam": rooster_naam,
        "momenten": momenten,
        "next_check_suggested": next_check_str,
    }, 200, headers)
