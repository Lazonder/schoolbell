#!/usr/bin/env python3
import os, json, re, secrets, fcntl
from contextlib import contextmanager
from datetime import date, timedelta, datetime, time, timezone
from dataclasses import asdict
from typing import Optional
from flask import Flask, request, redirect, url_for, flash, send_from_directory, session, jsonify, abort, render_template
from flask_httpauth import HTTPBasicAuth
from werkzeug.security import check_password_hash
from functools import wraps
import settings_store
from settings_store import Settings
from werkzeug.middleware.proxy_fix import ProxyFix

auth = HTTPBasicAuth()

# Fetch credentials from environment
ADMIN_USER = os.getenv("SCHOOLBELL_WEB_USER", "admin")
# Set either SCHOOLBELL_WEB_PWHASH (recommended) or temporarily SCHOOLBELL_WEB_PASS
ADMIN_HASH = os.getenv("SCHOOLBELL_WEB_PWHASH")      # e.g. pbkdf2:sha256:...
FALLBACK_PLAIN = os.getenv("SCHOOLBELL_WEB_PASS")    # only for first test

# Name validation: 1–35 chars, letters/digits/space/_/- only
NAME_RE = re.compile(r"^[A-Za-z0-9 _-]{1,35}$")

# CSS hex color: #rgb / #rrggbb (case-insensitive). Used to validate
# the huisstijl custom-color payload before it's stored and rendered
# unescaped into <html style="--sb-color-...">. A stricter check than
# eyeballing — anything that doesn't match isn't safe to inject.
_CSS_HEX_COLOR_RE = re.compile(r"#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})")

# Form-level sentinel sent by the agenda dropdown when the user picks
# '— geen bel —' for a date. Stored as JSON null in dagplanning to mean
# 'explicit silence override on this date'. The '!' prefix is outside
# NAME_RE so it can never collide with a real rooster name.
DAGPLANNING_SILENT_FORM_VALUE = "!off"

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

def _env_bool(name: str, default: bool) -> bool:
    """Read an env variable as a boolean.
    Not set -> default. '0', 'false', 'no', 'off' (case-insensitive) or empty -> False.
    Everything else -> True. Prevents the classic bug that bool("0") is True.
    """
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() not in ("0", "false", "no", "off", "")

# SESSION_COOKIE_SECURE must be True for HTTPS (browser only sends cookie back
# over TLS). For HTTP deployment (default install.sh setup, Nginx on port 80)
# this must be False, or login won't work. Default in code: True (secure).
# install.sh explicitly sets =0 in web.env.
app.config.update(
    SESSION_COOKIE_SECURE=_env_bool("SCHOOLBELL_SECURE_COOKIES", default=True),
    SESSION_COOKIE_SAMESITE="Lax",
)

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

# Time regex: 24-hour clock 00–23, with optional seconds (HH:MM or HH:MM:SS)
TIME_RE = re.compile(r"^(?:[01]?\d|2[0-3]):[0-5]\d(?::[0-5]\d)?$")
WEEKDAYS = [
    ("Mon", "Maandag"),
    ("Tue", "Dinsdag"),
    ("Wed", "Woensdag"),
    ("Thu", "Donderdag"),
    ("Fri", "Vrijdag"),
    ("Sat", "Zaterdag"),
    ("Sun", "Zondag"),
]

# ---- UI login (sessie) ----
def _check_password(plain: str) -> bool:
    if ADMIN_HASH:
        return check_password_hash(ADMIN_HASH, plain)
    if FALLBACK_PLAIN:
        return plain == FALLBACK_PLAIN
    return False

def ui_logged_in() -> bool:
    return session.get("user") == ADMIN_USER

def ui_login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if ui_logged_in():
            return view(*args, **kwargs)
        # remember where we need to return to
        nxt = request.full_path if request.query_string else request.path
        return redirect(url_for("login", next=nxt))
    return wrapper

@app.route("/login", methods=["GET", "POST"])
def login():
    if ui_logged_in():
        return redirect(url_for("agenda"))

    next_url = request.args.get("next") or request.form.get("next") or url_for("roosters")

    if request.method == "POST":
        u = (request.form.get("username") or "").strip()
        p = request.form.get("password") or ""
        if u == ADMIN_USER and _check_password(p):
            # Close session fixation: discard everything that was in the session
            # before login (including the CSRF token that was already generated
            # on the login page), so any injected cookie is immediately worthless.
            # get_csrf_token() then generates a fresh token on the first next render.
            session.clear()
            session.permanent = True
            session["user"] = ADMIN_USER
            return redirect(next_url)
        flash("Onjuiste inloggegevens.")

    return render_template(
        "login.html",
        next_url=next_url,
        admin_user=ADMIN_USER,
        csrf_token=get_csrf_token(),
        tab=None  # no active tab on the login page
    )

@app.route("/logout", methods=["POST", "GET"])
def logout():
    # session.clear() instead of just pop("user"): this also discards the
    # CSRF token and session.permanent flag. On the next login, everything
    # is rebuilt fresh.
    session.clear()
    return redirect(url_for("login"))

# --- CSRF helpers ---
def get_csrf_token() -> str:
    tok = session.get("csrf")
    if not tok:
        tok = secrets.token_urlsafe(32)
        session["csrf"] = tok
    return tok

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

# ---------- Helpers ----------
@app.errorhandler(413)
def too_large(_e):
    flash("Upload te groot (controleer de ingestelde limiet bij Voorkeuren).")
    return redirect(url_for("geluiden"))

@auth.verify_password
def verify_password(username, password):
    if username != ADMIN_USER:
        return False
    if ADMIN_HASH:
        return check_password_hash(ADMIN_HASH, password)
    if FALLBACK_PLAIN:
        return password == FALLBACK_PLAIN  # only for first test
    return False

def require_admin(f):
    """Require a logged-in admin session.
    For API routes we return 401 JSON instead of a redirect, so that
    fetch() clients get a machine-readable response.
    """
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not ui_logged_in():
            return jsonify(error="auth_required"), 401
        return f(*args, **kwargs)
    return wrapper

def _apply_settings_payload(s: Settings, payload: dict) -> None:
    """Mutate `s` in place from `payload`, validating each field.

    Raises Flask abort(400) on invalid input — when called inside
    settings_store.locked(), the abort propagates through the
    context manager, which means save() is NOT called and the file
    is left untouched. That's the right behavior for invalid input.
    """
    if "volume_percent" in payload:
        v = int(payload["volume_percent"])
        if not (0 <= v <= 100): abort(400, "volume_percent must be 0..100")
        s.volume_percent = v

    if "max_file_size_mb" in payload:
        m = int(payload["max_file_size_mb"])
        if not (1 <= m <= 1024): abort(400, "max_file_size_mb must be 1..1024")
        s.max_file_size_mb = m

    if "poll_interval_sec" in payload:
        p = int(payload["poll_interval_sec"])
        if not (1 <= p <= 60): abort(400, "poll_interval_sec must be 1..60")
        s.poll_interval_sec = p

    # Note: a "timezone" key in the payload is silently ignored. The
    # field was removed; the OS timezone is the source of truth.

    if "theme_mode" in payload:
        tm = str(payload["theme_mode"]).strip().lower()
        if tm not in ("light", "dark", "auto"):
            abort(400, "theme_mode must be one of: light, dark, auto")
        s.theme_mode = tm

    if "huisstijl" in payload:
        hs = str(payload["huisstijl"]).strip().lower()
        if hs not in ("standaard", "aangepast"):
            abort(400, "huisstijl must be one of: standaard, aangepast")
        s.huisstijl = hs

    # Validate the three custom-color fields independently so the user
    # can save partial updates from the Voorkeuren UI (e.g. only
    # tweaking the nav color). Each must look like a CSS hex code.
    # We don't try to enforce contrast or other accessibility checks —
    # the user picked these intentionally and may know what they want.
    for key in ("theme_custom_bg", "theme_custom_table", "theme_custom_nav"):
        if key in payload:
            v = str(payload[key]).strip()
            if not _CSS_HEX_COLOR_RE.fullmatch(v):
                abort(400, f"{key} must be a CSS hex color like #rrggbb")
            setattr(s, key, v.lower())

    if "vakantieregio" in payload:
        vr = str(payload["vakantieregio"]).strip()
        if vr not in VAKANTIE_REGIOS:
            abort(400, f"vakantieregio must be one of: {', '.join(VAKANTIE_REGIOS)}")
        s.vakantieregio = vr

    if "vakanties_scrape_enabled" in payload:
        # Accept proper booleans plus the common JSON/HTML form
        # representations ('true'/'false', '1'/'0', 'on'/'off',
        # checkbox-style 'on'/missing). The settings page uses a
        # checkbox which sends 'on' when checked and nothing when
        # unchecked; the JSON POST in settings.html maps that to a
        # real bool, but be defensive in case a future form posts
        # raw form data.
        v = payload["vakanties_scrape_enabled"]
        if isinstance(v, bool):
            s.vakanties_scrape_enabled = v
        elif isinstance(v, str):
            s.vakanties_scrape_enabled = v.strip().lower() in ("1", "true", "yes", "on")
        else:
            s.vakanties_scrape_enabled = bool(v)

# -- Settings API (no blueprint; simple app routes) --
@app.route("/api/settings", methods=["GET"])
@require_admin
def api_settings_get():
    return jsonify(asdict(Settings.load()))

@app.route("/api/settings", methods=["POST"])
@require_admin
def api_settings_post():
    if not request.is_json:
        abort(400, "JSON expected")
    payload = request.get_json() or {}
    # Hold the settings file lock for the entire load -> mutate ->
    # save sequence. Without the lock, two concurrent POSTs could
    # both load v1, each apply their own payload, and both save —
    # last write wins, the first user's change is silently lost.
    # If validation fails, _apply_settings_payload aborts, the
    # context manager unwinds without calling save(), and the file
    # is untouched.
    with settings_store.locked() as s:
        _apply_settings_payload(s, payload)
        result = asdict(s)
    return jsonify(result), 200

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

def safe_audio_filename(base_no_ext: str, ext: str) -> str:
    """Validate name and append the chosen extension (ext contains dot, e.g. '.mp3')."""
    base_no_ext = base_no_ext.strip()
    if not NAME_RE.match(base_no_ext):
        return ""
    if not (1 <= len(base_no_ext) <= 35):
        return ""
    return f"{base_no_ext}{ext}"

def normalize_time(t: str) -> str:
    """
    Accepts '8:05', '08:05', '11:30:00', etc.
    Always returns 'HH:MM', or '' if the time is invalid.
    """
    t = (t or "").strip()
    if not TIME_RE.match(t):
        return ""
    parts = t.split(":")
    if len(parts) < 2:
        return ""
    hh, mm = parts[0], parts[1]
    # prevent odd things like '007:03'
    try:
        hh_int = int(hh)
        mm_int = int(mm)
    except ValueError:
        return ""
    if not (0 <= hh_int <= 23 and 0 <= mm_int <= 59):
        return ""
    return f"{hh_int:02d}:{mm_int:02d}"

def normalize_and_sort_moments(moments):
    cleaned = []
    for m in moments:
        tijd_norm = normalize_time(m.get("tijd") or "")
        naam = (m.get("naam") or "").strip()
        bestand = (m.get("bestand") or "").strip()
        if not tijd_norm:
            continue
        if not naam:
            continue
        if not bestand:
            continue
        out = {"tijd": tijd_norm, "naam": naam, "bestand": bestand}

        # Optional warning bell: rings warn_min minutes before the
        # main moment with warn_bestand. Both fields must be valid
        # and non-empty for a warning to actually fire — anything
        # else is treated as "no warning". Keeps roosters.json
        # forward- and backward-compatible: legacy moments without
        # the keys load fine and simply don't get a warning.
        warn_min = m.get("warn_min")
        warn_bestand = (m.get("warn_bestand") or "").strip()
        try:
            warn_min_int = int(warn_min) if warn_min is not None else 0
        except (TypeError, ValueError):
            warn_min_int = 0
        if 1 <= warn_min_int <= 60 and warn_bestand:
            out["warn_min"] = warn_min_int
            out["warn_bestand"] = warn_bestand
        # If only one of the two is set, the warning is silently
        # dropped — no half-configured state survives normalization.

        cleaned.append(out)
    cleaned.sort(key=lambda x: x["tijd"])
    return cleaned

def default_roosters_obj():
    return {}

def default_dagplanning_obj():
    return {}

def default_standaardweek_obj():
    # Default: no rooster filled in
    return {k: "" for k, _ in WEEKDAYS}

def weekday_key(d: date) -> str:
    return ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"][d.weekday()]

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

def default_weken_uit_obj():
    return {}  # bijv. {"2025-W34": true}

def iso_week_key(d: date) -> str:
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
    tomorrow = now.date() + timedelta(days=1)
    return datetime.combine(tomorrow, time(0, 0, 0))


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


# ---------- Routes ----------
@app.route("/")
@ui_login_required
def home():
    return redirect(url_for("agenda"))

@app.route("/logs", methods=["GET"])
@ui_login_required
def logs_page():
    ensure_dirs()
    upcoming = compute_upcoming(20)
    evs = list(reversed(read_events(200)))
    recent_bell = [e for e in evs if e.get("type") == "bell"][:20]
    recent_ui   = [e for e in evs if e.get("type") == "ui"][:20]
    return render_template(
        "logs.html",
        tab="logs",
        csrf_token=get_csrf_token(),
        upcoming=upcoming,
        recent_bell=recent_bell,
        recent_ui=recent_ui,
    )

# -- Settings (pagina) --
@app.get("/settings")
@ui_login_required
def settings_page():
    return render_template(
        "settings.html",
        tab="settings",
        csrf_token=get_csrf_token(),
        vakanties_status=_build_vakanties_status(),
    )


def _build_vakanties_status() -> dict:
    """Gather everything the Voorkeuren status panel needs to render.

    Pulls together:
      - which schooljaren are saved in data/vakanties.json
      - when each was fetched
      - last attempt / success / error from data/vakanties_fetch_state.json

    Returns a plain dict that the template can iterate over directly.
    Best-effort: any read error falls back to a sensible empty value
    rather than raising, so a corrupt state file doesn't break the
    settings page.
    """
    status = {
        "saved_schooljaren": [],   # list of {schooljaar, fetched_at}
        "last_attempt_at": "",
        "last_success_at": "",
        "last_error": "",
        "last_failed_schooljaren": [],
    }

    data, _err = _load_vakanties_file()
    if data and isinstance(data.get("schooljaren"), dict):
        for sj_key in sorted(data["schooljaren"].keys()):
            block = data["schooljaren"][sj_key]
            status["saved_schooljaren"].append({
                "schooljaar": sj_key,
                "fetched_at": (block.get("fetched_at", "") if isinstance(block, dict) else ""),
            })

    # Daemon writes state at data/vakanties_fetch_state.json. Read it
    # directly here rather than importing daemon code (which pulls in
    # pygame and would slow down the settings render).
    state_path = os.path.join(DATA_DIR, "vakanties_fetch_state.json")
    try:
        with open(state_path, "r", encoding="utf-8") as f:
            state = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        state = {}

    status["last_attempt_at"] = state.get("last_attempt_at", "")
    status["last_success_at"] = state.get("last_success_at", "")
    status["last_error"] = state.get("last_error", "")
    status["last_failed_schooljaren"] = state.get("last_failed_schooljaren", []) or []

    return status

@app.route("/audio/<path:filename>")
@ui_login_required
def serve_audio(filename):
    ensure_dirs()
    return send_from_directory(AUDIO_DIR, filename)

# -- Roosters --
@app.route("/roosters", methods=["GET"])
@ui_login_required
def roosters():
    ensure_dirs()
    roosters = load_json(ROOSTERS_PATH, default_roosters_obj())
    geluiden = list_audio()
    return render_template(
        "roosters.html",
        tab="roosters",
        csrf_token=get_csrf_token(),
        roosters=roosters,
        geluiden=geluiden,
    )

@app.route("/roosters/add", methods=["POST"])
@ui_login_required
def add_rooster():
    ensure_dirs()
    naam = (request.form.get("naam") or "").strip()
    if not naam:
        flash("Naam van rooster is verplicht.")
        return redirect(url_for("roosters"))
    # Validate against NAME_RE so the rooster name can be used safely
    # everywhere it ends up (dropdown values, JSON keys, log lines).
    # Without this, a user could create '!off' which collides with
    # the silence sentinel in the agenda dropdown — the rooster would
    # appear as an option but selecting it would be misinterpreted as
    # an explicit silence override. The regex also blocks weirdness
    # like '../', '<script>', newlines, etc.
    if not NAME_RE.match(naam):
        flash("Ongeldige naam. Gebruik 1–35 tekens: letters, cijfers, spatie, _ of -.")
        return redirect(url_for("roosters"))

    with locked_json(ROOSTERS_PATH, default_roosters_obj()) as (roosters, save):
        if naam in roosters:
            flash("Er bestaat al een rooster met deze naam.")
            return redirect(url_for("roosters"))

        kopieer = "kopieer_van_eerste" in request.form
        if kopieer and roosters:
            first_name = next(iter(roosters.keys()))
            roosters[naam] = normalize_and_sort_moments(roosters[first_name])
        else:
            roosters[naam] = []

        save(roosters)

    log_event("ui", {"action": "add_rooster", "rooster": naam})
    flash(f"Rooster '{naam}' aangemaakt.")
    return redirect(url_for("roosters"))

@app.route("/roosters/<rooster>/delete", methods=["POST"])
@ui_login_required
def delete_rooster(rooster):
    ensure_dirs()
    with locked_json(ROOSTERS_PATH, default_roosters_obj()) as (roosters, save):
        if rooster not in roosters:
            flash("Onbekend rooster.")
            return redirect(url_for("roosters"))

        # Before deleting: check if the rooster is still used somewhere.
        # Without this check, references in standaardweek.json and dagplanning.json
        # would point to a deleted rooster — in the UI you'd still see the name,
        # but no bell would ring (silent bug). We deliberately choose block-and-warn
        # instead of cascading delete: you don't want a click in the Roosters
        # screen to silently remove days from the agenda. The user must first
        # manually remove those references in Standaardweek and Agenda, then retry.
        # These two reads don't need their own lock: save_json is atomic, so a
        # reader sees either the old or the new file, never partial. The check
        # is best-effort against very recent writes, not a guarantee.
        stdweek = load_json(STANDAARDWEEK_PATH, default_standaardweek_obj())
        dagplanning = load_json(DAGPLANNING_PATH, default_dagplanning_obj())

        gebruikt_in_stdweek = [dag for dag, r in stdweek.items() if r == rooster]
        gebruikt_in_dagplanning = sorted(d for d, r in dagplanning.items() if r == rooster)

        if gebruikt_in_stdweek or gebruikt_in_dagplanning:
            delen = []
            if gebruikt_in_stdweek:
                delen.append(f"Standaardweek ({', '.join(gebruikt_in_stdweek)})")
            if gebruikt_in_dagplanning:
                voorb = ", ".join(gebruikt_in_dagplanning[:3])
                meer = "" if len(gebruikt_in_dagplanning) <= 3 else f" en {len(gebruikt_in_dagplanning) - 3} meer"
                delen.append(f"Agenda ({voorb}{meer})")
            flash(
                f"Rooster '{rooster}' is nog in gebruik bij: {'; '.join(delen)}. "
                f"Haal deze verwijzingen eerst weg voordat je het rooster verwijdert."
            )
            return redirect(url_for("roosters"))

        del roosters[rooster]
        save(roosters)

    log_event("ui", {"action": "delete_rooster", "rooster": rooster})
    flash(f"Rooster '{rooster}' verwijderd.")
    return redirect(url_for("roosters"))

@app.route("/roosters/<rooster>/add-moment", methods=["POST"])
@ui_login_required
def add_moment(rooster):
    ensure_dirs()

    # Validate form input outside the lock — no need to block other
    # writers while we check for empty fields.
    tijd_raw = request.form.get("tijd", "")
    tijd = normalize_time(tijd_raw)
    naam = (request.form.get("naam") or "").strip()
    bestand = (request.form.get("bestand") or "").strip()

    if not tijd:
        flash("Tijd moet in formaat UU:MM (bijv. 8:05 of 08:05).")
        return redirect(url_for("roosters"))
    if not naam:
        flash("Naam is verplicht.")
        return redirect(url_for("roosters"))
    if not bestand:
        flash("Kies een geluidsbestand.")
        return redirect(url_for("roosters"))

    # Optional warning fields. Empty/0 → no warning. The form sends
    # both, the user just leaves them at defaults if they don't want
    # a warning. We validate ranges here so the user gets a flash
    # message; normalize_and_sort_moments() also defends downstream.
    warn_min_raw = (request.form.get("warn_min") or "").strip()
    warn_bestand = (request.form.get("warn_bestand") or "").strip()
    warn_min: int = 0
    if warn_min_raw:
        try:
            warn_min = int(warn_min_raw)
        except ValueError:
            flash("Waarschuwing: minuten moeten een getal zijn.")
            return redirect(url_for("roosters"))
        if not (0 <= warn_min <= 60):
            flash("Waarschuwing: minuten moeten tussen 0 en 60 liggen.")
            return redirect(url_for("roosters"))
    if warn_min > 0 and not warn_bestand:
        flash("Kies een geluid voor de waarschuwingsbel, of zet 'minuten eerder' op 0.")
        return redirect(url_for("roosters"))

    new_moment = {"tijd": tijd, "naam": naam, "bestand": bestand}
    if warn_min > 0 and warn_bestand:
        new_moment["warn_min"] = warn_min
        new_moment["warn_bestand"] = warn_bestand

    with locked_json(ROOSTERS_PATH, default_roosters_obj()) as (roosters, save):
        if rooster not in roosters:
            flash("Onbekend rooster.")
            return redirect(url_for("roosters"))

        roosters[rooster].append(new_moment)
        roosters[rooster] = normalize_and_sort_moments(roosters[rooster])
        save(roosters)

    log_event(
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
    flash(f"Moment toegevoegd aan '{rooster}'.")
    return redirect(url_for("roosters"))

@app.route("/roosters/<rooster>/delete-moment/<int:index>", methods=["POST"])
@ui_login_required
def delete_moment(rooster, index):
    ensure_dirs()
    with locked_json(ROOSTERS_PATH, default_roosters_obj()) as (roosters, save):
        if rooster not in roosters:
            flash("Onbekend rooster.")
            return redirect(url_for("roosters"))
        moments = roosters[rooster]
        if 0 <= index < len(moments):
            removed = moments.pop(index)
            roosters[rooster] = normalize_and_sort_moments(moments)
            save(roosters)
            log_event("ui", {"action": "delete_moment", "rooster": rooster, "tijd": removed.get("tijd",""), "naam": removed.get("naam","")})
            flash(f"Moment '{removed.get('naam','')}' verwijderd uit '{rooster}'.")
        else:
            flash("Onbekende regel.")
    return redirect(url_for("roosters"))

# -- Standaardweek --
@app.route("/standaardweek", methods=["GET", "POST"])
@ui_login_required
def standaardweek():
    ensure_dirs()

    if request.method == "POST":
        # Read roosters without a lock — best-effort validation against
        # currently known roosters. The lock on standaardweek is what
        # protects us from concurrent saves of the standard week itself.
        roosters = load_json(ROOSTERS_PATH, default_roosters_obj())
        with locked_json(STANDAARDWEEK_PATH, default_standaardweek_obj()) as (std, save):
            for key, _label in WEEKDAYS:
                keuze = (request.form.get(f"rooster_{key}") or "").strip()
                if keuze and keuze not in roosters:
                    flash(f"'{keuze}' bestaat niet als rooster; overslaan voor {_label}.")
                    continue
                std[key] = keuze
            save(std)
            log_event("ui", {"action": "save_standaardweek", "keuzes": std})
        flash("Standaardweek opgeslagen.")
        return redirect(url_for("standaardweek"))

    roosters = load_json(ROOSTERS_PATH, default_roosters_obj())
    std = load_json(STANDAARDWEEK_PATH, default_standaardweek_obj())
    opties = list(roosters.keys())

    return render_template(
        "standaardweek.html",
        tab="standaardweek",
        csrf_token=get_csrf_token(),
        weekdagen=WEEKDAYS,
        huidige=std,
        opties=opties,
    )

# -- Agenda (per-date override of standaardweek) --
@app.route("/agenda", methods=["GET", "POST"])
@ui_login_required
def agenda():
    ensure_dirs()
    roosters = load_json(ROOSTERS_PATH, default_roosters_obj())
    dagplanning = load_json(DAGPLANNING_PATH, default_dagplanning_obj())
    standaardweek = load_json(STANDAARDWEEK_PATH, default_standaardweek_obj())
    weken_uit = load_json(WEEKDISABLE_PATH, default_weken_uit_obj())

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
        # second) is fixed across all writers so there's no risk of
        # deadlock between concurrent requests. Currently this is the
        # only multi-file write path; if more are added, keep the same
        # alphabetical-by-path lock order.
        with locked_json(DAGPLANNING_PATH, default_dagplanning_obj()) as (dag_state, save_dag), \
             locked_json(WEEKDISABLE_PATH, default_weken_uit_obj()) as (_wk_state, save_wk):

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
                        flash(f"Ongeldig rooster voor {datum}: '{waarde}' bestaat niet. Overgeslagen.")

            # Update weeks off
            today = date.today()
            first_monday = today - timedelta(days=today.weekday())
            weeks_list = [first_monday + timedelta(weeks=i) for i in range(52)]

            new_weken_uit = {}
            for wk_start in weeks_list:
                y, w, _ = wk_start.isocalendar()
                wk_key = f"{y}-W{w:02d}"
                if f"week_off[{wk_key}]" in request.form:
                    new_weken_uit[wk_key] = True

            # Drop dagplanning entries from the past so the file doesn't
            # grow unbounded. Done at save time (rather than via a cron)
            # because save is the natural choke-point — the user just
            # made an explicit edit, so doing housekeeping here is the
            # least surprising moment to lose stale data.
            updated_dagplanning = prune_past_dates(updated_dagplanning, today)

            save_dag(updated_dagplanning)
            save_wk(new_weken_uit)

        # Logging + feedback
        dagen_keys = sorted(updated_dagplanning.keys())
        weken_keys = sorted(new_weken_uit.keys())
        log_event("ui", {
            "action": "save_agenda",
            "dagen_count": len(dagen_keys),
            "dagen_first": dagen_keys[0] if dagen_keys else "",
            "dagen_last": dagen_keys[-1] if dagen_keys else "",
            "weken_uit_count": len(weken_keys),
        })
        flash("Agenda opgeslagen.")
        return redirect(url_for("agenda"))

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
                            change anything — keeps the UI honest about
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
        y, w, _ = wk_start.isocalendar()
        wk_key = f"{y}-W{w:02d}"
        off = bool(weken_uit.get(wk_key, False))
        days = [wk_start + timedelta(days=i) for i in range(5)]  # Ma..Vr
        weeks.append({
            "key": wk_key,
            "off": off,
            "range": f"{days[0].strftime('%d-%m-%Y')} .. {days[-1].strftime('%d-%m-%Y')}",
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
        csrf_token=get_csrf_token(),
        weeks=weeks,
        opties=opties,
        vakanties_path_exists=os.path.exists(VAKANTIES_PATH),
        vakantieregio=s.vakantieregio,
        vakanties_scrape_enabled=s.vakanties_scrape_enabled,
    )

def _load_vakanties_file() -> tuple[Optional[dict], Optional[str]]:
    """Read and migrate data/vakanties.json. Returns (data, error_msg).

    On a missing file, returns (None, None) — the caller decides whether
    that's an error in their context (import: yes; status display: no).
    On parse failure, returns (None, error message). On success,
    returns the migrated multi-year dict and None.
    """
    if not os.path.exists(VAKANTIES_PATH):
        return None, None
    try:
        with open(VAKANTIES_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except json.JSONDecodeError as e:
        return None, f"vakantiebestand is geen geldige JSON: {e}"
    except Exception as e:
        return None, f"vakantiebestand kon niet worden gelezen: {e}"

    # Lazy import to keep webinterface free of beautifulsoup4 unless
    # somebody actually touches the vakanties path.
    import vakanties_fetcher
    return vakanties_fetcher.migrate_legacy_format(raw), None


@app.route("/agenda/import-vakanties", methods=["POST"])
@ui_login_required
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
    and safe to mix with manual 'Bel uit' checkboxes.
    """
    # Master-switch enforcement (see refresh_vakanties for rationale).
    if not Settings.load().vakanties_scrape_enabled:
        flash("Vakantie-scraping is uitgeschakeld in Voorkeuren.")
        return redirect(url_for("agenda"))
    ensure_dirs()

    data, err = _load_vakanties_file()
    if err is not None:
        flash(f"Importeren mislukt: {err}")
        return redirect(url_for("agenda"))
    if data is None:
        flash(
            f"Geen vakantiebestand gevonden ({VAKANTIES_PATH}). "
            f"Klik 'Verversen van rijksoverheid.nl' om het op te halen."
        )
        return redirect(url_for("agenda"))

    schooljaren = data.get("schooljaren", {})
    if not isinstance(schooljaren, dict) or not schooljaren:
        flash(
            "Vakantiebestand bevat geen 'schooljaren'. Klik "
            "'Verversen van rijksoverheid.nl' om opnieuw op te halen."
        )
        return redirect(url_for("agenda"))

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
            flash(
                f"Geen schooljaren in het bestand bevatten regio '{regio}'. "
                f"Aanwezige schooljaren: {', '.join(sorted(schooljaren.keys()))}."
            )
        else:
            flash(
                f"Geen weken om te markeren voor regio {regio}. "
                f"Controleer het vakantiebestand ({len(skipped)} ongeldige entries)."
            )
        return redirect(url_for("agenda"))

    # Merge into existing weken_uit under the file lock so a concurrent
    # agenda-save doesn't lose either the manual edits or the import.
    with locked_json(WEEKDISABLE_PATH, default_weken_uit_obj()) as (state, save):
        for wk in new_weeks:
            state[wk] = True
        save(state)

    log_event("ui", {
        "action": "import_vakanties",
        "regio": regio,
        "schooljaren": schooljaren_processed,
        "weken_count": len(new_weeks),
        "skipped_count": len(skipped),
    })

    msg = (
        f"{len(new_weeks)} week(weken) gemarkeerd als 'Bel uit' (regio {regio}, "
        f"uit {len(schooljaren_processed)} schooljaar/jaren: "
        f"{', '.join(schooljaren_processed)})."
    )
    if skipped:
        voorb = "; ".join(skipped[:3])
        meer = "" if len(skipped) <= 3 else f" en {len(skipped) - 3} meer"
        msg += f" Overgeslagen: {voorb}{meer}."
    flash(msg)
    return redirect(url_for("agenda"))

@app.route("/agenda/refresh-vakanties", methods=["POST"])
@ui_login_required
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
        flash("Vakantie-scraping is uitgeschakeld in Voorkeuren.")
        return redirect(url_for("agenda"))
    # Lazy import: vakanties_fetcher pulls in beautifulsoup4 and makes
    # a network call. The agenda render path doesn't need either, so
    # keeping the import inside the handler keeps cold-start cheaper
    # for the 99% of requests that don't refresh.
    import vakanties_fetcher

    today = date.today()
    targets = vakanties_fetcher.schooljaren_to_fetch(today)

    # Read the existing file (if any) so we can preserve last-good
    # data for any year that fails this round.
    previous, prev_err = _load_vakanties_file()
    if prev_err:
        # Treat parse-error of existing file as 'no prior data' rather
        # than aborting — the refresh's whole point is to write a
        # clean file. But surface it so the admin knows the old file
        # was bad.
        flash(f"Bestaand vakantiebestand kon niet gelezen worden ({prev_err}); wordt overschreven.")
        previous = None

    successes, failures = vakanties_fetcher.fetch_and_parse_multi(targets)

    if not successes:
        # Total failure: don't touch the existing file. Tell the admin
        # what happened so they can debug (network? parser? page?).
        first_err = failures[0][1] if failures else "unknown"
        log_event("ui", {
            "action": "refresh_vakanties_error",
            "targets": targets,
            "failures": [{"schooljaar": s, "error": e} for s, e in failures],
        })
        flash(
            f"Verversen mislukt voor alle {len(targets)} schooljaren. "
            f"Eerste fout: {first_err}"
        )
        return redirect(url_for("agenda"))

    payload = vakanties_fetcher.combined_payload(successes, previous=previous)
    vakanties_fetcher.write_atomically(VAKANTIES_PATH, payload)

    log_event("ui", {
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
    return redirect(url_for("agenda"))

@app.route("/healthz", methods=["GET"])
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
        os.makedirs(DATA_DIR, exist_ok=True)
        probe = os.path.join(DATA_DIR, ".healthz_probe")
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
        os.listdir(AUDIO_DIR)
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
    hb = get_daemon_heartbeat()
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

@app.route("/now", methods=["GET"])
def now_page():
    return render_template("now.html")


@app.route("/api/now", methods=["GET"])
def api_now():
    """JSON shape: see next_bell_for_now(). 'bell' is null when no
    upcoming bell today; the page treats that as 'geen bel meer
    vandaag'. Always 200 so the JS doesn't have to special-case
    network errors vs no-bell.
    """
    bell = next_bell_for_now(datetime.now())
    return jsonify({
        "now": datetime.now().isoformat(timespec="seconds"),
        "bell": bell,
    }), 200


@app.route("/api/effectief-rooster", methods=["GET"])
@auth.login_required
def api_effectief_rooster():
    """
    Return the effective schedule for a given day.
    Query params:
      - datum=YYYY-MM-DD (optional, default today)
      - empty_204=1       -> return 204 for empty schedule/week off
    """
    ensure_dirs()

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

    roosters = load_json(ROOSTERS_PATH, default_roosters_obj())
    dagplanning = load_json(DAGPLANNING_PATH, default_dagplanning_obj())
    standaardweek = load_json(STANDAARDWEEK_PATH, default_standaardweek_obj())
    weken_uit = load_json(WEEKDISABLE_PATH, default_weken_uit_obj())

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

# -- Geluiden --
@app.route("/geluiden", methods=["GET"])
@ui_login_required
def geluiden():
    ensure_dirs()
    files = list_audio()

    # Read settings for accept/hint
    s = Settings.load()
    allowed_exts = [e.lower() for e in s.allowed_extensions]
    accept_attr = ",".join(allowed_exts)

    return render_template(
        "geluiden.html",
        tab="geluiden",
        csrf_token=get_csrf_token(),
        files=files,
        allowed_exts=allowed_exts,
        accept_attr=accept_attr,
        max_mb=s.max_file_size_mb,
    )

@app.route("/geluiden/upload", methods=["POST"])
@ui_login_required
def geluiden_upload():
    ensure_dirs()

    s = Settings.load()
    max_bytes = int(s.max_file_size_mb) * 1024 * 1024
    allowed_exts = tuple(e.lower() for e in s.allowed_extensions)

    base = (request.form.get("naam") or "").strip()
    if "file" not in request.files:
        flash("Geen bestand ontvangen.")
        return redirect(url_for("geluiden"))

    f = request.files["file"]
    if not f or f.filename == "":
        flash("Geen bestand geselecteerd.")
        return redirect(url_for("geluiden"))

    # Extension + validation
    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in allowed_exts:
        flash(f"Alleen bestanden met deze extensies zijn toegestaan: {', '.join(allowed_exts)}.")
        return redirect(url_for("geluiden"))

    filename = safe_audio_filename(base, ext)
    if not filename:
        flash("Ongeldige naam. Gebruik 1–35 tekens: letters, cijfers, spatie, _ of -.")
        return redirect(url_for("geluiden"))

    dest = os.path.join(AUDIO_DIR, filename)
    if os.path.exists(dest):
        flash("Er bestaat al een audiobestand met deze naam. Kies een andere naam.")
        return redirect(url_for("geluiden"))

    # Quick pre-check
    if request.content_length and request.content_length > max_bytes + 64 * 1024:
        flash(f"Bestand is groter dan de ingestelde limiet van {s.max_file_size_mb} MB.")
        return redirect(url_for("geluiden"))

    # Precise check
    try:
        f.stream.seek(0, os.SEEK_END)
        size = f.stream.tell()
        f.stream.seek(0)
    except Exception:
        data = f.read()
        size = len(data)
        f.stream.seek(0)

    if size > max_bytes:
        flash(f"Bestand is groter dan de ingestelde limiet van {s.max_file_size_mb} MB.")
        return redirect(url_for("geluiden"))

    try:
        f.save(dest)
    except Exception as e:
        flash(f"Kon bestand niet opslaan: {e}")
        return redirect(url_for("geluiden"))

    # Pre-flight: try to actually load the file via pygame, the same
    # library the daemon uses to play it. If pygame can't decode it
    # (corrupt MP3, wrong-format renamed-to-mp3, 0-byte file with the
    # right extension), we delete the bad upload and tell the user
    # immediately — instead of letting it sit in the audio dir until
    # the bell tries to ring at 8:30 and the daemon logs 'File not
    # found' or a decoder error to events.jsonl.
    ok, msg = _validate_audio_file(dest)
    if not ok:
        try:
            os.remove(dest)
        except OSError:
            pass  # if cleanup fails, the user can delete via the UI
        log_event("ui", {
            "action": "upload_audio_rejected",
            "filename": filename,
            "size": size,
            "reason": msg,
        })
        flash(f"Bestand afgewezen: {msg}")
        return redirect(url_for("geluiden"))

    log_event("ui", {
        "action": "upload_audio",
        "filename": filename,
        "size": size,
        "limit_mb": s.max_file_size_mb
    })
    flash(f"Upload geslaagd: {filename}")
    return redirect(url_for("geluiden"))

def _validate_audio_file(path: str) -> tuple[bool, str]:
    """Verify pygame can actually decode this file.

    Returns (is_valid, message). Message is shown to the user when
    invalid; ignored when valid.

    We use pygame.mixer.music.load (same as the daemon) so 'pygame
    accepts it' = 'the daemon will accept it'. No length check —
    a 30-minute file is unusual for a school bell but technically
    valid; the user can decide. We only block the case where pygame
    refuses outright (corrupt, wrong format despite extension, etc.).

    pygame is imported lazily (same reason as in _play_via_pygame:
    keeps it out of the test suite).
    """
    try:
        import pygame
        if not pygame.mixer.get_init():
            pygame.mixer.init()
        # load() raises pygame.error if the file is unparseable.
        # It does NOT actually play; load is just metadata + decoder
        # priming, which is exactly the validation we want.
        pygame.mixer.music.load(path)
        # Be polite: clear the loaded ref so we don't hold the file
        # open for a subsequent rename/delete.
        pygame.mixer.music.unload()
        return True, ""
    except Exception as e:
        return False, f"Pygame kan dit bestand niet lezen ({e})"

def _play_via_pygame(path: str, volume: float) -> None:
    """Play an audio file through the web worker's own pygame mixer.

    Used by the 'test bell' button on the geluiden page. The daemon
    has its own mixer instance for scheduled bells; this is a
    completely separate one in the Flask worker process. ALSA's
    default dmix plugin lets multiple processes share the audio
    device, so daemon + webinterface playing simultaneously is fine
    (a scheduled bell mid-test is rare but you'd just hear both).

    pygame is imported lazily so the test suite (which imports
    webinterface) doesn't need pygame on the testbench. On the Pi
    it's already installed via requirements.txt for the daemon.

    pygame.mixer.get_init() lets us avoid a global 'is_initialized'
    flag — pygame already tracks state for us. mixer.init() is
    idempotent in practice but get_init() avoids the work.
    """
    import pygame  # local import: only when the button is actually used
    if not pygame.mixer.get_init():
        pygame.mixer.init()
    pygame.mixer.music.set_volume(volume)
    pygame.mixer.music.load(path)
    pygame.mixer.music.play()

@app.route("/geluiden/play", methods=["POST"])
@ui_login_required
def geluiden_play():
    """Trigger immediate playback through the school's speakers.

    Logs the action so there's an audit trail — if someone abuses
    the button you can see who and when in the Logboek.
    """
    ensure_dirs()
    name = (request.form.get("filename") or "").strip()
    name = os.path.basename(name)
    path = os.path.join(AUDIO_DIR, name)
    if not os.path.isfile(path):
        flash("Bestand niet gevonden.")
        return redirect(url_for("geluiden"))

    try:
        v = max(0, min(100, int(Settings.load().volume_percent))) / 100.0
        _play_via_pygame(path, v)
        log_event("ui", {"action": "test_bell", "filename": name})
        flash(f"Test gestart: {name}")
    except Exception as e:
        # Common failure modes here: ALSA can't open the device
        # (audio config wrong), or the file isn't a format pygame
        # can decode. Surface the error so the admin can debug.
        log_event("ui", {"action": "test_bell_error", "filename": name, "error": str(e)})
        flash(f"Afspelen mislukt: {e}")
    return redirect(url_for("geluiden"))

@app.route("/geluiden/delete", methods=["POST"])
@ui_login_required
def geluiden_delete():
    ensure_dirs()
    name = (request.form.get("filename") or "").strip()
    name = os.path.basename(name)
    path = os.path.join(AUDIO_DIR, name)
    if not os.path.isfile(path):
        flash("Bestand niet gevonden.")
        return redirect(url_for("geluiden"))

    # Block-and-warn if the file is still used by any rooster moment.
    # Without this check, deletion would succeed silently and the
    # daemon would later log 'File not found' when that bell tried
    # to ring — the bell wouldn't go off and the user wouldn't know
    # why. Mirrors the same pattern as delete_rooster.
    roosters = load_json(ROOSTERS_PATH, default_roosters_obj())
    used_by = []
    for rooster_naam, momenten in roosters.items():
        for m in momenten:
            if (m.get("bestand") or "") == name:
                used_by.append(f"{rooster_naam}: {m.get('tijd','??:??')} {m.get('naam','')}".rstrip())
                break  # one mention per rooster is enough
    if used_by:
        voorb = "; ".join(used_by[:3])
        meer = "" if len(used_by) <= 3 else f" en {len(used_by) - 3} meer"
        flash(
            f"Geluid '{name}' wordt nog gebruikt door: {voorb}{meer}. "
            f"Verwijder of vervang deze momenten eerst voordat je het bestand verwijdert."
        )
        return redirect(url_for("geluiden"))

    try:
        os.remove(path)
        log_event("ui", {"action": "delete_audio", "filename": name})
        flash(f"Verwijderd: {name}")
    except Exception as e:
        flash(f"Kon niet verwijderen: {e}")
    return redirect(url_for("geluiden"))

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
