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
serve clients that aren't a logged-in admin: a TV, a monitoring
agent, the daemon itself. Keeping them in one file makes it easy to
review what is exposed without authentication.
"""

import os
from datetime import date, datetime

from flask import (
    Blueprint,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

# Parent module: still owns the path constants, helpers (load_json,
# ensure_dirs, get_csrf_token, log helpers, get_daemon_heartbeat,
# next_bell_for_now), the auth object (HTTPBasicAuth) and the
# ui_login_required decorator. Later phases of issue #28 will pull
# these into core/, after which the wi.* indirection drops.
import webinterface as wi
from core import users as users_mod
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


# Map each tab key to the Flask endpoint that renders the
# corresponding page. Used by the ``/`` redirect to send each user
# to the first page they're allowed to see, instead of unconditionally
# going to /agenda (which would 403 for a Geluiden-only user).
# ``gebruikers`` is None until step 4 introduces the management page;
# admins still land on agenda because TAB_ORDER lists it first.
TAB_ENDPOINTS = {
    "agenda": "agenda.agenda",
    "roosters": "roosters.roosters",
    "standaardweek": "roosters.standaardweek",
    "geluiden": "geluiden.geluiden",
    "logs": "monitoring.logs_page",
    "settings": "settings.settings_page",
    "gebruikers": "gebruikers.lijst",
}


# Blueprint name 'monitoring' becomes the prefix for url_for():
# url_for('monitoring.logs_page'), etc.
monitoring_bp = Blueprint("monitoring", __name__)


@monitoring_bp.route("/")
def home():
    """Bare-site entry: redirect each visitor to their first tab.

    Anonymous → /login. Logged-in admin → /agenda (TAB_ORDER first).
    Logged-in user with restricted tabs → their first accessible
    tab. Users with no accessible tabs (broken admin config) are
    routed to /logout so they end up somewhere sensible rather than
    on a permanent 403 loop.
    """
    if not wi.ui_logged_in():
        return redirect(url_for("auth.login"))
    tabs = session.get("tabs") or []
    first = users_mod.first_accessible_tab(tabs)
    endpoint = TAB_ENDPOINTS.get(first) if first else None
    if endpoint is None:
        # User can't see anything — better to log them out than to
        # leave them stuck. An admin can reassign tabs and try again.
        # Clear the session here (rather than bouncing via /logout):
        # /logout is POST-only, so a GET redirect to it would 405.
        session.clear()
        return redirect(url_for("auth.login"))
    return redirect(url_for(endpoint))


@monitoring_bp.route("/logs", methods=["GET"])
@wi.tab_required("logs")
def logs_page():
    """Show the Logboek page with recent events and upcoming bells.

    Reads the last 200 events from the log file and splits them into
    bell events (the daemon played a sound) and UI events (an admin
    changed something). Also shows the next 20 scheduled bell moments.
    """
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
    """Health probe: a URL a monitoring tool can poll to ask "are you OK?".

    Returns 200 with a JSON status doc when the basic plumbing is
    OK, 503 when something is broken. Intentionally unauthenticated:
    monitoring agents (automatic uptime checkers that hit this URL
    every minute or so) typically can't carry session cookies.

    Because anyone can call this URL, the response must not leak
    internal details. On a failed check we therefore only return the
    exception's *type* (e.g. "PermissionError") — enough for a
    monitoring dashboard to categorize the problem. The full message,
    which may contain filesystem paths, goes to the server log
    (journalctl) where the admin can read it.

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
    # ensure_dirs() so a permission-bug surfaces here rather than being
    # hidden by os.makedirs(exist_ok=True).
    try:
        os.makedirs(wi.DATA_DIR, exist_ok=True)
        probe = os.path.join(wi.DATA_DIR, ".healthz_probe")
        with open(probe, "w") as f:
            f.write("ok")
        os.remove(probe)
        checks["data_dir_writable"] = True
    except Exception as e:
        checks["data_dir_writable"] = False
        # Type only — full detail (which may contain paths) goes to
        # the server log, not to the anonymous caller.
        checks["data_dir_error"] = type(e).__name__
        print(f"[WARN] healthz: data dir check failed: {e}")
        overall_ok = False

    # 2) Audio dir readable. Bell can't ring without it; the daemon
    # would log 'File not found' for every moment.
    try:
        os.listdir(wi.AUDIO_DIR)
        checks["audio_dir_readable"] = True
    except Exception as e:
        checks["audio_dir_readable"] = False
        checks["audio_dir_error"] = type(e).__name__
        print(f"[WARN] healthz: audio dir check failed: {e}")
        overall_ok = False

    # 3) Settings loadable. A corrupt config.json would crash routes
    # one by one; better to flag it here.
    try:
        Settings.load()
        checks["settings_loadable"] = True
    except Exception as e:
        checks["settings_loadable"] = False
        checks["settings_error"] = type(e).__name__
        print(f"[WARN] healthz: settings check failed: {e}")
        overall_ok = False

    # 4) Daemon heartbeat: the most operationally interesting signal.
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
    """Show the public 'next bell' countdown page.

    This page is designed to be shown on a TV screen in the staff room.
    No login is required. The page uses JavaScript to poll /api/now
    every few seconds and update the countdown display.
    """
    return render_template("now.html")


@monitoring_bp.route("/api/now", methods=["GET"])
def api_now():
    """JSON shape: see next_bell_for_now(). 'bell' is null when no
    upcoming bell today; the page treats that as 'geen bel meer
    vandaag'. Always 200 so the JS doesn't have to handle
    network errors and no-bell as separate cases.
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
    # 'private', not 'public': this endpoint requires a login (Basic
    # Auth). A 'shared cache' is a machine between client and server
    # (e.g. a school proxy) that stores responses and replays them to
    # later visitors to save traffic. The HTTP rules (RFC 9111) say
    # such caches may store a logged-in response when it's marked
    # 'public' — meaning the proxy could hand our schedule to clients
    # that never logged in at all. 'private' keeps the 5-minute
    # caching for the client itself and forbids shared caches.
    headers = {"Cache-Control": "private, max-age=300",
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
