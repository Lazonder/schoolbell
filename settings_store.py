import json
import os
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
    # UI theme. "auto" follows the browser's system preference
    # (prefers-color-scheme). See base.html for the application.
    theme_mode: str = "light"             # "light" | "dark" | "auto"
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
            # No config file yet → defaults
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
        The replace is atomic within a single filesystem — on a crash or
        power loss there is therefore either the old file or the complete
        new one. Never a half-written JSON.
        """
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        tmp = CONFIG_PATH.with_suffix(CONFIG_PATH.suffix + ".tmp")
        with open(tmp, "w") as f:
            json.dump(asdict(self), f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, CONFIG_PATH)
