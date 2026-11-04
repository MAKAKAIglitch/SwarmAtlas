from __future__ import annotations

from collections import OrderedDict
from pathlib import Path
from typing import Any, Iterable


class GeoLocator:
    """Read-only offline GeoIP lookup across several selected MMDB files."""

    CACHE_LIMIT = 8192
    WARMUP_ADDRESSES = ("1.1.1.1", "8.8.8.8")

    def __init__(self, database_paths: str | Iterable[str] = "", language: str = "en"):
        if isinstance(database_paths, str):
            paths = [database_paths] if database_paths else []
        else:
            paths = list(database_paths)

        self._readers: list[tuple[str, Any, str]] = []
        self._cache: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._errors: list[str] = []
        self.status = "GeoIP database not configured"

        selected = [Path(value).expanduser() for value in paths if str(value).strip()]
        if not selected:
            return

        try:
            import geoip2.database  # type: ignore[import-not-found]
        except ImportError:
            self.status = "Install the optional 'geoip' dependency"
            return

        for path in selected:
            if not path.is_file():
                self._errors.append(f"{path.name}: file not found")
                continue
            reader = None
            try:
                locales = ["ru", "en"] if language == "ru" else ["en", "ru"]
                reader = geoip2.database.Reader(str(path), locales=locales)
                database_type = str(reader.metadata().database_type)
                lowered = database_type.lower()

                if "city" in lowered:
                    kind = "city"
                elif "country" in lowered:
                    kind = "country"
                elif "asn" in lowered:
                    kind = "asn"
                elif "isp" in lowered:
                    kind = "isp"
                else:
                    self._errors.append(f"{path.name}: unsupported database type ({database_type})")
                    reader.close()
                    continue

                self._readers.append((kind, reader, path.name))
            except Exception as exc:
                if reader is not None:
                    try:
                        reader.close()
                    except Exception:
                        pass
                self._errors.append(f"{path.name}: {type(exc).__name__}: {exc}")

        self._readers.sort(key=lambda item: {"city": 0, "country": 1, "asn": 2, "isp": 3}[item[0]])

        if self._readers:
            self._warm_readers()
            kinds = ", ".join(
                f"{kind.upper()} ({name})" for kind, _, name in self._readers
            )
            self.status = f"Local GeoIP ready: {kinds}"
            if self._errors:
                self.status += f"; skipped {len(self._errors)}"
        elif self._errors:
            self.status = "No usable GeoIP databases: " + "; ".join(self._errors[:3])
        else:
            self.status = "GeoIP database not configured"

    def _warm_readers(self) -> None:
        """Prime each mapped reader with local lookups; never contacts the network."""
        for kind, reader, _ in self._readers:
            method = getattr(reader, kind, None)
            if method is None:
                continue
            for address in self.WARMUP_ADDRESSES:
                try:
                    method(address)
                except Exception:
                    continue

    def lookup(self, ip: str) -> dict[str, Any]:
        cached = self._cache.get(ip)
        if cached is not None:
            self._cache.move_to_end(ip)
            return dict(cached)

        result: dict[str, Any] = {}
        for kind, reader, _ in self._readers:
            try:
                response = getattr(reader, kind)(ip)
            except Exception:
                continue

            if kind == "city":
                country = getattr(response, "country", None)
                subdivision = getattr(getattr(response, "subdivisions", None), "most_specific", None)
                city = getattr(response, "city", None)
                location = getattr(response, "location", None)
                result["country_code"] = result.get("country_code") or getattr(country, "iso_code", None)
                result["country"] = result.get("country") or getattr(country, "name", None)
                result["region"] = result.get("region") or getattr(subdivision, "name", None)
                result["city"] = result.get("city") or getattr(city, "name", None)
                if result.get("latitude") is None:
                    result["latitude"] = getattr(location, "latitude", None)
                if result.get("longitude") is None:
                    result["longitude"] = getattr(location, "longitude", None)
            elif kind == "country":
                result["country_code"] = result.get("country_code") or response.country.iso_code
                result["country"] = result.get("country") or response.country.name
            elif kind == "asn":
                result["asn"] = result.get("asn") or response.autonomous_system_number
                result["organization"] = result.get("organization") or response.autonomous_system_organization
            elif kind == "isp":
                result["isp"] = result.get("isp") or response.isp
                result["organization"] = result.get("organization") or response.organization

        self._cache[ip] = result
        self._cache.move_to_end(ip)
        while len(self._cache) > self.CACHE_LIMIT:
            self._cache.popitem(last=False)
        return dict(result)

    def close(self) -> None:
        self._cache.clear()
        for _, reader, _ in self._readers:
            try:
                reader.close()
            except Exception:
                pass
        self._readers.clear()
