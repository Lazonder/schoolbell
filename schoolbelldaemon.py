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

# === Settings (can be reloaded) ===
settings = Settings.load()
print("[BOOT] Polling interval (sec):", settings.poll_interval_sec)
stop_event = threading.Event()

# Debug flag for extra verbose logging (e.g. 'No changes in day schedule'
# on every poll). Default off to keep journalctl clean.
DEBUG = os.getenv("SCHOOLBELL_DAEMON_DEBUG", "0").strip().lower() in ("1", "true", "yes", "on")

BACKOFF_ON_ERROR = 5 * 60      # wait 5 minutes on error

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

            # 2) Sleep time: use configured poll_interval_sec,
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
