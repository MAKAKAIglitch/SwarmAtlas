from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path


def app_data_dir() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    path = base / "SwarmAtlas"
    path.mkdir(parents=True, exist_ok=True)
    return path


@dataclass
class AppConfig:
    interface: str = "auto"
    geoip_database: str = ""
    theme: str = "dark"
    language: str = "ru"

    @classmethod
    def load(cls) -> "AppConfig":
        path = app_data_dir() / "settings.json"
        if not path.exists():
            return cls()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            theme = str(raw.get("theme", "dark"))
            language = str(raw.get("language", "ru"))
            return cls(
                interface=str(raw.get("interface", "auto")),
                geoip_database=str(raw.get("geoip_database", "")),
                theme=theme if theme in {"dark", "light"} else "dark",
                language=language if language in {"ru", "en"} else "ru",
            )
        except (OSError, ValueError, TypeError):
            return cls()

    def save(self) -> None:
        path = app_data_dir() / "settings.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(asdict(self), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        temporary.replace(path)


def database_path() -> Path:
    return app_data_dir() / "swarmatlas.sqlite3"
