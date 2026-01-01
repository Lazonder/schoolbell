#!/usr/bin/env python3
import os, json, re, secrets
from datetime import date, timedelta, datetime, time, timezone
from dataclasses import asdict
from flask import Flask, request, redirect, url_for, flash, send_from_directory, session, jsonify, abort, render_template
from flask_httpauth import HTTPBasicAuth
from werkzeug.security import check_password_hash
from functools import wraps
from settings_store import Settings
from werkzeug.middleware.proxy_fix import ProxyFix

auth = HTTPBasicAuth()

# Haal credentials uit environment
ADMIN_USER = os.getenv("SCHOOLBELL_WEB_USER", "admin")
# Zet óf SCHOOLBELL_WEB_PWHASH (aanrader) óf tijdelijk SCHOOLBELL_WEB_PASS
ADMIN_HASH = os.getenv("SCHOOLBELL_WEB_PWHASH")      # bijv. pbkdf2:sha256:...
FALLBACK_PLAIN = os.getenv("SCHOOLBELL_WEB_PASS")    # alleen voor eerste test

# Naamvalidatie: 1–35 tekens, alleen letters/cijfers/spatie/_/-
NAME_RE = re.compile(r"^[A-Za-z0-9 _-]{1,35}$")

# === Padconfiguratie ===
BASE_DIR = "/home/pi/schoolbell"
DATA_DIR = os.path.join(BASE_DIR, "data")
AUDIO_DIR = os.path.join(BASE_DIR, "static", "geluiden")
WEEKDISABLE_PATH = os.path.join(DATA_DIR, "weken_uit.json")
ROOSTERS_PATH = os.path.join(DATA_DIR, "roosters.json")
DAGPLANNING_PATH = os.path.join(DATA_DIR, "dagplanning.json")
STANDAARDWEEK_PATH = os.path.join(DATA_DIR, "standaardweek.json")
EVENTS_LOG_PATH = os.path.join(DATA_DIR, "events.jsonl")  # gedeeld log (UI + daemon)

# === Flask ===
app = Flask(__name__)
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.jinja_env.auto_reload = True
app.secret_key = os.environ.get("SCHOOLBELL_SECRET", "dev-secret")
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
# Zet een ruime bovengrens. De echte limiet handhaven we per request via Settings.
app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024 * 1024  # 1 GiB
app.permanent_session_lifetime = timedelta(minutes=30)  # 30 minuten
app.config.update(
    SESSION_COOKIE_SECURE=True,
    SESSION_COOKIE_SAMESITE="Lax",
)

# Tijd-Regex: 24-uurs klok 00–23, met optionele seconden (HH:MM of HH:MM:SS)
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
        # onthoud waarheen we terug moeten
        nxt = request.full_path if request.query_string else request.path
        return redirect(url_for("login", next=nxt))
    return wrapper

@app.route("/login", methods=["GET", "POST"])
def login():
    if ui_logged_in():
        return redirect(url_for("roosters"))

    next_url = request.args.get("next") or request.form.get("next") or url_for("roosters")

    if request.method == "POST":
        u = (request.form.get("username") or "").strip()
        p = request.form.get("password") or ""
        if u == ADMIN_USER and _check_password(p):
            session.permanent = True
            session["user"] = ADMIN_USER
            return redirect(next_url)
        flash("Onjuiste inloggegevens.")

    return render_template(
        "login.html",
        next_url=next_url,
        admin_user=ADMIN_USER,
        csrf_token=get_csrf_token(),
        tab=None  # geen actieve tab op loginpagina
    )

@app.route("/logout", methods=["POST", "GET"])
def logout():
    session.pop("user", None)
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
    # Alleen POST's in de UI controleren; de API laat je daemon ongemoeid.
    if request.method == "POST":
        # exempt voor API endpoints (daemon gebruikt Basic Auth, geen CSRF)
        if request.path.startswith("/api/"):
            return
        # loginpagina laten we ook controleren (heeft hidden _csrf)
        form_token = request.form.get("_csrf", "")
        sess_token = session.get("csrf", "")
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
        return password == FALLBACK_PLAIN  # alleen voor eerste test
    return False

def require_admin(f):  # placeholder: koppel eventueel aan sessie/rol
    @wraps(f)
    def wrapper(*args, **kwargs):
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

    if "timezone" in payload:
        tz = str(payload["timezone"])
        s.timezone = tz

    return s

# -- Settings API (geen blueprint; eenvoudige app-routes) --
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
        print(f"[WARN] Kon {path} niet laden: {e}")
    return default

def save_json(path, obj):
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)

def list_audio():
    """Toon bestanden volgens allowed_extensions uit Settings."""
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
    """Valideer naam en plak de gekozen extensie eraan vast (ext bevat punt, bv '.mp3')."""
    base_no_ext = base_no_ext.strip()
    if not NAME_RE.match(base_no_ext):
        return ""
    if not (1 <= len(base_no_ext) <= 35):
        return ""
    return f"{base_no_ext}{ext}"

def normalize_time(t: str) -> str:
    """
    Accepteert '8:05', '08:05', '11:30:00', etc.
    Geeft altijd 'HH:MM' terug, of '' als de tijd ongeldig is.
    """
    t = (t or "").strip()
    if not TIME_RE.match(t):
        return ""
    parts = t.split(":")
    if len(parts) < 2:
        return ""
    hh, mm = parts[0], parts[1]
    # voorkom rare dingen als '007:03'
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
    # Standaard: geen rooster ingevuld
    return {k: "" for k, _ in WEEKDAYS}

def weekday_key(d: date) -> str:
    return ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"][d.weekday()]

def effectieve_rooster_naam_for_date(d: date, dagplanning: dict, standaardweek: dict) -> str:
    d_iso = d.isoformat()
    if d_iso in dagplanning and dagplanning[d_iso]:
        return dagplanning[d_iso]
    return (standaardweek or {}).get(weekday_key(d), "") or ""

def default_weken_uit_obj():
    return {}  # bijv. {"2025-W34": true}

def iso_week_key(d: date) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"

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
    #Lees de laatste 'limit' events uit EVENTS_LOG_PATH zonder het hele bestand te lezen.
    #max_bytes bepaalt hoeveel bytes vanaf het einde we bekijken.    
    try:
        with open(EVENTS_LOG_PATH, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            start = max(0, size - max_bytes)
            f.seek(start)
            chunk = f.read()

        # Als we niet bij 0 begonnen, kunnen we midden in een regel zitten -> drop eerste partial line
        lines = chunk.splitlines()
        if start > 0 and lines:
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
            if d_iso in dagplanning and dagplanning[d_iso]:
                rname = dagplanning[d_iso]
            else:
                rname = standaardweek.get(weekday_key(day), "")
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
    return redirect(url_for("roosters"))

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
    roosters = load_json(ROOSTERS_PATH, default_roosters_obj())
    naam = (request.form.get("naam") or "").strip()
    if not naam:
        flash("Naam van rooster is verplicht.")
        return redirect(url_for("roosters"))
    if naam in roosters:
        flash("Er bestaat al een rooster met deze naam.")
        return redirect(url_for("roosters"))

    kopieer = "kopieer_van_eerste" in request.form
    if kopieer and roosters:
        first_name = next(iter(roosters.keys()))
        roosters[naam] = normalize_and_sort_moments(roosters[first_name])
    else:
        roosters[naam] = []

    save_json(ROOSTERS_PATH, roosters)
    log_event("ui", {"action": "add_rooster", "rooster": naam})
    flash(f"Rooster '{naam}' aangemaakt.")
    return redirect(url_for("roosters"))

@app.route("/roosters/<rooster>/delete", methods=["POST"])
@ui_login_required
def delete_rooster(rooster):
    ensure_dirs()
    roosters = load_json(ROOSTERS_PATH, default_roosters_obj())
    if rooster in roosters:
        del roosters[rooster]
        save_json(ROOSTERS_PATH, roosters)
        log_event("ui", {"action": "delete_rooster", "rooster": rooster})
        flash(f"Rooster '{rooster}' verwijderd.")
    else:
        flash("Onbekend rooster.")
    return redirect(url_for("roosters"))

@app.route("/roosters/<rooster>/add-moment", methods=["POST"])
@ui_login_required
def add_moment(rooster):
    ensure_dirs()
    roosters = load_json(ROOSTERS_PATH, default_roosters_obj())
    if rooster not in roosters:
        flash("Onbekend rooster.")
        return redirect(url_for("roosters"))

    # Nieuw: rauwe tijd uit het formulier, daarna normaliseren
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

    roosters[rooster].append({"tijd": tijd, "naam": naam, "bestand": bestand})
    roosters[rooster] = normalize_and_sort_moments(roosters[rooster])
    save_json(ROOSTERS_PATH, roosters)

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
    roosters = load_json(ROOSTERS_PATH, default_roosters_obj())
    if rooster not in roosters:
        flash("Onbekend rooster.")
        return redirect(url_for("roosters"))
    moments = roosters[rooster]
    if 0 <= index < len(moments):
        removed = moments.pop(index)
        roosters[rooster] = normalize_and_sort_moments(moments)
        save_json(ROOSTERS_PATH, roosters)
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
    roosters = load_json(ROOSTERS_PATH, default_roosters_obj())
    std = load_json(STANDAARDWEEK_PATH, default_standaardweek_obj())

    if request.method == "POST":
        for key, _label in WEEKDAYS:
            keuze = (request.form.get(f"rooster_{key}") or "").strip()
            if keuze and keuze not in roosters:
                flash(f"'{keuze}' bestaat niet als rooster; overslaan voor {_label}.")
                continue
            std[key] = keuze
        save_json(STANDAARDWEEK_PATH, std)
        log_event("ui", {"action": "save_standaardweek", "keuzes": std})
        flash("Standaardweek opgeslagen.")
        return redirect(url_for("standaardweek"))

    opties = list(roosters.keys())

    return render_template(
        "standaardweek.html",
        tab="standaardweek",
        csrf_token=get_csrf_token(),
        weekdagen=WEEKDAYS,
        huidige=std,
        opties=opties,
    )

# -- Agenda (per datum overschrijft standaardweek) --
@app.route("/agenda", methods=["GET", "POST"])
@ui_login_required
def agenda():
    ensure_dirs()
    roosters = load_json(ROOSTERS_PATH, default_roosters_obj())
    dagplanning = load_json(DAGPLANNING_PATH, default_dagplanning_obj())
    standaardweek = load_json(STANDAARDWEEK_PATH, default_standaardweek_obj())
    weken_uit = load_json(WEEKDISABLE_PATH, default_weken_uit_obj())

    opties = [""] + list(roosters.keys())

    # --- POST: opslaan ---
    if request.method == "POST" and request.form.get("_action") == "bulk_save":
        # Dagplanning bijwerken
        updated_dagplanning = dagplanning.copy()
        for key in request.form.keys():
            if key.startswith("day[") and key.endswith("]"):
                datum = key[4:-1]
                waarde = (request.form.get(key) or "").strip()
                if waarde:
                    if waarde in roosters:
                        updated_dagplanning[datum] = waarde
                    else:
                        flash(f"Ongeldig rooster voor {datum}: '{waarde}' bestaat niet. Overgeslagen.")
                else:
                    updated_dagplanning.pop(datum, None)

        # Weken uit bijwerken
        today = date.today()
        first_monday = today - timedelta(days=today.weekday())
        weeks_list = [first_monday + timedelta(weeks=i) for i in range(52)]

        new_weken_uit = {}
        for wk_start in weeks_list:
            y, w, _ = wk_start.isocalendar()
            wk_key = f"{y}-W{w:02d}"
            if f"week_off[{wk_key}]" in request.form:
                new_weken_uit[wk_key] = True

        save_json(DAGPLANNING_PATH, updated_dagplanning)
        save_json(WEEKDISABLE_PATH, new_weken_uit)

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

    # --- GET: render data voor template ---
    today = date.today()
    first_monday = today - timedelta(days=today.weekday())
    weeks_list = [first_monday + timedelta(weeks=i) for i in range(52)]

    weekday_key_map = ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"]

    def selected_for_date(d: date) -> str:
        d_iso = d.isoformat()
        if d_iso in dagplanning and dagplanning[d_iso]:
            return dagplanning[d_iso]
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
        opties=opjes if (opjes := opties) else [""]
    )

@app.route("/api/effectief-rooster", methods=["GET"])
@auth.login_required
def api_effectief_rooster():
    """
    Geeft het effectieve rooster voor een bepaalde dag terug.
    Queryparams:
      - datum=YYYY-MM-DD (optioneel, standaard vandaag)
      - empty_204=1       -> stuur 204 terug bij leeg rooster/week uit
    """
    ensure_dirs()

    datum_qs = (request.args.get("datum") or "").strip()
    empty_204 = (request.args.get("empty_204") == "1")

    if datum_qs:
        try:
            d = datetime.strptime(datum_qs, "%Y-%m-%d").date()
        except ValueError:
            return {"error": "Ongeldige datum. Gebruik YYYY-MM-DD."}, 400
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

    if d_iso in dagplanning:
        rooster_naam = dagplanning[d_iso]
        bron = "dagplanning"
    else:
        wkkey = weekday_key(d)
        rooster_naam = standaardweek.get(wkkey, "")
        bron = "standaardweek"

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

    # Lees settings voor accept/hint
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

    # Extensie + validatie
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

    # Snelle pre-check
    if request.content_length and request.content_length > max_bytes + 64 * 1024:
        flash(f"Bestand is groter dan de ingestelde limiet van {s.max_file_size_mb} MB.")
        return redirect(url_for("geluiden"))

    # Nauwkeurige check
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
