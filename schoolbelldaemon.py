#!/usr/bin/env python3
import os, time, json, threading, signal
from datetime import datetime, timezone, date, time as dtime, timedelta
import requests         # pip install requests
import schedule         # pip install schedule
import pygame           # pip install pygame
from settings_store import Settings

# === Paden / constante waarden ===
BASE_DIR = "/home/pi/schoolbell"
AUDIO_DIR = os.path.join(BASE_DIR, "static", "geluiden")
DATA_DIR = os.path.join(BASE_DIR, "data")
EVENTS_LOG_PATH = os.path.join(DATA_DIR, "events.jsonl")

# === Instellingen (kunnen herladen worden) ===
settings = Settings.load()
print("[BOOT] Polling interval (sec):", settings.poll_interval_sec)

BACKOFF_ON_ERROR = 5 * 60      # 5 minuten wachten bij fout

API_BASE = os.getenv("API_BASE", "https://127.0.0.1:5000")
API_USER = os.getenv("SCHOOLBELL_WEB_USER", "admin")
API_PASS = os.getenv("SCHOOLBELL_WEB_PASS", "geheim123")  # PLAINTEXT wachtwoord

# --- HTTP session (auth + self-signed TLS accepteren) ---
_http = requests.Session()
_http.auth = (API_USER, API_PASS)
_http.verify = False  # self-signed cert accepteren; zet op True als je CA vertrouwt

# --- Hot-reload vlag ---
_reload_settings = False
def _on_sighup(signum, frame):
    global _reload_settings
    _reload_settings = True
    print("[SIGHUP] Reload settings aangevraagd.")

signal.signal(signal.SIGHUP, _on_sighup)

# --- Logging van bel-events (UI leest dit in Logboek) ---
def log_bell_event(data: dict):
    rec = {"ts": datetime.now(timezone.utc).isoformat(),
           "type": "bell", "data": data}
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(EVENTS_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:
        print("[WARN] log_bell_event failed:", e)

# === Audio initialisatie / volume ===
def init_audio():
    try:
        pygame.mixer.init()
        print("[INFO] pygame.mixer init ok")
        apply_playback_volume()  # zet meteen het volume volgens settings
    except Exception as e:
        print(f"[WARN] pygame.mixer init mislukte: {e}")

def apply_playback_volume():
    """Pas alleen de pygame playback volume aan (0.0 .. 1.0)."""
    try:
        v = max(0, min(100, int(settings.volume_percent))) / 100.0
    except Exception:
        v = 0.7
    try:
        pygame.mixer.music.set_volume(v)
        print(f"[INFO] Playback volume gezet op {int(v*100)}% (pygame).")
    except Exception as e:
        print(f"[WARN] set_volume mislukt: {e}")

def speel_bel(bestand: str, naam: str = "", tijd: str = ""):
    """Speel een audio-bestand af; log ok/error events naar events.jsonl."""
    pad = os.path.join(AUDIO_DIR, bestand)
    print(f"[BELL] {datetime.now().strftime('%H:%M:%S')} → {pad}")
    if not os.path.exists(pad):
        msg = f"Bestand niet gevonden: {pad}"
        print(f"[ERROR] {msg}")
        log_bell_event({"status": "error", "naam": naam, "tijd": tijd,
                        "bestand": bestand, "message": msg})
        return
    try:
        # Volume per afspeelbeurt toepassen (voor het geval de setting net is gewijzigd)
        apply_playback_volume()

        pygame.mixer.music.load(pad)
        pygame.mixer.music.play()
        # Niet blokkeren; playback mag op achtergrond doorgaan.
        log_bell_event({"status": "ok", "naam": naam, "tijd": tijd, "bestand": bestand})
    except Exception as e:
        print(f"[ERROR] Afspelen mislukt: {e}")
        log_bell_event({"status": "error", "naam": naam, "tijd": tijd,
                        "bestand": bestand, "message": str(e)})

# === Plannen / opnieuw plannen ===
def cancel_all_jobs():
    schedule.clear('bells')
    print("[INFO] Alle bel-jobs geannuleerd.")

def plan_job_at(hhmm: str, audio_file: str, label: str = ""):
    """Plan een belmoment om HH:MM met optionele label (naam)."""
    try:
        datetime.strptime(hhmm, "%H:%M")
    except ValueError:
        print(f"[WARN] Ongeldig tijdformaat (HH:MM) voor job: {hhmm} ({label})")
        return
    # Geef ook naam en tijd mee aan speel_bel voor nette logging
    schedule.every().day.at(hhmm).do(
        speel_bel, bestand=audio_file, naam=label, tijd=hhmm
    ).tag('bells')
    lbl = f" ({label})" if label else ""
    print(f"[PLAN] {hhmm} → {audio_file}{lbl}")

def apply_day_schedule(momenten: list[dict]):
    """
    momenten: lijst van dicts met keys:
      - tijd: "HH:MM"
      - naam: weergavenaam (optioneel)
      - bestand: audio-bestandsnaam
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
    print(f"[INFO] {count} momenten ingepland voor vandaag.")

# === Hulpfuncties voor poller ===
def _local_next_midnight(now: datetime) -> datetime:
    tomorrow = now.date() + timedelta(days=1)
    return datetime.combine(tomorrow, dtime(0, 0, 0))

def fetch_effective_schedule() -> dict | None:
    """
    Vraagt het effectieve rooster voor vandaag op via je webinterface-API.
    Geeft dict met keys: datum, bron, rooster_naam, momenten (lijst)
    of None als er geen rooster is (HTTP 204 of lege set).
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
    """Compacte hash op basis van rooster_naam + momenten."""
    import hashlib
    core = {
        "rooster_naam": payload.get("rooster_naam", ""),
        "momenten": payload.get("momenten", []),
    }
    s = json.dumps(core, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(s.encode("utf-8")).hexdigest()

# === Poll-loop (met dynamische interval + SIGHUP reload) ===
def schedule_poller_loop():
    global settings, _reload_settings
    last_sig = None

    while True:
        now = datetime.now()

        # 1) Herlaad settings als SIGHUP ontvangen is
        if _reload_settings:
            try:
                settings = Settings.load()
                print(f"[RELOAD] Settings herladen: poll={settings.poll_interval_sec}s, volume={settings.volume_percent}%")
                apply_playback_volume()
            except Exception as e:
                print("[WARN] Settings reload faalde:", e)
            _reload_settings = False

        try:
            data = fetch_effective_schedule()
            if data is None:
                sig = "NO-SCHEDULE"
                if sig != last_sig:
                    cancel_all_jobs()
                    print(f"[INFO] {date.today()}: geen rooster actief.")
                    last_sig = sig
            else:
                sig = _signature(data)
                if sig != last_sig:
                    apply_day_schedule(data["momenten"])
                    print(f"[INFO] Dagrooster geladen: {data['rooster_naam']} ({len(data['momenten'])} momenten)")
                    last_sig = sig
                else:
                    print("[DEBUG] Geen wijzigingen in dagrooster.")

            # 2) Slaaptijd: neem de ingestelde poll_interval_sec,
            #    maar slaap nooit voorbij middernacht om het nieuwe dagrooster tijdig te pakken.
            next_midnight = _local_next_midnight(now)
            to_midnight = max(1, int((next_midnight - now).total_seconds()))
            sleep_s = max(1, min(int(settings.poll_interval_sec), to_midnight))
            time.sleep(sleep_s)

        except Exception:
            # Fout bij ophalen/parsen → korte backoff
            time.sleep(BACKOFF_ON_ERROR)

# === Main ===
def main():
    print("[BOOT] Schoolbeldaemon start...")
    os.makedirs(AUDIO_DIR, exist_ok=True)
    init_audio()

    # Start de poller in een aparte thread
    t = threading.Thread(target=schedule_poller_loop, name="SchedulePoller", daemon=True)
    t.start()

    # Schedule-loop (blijft pending jobs draaien)
    while True:
        schedule.run_pending()
        time.sleep(1)

if __name__ == "__main__":
    main()
