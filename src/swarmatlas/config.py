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
    database_directory: str = ""
    geoip_provider: str = "dbip"
    geoip_api_key: str = ""

    def configured_geoip_databases(self) -> list[str]:
        """Return only user-selected databases, excluding the automatic DB-IP fallback."""
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

    def resolved_geoip_databases(self) -> list[str]:
        configured = self.configured_geoip_databases()
        if configured:
            return configured
        default_path = app_data_dir() / "geoip" / "dbip-city-lite.mmdb"
        return [str(default_path)] if default_path.is_file() else []

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
                database_directory=str(raw.get("database_directory", "")).strip(),
                geoip_provider=str(raw.get("geoip_provider", "dbip")) if str(raw.get("geoip_provider", "dbip")) in {"dbip", "ip-api", "ipdata", "ipapi-is", "ipinfo-lite", "2ip"} else "dbip",
                geoip_api_key=str(raw.get("geoip_api_key", "")),
            )
        except (OSError, ValueError, TypeError):
            return cls()

    def save(self) -> None:
        self.geoip_databases = self.configured_geoip_databases()
        self.geoip_database = self.geoip_databases[0] if self.geoip_databases else ""
        path = app_data_dir() / "settings.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(asdict(self), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        temporary.replace(path)


def database_path(config: AppConfig | None = None) -> Path:
    """Resolve the SQLite location; settings remain in the standard app-data folder."""
    selected = str(config.database_directory).strip() if config is not None else ""
    directory = Path(selected).expanduser() if selected else app_data_dir()
    directory.mkdir(parents=True, exist_ok=True)
    return directory / "swarmatlas.sqlite3"
