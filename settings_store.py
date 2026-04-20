import json
import os
from dataclasses import dataclass, asdict
from typing import List
from pathlib import Path

# Waar het configuratiebestand staat.
# Voorrang: de env-variabele SCHOOLBELL_CONFIG (gezet door de systemd-units).
# Fallback: config.json naast dit Python-bestand (handig bij lokaal draaien).
CONFIG_PATH = Path(
    os.environ.get("SCHOOLBELL_CONFIG")
    or Path(__file__).with_name("config.json")
)

@dataclass
class Settings:
    volume_percent: int = 70              # 0..100
    max_file_size_mb: int = 15            # 1..1024
    poll_interval_sec: int = 2            # 1..60
    timezone: str = "Europe/Amsterdam"
    allowed_extensions: List[str] = (".mp3", ".wav", ".ogg")

    @classmethod
    def load(cls) -> "Settings":
        """Laad de instellingen uit CONFIG_PATH. Als het bestand niet bestaat, gebruik defaults."""
        try:
            with open(CONFIG_PATH, "r") as f:
                data = json.load(f)
        except FileNotFoundError:
            # Als er nog geen config-bestand is → defaults
            return cls()
        except json.JSONDecodeError as e:
            raise RuntimeError(f"Configuratiebestand is ongeldig JSON: {e}")

        # Combineer defaults met geladen data (zodat nieuwe velden altijd een waarde krijgen)
        defaults = cls()
        merged = {**asdict(defaults), **data}
        return cls(**merged)

    def save(self):
        """Sla de instellingen op naar CONFIG_PATH."""
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        with open(CONFIG_PATH, "w") as f:
            json.dump(asdict(self), f, indent=2)
