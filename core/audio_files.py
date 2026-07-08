"""Audio-file helpers that don't need to know where files live.

Each function takes a full path or a filename as a parameter. They
do not read the AUDIO_DIR module variable, so you can use them in
tests by passing a path inside ``tmp_path`` and skipping ``pygame``
mocks where possible.
"""

import os
import re
import threading
import time

from werkzeug.utils import safe_join

from core.rooster import NAME_RE


# Matches a complete audio filename: a NAME_RE-compatible stem (1-35
# chars of letters/digits/space/_/-), then a single dot, then a short
# alphanumeric extension (mp3, wav, ogg, m4a, ...). Used by
# safe_audio_path() to validate user-supplied filenames for the play
# and delete handlers before they touch the filesystem.
_AUDIO_FILENAME_RE = re.compile(r"^[A-Za-z0-9 _-]{1,35}\.[A-Za-z0-9]{1,5}$")


def safe_audio_filename(base_no_ext: str, ext: str) -> str:
    """Build a safe filename like ``"bel.mp3"`` from a base name and extension.

    Returns ``""`` when the base name has any character that is not
    in NAME_RE (letters, digits, space, ``_``, ``-``). That guards
    against tricks like ``"../etc/passwd"`` or filenames with weird
    characters that some operating systems can't store.

    The extension already contains its dot, e.g. ``".mp3"``.
    """
    base_no_ext = base_no_ext.strip()
    if not NAME_RE.match(base_no_ext):
        return ""
    return f"{base_no_ext}{ext}"


def safe_audio_path(name: str, audio_dir: str) -> str | None:
    """Resolve a user-supplied filename to a safe path inside ``audio_dir``.

    Used by routes that accept a filename from a form (play, delete,
    upload) and need to turn it into a path on disk without giving
    the submitter a way to escape ``audio_dir``.

    Three layered checks:

    1. **Allow-list on the name.** The name must match
       ``_AUDIO_FILENAME_RE``: a NAME_RE-style stem plus a short
       alphanumeric extension. That rejects path separators,
       parent-dir markers, control characters, NUL bytes, and
       basically anything that isn't a plain filename.

    2. **werkzeug's ``safe_join``.** This is the standard path-
       traversal sanitizer recognised by static analyzers like
       CodeQL. It returns ``None`` when ``name`` contains an OS
       alternate separator, an absolute path, or a parent-dir
       marker. Belt-and-braces with step 1.

    3. **Realpath confinement.** ``safe_join`` is a pure string
       operation and does not follow symlinks. If somebody managed
       to plant a symlink in ``audio_dir`` pointing elsewhere, the
       resolved path would still be flagged here.

    Returns the path on success and ``None`` otherwise. Callers are
    still responsible for checking existence with ``os.path.isfile``
    — this function only guarantees the path is safe to *form*, not
    that anything lives there yet.
    """
    if not isinstance(name, str) or not name:
        return None
    if not _AUDIO_FILENAME_RE.match(name):
        return None

    joined = safe_join(audio_dir, name)
    if joined is None:
        return None

    # Defense in depth against symlinks pointing outside audio_dir.
    real = os.path.realpath(joined)
    base = os.path.realpath(audio_dir)
    # Sep-anchored prefix: '/foo/barx' must not match base '/foo/bar'.
    if real != base and not real.startswith(base + os.sep):
        return None
    return joined


def _validate_audio_file(path: str) -> tuple[bool, str]:
    """Verify pygame can actually decode this file.

    Returns (is_valid, message). Message is shown to the user when
    invalid; ignored when valid.

    We use pygame.mixer.music.load (same as the daemon) so 'pygame
    accepts it' = 'the daemon will accept it'. No length check.
    A 30-minute file is unusual for a school bell but technically
    valid, so the user can decide. We only block the case where
    pygame refuses outright (corrupt, wrong format despite
    extension, etc.).

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


# ---- Stop flag ------------------------------------------------------
#
# The 'Stop' button on the geluiden page must be able to silence
# audio that is playing in a *different process*: Gunicorn runs
# multiple workers (each with its own pygame mixer) and scheduled
# bells play in the daemon. A stop request can land on any of them.
# The shared state is a tiny flag file: requesting a stop bumps its
# mtime; every process that plays audio watches the mtime and stops
# its own mixer when the flag is newer than the moment its playback
# started. The file is never deleted — a stale flag is simply older
# than any new playback and therefore ignored. That avoids all
# delete/recreate races between consumers.

# How often the web worker's watcher thread polls the flag while a
# test sound is playing. 0.2 s keeps the button feeling instant
# without measurable CPU cost.
STOP_FLAG_POLL_SEC = 0.2


def request_stop(flag_path: str) -> None:
    """Signal every audio-playing process to stop, by touching the flag.

    Writing the file (or updating its mtime when it already exists)
    is atomic enough for this purpose: consumers only compare the
    mtime against their playback start time, never read the content.
    """
    os.makedirs(os.path.dirname(flag_path), exist_ok=True)
    with open(flag_path, "w", encoding="utf-8") as f:
        f.write("stop\n")


def stop_flag_mtime(flag_path: str) -> float | None:
    """The flag file's mtime, or None when it doesn't exist yet."""
    try:
        return os.stat(flag_path).st_mtime
    except OSError:
        return None


def _watch_stop_flag(flag_path: str, started_at: float) -> None:
    """Body of the watcher thread started by _play_via_pygame.

    Polls while this process's music channel is busy; stops the
    mixer as soon as the flag is touched after ``started_at``.
    Exits silently when playback ends by itself. Any pygame error
    (e.g. mixer torn down during interpreter shutdown) just ends
    the watcher — it must never take the worker down.
    """
    import pygame
    try:
        while pygame.mixer.get_init() and pygame.mixer.music.get_busy():
            mtime = stop_flag_mtime(flag_path)
            if mtime is not None and mtime >= started_at:
                pygame.mixer.music.stop()
                return
            time.sleep(STOP_FLAG_POLL_SEC)
    except Exception:
        pass


def _play_via_pygame(path: str, volume: float, stop_flag_path: str | None = None) -> None:
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
    flag. pygame already tracks state for us. mixer.init() is
    idempotent (safe to run more than once with the same result)
    in practice but get_init() avoids the work.

    When ``stop_flag_path`` is given, a small daemon thread watches
    that file for the duration of the playback and stops the mixer
    when the Stop button touches it (see the stop-flag section
    above). Threads are per-playback; a thread whose sound already
    finished sees get_busy() == False and exits immediately.
    """
    import pygame  # local import: only when the button is actually used
    if not pygame.mixer.get_init():
        pygame.mixer.init()
    pygame.mixer.music.set_volume(volume)
    pygame.mixer.music.load(path)
    started_at = time.time()
    pygame.mixer.music.play()
    if stop_flag_path:
        threading.Thread(
            target=_watch_stop_flag,
            args=(stop_flag_path, started_at),
            name="StopFlagWatcher",
            daemon=True,
        ).start()
