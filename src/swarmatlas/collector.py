from __future__ import annotations

import ipaddress
import logging
import socket
import threading
import time
from typing import Any

import psutil
from PySide6.QtCore import QThread, Signal

from .config import AppConfig, database_path
from .geoip import GeoLocator
from .geoip_data import ensure_default_geoip_database
from .map_data import load_world_boundary_paths
from .native import (
    NATIVE_AVAILABLE,
    NATIVE_CAPABILITIES,
    backend_status,
    count_udp_bindings,
    list_tcp_connections,
)
from .storage import Store
from .theme import resolve_language


_LOGGER = logging.getLogger(__name__)


# Conservative polling: the socket/owner tables are scanned less often than byte counters.
COUNTER_SAMPLE_INTERVAL_SEC = 1.0
INTERFACE_CHECK_INTERVAL_SEC = 5.0
ENDPOINT_SCAN_INTERVAL_SEC = 5.0
POWER_SAVE_COUNTER_SAMPLE_INTERVAL_SEC = 5.0
POWER_SAVE_INTERFACE_CHECK_INTERVAL_SEC = 20.0
POWER_SAVE_ENDPOINT_SCAN_INTERVAL_SEC = 15.0
HISTORY_SAMPLE_INTERVAL_SEC = 10.0
MAX_TRACKED_ENDPOINTS = 2000
PROCESS_NAME_CACHE_LIMIT = 4096
PROCESS_NAME_CACHE_TTL_SEC = 30.0

VIRTUAL_MARKERS = (
    "loopback", "virtual", "vmware", "virtualbox", "hyper-v", "vethernet",
    "wintun", "wireguard", "zerotier", "tailscale", "tap-", "docker", "wsl",
)
_PROCESS_NAME_CACHE: dict[int, tuple[float, str | None]] = {}


def sampling_intervals(power_saver_enabled: bool) -> tuple[float, float, float]:
    """Return counter, interface-check and endpoint-scan intervals for the current window state."""
    if power_saver_enabled:
        return (
            POWER_SAVE_COUNTER_SAMPLE_INTERVAL_SEC,
            POWER_SAVE_INTERFACE_CHECK_INTERVAL_SEC,
            POWER_SAVE_ENDPOINT_SCAN_INTERVAL_SEC,
        )
    return (
        COUNTER_SAMPLE_INTERVAL_SEC,
        INTERFACE_CHECK_INTERVAL_SEC,
        ENDPOINT_SCAN_INTERVAL_SEC,
    )


def available_interfaces() -> list[str]:
    """Read interface state without changing adapter settings."""
    stats = psutil.net_if_stats()
    return sorted(name for name, item in stats.items() if item.isup)


def choose_default_interface() -> str:
    """Choose an active interface using read-only OS counters."""
    stats = psutil.net_if_stats()
    counters = psutil.net_io_counters(pernic=True) or {}
    candidates: list[str] = []
    for name, item in stats.items():
        if not item.isup or any(marker in name.lower() for marker in VIRTUAL_MARKERS):
            continue
        candidates.append(name)

    if not candidates:
        candidates = [name for name, item in stats.items() if item.isup]
    if not candidates:
        return ""

    preferred = [
        name for name in candidates
        if any(token in name.lower() for token in ("ethernet", "wi-fi", "wifi", "wlan", "eth", "enp"))
    ]
    pool = preferred or candidates
    return max(
        pool,
        key=lambda name: (
            getattr(counters.get(name), "bytes_sent", 0)
            + getattr(counters.get(name), "bytes_recv", 0)
        ),
    )


def _process_name(process_id: int | None) -> str | None:
    """Resolve a PID name with a short cache lifetime to limit stale PID reuse."""
    if not process_id or process_id <= 0:
        return None

    now = time.monotonic()
    cached = _PROCESS_NAME_CACHE.get(process_id)
    if cached is not None and now - cached[0] < PROCESS_NAME_CACHE_TTL_SEC:
        return cached[1]

    try:
        name = psutil.Process(process_id).name()
    except (psutil.Error, OSError):
        name = None

    if len(_PROCESS_NAME_CACHE) >= PROCESS_NAME_CACHE_LIMIT:
        expired = [
            pid for pid, (checked_at, _) in _PROCESS_NAME_CACHE.items()
            if now - checked_at >= PROCESS_NAME_CACHE_TTL_SEC
        ]
        for pid in expired:
            _PROCESS_NAME_CACHE.pop(pid, None)
        if len(_PROCESS_NAME_CACHE) >= PROCESS_NAME_CACHE_LIMIT:
            _PROCESS_NAME_CACHE.pop(next(iter(_PROCESS_NAME_CACHE)))

    _PROCESS_NAME_CACHE[process_id] = (now, name)
    return name


def _warm_process_name_cache(process_ids: Any) -> None:
    """Resolve uncached PIDs in one process-table pass instead of opening each PID separately."""
    now = time.monotonic()
    requested: set[int] = set()
    for process_id in process_ids:
        if process_id is None:
            continue
        try:
            pid = int(process_id)
        except (TypeError, ValueError, OverflowError):
            continue
        if pid > 0:
            requested.add(pid)
    if not requested:
        return

    expired = [
        pid for pid, (checked_at, _) in _PROCESS_NAME_CACHE.items()
        if now - checked_at >= PROCESS_NAME_CACHE_TTL_SEC
    ]
    for pid in expired:
        _PROCESS_NAME_CACHE.pop(pid, None)

    missing = {
        pid for pid in requested
        if pid not in _PROCESS_NAME_CACHE
    }
    if not missing:
        return

    try:
        processes = psutil.process_iter(attrs=["pid", "name"])
        for process in processes:
            try:
                info = process.info
                pid = int(info.get("pid") or 0)
                if pid not in missing:
                    continue
                name = info.get("name")
                _PROCESS_NAME_CACHE[pid] = (
                    now,
                    str(name) if name else None,
                )
                missing.remove(pid)
                if not missing:
                    break
            except (psutil.Error, OSError, AttributeError, TypeError, ValueError):
                continue
    except (psutil.Error, OSError):
        # Fall back to the per-PID resolver below when process enumeration is restricted.
        return

    for pid in missing:
        _PROCESS_NAME_CACHE[pid] = (now, None)

    while len(_PROCESS_NAME_CACHE) > PROCESS_NAME_CACHE_LIMIT:
        _PROCESS_NAME_CACHE.pop(next(iter(_PROCESS_NAME_CACHE)), None)


def _address_parts(value: Any) -> tuple[str, int] | None:
    """Accept psutil named tuples and ordinary two-item socket address tuples."""
    if value is None:
        return None

    try:
        ip_value = value.ip
        port_value = value.port
    except (AttributeError, TypeError):
        try:
            ip_value, port_value = value[0], value[1]
        except (IndexError, KeyError, TypeError):
            return None

    try:
        port = int(port_value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not 0 <= port <= 65535:
        return None
    return str(ip_value), port


def _parse_remote(remote: Any) -> tuple[str, int] | None:
    parts = _address_parts(remote)
    if parts is None:
        return None
    ip, port = parts
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return None
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
        ip = str(address)
    if not address.is_global:
        return None
    return ip, port


def _make_endpoint(
    ip: str,
    port: int,
    protocol: str,
    process_id: int | None = None,
    local_port: int = 0,
    connection_state: str = "",
) -> dict[str, Any]:
    return {
        "ip": ip,
        "port": int(port),
        "protocol": protocol,
        "process_id": int(process_id) if process_id else None,
        "process_name": _process_name(int(process_id)) if process_id else None,
        "local_port": int(local_port or 0),
        "connection_state": connection_state or None,
    }



def _enrich_endpoints_with_geoip(
    endpoints: list[dict[str, Any]],
    locator: GeoLocator,
) -> None:
    """Look up each destination IP once per scan, even if many sockets share it."""
    locations: dict[str, dict[str, Any]] = {}
    for endpoint in endpoints:
        ip = str(endpoint["ip"])
        location = locations.get(ip)
        if location is None:
            location = locator.lookup(ip)
            locations[ip] = location
        endpoint.update(location)


def _interleave_endpoints(
    tcp: dict[tuple[str, int, str, int, int], dict[str, Any]],
    udp: dict[tuple[str, int, str, int, int], dict[str, Any]],
    limit: int,
) -> list[dict[str, Any]]:
    """Keep UDP visible when a busy TCP host reaches the endpoint display cap."""
    tcp_items = iter(tcp.values())
    udp_items = iter(udp.values())
    result: list[dict[str, Any]] = []
    while len(result) < limit:
        progressed = False
        try:
            result.append(next(tcp_items))
            progressed = True
        except StopIteration:
            pass
        if len(result) >= limit:
            break
        try:
            result.append(next(udp_items))
            progressed = True
        except StopIteration:
            pass
        if not progressed:
            break
    return result


def collect_public_endpoint_snapshot(
    limit: int = MAX_TRACKED_ENDPOINTS,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Collect public peers and report local UDP sockets separately from UDP peers."""
    max_items = max(1, int(limit))
    tcp_found: dict[tuple[str, int, str, int, int], dict[str, Any]] = {}
    udp_found: dict[tuple[str, int, str, int, int], dict[str, Any]] = {}
    udp_binding_count = 0
    udp_binding_error: str | None = None

    if NATIVE_AVAILABLE:
        native_ok = False
        try:
            tcp_rows = list_tcp_connections(limit=max_items)
            _warm_process_name_cache(item.get("process_id") for item in tcp_rows)
            native_ok = True
            for item in tcp_rows:
                if "tcp-public-endpoints" in NATIVE_CAPABILITIES:
                    # The current Rust backend already validates/canonicalizes
                    # public IPs and ports before crossing the PyO3 boundary.
                    ip = str(item["ip"])
                    port = int(item["port"])
                else:
                    parsed = _parse_remote((item["ip"], item["port"]))
                    if parsed is None:
                        continue
                    ip, port = parsed
                endpoint = _make_endpoint(
                    ip,
                    port,
                    "TCP",
                    item.get("process_id"),
                    item.get("local_port", 0),
                    item.get("connection_state", ""),
                )
                key = (
                    ip, port, "TCP",
                    int(endpoint.get("process_id") or 0),
                    int(endpoint.get("local_port") or 0),
                )
                tcp_found[key] = endpoint
                if len(tcp_found) >= max_items:
                    break
        except Exception:
            # A missing DLL, denied table, or incompatible extension falls back
            # to psutil. Nothing is elevated or changed in the Windows stack.
            native_ok = False
            tcp_found.clear()

        if native_ok:
            # A successful Windows API call may still produce no usable public
            # rows. Cross-check an empty native result with psutil before
            # declaring that there are no active TCP destinations.
            if not tcp_found:
                try:
                    fallback_tcp = psutil.net_connections(kind="tcp")
                except psutil.Error:
                    fallback_tcp = []

                _warm_process_name_cache(
                    getattr(connection, "pid", None) for connection in fallback_tcp
                )
                for connection in fallback_tcp:
                    parsed = _parse_remote(connection.raddr)
                    if parsed is None:
                        continue
                    ip, port = parsed
                    local = _address_parts(connection.laddr)
                    local_port = local[1] if local is not None else 0
                    process_id = getattr(connection, "pid", None)
                    endpoint = _make_endpoint(
                        ip,
                        port,
                        "TCP",
                        process_id,
                        local_port,
                        str(getattr(connection, "status", "") or ""),
                    )
                    key = (
                        ip, port, "TCP",
                        int(endpoint.get("process_id") or 0),
                        int(endpoint.get("local_port") or 0),
                    )
                    tcp_found[key] = endpoint
                    if len(tcp_found) >= max_items:
                        break

            # Windows' owner-PID UDP table contains local bindings only; it
            # has no remote address field. Count it in Rust without asking
            # psutil to read and materialize the same table a second time.
            try:
                udp_binding_count = count_udp_bindings()
                udp_binding_error = None
            except Exception as exc:
                udp_binding_count = 0
                udp_binding_error = f"{type(exc).__name__}: {exc}"

            stats = {
                "udp_local_sockets": udp_binding_count,
                "udp_public_peers": len(udp_found),
                "udp_binding_error": udp_binding_error,
            }
            return _interleave_endpoints(tcp_found, udp_found, max_items), stats

    # Compatibility backend. This is also used when the native extension is
    # not built, preserving the existing first-run experience.
    connections = psutil.net_connections(kind="inet")
    udp_binding_count = sum(
        1 for connection in connections if connection.type == socket.SOCK_DGRAM
    )
    _warm_process_name_cache(
        getattr(connection, "pid", None) for connection in connections
    )
    for connection in connections:
        parsed = _parse_remote(connection.raddr)
        if parsed is None:
            continue
        ip, port = parsed
        protocol = "UDP" if connection.type == socket.SOCK_DGRAM else "TCP"
        local = _address_parts(connection.laddr)
        local_port = local[1] if local is not None else 0
        process_id = getattr(connection, "pid", None)
        endpoint = _make_endpoint(
            ip,
            port,
            protocol,
            process_id,
            local_port,
            str(getattr(connection, "status", "") or ""),
        )
        key = (
            ip, port, protocol,
            int(endpoint.get("process_id") or 0),
            int(endpoint.get("local_port") or 0),
        )
        target = udp_found if protocol == "UDP" else tcp_found
        if len(target) < max_items:
            target[key] = endpoint
        if len(tcp_found) >= max_items and len(udp_found) >= max_items:
            break

    stats = {
        "udp_local_sockets": udp_binding_count,
        "udp_public_peers": len(udp_found),
        "udp_binding_error": None,
    }
    return _interleave_endpoints(tcp_found, udp_found, max_items), stats


def collect_public_endpoints(limit: int = MAX_TRACKED_ENDPOINTS) -> list[dict[str, Any]]:
    """Backward-compatible peer-only view of the endpoint collector."""
    endpoints, _stats = collect_public_endpoint_snapshot(limit)
    return endpoints


class NetworkWorker(QThread):
    snapshot_ready = Signal(dict)
    status_changed = Signal(str)
    startup_ready = Signal(str)
    map_data_ready = Signal(object, object)

    def __init__(self, config: AppConfig):
        super().__init__()
        self._config = config
        self._lock = threading.Lock()
        self._requested_interface = config.interface
        self._requested_geoip_databases = tuple(config.configured_geoip_databases())
        self._requested_geoip_provider = config.geoip_provider
        self._requested_geoip_api_key = config.geoip_api_key
        self._history_range_seconds = 3600
        self._power_saver_mode = False
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()

    def _resolve_interface(self, requested: str | None) -> str:
        """Resolve the selected adapter, falling back to automatic selection if unavailable."""
        name = str(requested or "auto")
        if not name.strip() or name.strip().casefold() == "auto":
            return choose_default_interface()

        stats = psutil.net_if_stats()
        state = stats.get(name)
        if state is not None and state.isup:
            return name
        return choose_default_interface()
    def set_interface(self, name: str) -> None:
        with self._lock:
            self._requested_interface = name

    def set_geoip_databases(self, paths: list[str]) -> None:
        with self._lock:
            self._requested_geoip_databases = tuple(paths)

    def set_geoip_provider(self, provider: str, api_key: str = "") -> None:
        with self._lock:
            self._requested_geoip_provider = provider
            self._requested_geoip_api_key = api_key

    def set_history_range(self, seconds: int) -> None:
        with self._lock:
            self._history_range_seconds = max(60, min(int(seconds), 5 * 365 * 24 * 60 * 60))

    def set_power_saver_mode(self, enabled: bool) -> None:
        """Reduce background polling while the desktop window is minimized."""
        with self._lock:
            changed = self._power_saver_mode != bool(enabled)
            self._power_saver_mode = bool(enabled)
        if changed:
            self._wake_event.set()

    def stop(self, timeout_ms: int = 10000) -> bool:
        """Request graceful shutdown; never forcibly terminate the worker."""
        self._stop_event.set()
        self._wake_event.set()
        return self.wait(timeout_ms)

    def restart(self) -> None:
        """Restart a finished worker after a user-requested storage move."""
        if self.isRunning():
            return
        self._stop_event.clear()
        self._wake_event.clear()
        self.start()

    def run(self) -> None:
        store: Store | None = None
        locator: GeoLocator | None = None
        startup_notified = False
        try:
            store = Store(database_path(self._config))
            interface = self._resolve_interface(self._requested_interface)

            # Show the desktop immediately. First-run data downloads continue
            # on this worker and never block the Qt GUI thread.
            self.startup_ready.emit("Preparing local network monitor")
            startup_notified = True

            with self._lock:
                configured_geoip = self._requested_geoip_databases
                selected_provider = self._requested_geoip_provider
                selected_api_key = self._requested_geoip_api_key

            effective_geoip: tuple[str, ...] = ()
            if selected_provider == "dbip":
                if configured_geoip:
                    effective_geoip = configured_geoip
                else:
                    self.status_changed.emit("Downloading or checking the local DB-IP City Lite database…")
                    try:
                        database = ensure_default_geoip_database(
                            progress=lambda value: self.status_changed.emit(
                                f"Downloading DB-IP City Lite database: {value}%"
                            )
                        )
                        effective_geoip = (str(database),)
                    except Exception as exc:
                        _LOGGER.exception("Could not prepare the default DB-IP City Lite database")
                        self.status_changed.emit(
                            f"Could not prepare the default DB-IP database: {type(exc).__name__}: {exc}"
                        )

            locator = GeoLocator(
                effective_geoip,
                language=resolve_language(self._config.language),
                provider=selected_provider,
                api_key=selected_api_key,
            )
            loaded_geoip = (effective_geoip, selected_provider, selected_api_key)
            self.status_changed.emit(locator.status)
            if not interface:
                self.status_changed.emit("No active network interface found")

            # Download and parse Natural Earth polygons off the GUI thread.
            # Once cached, the map works offline on every later launch.
            try:
                map_started = time.perf_counter()
                # Stream one shapefile ring at a time into simplified Qt paths.
                # This avoids retaining all raw Natural Earth coordinates before
                # allocating the renderable geometry.
                country_path, region_path = load_world_boundary_paths(
                    progress=lambda message: self.status_changed.emit(message)
                )
                _LOGGER.info(
                    "Prepared map geometry off-thread in %.1f ms "
                    "(%d country, %d region path elements)",
                    (time.perf_counter() - map_started) * 1000.0,
                    country_path.elementCount(),
                    region_path.elementCount(),
                )
                self.map_data_ready.emit(country_path, region_path)
                self.status_changed.emit("Country and region map ready")
            except Exception as exc:
                _LOGGER.exception("Could not load offline Natural Earth country/region boundaries")
                self.status_changed.emit(
                    f"Offline country/region map unavailable: {type(exc).__name__}: {exc}"
                )

            previous_time = time.monotonic()
            last_interface_check = previous_time
            last_interface_request = self._requested_interface
            last_endpoint_scan = 0.0
            last_history_sample = 0.0
            upload_rate = 0.0
            download_rate = 0.0
            interface_totals = {"upload_total": 0, "download_total": 0}
            cached_destination_summary: dict[str, Any] = {
                "active_endpoints": [],
                "countries": [],
                "regions": [],
            }
            cached_history: list[dict[str, Any]] = []
            cached_history_range: int | None = None
            last_history_refresh = 0.0
            connection_status = "Waiting for first network sample"

            while not self._stop_event.is_set():
                now_mono = time.monotonic()
                now_wall = time.time()

                with self._lock:
                    requested = self._requested_interface
                    requested_geoip = self._requested_geoip_databases
                    requested_provider = self._requested_geoip_provider
                    requested_api_key = self._requested_geoip_api_key
                    history_range_seconds = self._history_range_seconds
                    power_saver_mode = self._power_saver_mode

                (
                    counter_sample_interval,
                    interface_check_interval,
                    endpoint_scan_interval,
                ) = sampling_intervals(power_saver_mode)

                # Local DB-IP remains the private default. Remote lookup is only
                # active after the user explicitly selects an online provider.
                effective_geoip: tuple[str, ...] = ()
                if requested_provider == "dbip":
                    if requested_geoip:
                        effective_geoip = requested_geoip
                    else:
                        from .geoip_data import default_geoip_database_path

                        default_path = default_geoip_database_path()
                        effective_geoip = (str(default_path),) if default_path.is_file() else ()

                geoip_signature = (effective_geoip, requested_provider, requested_api_key)
                if geoip_signature != loaded_geoip:
                    if locator is not None:
                        locator.close()
                    locator = GeoLocator(
                        effective_geoip,
                        language=resolve_language(self._config.language),
                        provider=requested_provider,
                        api_key=requested_api_key,
                    )
                    loaded_geoip = geoip_signature
                    self.status_changed.emit(locator.status)

                interface_request_changed = requested != last_interface_request
                interface_check_due = (
                    now_mono - last_interface_check >= interface_check_interval
                )
                if interface_request_changed or interface_check_due:
                    try:
                        resolved = self._resolve_interface(requested)
                    except psutil.Error:
                        resolved = interface
                    last_interface_request = requested
                    last_interface_check = now_mono
                    if resolved != interface:
                        interface = resolved
                        previous_time = now_mono

                try:
                    counters = psutil.net_io_counters(pernic=True) or {}
                    current = counters.get(interface) if interface else None
                except psutil.Error as exc:
                    current = None
                    connection_status = f"Interface counters unavailable: {exc}"

                elapsed = max(0.001, now_mono - previous_time)
                previous_time = now_mono

                if current is not None:
                    totals = store.record_interface(
                        interface, current.bytes_sent, current.bytes_recv, now_wall
                    )
                    interface_totals = {
                        "upload_total": int(totals["upload_total"]),
                        "download_total": int(totals["download_total"]),
                    }
                    upload_rate = totals["upload_delta"] / elapsed
                    download_rate = totals["download_delta"] / elapsed
                else:
                    upload_rate = 0.0
                    download_rate = 0.0

                if now_mono - last_endpoint_scan >= endpoint_scan_interval:
                    scan_started = time.perf_counter()
                    scanned_endpoint_count = 0
                    try:
                        endpoints, endpoint_stats = collect_public_endpoint_snapshot()
                        scanned_endpoint_count = len(endpoints)
                        _enrich_endpoints_with_geoip(endpoints, locator)
                        store.update_destinations(endpoints, now_wall)
                        cached_destination_summary = store.destination_summary(now_wall)
                        owner_count = sum(
                            1 for endpoint in endpoints if endpoint.get("process_id")
                        )
                        tcp_count = sum(
                            1 for endpoint in endpoints if endpoint.get("protocol") == "TCP"
                        )
                        udp_count = int(endpoint_stats.get("udp_public_peers", 0))
                        udp_sockets = int(endpoint_stats.get("udp_local_sockets", 0))
                        connection_status = (
                            f"{len(endpoints)} public endpoints observed "
                            f"(TCP: {tcp_count}, UDP destinations: {udp_count}); "
                            f"local UDP sockets: {udp_sockets}; "
                            f"{owner_count} with process owner "
                            f"(scan every {endpoint_scan_interval:g} s)"
                        )
                        if udp_count == 0 and udp_sockets:
                            connection_status += (
                                "; Windows exposes local UDP bindings, not remote "
                                "addresses for unconnected UDP sockets"
                            )
                        udp_error = endpoint_stats.get("udp_binding_error")
                        if udp_error:
                            connection_status += f"; UDP table warning: {udp_error}"
                    except psutil.AccessDenied:
                        connection_status = "Socket table access restricted; interface totals still work"
                    except psutil.Error as exc:
                        connection_status = f"Socket table unavailable: {exc}"
                    except Exception as exc:
                        connection_status = f"Endpoint scan unavailable: {exc}"
                    scan_finished = time.monotonic()
                    scan_duration_ms = (time.perf_counter() - scan_started) * 1000.0
                    connection_status += f"; scan {scan_duration_ms:.0f} ms"
                    _LOGGER.debug(
                        "Endpoint scan completed in %.1f ms (%d public endpoints)",
                        scan_duration_ms,
                        scanned_endpoint_count,
                    )
                    # Schedule the next poll after completion so a slow
                    # scan cannot trigger a tight retry loop.
                    last_endpoint_scan = scan_finished

                if now_mono - last_history_sample >= HISTORY_SAMPLE_INTERVAL_SEC:
                    store.add_sample(now_wall, upload_rate, download_rate)
                    last_history_sample = now_mono

                # History rows only change every ten seconds (or when the user
                # changes the selected range). Do not run an aggregation query on
                # every one-second rate tick.
                history_range_changed = cached_history_range != history_range_seconds
                history_refresh_due = (
                    now_mono - last_history_refresh >= HISTORY_SAMPLE_INTERVAL_SEC
                )
                if history_range_changed or history_refresh_due:
                    cached_history = store.history(history_range_seconds, now_wall)
                    cached_history_range = history_range_seconds
                    last_history_refresh = now_mono

                if interface:
                    payload = {
                        **interface_totals,
                        **cached_destination_summary,
                        "samples": cached_history,
                    }
                    payload.update({
                        "history_range_seconds": history_range_seconds,
                        "interface": interface,
                        "upload_bps": upload_rate,
                        "download_bps": download_rate,
                        "connection_status": connection_status,
                        "geoip_status": locator.status,
                        "native_status": backend_status(),
                        "read_only": True,
                    })
                    self.snapshot_ready.emit(payload)

                # A mode change wakes the worker immediately instead of making
                # the user wait for the previous low-power timeout to expire.
                self._wake_event.wait(counter_sample_interval)
                self._wake_event.clear()

        except Exception as exc:
            _LOGGER.exception("SwarmAtlas network worker stopped after an unexpected error")
            message = f"Monitor stopped safely: {type(exc).__name__}: {exc}"
            self.status_changed.emit(message)
            if not startup_notified:
                self.startup_ready.emit(message)
                startup_notified = True
        finally:
            if not startup_notified:
                self.startup_ready.emit("Monitor initialization ended")
            if locator is not None:
                try:
                    locator.close()
                except Exception:
                    pass
            if store is not None:
                try:
                    store.close()
                except Exception:
                    pass
