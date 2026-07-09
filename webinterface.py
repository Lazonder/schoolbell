#!/usr/bin/env python3
import hmac
import ipaddress
import os, json, fcntl, secrets, sys
from contextlib import contextmanager
from datetime import date, timedelta, datetime, time, timezone
from flask import Flask, request, redirect, url_for, flash, session
from settings_store import Settings
from werkzeug.middleware.proxy_fix import ProxyFix

# Pure helpers live in the core/ package so this file stays smaller
# and they can be tested without importing Flask. The names below are
# re-exported (the import binds them as module-level attributes), so
# existing test code that does ``from webinterface import iso_week_key``
# keeps working.
from core.util import _env_bool  # noqa: F401  (re-export for tests)
from core.dates import (  # noqa: F401  (re-exports)
    WEEKDAYS,
    _next_local_midnight,
    effective_rooster_for_date,
    effectieve_rooster_naam_for_date,
    iso_week_key,
    iso_weeks_with_weekday_in_range,
    prune_past_dates,
    weekday_key,
)
from core.rooster import (  # noqa: F401  (re-exports)
    DAGPLANNING_SILENT_FORM_VALUE,
    NAME_RE,
    TIME_RE,
    default_dagplanning_obj,
    default_roosters_obj,
    default_standaardweek_obj,
    default_weken_uit_obj,
    normalize_and_sort_moments,
    normalize_time,
)
from core.audio_files import (  # noqa: F401  (re-exports)
    _play_via_pygame,
    _validate_audio_file,
    safe_audio_filename,
)
from core.auth import (  # noqa: F401  (re-exports for blueprints + tests)
    ADMIN_HASH,
    ADMIN_USER,
    FALLBACK_PLAIN,
    _check_password,
    admin_page_required,
    auth,
    get_csrf_token,
    require_admin,
    tab_required,
    ui_logged_in,
    ui_login_required,
    verify_password,
)

# NAME_RE, TIME_RE, DAGPLANNING_SILENT_FORM_VALUE — see core/rooster.py

# Dutch school vacation regions. The country is split into three by
# the Ministry of Education for staggered school holidays. Used as
# the keys in vakanties.json's 'regios' object and as the only valid
# values for Settings.vakantieregio.
VAKANTIE_REGIOS = ("Noord", "Midden", "Zuid")

# === Path configuration ===
# Priority: SCHOOLBELL_BASE_DIR env var (useful for tests or non-standard
# installations). Fallback: the directory containing this file itself.
# Previously hardcoded "/home/pi/schoolbell" — that broke when installing as
# a user other than `pi`.
BASE_DIR = os.environ.get("SCHOOLBELL_BASE_DIR") or os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
AUDIO_DIR = os.path.join(BASE_DIR, "static", "geluiden")
WEEKDISABLE_PATH = os.path.join(DATA_DIR, "weken_uit.json")
ROOSTERS_PATH = os.path.join(DATA_DIR, "roosters.json")
DAGPLANNING_PATH = os.path.join(DATA_DIR, "dagplanning.json")
STANDAARDWEEK_PATH = os.path.join(DATA_DIR, "standaardweek.json")
EVENTS_LOG_PATH = os.path.join(DATA_DIR, "events.jsonl")  # shared log (UI + daemon)
# Optional file maintained by the admin: school holiday periods used by
# the 'Vakanties importeren' button on the agenda page. Format: see
# vakanties.example.json in the repo root. Missing file is fine — the
# button just flashes a hint when it doesn't exist.
VAKANTIES_PATH = os.path.join(DATA_DIR, "vakanties.json")
# Daemon heartbeat: a tiny file the daemon rewrites on every poll
# iteration. The header reads it to render a green/red dot. See
# schoolbelldaemon._write_heartbeat for the writer side.
DAEMON_HEARTBEAT_PATH = os.path.join(DATA_DIR, "daemon_heartbeat.json")
# Stop flag: touched by the Stop button on the geluiden page. Both the
# web workers (test playback) and the daemon (scheduled bells) watch
# this file's mtime and stop their own mixer when it's newer than
# their playback start. See core/audio_files.py for the mechanism.
STOP_FLAG_PATH = os.path.join(DATA_DIR, "stop_playback")

# === Flask ===
app = Flask(__name__)
# TEMPLATES_AUTO_RELOAD stats each template file on every render. Useful
# during dev; unnecessary I/O in production behind Gunicorn. Enable via
# SCHOOLBELL_DEBUG=1 when working locally.
_debug_mode = os.environ.get("SCHOOLBELL_DEBUG", "0").strip().lower() in ("1", "true", "yes", "on")
app.config["TEMPLATES_AUTO_RELOAD"] = _debug_mode
app.jinja_env.auto_reload = _debug_mode
def _load_or_create_secret_key() -> str:
    """Return the Flask session secret, never a hardcoded default.

    Priority:
      1. SCHOOLBELL_SECRET env var (set by install.sh in web.env).
      2. A persistent random key in data/secret_key, generated on
         first use with 0600 permissions.

    Why not a hardcoded fallback: the secret is the key Flask uses
    to put a tamper-proof signature on the session cookie (the
    little file in your browser that proves you're logged in). If
    the key is a known, public value like "dev-secret", anyone can
    craft their own cookie that says user=admin, sign it with that
    key, and skip the login page entirely. A manual deployment that
    forgets the env var must not silently run in that state.

    Why a *file* instead of a random value per process: Gunicorn
    runs multiple workers. If each generated its own in-memory key,
    a login handled by worker A would be an invalid cookie for
    worker B, and users would be bounced to /login at random. The
    file makes all workers (and restarts) agree. O_EXCL (the
    "create only if it doesn't exist yet" flag) handles the case
    where two workers try to create the file at the same moment:
    exactly one succeeds, and the loser reads back what the winner
    wrote.
    """
    env = os.environ.get("SCHOOLBELL_SECRET", "").strip()
    if env:
        return env

    path = os.path.join(DATA_DIR, "secret_key")
    try:
        with open(path, "r", encoding="utf-8") as f:
            key = f.read().strip()
        if key:
            return key
    except OSError:
        pass

    key = secrets.token_hex(32)
    os.makedirs(DATA_DIR, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(key)
    except FileExistsError:
        # Another worker won the race — use its key.
        with open(path, "r", encoding="utf-8") as f:
            key = f.read().strip() or key
    print(
        "[WARN] SCHOOLBELL_SECRET not set; using generated key in "
        f"{path}. Set the env var in /etc/schoolbell/web.env for "
        "production installs.",
        file=sys.stderr,
    )
    return key


app.secret_key = _load_or_create_secret_key()
# Nginx sits in front of this app and forwards each request. Without
# help, Flask would then think every visitor is 127.0.0.1 (nginx's own
# address). ProxyFix reads the X-Forwarded-* headers nginx adds, so
# request.remote_addr is the real visitor's IP and links use https.
# The '1's mean: trust exactly one proxy in front of us — no more.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)
# Hard limit on upload size — Flask/Werkzeug rejects larger with 413
# before the handler runs (prevents malicious traffic from straining a Pi with
# limited RAM/disk). The soft limit from Settings (max_file_size_mb) is then
# enforced in the handler for clean error messages; it must therefore be
# <= this number.
app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024  # 100 MiB
app.permanent_session_lifetime = timedelta(minutes=30)  # 30 minutes

# _env_bool — see core/util.py

# SESSION_COOKIE_SECURE must be True for HTTPS (browser only sends cookie back
# over TLS). For HTTP deployment (default install.sh setup, Nginx on port 80)
# this must be False, or login won't work. Default in code: True (secure).
# install.sh explicitly sets =0 in web.env.
app.config.update(
    SESSION_COOKIE_SECURE=_env_bool("SCHOOLBELL_SECURE_COOKIES", default=True),
    SESSION_COOKIE_SAMESITE="Lax",
)

# === Flask-Babel: language plumbing ===
# Babel itself does the per-request lookup of translated strings.
# select_locale() (in core/i18n.py) decides which language to use:
# Settings.taal first, then the browser's Accept-Language header
# when the user picked "auto". Phase 1 of issue #29 only wires this
# up — no strings are marked for translation yet, so every page
# still renders in Dutch regardless of the chosen locale.
from flask_babel import Babel, gettext as _  # noqa: E402

from core.i18n import DEFAULT_LOCALE, SUPPORTED_LOCALES, select_locale  # noqa: E402, F401

app.config["BABEL_DEFAULT_LOCALE"] = DEFAULT_LOCALE
# Dutch installs use Europe/Amsterdam; localized date formatting
# falls back to system time when this is unset, which is fine on
# the Pi but inconsistent across machines.
app.config["BABEL_DEFAULT_TIMEZONE"] = "Europe/Amsterdam"

babel = Babel(app, locale_selector=select_locale)


def get_daemon_heartbeat() -> dict:
    """Read the daemon's heartbeat file and decide if it's still alive.

    Returns a dict the templates and /healthz can use directly:
        alive:          bool — heartbeat file exists and is fresh
        last_poll_at:   ISO string from the file, or "" if missing
        age_seconds:    int seconds since the heartbeat (None on error)
        threshold_seconds: int — the freshness window we used

    The freshness threshold scales with poll_interval_sec so we don't
    give a false alarm on installs with an unusually long polling
    interval. We also enforce a 10-second minimum because at the
    default 2s poll interval, 3x = 6s is too tight. A single garbage
    collector pause (when Python pauses to clean up memory) or disk
    hiccup would flip the indicator to 'down' for one render. 10s
    gives a little slack without making a real outage invisible.
    """
    try:
        with open(DAEMON_HEARTBEAT_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        last_iso = data.get("last_poll_at", "")
        last_dt = datetime.fromisoformat(last_iso)
    except (FileNotFoundError, json.JSONDecodeError, ValueError, KeyError, TypeError, OSError):
        return {
            "alive": False,
            "last_poll_at": "",
            "age_seconds": None,
            "threshold_seconds": 0,
        }

    # Normalize to UTC-aware so the subtraction always works regardless
    # of how the daemon serialized its tz. The daemon currently writes
    # UTC ISO strings, so this is extra protection just in case.
    if last_dt.tzinfo is None:
        last_dt = last_dt.replace(tzinfo=timezone.utc)

    now = datetime.now(timezone.utc)
    age = int((now - last_dt).total_seconds())
    poll_interval = 2
    try:
        poll_interval = int(Settings.load().poll_interval_sec)
    except Exception:
        pass
    threshold = max(10, poll_interval * 3)
    return {
        "alive": age <= threshold,
        "last_poll_at": last_iso,
        "age_seconds": age,
        "threshold_seconds": threshold,
    }

# Make `now()` available in all templates. Used e.g. in base.html for the
# footer year. Without this processor, `{{ now().year }}` would give an
# UndefinedError; previously there was a permanent-falsy dummy.
#
# theme_mode is also injected here so base.html can include it server-side
# (avoids flash-of-wrong-theme and works on /login, where /api/settings
# would return 401 because the user isn't logged in yet).
#
# daemon_heartbeat is injected too so the header indicator is available
# everywhere without each route having to remember to pass it.
@app.context_processor
def _inject_template_globals():
    """Add useful variables to every HTML template automatically.

    Flask calls this function before rendering any page. Whatever we
    return here becomes available in every template without the route
    handler having to pass it manually. For example, ``{{ now().year }}``
    in a template works because ``now`` is added here.
    """
    # Expose flask_babel.get_locale to templates so <html lang="..."> can
    # render the active locale code. Flask-Babel auto-exposes gettext,
    # ngettext, and the _() alias, but not get_locale.
    from flask_babel import get_locale as _get_locale
    try:
        s = Settings.load()
        mode = s.theme_mode
        huisstijl = s.huisstijl
        custom_bg = s.theme_custom_bg
        custom_table = s.theme_custom_table
        custom_nav = s.theme_custom_nav
    except Exception:
        # Be tolerant on /login where the settings file might be
        # missing or unreadable: fall back to "light + standaard"
        # so the page still renders.
        mode = "light"
        huisstijl = "standaard"
        custom_bg = custom_table = custom_nav = ""
    if mode not in ("light", "dark", "auto"):
        mode = "light"
    if huisstijl not in ("standaard", "aangepast"):
        huisstijl = "standaard"
    # Tab-access predicate used by base.html to render only the
    # nav-links the current user is allowed to follow. Closes over
    # the session, so it must be defined per-request. Returns True
    # for admins (whose tabs list is ["*"]) and for any tab the user
    # explicitly has. Anonymous visitors (no session yet, e.g. on
    # /login) get False for everything — the navigation isn't shown
    # there anyway.
    _user_tabs = session.get("tabs") or []

    def _mag_tab(naam: str) -> bool:
        return "*" in _user_tabs or naam in _user_tabs

    return {
        "now": datetime.now,
        "get_locale": _get_locale,
        "theme_mode": mode,
        "huisstijl": huisstijl,
        "theme_custom_bg": custom_bg,
        "theme_custom_table": custom_table,
        "theme_custom_nav": custom_nav,
        "daemon_heartbeat": get_daemon_heartbeat(),
        "mag_tab": _mag_tab,
    }

# TIME_RE — see core/rooster.py
# WEEKDAYS — see core/dates.py

# Auth helpers and the /login + /logout routes moved to:
#   core/auth.py        — _check_password, ui_logged_in,
#                         ui_login_required, get_csrf_token,
#                         require_admin, verify_password,
#                         the HTTPBasicAuth instance
#   blueprints/auth.py  — /login, /logout
#
# csrf_protect and the 413 errorhandler stay below — they hook into
# the Flask app object via @app.before_request / @app.errorhandler,
# which is the kind of registration that has to happen in the same
# module that creates the app.


def _client_is_loopback() -> bool:
    """True when the current request comes from this machine itself.

    Two addresses are checked, and both must be loopback (127.x / ::1):

    1. ``request.remote_addr`` — after ProxyFix this is the client IP
       taken from the X-Forwarded-For header that nginx adds.
    2. The *direct* TCP peer, which ProxyFix stashes in the WSGI
       environ before overwriting it. Checking this too closes a
       spoofing hole: when gunicorn is reachable on the network
       directly (no nginx in front, e.g. a laptop install without a
       proxy), a LAN client could send a forged
       ``X-Forwarded-For: 127.0.0.1`` header and ProxyFix would
       happily report loopback. The real peer address gives them away.

    Unparseable or missing addresses count as "not loopback": when in
    doubt, treat the client as remote. That can never lock out the
    daemon or a browser on the machine itself — for a genuine local
    connection both values are a plain 127.0.0.1/::1.
    """
    candidates = [request.remote_addr]
    # Modern werkzeug: dict with the original environ values.
    # Older werkzeug used a flat key; check both, use whatever exists.
    orig = request.environ.get("werkzeug.proxy_fix.orig") or {}
    orig_peer = (
        orig.get("REMOTE_ADDR")
        or request.environ.get("werkzeug.proxy_fix.orig_remote_addr")
    )
    if orig_peer:
        candidates.append(orig_peer)
    for addr in candidates:
        if not addr:
            return False
        try:
            if not ipaddress.ip_address(addr).is_loopback:
                return False
        except ValueError:
            return False
    return True


@app.before_request
def lan_toegang_filter():
    """Refuse clients elsewhere on the network when LAN access is off.

    Runtime companion to the Voorkeuren setting ``lan_toegang``. When
    that setting is False, every request from a non-loopback address
    gets a 403 before any route (or even the login page) runs. The
    daemon is never affected: it polls via 127.0.0.1.

    Registered before csrf_protect on purpose — a before_request
    handler that returns a response stops the chain, so a refused
    client triggers no CSRF checks, no user bootstrap, nothing.

    This is an application-level door policy, not a firewall: the
    TCP port stays open and nginx/gunicorn still accept the
    connection; the app then refuses to serve it. Truly not
    listening on the LAN requires changing the bind address at
    install time (GUNICORN_BIND and the nginx listen directives,
    both in install.sh).

    Cost: one read of config.json per request, same trade-off as
    _refresh_user_permissions' read of users.json below.
    """
    if Settings.load().lan_toegang:
        return
    if _client_is_loopback():
        return
    # Plain-text refusal, deliberately not the templated 403 page:
    # that template is designed for logged-in users who lack a tab,
    # and there's no reason to render navigation for a client we're
    # refusing at the front door.
    return _("Toegang via het netwerk is uitgeschakeld (zie Voorkeuren op het apparaat zelf)."), 403


@app.before_request
def csrf_protect():
    """Reject form submissions that are missing a valid security token.

    This protection is called a CSRF check. It makes sure that a form
    POST actually came from our own page and not from a malicious
    website that tricks a logged-in user into clicking a button.
    Every form in the app includes a hidden ``_csrf`` field with a
    secret token. This function checks that the token matches.
    """
    # Only check POST requests. (No exemption needed for the daemon's
    # /api/effectief-rooster: that endpoint is GET-only, so it never
    # reaches this check. The old exemption for it was dead code.)
    if request.method != "POST":
        return
    # Accept both form field (UI forms) and header (JSON API from settings.html).
    sess_token = session.get("csrf", "")
    form_token = request.form.get("_csrf") or request.headers.get("X-CSRF-Token", "")
    # compare_digest instead of != : a plain string comparison stops
    # at the first character that differs, so comparing "abc..." to
    # "abd..." is a tiny bit faster than comparing it to "abc...".
    # An attacker who measures response times very precisely could
    # in theory use that to guess the token character by character.
    # compare_digest always takes the same amount of time no matter
    # where the difference is. Standard fix, costs nothing.
    if not sess_token or not form_token or not hmac.compare_digest(form_token, sess_token):
        return "CSRF token invalid or missing", 400


@app.after_request
def _security_headers(resp):
    """Add defensive HTTP headers to every response.

    Done in Flask rather than in the Nginx config so they also apply
    on installs that front the app differently (or not at all, when
    running the dev server). setdefault, not assignment: a route that
    deliberately sets its own value wins.

    - X-Content-Type-Options: nosniff — stops browsers from guessing
      ('sniffing') content types, e.g. treating an uploaded file as
      HTML/JS because it happens to start with '<'.
    - X-Frame-Options: SAMEORIGIN — the app has no reason to be
      embedded in an iframe (a page shown inside another page) on
      some other site. Blocks 'clickjacking': a trick where an
      attacker's page puts our page invisibly on top of a harmless-
      looking button, so your click lands on our page without you
      knowing.
    - Referrer-Policy: same-origin — when you click a link, browsers
      normally tell the next site which page you came from. Our URLs
      can contain rooster names and ?next= paths; no need to share
      those with external sites if someone ever puts an outbound
      link in a template.

    A Content-Security-Policy is deliberately NOT set here: the
    templates use inline <script> and <style> blocks, so a useful
    CSP needs nonces or a refactor first. A CSP with 'unsafe-inline'
    everywhere would add noise, not protection.
    """
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    return resp


@app.errorhandler(413)
def too_large(_e):
    """Show a friendly error message when an uploaded file is too large.

    Flask automatically calls this handler when a request body exceeds
    ``MAX_CONTENT_LENGTH``. We flash a message and send the user back
    to the audio-files page.
    """
    flash(_("Upload te groot (controleer de ingestelde limiet bij Voorkeuren)."))
    return redirect(url_for("geluiden.geluiden"))


@app.errorhandler(403)
def forbidden(_e):
    """Show a styled '403 Forbidden' page instead of Flask's plain text one.

    A 403 response means the user is logged in but not allowed to visit
    the page they requested. For example, a user without the 'roosters'
    tab gets a 403 when they try to open /roosters.
    """
    # Replaces Flask's default plain-text 403 page with a templated
    # one that fits the rest of the UI. Triggered by abort(403) in
    # tab_required / admin_page_required when the visitor is logged
    # in but lacks the required tab/role. Anonymous visitors are
    # redirected to /login by those decorators, so this handler only
    # fires for authenticated users hitting a wall.
    from flask import render_template
    return render_template("403.html"), 403

def ensure_dirs():
    """Create the data and audio folders if they do not exist yet.

    ``exist_ok=True`` means Python will not crash if the folder is
    already there — it just does nothing in that case.
    """
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(AUDIO_DIR, exist_ok=True)

def load_json(path, default):
    """Read a JSON file and return its contents as a Python object.

    If the file does not exist or cannot be parsed, return ``default``
    instead of crashing. That way the app still works on a fresh install
    where the data files have not been created yet.
    """
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        print(f"[WARN] Could not load {path}: {e}")
    return default

def save_json(path, obj):
    """Save a Python object to a JSON file in a safe way.

    We first write to a temporary file (``path.tmp``), make sure the
    data is fully on disk, and then rename it over the real file.
    This means the original file is never half-written: you always end
    up with either the old version or the complete new version.
    """
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)

@contextmanager
def locked_json(path, default):
    """Read-modify-write a JSON state file under an exclusive lock.

    Why this exists: save_json() writes atomically (tmp + os.replace),
    but the typical request flow is load -> mutate -> save. Two
    concurrent requests can both load the same state, each mutate
    their own copy, and both save. The second one overwrites the
    first, silently losing an update. With Gunicorn at 2 workers x
    4 threads that race condition (when two operations happen at
    almost the same moment and step on each other) is real: two
    people clicking 'add moment' at the same time can lose one of
    the moments.

    Usage:
        with locked_json(ROOSTERS_PATH, default_roosters_obj()) as (data, save):
            data["new"] = []
            save(data)

    The lock is released when the with-block exits, whether or not
    save() was called. If validation fails, just return without calling
    save() and the file is untouched.

    Implementation notes:
    - We lock a separate `.lock` file that sits next to the data file,
      rather than the data file itself. Reason: save_json() swaps in
      a whole new file via os.replace(), and a lock taken on the old
      file would quietly keep pointing at the replaced, now-orphaned
      version. The sidecar file never gets replaced, so the lock
      stays meaningful.
    - fcntl.flock is 'advisory': it doesn't physically prevent
      writing, it only blocks other code that also asks for the lock
      first. That's fine here — all writers go through this helper,
      so everyone asks.
    - The lock works between threads in one worker AND between the
      separate Gunicorn worker processes, which is exactly the mix
      we have in production.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    lock_path = path + ".lock"
    lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        data = load_json(path, default)

        def _save(new_data):
            # Caller passes the data they want persisted, so reassigning
            # `data = ...` inside the with-block still works. If they
            # mutate in place, just call save(data).
            save_json(path, new_data)

        yield data, _save
    finally:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(lock_fd)

def list_audio():
    """Show files according to allowed_extensions from Settings."""
    ensure_dirs()
    s = Settings.load()
    allowed_exts = tuple(e.lower() for e in s.allowed_extensions)
    files = []
    for name in sorted(os.listdir(AUDIO_DIR)):
        p = os.path.join(AUDIO_DIR, name)
        if os.path.isfile(p) and name.lower().endswith(allowed_exts):
            files.append(name)
    return files

# Pure helpers extracted to core/ — see imports at the top of this file:
#   safe_audio_filename, normalize_time, normalize_and_sort_moments,
#   default_roosters_obj, default_dagplanning_obj, default_standaardweek_obj,
#   default_weken_uit_obj, weekday_key, iso_week_key,
#   iso_weeks_with_weekday_in_range, effective_rooster_for_date,
#   effectieve_rooster_naam_for_date, prune_past_dates, _next_local_midnight


def next_bell_for_now(now: datetime) -> dict | None:
    """Return the next-upcoming bell after ``now``, or None if there is none today.

    Used by the public /now page and its companion /api/now endpoint
    so a screen in the staff room can display "Volgende bel: ... over
    3:42" without anyone having to log in. Today-only on purpose:
    showing "next bell at 8:30 tomorrow" on a Friday afternoon would
    create confusing weekend states. The page handles None as
    "geen bel meer vandaag".

    The result dict matches the shape /api/now returns:
      {
        "naam":            str   — bell name from the rooster
        "tijd":            str   — HH:MM or HH:MM:SS, as stored
        "bestand":         str   — audio filename (so /now could
                                   prefetch / preview if desired)
        "seconds_until":   int   — non-negative; 0 means it just hit
        "datum":           str   — YYYY-MM-DD, useful for sanity in
                                   tests across midnight rolls
      }

    Reads the same JSON files /api/effectief-rooster does (no caching
    between callers) so a planning change in the UI shows up on /now
    on the next refetch.
    """
    d = now.date()

    # Week-off override: if the whole ISO week is marked as "uit"
    # (vacation week or any week the user toggled off), no bells
    # ring regardless of the rooster.
    weken_uit = load_json(WEEKDISABLE_PATH, default_weken_uit_obj())
    if weken_uit.get(iso_week_key(d)):
        return None

    dagplanning = load_json(DAGPLANNING_PATH, default_dagplanning_obj())
    standaardweek = load_json(STANDAARDWEEK_PATH, default_standaardweek_obj())
    rooster_naam, _bron = effective_rooster_for_date(d, dagplanning, standaardweek)
    if not rooster_naam:
        return None

    roosters = load_json(ROOSTERS_PATH, default_roosters_obj())
    momenten = roosters.get(rooster_naam) or []
    if not momenten:
        return None

    # Compare HH:MM:SS strings. tijd is "HH:MM" or "HH:MM:SS"; pad
    # to 8 chars so a moment at "08:30" sorts before "08:30:01".
    now_tijd = now.strftime("%H:%M:%S")

    def _pad(t: str) -> str:
        return t if len(t) == 8 else t + ":00"

    # Skip moments with a missing or malformed tijd instead of
    # crashing the public /now page on them. normalize_and_sort_moments
    # guarantees clean data on every save through the UI, but a
    # hand-edited roosters.json shouldn't take the page down — and
    # compute_upcoming already tolerates the same garbage, so the two
    # readers stay consistent.
    valid = [
        m for m in momenten
        if TIME_RE.match((m.get("tijd") or "").strip())
    ]
    upcoming = [m for m in valid if _pad(m["tijd"]) > now_tijd]
    if not upcoming:
        return None

    # momenten is already sorted by tijd, but be defensive — a future
    # change to normalize_and_sort_moments could regress that.
    upcoming.sort(key=lambda m: _pad(m["tijd"]))
    nxt = upcoming[0]

    # Compute seconds_until from a real datetime to avoid hand-rolling
    # arithmetic across the HH:MM/HH:MM:SS variants. int() can't fail:
    # TIME_RE pinned the format above.
    h, mn, *rest = _pad(nxt["tijd"]).split(":")
    sec = int(rest[0]) if rest else 0
    bell_dt = datetime.combine(d, time(int(h), int(mn), sec))
    secs = max(0, int((bell_dt - now).total_seconds()))

    return {
        "naam": nxt.get("naam", ""),
        "tijd": nxt["tijd"],
        "bestand": nxt.get("bestand", ""),
        "seconds_until": secs,
        "datum": d.isoformat(),
    }


def _ts_now_iso():
    """Return the current date and time as a text string in UTC.

    Example result: ``'2025-10-18T14:30:00.123456+00:00'``.
    UTC means the time is not adjusted for a local timezone, so the
    log timestamps are always comparable regardless of where the Pi is.
    """
    return datetime.now(timezone.utc).isoformat()

def log_event(ev_type: str, data: dict):
    """Write one event to the shared log file (events.jsonl).

    ``ev_type`` is a short category like ``"bell"`` or ``"ui"``.
    ``data`` is a dict with any extra details about what happened.
    Each event is stored as one JSON line so the file is easy to read
    back one line at a time.
    """
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        rec = {"ts": _ts_now_iso(), "type": ev_type, "data": data}
        with open(EVENTS_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"[WARN] log_event failed: {e}")

def read_events(limit=200, max_bytes=256_000):
    # Read the last 'limit' events from EVENTS_LOG_PATH without reading the entire file.
    # max_bytes determines how many bytes from the end we look at.
    try:
        with open(EVENTS_LOG_PATH, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            start = max(0, size - max_bytes)
            # Peek at the byte just before `start`. If it is a newline,
            # the chunk begins exactly on a line boundary and the first
            # line is complete. Otherwise we landed mid-line and must
            # drop the first (truncated) line. Without this peek, the
            # old code unconditionally dropped the first line whenever
            # start > 0, silently losing a valid record whenever the
            # window happened to align with a newline.
            prev_is_newline = False
            if start > 0:
                f.seek(start - 1)
                prev_is_newline = f.read(1) == b"\n"
            f.seek(start)
            chunk = f.read()

        lines = chunk.splitlines()
        if start > 0 and not prev_is_newline and lines:
            lines = lines[1:]

        out = []
        for bline in lines:
            try:
                line = bline.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                out.append(json.loads(line))
            except Exception:
                pass

        return out[-limit:]
    except FileNotFoundError:
        return []
    except Exception as e:
        print(f"[WARN] read_events failed: {e}")
        return []

def compute_upcoming(limit=20):
    """Return a list of the next upcoming bell moments.

    Looks forward up to 14 days and collects all scheduled bell times.
    Skips days that are in a 'week off' and days without an active
    rooster. Returns at most ``limit`` results, sorted by date and time.
    Used by the Logboek page to show what is coming up next.
    """
    roosters = load_json(ROOSTERS_PATH, default_roosters_obj())
    dagplanning = load_json(DAGPLANNING_PATH, default_dagplanning_obj())
    standaardweek = load_json(STANDAARDWEEK_PATH, default_standaardweek_obj())
    weken_uit = load_json(WEEKDISABLE_PATH, default_weken_uit_obj())

    now = datetime.now()
    results = []

    day = date.today()
    for _i in range(14):
        wk_key = iso_week_key(day)
        if not weken_uit.get(wk_key):
            d_iso = day.isoformat()
            # Route via the single helper so we honour explicit silence
            # overrides (None in dagplanning) — without this, a day that
            # the user silenced via '— geen bel —' would still appear in
            # the upcoming-bells list.
            rname = effectieve_rooster_naam_for_date(day, dagplanning, standaardweek)
            moments = roosters.get(rname, []) if rname else []
            for m in moments:
                try:
                    hh, mm = map(int, (m.get("tijd") or "00:00").split(":")[:2])
                except Exception:
                    continue
                dt = datetime(day.year, day.month, day.day, hh, mm)
                if dt >= now:
                    results.append({
                        "dt": dt,
                        "datum": d_iso,
                        "tijd": m.get("tijd"),
                        "naam": m.get("naam",""),
                        "bestand": m.get("bestand",""),
                        "rooster": rname or ""
                    })
        day = day + timedelta(days=1)
        if len(results) >= limit: break

    results.sort(key=lambda x: x["dt"])
    return results[:limit]


# ---------- Blueprints ----------
# Every route in the app now lives in a blueprint under blueprints/:
#   blueprints/auth.py        — /login, /logout
#   blueprints/agenda.py      — /agenda, /agenda/import-vakanties, /agenda/refresh-vakanties
#   blueprints/geluiden.py    — /audio/<file>, /geluiden, /geluiden/{upload,play,delete}
#   blueprints/monitoring.py  — /, /logs, /healthz, /now, /api/now, /api/effectief-rooster
#   blueprints/roosters.py    — /roosters/*, /standaardweek
#   blueprints/settings.py    — /settings, /api/settings (GET + POST)
#
# Registering at the bottom of the file means webinterface is fully
# defined (all module-level constants and helpers exist) before each
# blueprint module imports it back. That avoids partial-import
# surprises like 'AttributeError: module has no attribute AUDIO_DIR'.
#
# _apply_settings_payload is also re-imported here for backwards
# compatibility: tests import it as ``webinterface._apply_settings_payload``.
from blueprints.agenda import agenda_bp  # noqa: E402
from blueprints.auth import auth_bp  # noqa: E402
from blueprints.gebruikers import gebruikers_bp  # noqa: E402
from blueprints.geluiden import geluiden_bp  # noqa: E402
from blueprints.monitoring import monitoring_bp  # noqa: E402
from blueprints.roosters import roosters_bp  # noqa: E402
from blueprints.settings import (  # noqa: E402, F401
    _apply_settings_payload,
    settings_bp,
)

app.register_blueprint(agenda_bp)
app.register_blueprint(auth_bp)
app.register_blueprint(gebruikers_bp)
app.register_blueprint(geluiden_bp)
app.register_blueprint(monitoring_bp)
app.register_blueprint(roosters_bp)
app.register_blueprint(settings_bp)


# ---------- User store bootstrap ----------
# Pre-multi-user installs only had SCHOOLBELL_WEB_USER and
# SCHOOLBELL_WEB_PWHASH in /etc/schoolbell/web.env. From this commit
# onwards the canonical source of truth is data/users.json, with the
# env vars demoted to a seed for the very first start.
#
# We can't call bootstrap_from_env() at module import time: tests
# monkeypatch core.users.USERS_PATH in their fixtures, and that has
# to happen *before* the bootstrap writes anything. Hooking into
# before_request keeps the patch order correct (fixture sets up,
# test triggers a request, bootstrap then sees the tmp path).
#
# bootstrap_from_env() is idempotent (fast-path read, then locked
# re-check), so calling it on every request is cheap once the store
# has been seeded.
from core import users as _users  # noqa: E402

@app.before_request
def _bootstrap_users():
    """Seed the user store from environment variables on the very first request.

    On a fresh install, no ``users.json`` file exists yet. This function
    checks whether the file is empty and, if so, creates the first admin
    account from the ``SCHOOLBELL_WEB_USER`` and ``SCHOOLBELL_WEB_PWHASH``
    environment variables. After that it does nothing (fast no-op).
    """
    _users.bootstrap_from_env(ADMIN_USER, ADMIN_HASH)


@app.before_request
def _refresh_user_permissions():
    """Sync the session's rol/tabs with users.json on every request.

    Login used to cache rol and tabs in the session cookie, with the
    documented trade-off that permission changes only took effect
    after the user logged back in. The nasty edge of that trade-off:
    a *deleted* user kept full access until their cookie expired
    (up to 30 minutes). This hook closes both gaps — an admin's
    change to a user's role or tabs applies on that user's very next
    request, and a deleted user's session dies immediately.

    Cost: one read of users.json per authenticated request. The file
    is a few hundred bytes and the operating system keeps recently
    read files in memory anyway, so this is far cheaper than the
    template render that follows. The session
    is only written back when something actually changed, so we
    don't emit a Set-Cookie header on every response.
    """
    username = session.get("user")
    if not username:
        return
    rec = _users.get_user(username)
    if rec is None:
        # User was deleted (or users.json was reset): kill the
        # session. The tab/admin decorators then treat the request
        # as anonymous and redirect to /login.
        session.clear()
        return
    rol = rec.get("rol", "gebruiker")
    tabs = list(rec.get("tabs") or [])
    if session.get("rol") != rol:
        session["rol"] = rol
    if session.get("tabs") != tabs:
        session["tabs"] = tabs


# ---------- Dev server ----------
# The previous version of this block also pre-created roosters.json,
# dagplanning.json, standaardweek.json and weken_uit.json with empty
# defaults. That bootstrap was dead in production: Gunicorn never runs
# `__main__`, it imports the app object. So in production the files
# only existed after the first save anyway. Removed because:
#
#   1. load_json() already returns the right default when the file
#      is missing, so the routes work fine without the files.
#   2. Hoisting the bootstrap to module level would also fire on
#      every `import webinterface` (including from pytest), creating
#      stray files in the project's data/ folder.
#
# Net result: missing JSON state files are handled lazily everywhere.
# First save by any route creates the file (atomically via tmp+
# os.replace).
if __name__ == "__main__":
    # Production runs through Gunicorn (see install.sh / systemd unit);
    # this block only runs `python3 webinterface.py` for local hacking.
    app.run(host="127.0.0.1", port=5000) #, ssl_context=("certs/cert.pem", "certs/key.pem"))
