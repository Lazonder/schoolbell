#!/usr/bin/env python3
import os, sys, time, json, threading, signal
from datetime import datetime, timezone, date, time as dtime, timedelta
import requests         # pip install requests
import schedule         # pip install schedule
import pygame           # pip install pygame
from settings_store import Settings

# === Paths / constant values ===
# Priority: SCHOOLBELL_BASE_DIR env var. Fallback: directory containing this
# file itself. Previously hardcoded "/home/pi/schoolbell".
BASE_DIR = os.environ.get("SCHOOLBELL_BASE_DIR") or os.path.dirname(os.path.abspath(__file__))
AUDIO_DIR = os.path.join(BASE_DIR, "static", "geluiden")
DATA_DIR = os.path.join(BASE_DIR, "data")
EVENTS_LOG_PATH = os.path.join(DATA_DIR, "events.jsonl")
VAKANTIES_PATH = os.path.join(DATA_DIR, "vakanties.json")
# State for the August 1 auto-refresh: tracks last attempt + outcome
# so we (a) don't refresh more than once per calendar year, and (b)
# can still see in the UI/log when the last successful refresh was.
VAKANTIES_FETCH_STATE_PATH = os.path.join(DATA_DIR, "vakanties_fetch_state.json")

# === Settings (can be reloaded) ===
settings = Settings.load()
print("[BOOT] Polling interval (sec):", settings.poll_interval_sec)
stop_event = threading.Event()

# Debug flag for extra verbose logging (e.g. 'No changes in day schedule'
# on every poll). Default off to keep journalctl clean.
DEBUG = os.getenv("SCHOOLBELL_DAEMON_DEBUG", "0").strip().lower() in ("1", "true", "yes", "on")

# Wait this long before retrying after a fetch error. Was 5 minutes,
# which is fine for mid-day recovery but too long at boot: if the
# daemon starts shortly before the first scheduled bell and the very
# first API call fails (e.g. nginx isn't ready yet), waiting 5 minutes
# could mean missing that first bell entirely. 1 minute gives us 5
# tries inside that 5-minute window — enough recovery time without
# blowing past the next scheduled moment.
BACKOFF_ON_ERROR = 60          # seconds

API_BASE = os.getenv("API_BASE", "http://127.0.0.1:5000")
API_USER = os.getenv("SCHOOLBELL_WEB_USER")
API_PASS = os.getenv("SCHOOLBELL_WEB_PASS")  # must be plaintext; daemon logs in to the web API

# Hard fail if credentials are missing. Otherwise _http.auth = (None, None),
# which causes requests to raise a cryptic traceback on first call. systemd
# gets a clear message in the log and restart loops are easier to diagnose.
if not API_USER or not API_PASS:
    print(
        "[FATAL] SCHOOLBELL_WEB_USER and/or SCHOOLBELL_WEB_PASS not set. "
        "See /etc/schoolbell/daemon.env (or run install.sh again).",
        file=sys.stderr,
    )
    sys.exit(1)

# --- HTTP session (auth + TLS verification) ---
# Default: requests validates the TLS cert against the system CA bundle.
# This only matters if API_BASE points to https; for http (install.sh
# default: http://127.0.0.1:5000) verify does nothing. If someone later
# uses a self-signed cert, they can set SCHOOLBELL_API_VERIFY_TLS=0.
# DO NOT disable for production HTTPS.
_http = requests.Session()
_http.auth = (API_USER, API_PASS)
if os.getenv("SCHOOLBELL_API_VERIFY_TLS", "1").strip().lower() in ("0", "false", "no", "off"):
    _http.verify = False
    # Suppress the InsecureRequestWarning that requests otherwise logs per call.
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    print("[WARN] TLS verification is DISABLED (SCHOOLBELL_API_VERIFY_TLS=0).", file=sys.stderr)

# --- Hot-reload flag ---
_reload_settings = False
def _on_sighup(signum, frame):
    global _reload_settings
    _reload_settings = True
    print("[SIGHUP] Reload settings requested.")

signal.signal(signal.SIGHUP, _on_sighup)

def _on_sigterm(signum, frame):
    print("[SIGTERM] Stop requested, daemon shutting down...")
    stop_event.set()
    try:
        pygame.mixer.music.stop()
    except Exception:
        pass
    try:
        pygame.mixer.quit()
    except Exception:
        pass

signal.signal(signal.SIGTERM, _on_sigterm)
signal.signal(signal.SIGINT, _on_sigterm)

# --- Logging of bell events (UI reads this in Logs) ---
def log_bell_event(data: dict):
    rec = {"ts": datetime.now(timezone.utc).isoformat(),
           "type": "bell", "data": data}
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(EVENTS_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:
        print("[WARN] log_bell_event failed:", e)

# === Audio initialization / volume ===
def init_audio():
    try:
        pygame.mixer.init()
        print("[INFO] pygame.mixer init ok")
        apply_playback_volume()  # set volume immediately from settings
    except Exception as e:
        print(f"[WARN] pygame.mixer init failed: {e}")

def apply_playback_volume():
    """Adjust only the pygame playback volume (0.0 .. 1.0)."""
    try:
        v = max(0, min(100, int(settings.volume_percent))) / 100.0
    except Exception:
        v = 0.7
    try:
        pygame.mixer.music.set_volume(v)
        print(f"[INFO] Playback volume set to {int(v*100)}% (pygame).")
    except Exception as e:
        print(f"[WARN] set_volume failed: {e}")

def speel_bel(bestand: str, naam: str = "", tijd: str = ""):
    """Play an audio file; log ok/error events to events.jsonl."""
    pad = os.path.join(AUDIO_DIR, bestand)
    print(f"[BELL] {datetime.now().strftime('%H:%M:%S')} → {pad}")
    if not os.path.exists(pad):
        msg = f"File not found: {pad}"
        print(f"[ERROR] {msg}")
        log_bell_event({"status": "error", "naam": naam, "tijd": tijd,
                        "bestand": bestand, "message": msg})
        return
    try:
        # Apply volume per playback (in case setting just changed)
        apply_playback_volume()

        pygame.mixer.music.load(pad)
        pygame.mixer.music.play()
        # Don't block; playback can continue in background.
        log_bell_event({"status": "ok", "naam": naam, "tijd": tijd, "bestand": bestand})
    except Exception as e:
        print(f"[ERROR] Playback failed: {e}")
        log_bell_event({"status": "error", "naam": naam, "tijd": tijd,
                        "bestand": bestand, "message": str(e)})

# === Schedule / reschedule ===
def cancel_all_jobs():
    schedule.clear('bells')
    print("[INFO] All bell jobs cancelled.")

def plan_job_at(hhmm: str, audio_file: str, label: str = ""):
    """Schedule a bell moment at HH:MM with optional label (name)."""
    try:
        datetime.strptime(hhmm, "%H:%M")
    except ValueError:
        print(f"[WARN] Invalid time format (HH:MM) for job: {hhmm} ({label})")
        return
    # Also pass name and time to speel_bel for clean logging
    schedule.every().day.at(hhmm).do(
        speel_bel, bestand=audio_file, naam=label, tijd=hhmm
    ).tag('bells')
    lbl = f" ({label})" if label else ""
    print(f"[PLAN] {hhmm} → {audio_file}{lbl}")

def apply_day_schedule(momenten: list[dict]):
    """
    momenten: list of dicts with keys:
      - tijd: "HH:MM"
      - naam: display name (optional)
      - bestand: audio filename
    """
    cancel_all_jobs()
    count = 0
    for m in sorted(momenten, key=lambda x: x.get("tijd", "")):
        t = (m.get("tijd") or "").strip()
        f = (m.get("bestand") or "").strip()
        nm = (m.get("naam") or "").strip()
        if t and f:
            plan_job_at(t, f, label=nm)
            count += 1
    print(f"[INFO] {count} moments scheduled for today.")

# === Helper functions for poller ===
def _local_next_midnight(now: datetime) -> datetime:
    tomorrow = now.date() + timedelta(days=1)
    return datetime.combine(tomorrow, dtime(0, 0, 0))

def fetch_effective_schedule() -> dict | None:
    """
    Fetch the effective schedule for today via the web interface API.
    Returns dict with keys: datum, bron, rooster_naam, momenten (list)
    or None if there is no schedule (HTTP 204 or empty set).
    """
    try:
        r = _http.get(f"{API_BASE}/api/effectief-rooster",
                      params={"empty_204": "1"}, timeout=10)
        if r.status_code == 204:
            return None
        r.raise_for_status()
        data = r.json()
        if not data.get("rooster_naam") or not data.get("momenten"):
            return None
        return data
    except requests.exceptions.SSLError as e:
        print(f"[WARN] fetch_effective_schedule TLS: {e}")
        log_bell_event({"status": "error", "message": f"TLS error: {e}"})
        raise
    except requests.exceptions.RequestException as e:
        print(f"[WARN] fetch_effective_schedule HTTP: {e}")
        log_bell_event({"status": "error", "message": f"HTTP error: {e}"})
        raise
    except Exception as e:
        print(f"[WARN] fetch_effective_schedule: {e}")
        log_bell_event({"status": "error", "message": f"Unexpected: {e}"})
        raise

def _signature(payload: dict) -> str:
    """Compact hash based on rooster_naam + momenten."""
    import hashlib
    core = {
        "rooster_naam": payload.get("rooster_naam", ""),
        "momenten": payload.get("momenten", []),
    }
    s = json.dumps(core, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(s.encode("utf-8")).hexdigest()

# === Vakanties refresh cadence ===
# Refresh the vakanties data when the last successful refresh was
# more than this many days ago. Picked at ~30 days for two reasons:
#   1. Rijksoverheid publishes ~5 schooljaren ahead, so a single
#      missed refresh is never a real problem — the data we have
#      remains valid for the bulk of the relevant year.
#   2. Anchoring on a specific date (e.g. August 1) made the trigger
#      brittle: a network blip or service restart on that one day
#      would cost us a year. With a rolling 30-day window we get
#      ~12 chances per year to pick up changes, including the
#      flip to a new schooljaar that target_schooljaar() does at
#      Aug 1 (we'll catch the new year on the next monthly cycle).
VAKANTIES_REFRESH_INTERVAL_DAYS = 30

def _load_vakanties_fetch_state() -> dict:
    """Read the small JSON state file. Missing/bad file → empty dict."""
    try:
        with open(VAKANTIES_FETCH_STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    except Exception as e:
        print(f"[WARN] Could not read {VAKANTIES_FETCH_STATE_PATH}: {e}")
        return {}

def _save_vakanties_fetch_state(state: dict) -> None:
    """Atomically persist the fetch-state. Same tmp+os.replace pattern
    as elsewhere — robust against power loss mid-write."""
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp = VAKANTIES_FETCH_STATE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, VAKANTIES_FETCH_STATE_PATH)
    except Exception as e:
        print(f"[WARN] Could not write {VAKANTIES_FETCH_STATE_PATH}: {e}")

def _should_refresh_vakanties_today(today: date, state: dict) -> bool:
    """Should we attempt a vakanties refresh on this date?

    Rule: refresh if the last successful refresh was more than
    VAKANTIES_REFRESH_INTERVAL_DAYS ago (or never). Corrupt /
    unparseable state is treated as 'never refreshed'.

    No special-cased calendar date. The schooljaar boundary is
    handled implicitly: target_schooljaar() flips on August 1, so
    the next monthly refresh after that automatically picks up
    the new year. Rijksoverheid publishes 5 years ahead anyway,
    so we don't need a tight day-of-year anchor.
    """
    last_iso = state.get("last_success_at", "")
    if not last_iso:
        return True
    try:
        last_date = datetime.fromisoformat(last_iso.replace("Z", "+00:00")).date()
    except ValueError:
        # Corrupt timestamp -> treat as never refreshed. Better to
        # try once than to silently never refresh again.
        return True
    return (today - last_date).days >= VAKANTIES_REFRESH_INTERVAL_DAYS

def _maybe_refresh_vakanties() -> None:
    """If we're due for a vakanties refresh, fetch from rijksoverheid.nl
    and overwrite vakanties.json.

    'Due' = 30+ days since last successful refresh, or never refreshed.
    On any failure, leave the existing vakanties.json untouched and
    record the error in the state file. Successes record the schooljaar
    that was fetched, so the UI can later show 'last refresh: YYYY-MM-DD'.
    """
    today = date.today()
    state = _load_vakanties_fetch_state()
    if not _should_refresh_vakanties_today(today, state):
        return

    # Lazy import keeps beautifulsoup4 out of the daemon's hot path
    # for users who don't run the August 1 refresh (or whose Pi
    # reboots more often than once a year).
    try:
        import vakanties_fetcher
    except ImportError as e:
        print(f"[WARN] vakanties_fetcher not importable: {e}")
        return

    schooljaar = vakanties_fetcher.target_schooljaar(today)
    print(f"[INFO] Vakanties refresh due: fetching {schooljaar} from rijksoverheid.nl")

    now_iso = datetime.now(timezone.utc).isoformat()
    state["last_attempt_at"] = now_iso
    state["last_attempt_schooljaar"] = schooljaar

    try:
        result = vakanties_fetcher.fetch_and_parse(schooljaar)
        vakanties_fetcher.write_atomically(VAKANTIES_PATH, result.to_json_obj())
    except Exception as e:
        state["last_error"] = str(e)
        _save_vakanties_fetch_state(state)
        print(f"[WARN] Vakanties refresh failed: {e}")
        log_bell_event({
            "status": "error",
            "message": f"vakanties refresh failed: {e}",
            "schooljaar": schooljaar,
        })
        return

    state["last_success_at"] = now_iso
    state["last_success_schooljaar"] = schooljaar
    state["last_error"] = ""
    _save_vakanties_fetch_state(state)
    print(f"[INFO] Vakanties refresh OK for {schooljaar}")
    log_bell_event({
        "status": "ok",
        "message": "vakanties refresh OK",
        "schooljaar": schooljaar,
    })

# === Poll loop (with dynamic interval + SIGHUP reload) ===
def schedule_poller_loop():
    global settings, _reload_settings
    last_sig = None

    while not stop_event.is_set():
        now = datetime.now()

        # 1) Reload settings if SIGHUP received
        if _reload_settings:
            try:
                settings = Settings.load()
                print(f"[RELOAD] Settings reloaded: poll={settings.poll_interval_sec}s, volume={settings.volume_percent}%")
                apply_playback_volume()
            except Exception as e:
                print("[WARN] Settings reload failed:", e)
            _reload_settings = False

        # 2) Vakanties refresh check (~monthly). Cheap (one disk
        # read of a tiny state file plus a date comparison) so doing
        # it on every poll is fine; the date math gates the actual
        # network call so it only fires every VAKANTIES_REFRESH_
        # INTERVAL_DAYS.
        try:
            _maybe_refresh_vakanties()
        except Exception as e:
            # Defensive: never let a refresh failure kill the bell loop.
            print(f"[WARN] Unexpected error in _maybe_refresh_vakanties: {e}")

        try:
            data = fetch_effective_schedule()
            if data is None:
                sig = "NO-SCHEDULE"
                if sig != last_sig:
                    cancel_all_jobs()
                    print(f"[INFO] {date.today()}: no schedule active.")
                    last_sig = sig
            else:
                sig = _signature(data)
                if sig != last_sig:
                    apply_day_schedule(data["momenten"])
                    print(f"[INFO] Day schedule loaded: {data['rooster_naam']} ({len(data['momenten'])} moments)")
                    last_sig = sig
                elif DEBUG:
                    print("[DEBUG] No changes in day schedule.")

            # 3) Sleep time: use configured poll_interval_sec,
            #    but never sleep past midnight to pick up the new schedule in time.
            next_midnight = _local_next_midnight(now)
            to_midnight = max(1, int((next_midnight - now).total_seconds()))
            sleep_s = max(1, min(int(settings.poll_interval_sec), to_midnight))
            stop_event.wait(sleep_s)

        except Exception:
            # Error fetching/parsing → backoff, but cleanly stoppable
            stop_event.wait(BACKOFF_ON_ERROR)

# === Main ===
def main():
    print("[BOOT] Schoolbell daemon starting...")
    os.makedirs(AUDIO_DIR, exist_ok=True)
    init_audio()

    # Start the poller in a separate thread
    t = threading.Thread(target=schedule_poller_loop, name="SchedulePoller", daemon=True)
    t.start()

    # Schedule loop (keeps running pending jobs)
    while not stop_event.is_set():
        schedule.run_pending()
        stop_event.wait(1)

if __name__ == "__main__":
    main()
