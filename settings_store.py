import fcntl
import json
import os
from contextlib import contextmanager
from dataclasses import dataclass, asdict, field
from typing import List
from pathlib import Path

# Where the configuration file lives.
# Priority: the SCHOOLBELL_CONFIG env variable (set by the systemd units).
# Fallback: config.json next to this Python file (useful for local runs).
CONFIG_PATH = Path(
    os.environ.get("SCHOOLBELL_CONFIG")
    or Path(__file__).with_name("config.json")
)

@dataclass
class Settings:
    volume_percent: int = 70              # 0..100
    max_file_size_mb: int = 15            # 1..1024
    poll_interval_sec: int = 2            # 1..60
    # UI language. Used by Flask-Babel to pick a translation. The
    # special value "auto" tells the server to read the browser's
    # Accept-Language header on every request and pick the best
    # matching supported locale. Anything else must match an entry in
    # SUPPORTED_LOCALES (see core/i18n.py). Unknown values fall back
    # to the default ("nl").
    taal: str = "nl"                      # "nl" | "en" | "de" | "fr" | "auto"
    # UI theme. "auto" follows the browser's system preference
    # (prefers-color-scheme). See base.html for the application.
    theme_mode: str = "light"             # "light" | "dark" | "auto"
    # House-style overrides, independent of theme_mode. "standaard"
    # means: don't override anything, follow theme_mode as before.
    # "aangepast" reads the three theme_custom_* fields below and
    # injects them as inline CSS custom properties on <html>, so they
    # win over both :root and html[data-theme="dark"].
    huisstijl: str = "standaard"          # "standaard" | "aangepast"
    # Custom-style colors. Only consulted when huisstijl == "aangepast".
    # Stored as CSS hex strings (#rrggbb / #rgb). The three fields map
    # to the elements the user can recolor:
    #   - theme_custom_bg    -> page background     (--sb-color-bg)
    #   - theme_custom_table -> cards & table fill  (--sb-color-surface
    #                                                + --sb-color-row-alt)
    #   - theme_custom_nav   -> navigation bar      (.sb-header: flat
    #                                                solid color, no
    #                                                gradient in custom)
    # Defaults match the light theme so an "aangepast" with no further
    # changes looks identical to "Licht / standaard".
    theme_custom_bg: str = "#ffffff"
    theme_custom_table: str = "#f7f7f9"
    theme_custom_nav: str = "#5b62ff"
    # Dutch school vacation region used by the 'Vakanties importeren'
    # button on the agenda page. The shared vakanties.json file lists
    # vacation dates per region. This picks which list to read.
    # Allowed values: "Noord" | "Midden" | "Zuid".
    vakantieregio: str = "Noord"
    # Master switch for the vakantie-scrape feature. When False:
    #   - the daemon skips its periodic rijksoverheid.nl refresh,
    #   - the Agenda's Vakanties card and Voorkeuren's status panel
    #     are hidden,
    #   - data/vakanties.json is left alone.
    # Useful for installs outside the Netherlands (where Dutch school
    # vacations don't apply) or admins who maintain vakanties.json
    # by hand. Default True so a fresh NL install gets the feature
    # without any extra setup.
    vakanties_scrape_enabled: bool = True
    # field(default_factory=...) is the proper way to specify a mutable
    # default for a dataclass field. Previously this was a tuple, which
    # didn't match the List[str] annotation.
    allowed_extensions: List[str] = field(
        default_factory=lambda: [".mp3", ".wav", ".ogg"]
    )

    @classmethod
    def load(cls) -> "Settings":
        """Load settings from CONFIG_PATH. If the file doesn't exist, use defaults."""
        try:
            with open(CONFIG_PATH, "r") as f:
                data = json.load(f)
        except FileNotFoundError:
            # No config file yet -> defaults
            return cls()
        except json.JSONDecodeError as e:
            raise RuntimeError(f"Config file is invalid JSON: {e}")

        # Merge defaults with loaded data, but only keep keys that the
        # current dataclass actually defines. This makes the loader
        # forward-compatible: an existing config.json with a removed
        # field (e.g. the old "timezone" setting) doesn't crash with
        # `TypeError: __init__() got an unexpected keyword argument`.
        defaults = cls()
        known_keys = {f.name for f in cls.__dataclass_fields__.values()}
        merged = {**asdict(defaults), **{k: v for k, v in data.items() if k in known_keys}}
        return cls(**merged)

    def save(self):
        """Save settings atomically to CONFIG_PATH.

        Write to a tmp file in the same directory first, then `os.replace()`.
        The replace is atomic within a single filesystem. On a crash or
        power loss there is therefore either the old file or the complete
        new one. Never a half-written JSON.

        Note: this is atomic per call, but a load -> mutate -> save
        sequence by a route handler is not safe against a race
        condition (when two operations happen at almost the same
        moment and step on each other) without an additional lock.
        Use Settings.locked() for that pattern.
        """
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        tmp = CONFIG_PATH.with_suffix(CONFIG_PATH.suffix + ".tmp")
        with open(tmp, "w") as f:
            json.dump(asdict(self), f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, CONFIG_PATH)


@contextmanager
def locked():
    """Hold an exclusive lock for read-modify-write of the settings file.

    Same pattern as webinterface.locked_json: a `.lock` file next to
    the config is locked via fcntl.flock LOCK_EX (an *exclusive* lock:
    only one holder at a time, everyone else waits) for the duration
    of the with-block.
    Loads inside the lock, yields the Settings instance for the caller
    to mutate, and saves on a clean exit. If the with-body raises, no
    save happens. Useful when validation rejects an incoming payload.

    Usage:
        with settings_store.locked() as s:
            s.volume_percent = 80
            # automatic save on exit

    Why a separate lock helper here (instead of webinterface.locked_json):
    settings live at /etc/schoolbell/config.json, outside of DATA_DIR,
    and have their own dedicated load/save methods on the Settings
    dataclass. Keeping the lock here means callers don't have to know
    about CONFIG_PATH. They just say `with locked()`.
    """
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    lock_path = str(CONFIG_PATH) + ".lock"
    lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        s = Settings.load()
        yield s
        # Reached only if the with-body completed without raising;
        # callers that want to bail without saving should raise.
        s.save()
    finally:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(lock_fd)
