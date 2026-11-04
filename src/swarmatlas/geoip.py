from __future__ import annotations

from collections import OrderedDict
from pathlib import Path
from typing import Any


class GeoLocator:
    """Optional offline GeoIP lookup. No address is sent to a remote service."""

    CACHE_LIMIT = 4096

    def __init__(self, database_path: str = ""):
        self._reader: Any | None = None
        self._cache: OrderedDict[str, dict[str, Any]] = OrderedDict()
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
        cached = self._cache.get(ip)
        if cached is not None:
            self._cache.move_to_end(ip)
            return dict(cached)
        if self._reader is None:
            return {}

        result: dict[str, Any] = {}
        try:
            response = self._reader.city(ip)
            subdivision = response.subdivisions.most_specific
            result = {
                "country_code": response.country.iso_code,
                "country": response.country.name,
                "region": subdivision.name,
                "city": response.city.name,
                "latitude": response.location.latitude,
                "longitude": response.location.longitude,
            }
        except Exception:
            result = {}

        self._cache[ip] = result
        self._cache.move_to_end(ip)
        while len(self._cache) > self.CACHE_LIMIT:
            self._cache.popitem(last=False)
        return dict(result)

    def close(self) -> None:
        self._cache.clear()
        if self._reader is not None:
            self._reader.close()
            self._reader = None
