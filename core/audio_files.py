"""Audio-file helpers that don't need to know where files live.

Each function takes a full path or a filename as a parameter. They
do not read the AUDIO_DIR module variable, so you can use them in
tests by passing a path inside ``tmp_path`` and skipping ``pygame``
mocks where possible.
"""

from core.rooster import NAME_RE


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
    if not (1 <= len(base_no_ext) <= 35):
        return ""
    return f"{base_no_ext}{ext}"


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
