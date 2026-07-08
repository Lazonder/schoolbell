#!/usr/bin/env python3
import os, sys, json, threading, signal
from datetime import datetime, timezone, date, time as dtime, timedelta
import requests         # pip install requests
import schedule         # pip install schedule
import pygame           # pip install pygame
from settings_store import CONFIG_PATH, Settings

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
# Tiny "I'm alive" file the daemon updates every poll iteration. The
# webinterface reads it to show a green/red dot in the header so the
# admin can see at a glance whether the daemon is still running. The
# file is intentionally lightweight (one ISO timestamp) so the write
# cost is negligible even when poll_interval_sec is set low.
DAEMON_HEARTBEAT_PATH = os.path.join(DATA_DIR, "daemon_heartbeat.json")

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
# tries inside that 5-minute window. Enough recovery time without
# missing the next scheduled moment.
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


def _config_mtime() -> float | None:
    """Modification time of config.json, or None when it's missing.

    Used by the poll loop to detect that the webinterface saved new
    settings. settings_store.save() writes atomically via tmp +
    os.replace, and a replace always bumps the file's mtime, so
    'mtime changed' reliably means 'content may have changed'.
    """
    try:
        return os.stat(CONFIG_PATH).st_mtime
    except OSError:
        return None
def _on_sighup(signum, frame):
    """Handle the SIGHUP signal from the operating system.

    SIGHUP is a signal that an admin can send to ask the daemon to
    reload its settings without restarting. We just set a flag here;
    the main loop checks the flag and does the actual reload.
    """
    global _reload_settings
    _reload_settings = True
    print("[SIGHUP] Reload settings requested.")

signal.signal(signal.SIGHUP, _on_sighup)

def _on_sigterm(signum, frame):
    """Handle a stop request from the operating system.

    Called when the system sends SIGTERM (normal shutdown) or when the
    user presses Ctrl-C (SIGINT). We stop any audio that is playing and
    set the stop flag so the main loop exits cleanly.
    """
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

# --- Heartbeat ----------------------------------------------------------------
def _write_heartbeat() -> None:
    """Write the current UTC timestamp to the heartbeat file.

    Called once per poller iteration. Intentionally simple (no
    steps where we write to a tmp file then rename). The reader
    tolerates missing/corrupt files by treating them as 'no recent
    heartbeat', and a partially written file gets overwritten on
    the next iteration.
    """
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        payload = {
            "last_poll_at": datetime.now(timezone.utc).isoformat(),
        }
        with open(DAEMON_HEARTBEAT_PATH, "w", encoding="utf-8") as f:
            json.dump(payload, f)
    except Exception as e:
        # Non-fatal: a heartbeat write failure shouldn't kill the bell loop.
        print(f"[WARN] heartbeat write failed: {e}")

# --- Logging of bell events (UI reads this in Logs) ---
def log_bell_event(data: dict):
    """Write a bell event to the shared log file (events.jsonl).

    Called after every bell attempt, whether it succeeded or failed.
    The log file is shared with the web interface so the admin can
    see past bell events in the Logboek page.
    """
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
    """Set up the pygame audio system so the Pi can play sound files.

    This must be called once before any bell can ring. If something
    goes wrong (for example, no audio device is found), we print a
    warning but do not crash — the daemon keeps running and will try
    to play audio anyway when a bell moment arrives.
    """
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
    """Remove all currently scheduled bell jobs.

    Called before loading a new day's schedule so old jobs do not
    keep firing after the schedule changes.
    """
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

def _subtract_minutes(hhmm: str, minutes: int) -> str | None:
    """Return hhmm minus N minutes as 'HH:MM', or None if it would
    cross midnight (i.e. land on the previous day).

    Returning None on midnight cross is intentional: 'schedule' fires
    every day at the given time, so a pre-midnight warning would ring
    on the wrong day. A bell at 00:30 with a 60-min warning is the
    edge case here — we just skip the warning rather than try to
    span days.
    """
    try:
        h, m = hhmm.split(":")
        total = int(h) * 60 + int(m) - int(minutes)
    except (ValueError, AttributeError):
        return None
    if total < 0:
        return None
    return f"{total // 60:02d}:{total % 60:02d}"


def apply_day_schedule(momenten: list[dict]):
    """
    momenten: list of dicts with keys:
      - tijd: "HH:MM"
      - naam: display name (optional)
      - bestand: audio filename
      - warn_min: int (optional) — N minutes before to ring a warning
      - warn_bestand: str (optional) — audio file for the warning
    """
    cancel_all_jobs()
    count = 0
    warn_count = 0
    for m in sorted(momenten, key=lambda x: x.get("tijd", "")):
        t = (m.get("tijd") or "").strip()
        f = (m.get("bestand") or "").strip()
        nm = (m.get("naam") or "").strip()
        if not (t and f):
            continue
        plan_job_at(t, f, label=nm)
        count += 1

        # Optional warning bell: rings warn_min minutes earlier with
        # warn_bestand. The web side already validates 1..60 and
        # filters out half-configured states (only one of the two
        # set), so we just check both are present here.
        warn_min = m.get("warn_min")
        warn_f = (m.get("warn_bestand") or "").strip()
        if warn_min and warn_f:
            warn_t = _subtract_minutes(t, int(warn_min))
            if warn_t is None:
                print(
                    f"[WARN] Skipping warning for {nm or t}: "
                    f"{warn_min} min before {t} crosses midnight."
                )
                continue
            plan_job_at(warn_t, warn_f, label=f"{nm} (waarschuwing)")
            warn_count += 1

    suffix = f", {warn_count} warnings" if warn_count else ""
    print(f"[INFO] {count} moments scheduled for today{suffix}.")

# === Helper functions for poller ===
def _local_next_midnight(now: datetime) -> datetime:
    """Return the datetime for midnight at the very start of tomorrow.

    Used to decide how long the daemon should sleep: never past midnight,
    because a new day might have a different bell schedule.
    """
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
#      missed refresh is never a real problem. The data we have
#      remains valid for the bulk of the relevant year.
#   2. Anchoring on a specific date (e.g. August 1) made the trigger
#      brittle: a network blip or service restart on that one day
#      would cost us a year. With a rolling 30-day window we get
#      ~12 chances per year to pick up changes, including the
#      flip to a new schooljaar that target_schooljaar() does at
#      Aug 1 (we'll catch the new year on the next monthly cycle).
VAKANTIES_REFRESH_INTERVAL_DAYS = 30

# Minimum time between two refresh *attempts*. Without this, a failed
# refresh (no network, rijksoverheid.nl down, fresh install without
# connectivity) would be retried on every poll iteration — at the
# default poll_interval_sec of 2 that means hammering the site with
# 5 fetches every 2 seconds until it succeeds. One retry per hour is
# plenty: the data stays valid for months, so recovery latency is
# not critical.
VAKANTIES_RETRY_MIN_INTERVAL_SEC = 60 * 60  # 1 hour

def _load_vakanties_fetch_state() -> dict:
    """Read the small JSON state file. Missing/bad file -> empty dict."""
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
    as elsewhere: robust against power loss mid-write."""
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

def _should_refresh_vakanties_today(today: date, state: dict, now: datetime | None = None) -> bool:
    """Should we attempt a vakanties refresh on this date?

    Two rules, both must allow it:

    1. Success recency: refresh only if the last *successful* refresh
       was VAKANTIES_REFRESH_INTERVAL_DAYS or more ago (or never).
       Corrupt or unparseable state is treated as 'never refreshed'.
    2. Attempt throttle: after any attempt (success or failure), wait
       at least VAKANTIES_RETRY_MIN_INTERVAL_SEC before trying again.
       This keeps a failing install (no network, site down) from
       retrying on every poll iteration.

    ``now`` is injectable for tests; defaults to the current UTC time.

    No special-cased calendar date. The schooljaar boundary is
    handled implicitly: target_schooljaar() flips on August 1, so
    the next monthly refresh after that automatically picks up
    the new year. Rijksoverheid publishes 5 years ahead anyway,
    so we don't need a tight day-of-year anchor.
    """
    last_iso = state.get("last_success_at", "")
    if last_iso:
        try:
            last_date = datetime.fromisoformat(last_iso.replace("Z", "+00:00")).date()
            if (today - last_date).days < VAKANTIES_REFRESH_INTERVAL_DAYS:
                return False
        except ValueError:
            # Corrupt timestamp -> treat as never refreshed. Better to
            # try once than to silently never refresh again.
            pass

    # Attempt throttle. A corrupt/missing last_attempt_at means we
    # can't prove a recent attempt, so allow the refresh.
    attempt_iso = state.get("last_attempt_at", "")
    if not attempt_iso:
        return True
    try:
        last_attempt = datetime.fromisoformat(attempt_iso.replace("Z", "+00:00"))
    except ValueError:
        return True
    if last_attempt.tzinfo is None:
        last_attempt = last_attempt.replace(tzinfo=timezone.utc)
    if now is None:
        now = datetime.now(timezone.utc)
    return (now - last_attempt).total_seconds() >= VAKANTIES_RETRY_MIN_INTERVAL_SEC

def _maybe_refresh_vakanties() -> None:
    """If we're due for a vakanties refresh, fetch from rijksoverheid.nl
    and merge into vakanties.json.

    'Due' = 30+ days since last successful refresh, or never refreshed.

    Fetches the current schooljaar plus 4 ahead (5 years). Per-year
    failures are independent: if one year 404s, the others still land.
    Years that previously succeeded but fail this round keep their
    last-good data via combined_payload(..., previous=...).

    'Successful' for state-tracking purposes means at least one
    schooljaar was fetched and saved. A run where all 5 years fail
    counts as a failure: state.last_error is set, last_success_at
    is unchanged, so we'll retry next poll cycle (subject to
    throttling).
    """
    if not settings.vakanties_scrape_enabled:
        # Master switch is off. Skip silently. The Voorkeuren status
        # panel surfaces this state to the admin without us needing
        # to flood the log on every poll.
        return

    today = date.today()
    state = _load_vakanties_fetch_state()
    if not _should_refresh_vakanties_today(today, state):
        return

    # Lazy import keeps beautifulsoup4 out of the daemon's main loop
    # for users who don't run the refresh (or whose Pi was offline
    # for more than the refresh interval).
    try:
        import vakanties_fetcher
    except ImportError as e:
        print(f"[WARN] vakanties_fetcher not importable: {e}")
        return

    targets = vakanties_fetcher.schooljaren_to_fetch(today)
    print(
        f"[INFO] Vakanties refresh due: fetching {len(targets)} schooljaren "
        f"({', '.join(targets)}) from rijksoverheid.nl"
    )

    now_iso = datetime.now(timezone.utc).isoformat()
    state["last_attempt_at"] = now_iso
    state["last_attempt_schooljaren"] = list(targets)

    # Read existing file (if any) for partial-failure preservation.
    previous = None
    if os.path.exists(VAKANTIES_PATH):
        try:
            with open(VAKANTIES_PATH, "r", encoding="utf-8") as f:
                previous = vakanties_fetcher.migrate_legacy_format(json.load(f))
        except Exception as e:
            print(f"[WARN] Could not read existing {VAKANTIES_PATH}: {e}")
            previous = None

    successes, failures = vakanties_fetcher.fetch_and_parse_multi(targets)

    if not successes:
        # Total failure: don't touch the file. Record + emit an event
        # so the UI status panel can show 'last attempt failed' even
        # though we still have valid data on disk.
        first_err = failures[0][1] if failures else "unknown"
        state["last_error"] = first_err
        state["last_failed_schooljaren"] = [s for s, _ in failures]
        _save_vakanties_fetch_state(state)
        print(f"[WARN] Vakanties refresh: all {len(targets)} years failed. First: {first_err}")
        log_bell_event({
            "status": "error",
            "message": f"vakanties refresh failed for all {len(targets)} years: {first_err}",
        })
        return

    payload = vakanties_fetcher.combined_payload(successes, previous=previous)
    vakanties_fetcher.write_atomically(VAKANTIES_PATH, payload)

    state["last_success_at"] = now_iso
    state["last_success_schooljaren"] = sorted(successes.keys())
    state["last_failed_schooljaren"] = [s for s, _ in failures]
    state["last_error"] = "" if not failures else f"Partial: {len(failures)} of {len(targets)} failed"
    _save_vakanties_fetch_state(state)

    if failures:
        print(
            f"[INFO] Vakanties refresh: {len(successes)}/{len(targets)} OK "
            f"({', '.join(sorted(successes.keys()))}); "
            f"failed: {', '.join(s for s, _ in failures)}"
        )
        log_bell_event({
            "status": "warning",
            "message": (
                f"vakanties refresh partial: "
                f"{len(successes)}/{len(targets)} years OK"
            ),
        })
    else:
        print(f"[INFO] Vakanties refresh OK for all {len(successes)} years")
        log_bell_event({
            "status": "ok",
            "message": f"vakanties refresh OK ({len(successes)} years)",
        })

# === Poll loop (with dynamic interval + SIGHUP reload) ===
def schedule_poller_loop():
    """The main loop that keeps the daemon running.

    On every iteration it:
    1. Writes a heartbeat timestamp so the web interface knows it is alive.
    2. Reloads settings if the admin asked for it (via SIGHUP).
    3. Checks whether the vacation data needs refreshing.
    4. Fetches today's bell schedule from the web API.
    5. Updates the scheduled jobs if the schedule changed.
    6. Sleeps until the next check (but never past midnight).
    """
    global settings, _reload_settings
    last_sig = None
    last_config_mtime = _config_mtime()

    while not stop_event.is_set():
        now = datetime.now()

        # 0) Heartbeat: signal to the webinterface (and any external
        # monitor) that we're alive. Done at the top of every iteration
        # so the timestamp reflects the start of the work, not the end.
        # Gives the reader a tighter 'how stale is this?' read.
        _write_heartbeat()

        # 1) Reload settings on SIGHUP or when config.json changed on
        # disk. The mtime check is what makes the Voorkeuren page work
        # end-to-end: the webinterface saves config.json, and without
        # this the daemon would keep playing at the old volume (and
        # old poll interval) until a manual SIGHUP or restart —
        # something no admin ever sent.
        config_mtime = _config_mtime()
        if _reload_settings or config_mtime != last_config_mtime:
            try:
                settings = Settings.load()
                print(f"[RELOAD] Settings reloaded: poll={settings.poll_interval_sec}s, volume={settings.volume_percent}%")
                apply_playback_volume()
            except Exception as e:
                print("[WARN] Settings reload failed:", e)
            last_config_mtime = config_mtime
            _reload_settings = False

        # 2) Vakanties refresh check (~monthly). Cheap (one disk
        # read of a tiny state file plus a date comparison) so doing
        # it on every poll is fine. The date math gates the actual
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
            # Error fetching/parsing -> backoff, but cleanly stoppable
            stop_event.wait(BACKOFF_ON_ERROR)

# === Main ===
def main():
    """Start the schoolbell daemon.

    Sets up the audio system, starts the poller in a background thread,
    and then keeps running the schedule (firing bell jobs at the right
    times) until the daemon is stopped.
    """
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
