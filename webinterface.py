#!/usr/bin/env python3
import os, json, re, secrets, fcntl
from contextlib import contextmanager
from datetime import date, timedelta, datetime, time, timezone
from dataclasses import asdict
from flask import Flask, request, redirect, url_for, flash, send_from_directory, session, jsonify, abort, render_template
from flask_httpauth import HTTPBasicAuth
from werkzeug.security import check_password_hash
from functools import wraps
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

# Form-level sentinel sent by the agenda dropdown when the user picks
# '— geen bel —' for a date. Stored as JSON null in dagplanning to mean
# 'explicit silence override on this date'. The '!' prefix is outside
# NAME_RE so it can never collide with a real rooster name.
DAGPLANNING_SILENT_FORM_VALUE = "!off"

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

# Make `now()` available in all templates. Used e.g. in base.html for the
# footer year. Without this processor, `{{ now().year }}` would give an
# UndefinedError; previously there was a permanent-falsy dummy.
#
# theme_mode is also injected here so base.html can bake it server-side
# (avoids flash-of-wrong-theme and works on /login, where /api/settings
# would return 401 because the user isn't logged in yet).
@app.context_processor
def _inject_template_globals():
    try:
        mode = Settings.load().theme_mode
    except Exception:
        mode = "light"
    if mode not in ("light", "dark", "auto"):
        mode = "light"
    return {
        "now": datetime.now,
        "theme_mode": mode,
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

def _settings_validate_and_apply(payload):
    s = Settings.load()
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

    return s

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
    settings = _settings_validate_and_apply(request.get_json() or {})
    settings.save()
    return jsonify(asdict(settings)), 200

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
        cleaned.append({"tijd": tijd_norm, "naam": naam, "bestand": bestand})
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
    return render_template("settings.html", tab="settings", csrf_token=get_csrf_token())

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

    with locked_json(ROOSTERS_PATH, default_roosters_obj()) as (roosters, save):
        if rooster not in roosters:
            flash("Onbekend rooster.")
            return redirect(url_for("roosters"))

        roosters[rooster].append({"tijd": tijd, "naam": naam, "bestand": bestand})
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

    return render_template(
        "agenda.html",
        tab="agenda",
        csrf_token=get_csrf_token(),
        weeks=weeks,
        opties=opties
    )

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
        log_event("ui", {
            "action": "upload_audio",
            "filename": filename,
            "size": size,
            "limit_mb": s.max_file_size_mb
        })
        flash(f"Upload geslaagd: {filename}")
    except Exception as e:
        flash(f"Kon bestand niet opslaan: {e}")

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

# ---------- Start ----------
if __name__ == "__main__":
    ensure_dirs()
    if not os.path.exists(ROOSTERS_PATH):
        save_json(ROOSTERS_PATH, default_roosters_obj())
    if not os.path.exists(DAGPLANNING_PATH):
        save_json(DAGPLANNING_PATH, default_dagplanning_obj())
    if not os.path.exists(STANDAARDWEEK_PATH):
        save_json(STANDAARDWEEK_PATH, default_standaardweek_obj())
    if not os.path.exists(WEEKDISABLE_PATH):
        save_json(WEEKDISABLE_PATH, default_weken_uit_obj())
    app.run(host="127.0.0.1", port=5000) #, ssl_context=("certs/cert.pem", "certs/key.pem"))
