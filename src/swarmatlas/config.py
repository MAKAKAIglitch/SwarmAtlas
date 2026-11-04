from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
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
    # Legacy single-file setting retained for backwards compatibility.
    geoip_database: str = ""
    geoip_databases: list[str] = field(default_factory=list)
    theme: str = "system"
    language: str = "system"

    def resolved_geoip_databases(self) -> list[str]:
        values = list(self.geoip_databases)
        if not values and self.geoip_database:
            values = [self.geoip_database]
        result: list[str] = []
        seen: set[str] = set()
        for value in values:
            path = str(value).strip()
            key = os.path.normcase(os.path.abspath(os.path.expanduser(path))) if path else ""
            if key and key not in seen:
                seen.add(key)
                result.append(path)
        return result

    @classmethod
    def load(cls) -> "AppConfig":
        path = app_data_dir() / "settings.json"
        if not path.exists():
            return cls()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                return cls()
            theme = str(raw.get("theme", "system"))
            language = str(raw.get("language", "system"))
            raw_databases = raw.get("geoip_databases", [])
            if isinstance(raw_databases, str):
                raw_databases = [raw_databases]
            if not isinstance(raw_databases, list):
                raw_databases = []
            databases = [str(item) for item in raw_databases if isinstance(item, str) and item.strip()]
            legacy = str(raw.get("geoip_database", ""))
            if not databases and legacy:
                databases = [legacy]
            return cls(
                interface=str(raw.get("interface", "auto")),
                geoip_database=legacy,
                geoip_databases=databases,
                theme=theme if theme in {"system", "dark", "light"} else "system",
                language=language if language in {"system", "ru", "en"} else "system",
            )
        except (OSError, ValueError, TypeError):
            return cls()

    def save(self) -> None:
        self.geoip_databases = self.resolved_geoip_databases()
        self.geoip_database = self.geoip_databases[0] if self.geoip_databases else ""
        path = app_data_dir() / "settings.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(asdict(self), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        temporary.replace(path)


def database_path() -> Path:
    return app_data_dir() / "swarmatlas.sqlite3"
