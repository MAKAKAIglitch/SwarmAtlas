from __future__ import annotations

from pathlib import Path
from typing import Any


class GeoLocator:
    """Optional offline GeoIP lookup. No address is sent to a remote service."""

    def __init__(self, database_path: str = ""):
        self._reader: Any | None = None
        self.status = "GeoIP database not configured"
        path = Path(database_path).expanduser() if database_path else None
        if path is None or not path.is_file():
            return

        try:
            import geoip2.database  # type: ignore[import-not-found]

            self._reader = geoip2.database.Reader(str(path))
            self.status = f"Local GeoIP: {path.name}"
        except ImportError:
            self.status = "Install the optional 'geoip' dependency"
        except Exception as exc:
            self.status = f"Could not open GeoIP database: {exc}"

    def lookup(self, ip: str) -> dict[str, Any]:
        if self._reader is None:
            return {}
        try:
            response = self._reader.city(ip)
            subdivision = response.subdivisions.most_specific
            return {
                "country_code": response.country.iso_code,
                "country": response.country.name,
                "region": subdivision.name,
                "city": response.city.name,
                "latitude": response.location.latitude,
                "longitude": response.location.longitude,
            }
        except Exception:
            return {}

    def close(self) -> None:
        if self._reader is not None:
            self._reader.close()
            self._reader = None
