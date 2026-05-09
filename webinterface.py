#!/usr/bin/env python3
import os, json, fcntl
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
    auth,
    get_csrf_token,
    require_admin,
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

# === Flask ===
app = Flask(__name__)
# TEMPLATES_AUTO_RELOAD stats each template file on every render. Useful
# during dev; unnecessary I/O in production behind Gunicorn. Enable via
# SCHOOLBELL_DEBUG=1 when working locally.
_debug_mode = os.environ.get("SCHOOLBELL_DEBUG", "0").strip().lower() in ("1", "true", "yes", "on")
app.config["TEMPLATES_AUTO_RELOAD"] = _debug_mode
app.jinja_env.auto_reload = _debug_mode
app.secret_key = os.environ.get("SCHOOLBELL_SECRET", "dev-secret")
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
from flask_babel import Babel  # noqa: E402

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
    cry wolf on installs with an unusually long polling interval. We
    also enforce a 10-second minimum because at the default 2s poll
    interval, 3× = 6s — too tight; a single GC pause or disk hiccup
    would flip the indicator to 'down' for one render. 10s gives a
    little slack without making a real outage invisible.
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
    # UTC ISO strings, so this is belt-and-braces.
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
# theme_mode is also injected here so base.html can bake it server-side
# (avoids flash-of-wrong-theme and works on /login, where /api/settings
# would return 401 because the user isn't logged in yet).
#
# daemon_heartbeat is injected too so the header indicator is available
# everywhere without each route having to remember to pass it.
@app.context_processor
def _inject_template_globals():
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
    return {
        "now": datetime.now,
        "theme_mode": mode,
        "huisstijl": huisstijl,
        "theme_custom_bg": custom_bg,
        "theme_custom_table": custom_table,
        "theme_custom_nav": custom_nav,
        "daemon_heartbeat": get_daemon_heartbeat(),
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


@app.before_request
def csrf_protect():
    # Only check POST requests.
    if request.method != "POST":
        return
    # Daemon endpoint uses Basic Auth from localhost, no browser -> no CSRF.
    if request.path == "/api/effectief-rooster":
        return
    # Accept both form field (UI forms) and header (JSON API from settings.html).
    sess_token = session.get("csrf", "")
    form_token = request.form.get("_csrf") or request.headers.get("X-CSRF-Token", "")
    if not sess_token or not form_token or form_token != sess_token:
        return "CSRF token invalid or missing", 400


@app.errorhandler(413)
def too_large(_e):
    flash("Upload te groot (controleer de ingestelde limiet bij Voorkeuren).")
    return redirect(url_for("geluiden.geluiden"))

def ensure_dirs():
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(AUDIO_DIR, exist_ok=True)

def load_json(path, default):
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        print(f"[WARN] Could not load {path}: {e}")
    return default

def save_json(path, obj):
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)

@contextmanager
def locked_json(path, default):
    """Read-modify-write a JSON state file under an exclusive lock.

    Why this exists: save_json() writes atomically (tmp + os.replace),
    but the typical request flow is load -> mutate -> save. Two
    concurrent requests can both load the same state, each mutate
    their own copy, and both save — the second one overwrites the
    first, silently losing an update. With Gunicorn at 2 workers ×
    4 threads that race is real: two people clicking 'add moment'
    at the same time can lose one of the moments.

    Usage:
        with locked_json(ROOSTERS_PATH, default_roosters_obj()) as (data, save):
            data["new"] = []
            save(data)

    The lock is released when the with-block exits, whether or not
    save() was called. If validation fails, just return without calling
    save() and the file is untouched.

    Implementation notes:
    - We lock a sidecar `.lock` file rather than the data file itself.
      save_json() replaces the data file via os.replace(), which would
      invalidate any fd we held open against the original inode. The
      sidecar lock decouples lock identity from data identity.
    - fcntl.flock is advisory: it only blocks other processes/threads
      that also call flock on the same file. That's fine here — we
      control all writers (web routes via this helper).
    - Locks are per-process-fd, so within Gunicorn this serializes
      writers across both threads (in one worker) and across workers.
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

    upcoming = [m for m in momenten if _pad(m["tijd"]) > now_tijd]
    if not upcoming:
        return None

    # momenten is already sorted by tijd, but be defensive — a future
    # change to normalize_and_sort_moments could regress that.
    upcoming.sort(key=lambda m: _pad(m["tijd"]))
    nxt = upcoming[0]

    # Compute seconds_until from a real datetime to avoid hand-rolling
    # arithmetic across the HH:MM/HH:MM:SS variants.
    h, mn, *rest = _pad(nxt["tijd"]).split(":")
    sec = int(rest[0]) if rest else 0
    bell_dt = datetime.combine(d, time(int(h), int(mn), sec))
    secs = max(0, int((bell_dt - now).total_seconds()))

    return {
        "naam": nxt["naam"],
        "tijd": nxt["tijd"],
        "bestand": nxt["bestand"],
        "seconds_until": secs,
        "datum": d.isoformat(),
    }


def _ts_now_iso():
    return datetime.now(timezone.utc).isoformat()

def log_event(ev_type: str, data: dict):
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
    roosters = load_json(ROOSTERS_PATH, default_roosters_obj())
    dagplanning = load_json(DAGPLANNING_PATH, default_dagplanning_obj())
    standaardweek = load_json(STANDAARDWEEK_PATH, default_standaardweek_obj())
    weken_uit = load_json(WEEKDISABLE_PATH, default_weken_uit_obj())

    now = datetime.now()
    results = []

    day = date.today()
    for _ in range(14):
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
                    hh, mm = map(int, (m.get("tijd") or "00:00").split(":"))
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
from blueprints.geluiden import geluiden_bp  # noqa: E402
from blueprints.monitoring import monitoring_bp  # noqa: E402
from blueprints.roosters import roosters_bp  # noqa: E402
from blueprints.settings import (  # noqa: E402, F401
    _apply_settings_payload,
    settings_bp,
)

app.register_blueprint(agenda_bp)
app.register_blueprint(auth_bp)
app.register_blueprint(geluiden_bp)
app.register_blueprint(monitoring_bp)
app.register_blueprint(roosters_bp)
app.register_blueprint(settings_bp)


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
