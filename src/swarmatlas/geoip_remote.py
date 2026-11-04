from __future__ import annotations

import ipaddress
import json
import logging
import queue
import re
import threading
import time
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .config import app_data_dir


_LOGGER = logging.getLogger(__name__)
MAX_RESPONSE_BYTES = 1024 * 1024
MAX_PENDING_LOOKUPS = 4000
CACHE_LIMIT = 8192

PROVIDER_NAMES = {
    "ip-api": "IP-API.com",
    "ipdata": "ipdata.co",
    "ipapi-is": "ipapi.is",
    "ipinfo-lite": "IPinfo Lite",
    "ip2location": "IP2Location.io",
    "2ip": "2ip.io",
}


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if result == result and abs(result) != float("inf") else None


def _asn_number(value: Any) -> int | None:
    if isinstance(value, dict):
        value = value.get("asn") or value.get("id") or value.get("number")
    match = re.search(r"\d+", str(value or ""))
    return int(match.group()) if match else None


def _normalize(provider: str, data: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    if provider == "ip-api":
        if data.get("status") != "success":
            return result
        result.update({
            "country": data.get("country"),
            "country_code": data.get("countryCode"),
            "region": data.get("regionName"),
            "city": data.get("city"),
            "latitude": _number(data.get("lat")),
            "longitude": _number(data.get("lon")),
            "isp": data.get("isp"),
            "organization": data.get("org"),
            "asn": _asn_number(data.get("as")),
        })
    elif provider == "ipdata":
        asn_value = data.get("asn")
        asn = asn_value if isinstance(asn_value, dict) else {}
        company_value = data.get("company")
        company = company_value if isinstance(company_value, dict) else {}
        raw_country = data.get("country")
        country_code = data.get("country_code")
        if not country_code and isinstance(raw_country, str) and len(raw_country.strip()) == 2:
            country_code = raw_country.strip().upper()
        country_name = data.get("country_name")
        if not country_name and isinstance(raw_country, str) and len(raw_country.strip()) != 2:
            country_name = raw_country
        organization = (
            asn.get("name")
            or data.get("org")
            or data.get("name")
            or company.get("name")
            or (company_value if isinstance(company_value, str) else None)
            or data.get("organisation")
            or data.get("organization")
        )
        result.update({
            "country": country_name or country_code,
            "country_code": country_code,
            "region": data.get("region"),
            "city": data.get("city"),
            "latitude": _number(data.get("latitude")),
            "longitude": _number(data.get("longitude")),
            "isp": organization,
            "organization": organization,
            "asn": _asn_number(asn.get("asn") or asn.get("number") or asn_value),
        })
    elif provider == "ipapi-is":
        location = data.get("location") if isinstance(data.get("location"), dict) else {}
        company_value = data.get("company")
        company = company_value if isinstance(company_value, dict) else {}
        company_name = (
            company.get("name")
            if company
            else company_value if isinstance(company_value, str) else None
        )
        asn_value = data.get("asn")
        asn = asn_value if isinstance(asn_value, dict) else {}
        result.update({
            "country": location.get("country") or data.get("country"),
            "country_code": location.get("country_code") or data.get("country_code"),
            "region": location.get("state") or location.get("region") or data.get("region"),
            "city": location.get("city") or data.get("city"),
            "latitude": _number(
                location.get("latitude") or data.get("latitude") or data.get("lat")
            ),
            "longitude": _number(
                location.get("longitude") or data.get("longitude") or data.get("lon")
            ),
            "organization": company_name or asn.get("org") or data.get("company_name"),
            "asn": _asn_number(asn.get("asn") or asn_value),
        })
    elif provider == "ipinfo-lite":
        result.update({
            "country": data.get("country"),
            "country_code": data.get("country_code"),
            "organization": data.get("as_name"),
            "asn": _asn_number(data.get("asn")),
        })
    elif provider == "ip2location":
        result.update({
            "country": data.get("country_name"),
            "country_code": data.get("country_code"),
            "region": data.get("region_name"),
            "city": data.get("city_name"),
            "latitude": _number(data.get("latitude")),
            "longitude": _number(data.get("longitude")),
            "isp": data.get("isp"),
            "organization": data.get("as"),
            "asn": _asn_number(data.get("asn")),
        })
    elif provider == "2ip":
        asn = data.get("asn") if isinstance(data.get("asn"), dict) else {}
        result.update({
            "country": data.get("country"),
            "country_code": data.get("code"),
            "city": data.get("city"),
            "latitude": _number(data.get("lat")),
            "longitude": _number(data.get("lon")),
            "organization": asn.get("name"),
            "asn": _asn_number(asn.get("id")),
        })
    return {key: value for key, value in result.items() if value not in (None, "")}


class RemoteGeoLocator:
    """Opt-in asynchronous GeoIP lookups; only the public destination IP is sent."""

    def __init__(self, provider: str, api_key: str = ""):
        if provider not in PROVIDER_NAMES:
            raise ValueError(f"Unsupported remote GeoIP provider: {provider}")
        self.provider = provider
        self.api_key = api_key.strip()
        self.status = self._initial_status()
        self._cache: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._failed_until: dict[str, float] = {}
        self._pending: set[str] = set()
        self._queue: queue.Queue[str] = queue.Queue(maxsize=MAX_PENDING_LOOKUPS)
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._usage_day = datetime.now(timezone.utc).date().isoformat()
        self._usage_count = 0
        self._usage_month = datetime.now(timezone.utc).strftime("%Y-%m")
        self._monthly_usage_count = 0
        self._quota_reached = False
        self._monthly_quota_reached = False
        self._provider_wait_until = 0.0
        self._usage_path = app_data_dir() / "geoip_provider_usage.json"
        self._load_usage()
        self._thread: threading.Thread | None = None
        if not self._requires_key() or self.api_key:
            self._thread = threading.Thread(
                target=self._run,
                name="SwarmAtlas-GeoIP",
                daemon=True,
            )
            self._thread.start()

    def _requires_key(self) -> bool:
        return self.provider in {"ipinfo-lite", "2ip"}

    def _initial_status(self) -> str:
        name = PROVIDER_NAMES[self.provider]
        if self.provider == "ipinfo-lite" and not self.api_key:
            return f"{name}: API token required; country and ASN data only"
        if self.provider == "ip2location" and not self.api_key:
            return f"{name}: keyless free tier · up to 1,000 lookups/day"
        if self.provider == "ip2location":
            return f"{name}: free key configured · 50,000 lookups/month; SwarmAtlas caps at 47,500/month"
        if self.provider == "2ip" and not self.api_key:
            return "2ip.io: API token required; free tokens allow up to 3 requests/second"
        if self.provider == "ip-api":
            return "IP-API.com: free non-commercial HTTP service; limit 45 requests/minute"
        if self.provider == "ipdata":
            return (
                "ipdata.free: 1,000 city-level lookups/day per public caller IP; "
                "country/ASN continues after the limit; attribution required; "
                "SwarmAtlas caps at 950 lookups/day per installation"
            )
        if self.provider == "ipapi-is":
            limit = "1,000/day with a key" if self.api_key else "30/day without a key"
            return f"ipapi.is: free tier · {limit}"
        if self.provider == "2ip":
            return "2ip.io: token configured · up to 10,000 free requests/month · 3 requests/second; SwarmAtlas caps at 9,500/month"
        return f"{name}: ready"

    def _load_usage(self) -> None:
        try:
            raw = json.loads(self._usage_path.read_text(encoding="utf-8"))
            today = datetime.now(timezone.utc).date().isoformat()
            if isinstance(raw, dict) and raw.get("date") == today:
                counts = raw.get("counts", {})
                if isinstance(counts, dict):
                    self._usage_count = max(0, int(counts.get(self.provider, 0)))
            self._usage_day = today
            month = datetime.now(timezone.utc).strftime("%Y-%m")
            if isinstance(raw, dict) and raw.get("month") == month:
                monthly_counts = raw.get("monthly_counts", {})
                if isinstance(monthly_counts, dict):
                    self._monthly_usage_count = max(
                        0, int(monthly_counts.get(self.provider, 0))
                    )
            self._usage_month = month
        except (OSError, ValueError, TypeError):
            self._usage_day = datetime.now(timezone.utc).date().isoformat()
            self._usage_count = 0

    def _save_usage(self) -> None:
        today = datetime.now(timezone.utc).date().isoformat()
        counts: dict[str, int] = {}
        monthly_counts: dict[str, int] = {}
        month = datetime.now(timezone.utc).strftime("%Y-%m")
        try:
            raw = json.loads(self._usage_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                if raw.get("date") == today and isinstance(raw.get("counts"), dict):
                    counts = {str(k): max(0, int(v)) for k, v in raw["counts"].items()}
                if raw.get("month") == month and isinstance(raw.get("monthly_counts"), dict):
                    monthly_counts = {
                        str(k): max(0, int(v))
                        for k, v in raw["monthly_counts"].items()
                    }
        except (OSError, ValueError, TypeError):
            pass
        counts[self.provider] = self._usage_count
        monthly_counts[self.provider] = self._monthly_usage_count
        self._usage_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._usage_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps({
                "date": today,
                "counts": counts,
                "month": month,
                "monthly_counts": monthly_counts,
            }, indent=2),
            encoding="utf-8",
        )
        temporary.replace(self._usage_path)
        self._usage_day = today
        self._usage_month = month

    def _daily_limit(self) -> int | None:
        if self.provider == "ipdata":
            return 950
        if self.provider == "ipapi-is":
            return 950 if self.api_key else 25
        if self.provider == "ip2location" and not self.api_key:
            return 950
        return None

    def _monthly_limit(self) -> int | None:
        # Leave headroom below each provider's documented free-tier quota.
        if self.provider == "2ip":
            return 9500
        if self.provider == "ip2location" and self.api_key:
            return 47500
        return None

    def _minimum_interval(self) -> float:
        return {
            "ip-api": 1.4,       # stays below the published 45/minute cap
            "ipdata": 0.15,
            "ipapi-is": 0.15,
            "ipinfo-lite": 0.25,
            "ip2location": 0.5, # conservative pacing for either free plan
            "2ip": 0.4,          # below 3 requests/second
        }[self.provider]

    def lookup(self, ip: str) -> dict[str, Any]:
        try:
            address = ipaddress.ip_address(ip)
        except ValueError:
            return {}
        if not address.is_global:
            return {}
        with self._lock:
            today = datetime.now(timezone.utc).date().isoformat()
            if self._usage_day != today:
                self._usage_day = today
                self._usage_count = 0
                self._quota_reached = False
            month = datetime.now(timezone.utc).strftime("%Y-%m")
            if self._usage_month != month:
                self._usage_month = month
                self._monthly_usage_count = 0
                self._monthly_quota_reached = False
            cached = self._cache.get(ip)
            if cached is not None:
                self._cache.move_to_end(ip)
                return dict(cached)
            retry_at = self._failed_until.get(ip)
            if retry_at is not None:
                if retry_at > time.monotonic():
                    return {}
                self._failed_until.pop(ip, None)
            if (
                ip not in self._pending
                and self._thread is not None
                and not self._stop.is_set()
                and not self._quota_reached
                and not self._monthly_quota_reached
            ):
                try:
                    self._queue.put_nowait(ip)
                    self._pending.add(ip)
                except queue.Full:
                    self.status = f"{PROVIDER_NAMES[self.provider]} lookup queue is full; waiting for capacity"
        return {}

    def _url(self, ip: str) -> str:
        encoded_ip = ip
        if self.provider == "ip-api":
            fields = "status,message,country,countryCode,regionName,city,lat,lon,isp,org,as"
            return f"http://ip-api.com/json/{encoded_ip}?fields={fields}"
        if self.provider == "ipdata":
            base = f"https://ipdata.free/{encoded_ip}"
            return base + ("?" + urlencode({"api-key": self.api_key}) if self.api_key else "")
        if self.provider == "ipapi-is":
            return "https://api.ipapi.is/"
        if self.provider == "ipinfo-lite":
            return f"https://api.ipinfo.io/lite/{encoded_ip}?" + urlencode({"token": self.api_key})
        if self.provider == "ip2location":
            return "https://api.ip2location.io/?" + urlencode({"ip": encoded_ip})
        if self.provider == "2ip":
            return f"https://api.2ip.io/{encoded_ip}?" + urlencode({"token": self.api_key})
        raise ValueError("Unsupported GeoIP provider")

    def _fetch(self, ip: str) -> dict[str, Any]:
        headers = {
            "User-Agent": "SwarmAtlas/0.2 (user-selected GeoIP provider)",
            "Accept": "application/json",
        }
        if self.provider == "ip2location" and self.api_key:
            # Bearer authentication avoids putting the API key into the URL.
            headers["Authorization"] = f"Bearer {self.api_key}"
        if self.provider == "ipapi-is":
            body: dict[str, str] = {"q": ip}
            if self.api_key:
                body["key"] = self.api_key
            headers["Content-Type"] = "application/json"
            request = Request(
                self._url(ip),
                data=json.dumps(body).encode("utf-8"),
                headers=headers,
                method="POST",
            )
        else:
            request = Request(self._url(ip), headers=headers)
        with urlopen(request, timeout=7) as response:
            payload = response.read(MAX_RESPONSE_BYTES + 1)
            if self.provider == "ip-api" and str(response.headers.get("X-Rl", "")) == "0":
                try:
                    ttl = max(1.0, float(response.headers.get("X-Ttl", "60")))
                except (TypeError, ValueError):
                    ttl = 60.0
                self._provider_wait_until = max(
                    self._provider_wait_until, time.monotonic() + ttl
                )
        if len(payload) > MAX_RESPONSE_BYTES:
            raise ValueError("GeoIP provider returned an oversized response")
        data = json.loads(payload.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("GeoIP provider returned an unexpected response")
        if self.provider == "ipdata" and data.get("message") and not data.get("country_code"):
            raise RuntimeError(str(data.get("message")))
        if self.provider == "ipapi-is" and data.get("error"):
            raise RuntimeError(str(data.get("reason") or data.get("error")))
        if self.provider == "2ip" and data.get("error"):
            raise RuntimeError(str(data.get("message") or data.get("error")))
        return _normalize(self.provider, data)

    def _run(self) -> None:
        next_request_at = 0.0
        while not self._stop.is_set():
            try:
                ip = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                now = time.monotonic()
                request_due = max(next_request_at, self._provider_wait_until)
                delay = max(0.0, request_due - now)
                if delay and self._stop.wait(delay):
                    break
                today = datetime.now(timezone.utc).date().isoformat()
                month = datetime.now(timezone.utc).strftime("%Y-%m")
                if self._usage_day != today:
                    self._usage_count = 0
                    self._quota_reached = False
                    self._usage_day = today
                if self._usage_month != month:
                    self._monthly_usage_count = 0
                    self._monthly_quota_reached = False
                    self._usage_month = month
                daily_limit = self._daily_limit()
                if daily_limit is not None and self._usage_count >= daily_limit:
                    self._quota_reached = True
                    self.status = f"{PROVIDER_NAMES[self.provider]} daily free lookup budget reached ({daily_limit})"
                    continue
                monthly_limit = self._monthly_limit()
                if monthly_limit is not None and self._monthly_usage_count >= monthly_limit:
                    self._monthly_quota_reached = True
                    self.status = f"{PROVIDER_NAMES[self.provider]} monthly free lookup budget reached ({monthly_limit})"
                    continue
                self._usage_count += 1
                self._monthly_usage_count += 1
                try:
                    self._save_usage()
                except OSError:
                    _LOGGER.warning("Could not persist GeoIP provider request counter")
                next_request_at = time.monotonic() + self._minimum_interval()
                try:
                    result = self._fetch(ip)
                except Exception as exc:
                    backoff = 300.0
                    if isinstance(exc, HTTPError) and exc.code == 429:
                        try:
                            backoff = max(
                                60.0,
                                min(
                                    3600.0,
                                    float(
                                        exc.headers.get("Retry-After")
                                        or exc.headers.get("X-Ttl")
                                        or "300"
                                    ),
                                ),
                            )
                        except (TypeError, ValueError):
                            backoff = 300.0
                        # A rate-limit response applies to this client/provider,
                        # not just the one destination IP.
                        self._provider_wait_until = max(
                            self._provider_wait_until, time.monotonic() + backoff
                        )
                    with self._lock:
                        self._failed_until[ip] = time.monotonic() + backoff
                        while len(self._failed_until) > CACHE_LIMIT:
                            oldest = next(iter(self._failed_until))
                            self._failed_until.pop(oldest, None)
                    detail = str(exc)
                    if self.api_key:
                        detail = detail.replace(self.api_key, "[redacted]")
                    self.status = (
                        f"{PROVIDER_NAMES[self.provider]} lookup failed: "
                        f"{type(exc).__name__}: {detail}"
                    )
                    _LOGGER.debug(
                        "Remote GeoIP lookup failed (%s): %s",
                        type(exc).__name__,
                        detail,
                    )
                    continue
                if result:
                    with self._lock:
                        self._failed_until.pop(ip, None)
                        self._cache[ip] = result
                        self._cache.move_to_end(ip)
                        while len(self._cache) > CACHE_LIMIT:
                            self._cache.popitem(last=False)
                    self.status = f"{PROVIDER_NAMES[self.provider]} connected · cached {len(self._cache)} addresses"
                else:
                    # Negative-cache valid but currently unlocatable records as
                    # well, otherwise each 15-second socket scan repeats them.
                    with self._lock:
                        self._failed_until[ip] = time.monotonic() + 3600.0
            finally:
                with self._lock:
                    self._pending.discard(ip)
                self._queue.task_done()

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        with self._lock:
            self._pending.clear()
            self._cache.clear()
            self._failed_until.clear()
