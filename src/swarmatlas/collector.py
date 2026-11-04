from __future__ import annotations

import ipaddress
import socket
import threading
import time
from collections import defaultdict
from typing import Any

import psutil
from PySide6.QtCore import QThread, Signal

from .config import AppConfig, database_path
from .geoip import GeoLocator
from .native import NATIVE_AVAILABLE, backend_status, list_tcp_connections, list_udp_bindings
from .storage import Store


# Conservative polling: the socket/owner tables are scanned less often than byte counters.
COUNTER_SAMPLE_INTERVAL_SEC = 1.0
ENDPOINT_SCAN_INTERVAL_SEC = 15.0
HISTORY_SAMPLE_INTERVAL_SEC = 10.0
MAX_TRACKED_ENDPOINTS = 2000
PROCESS_NAME_CACHE_LIMIT = 512
PROCESS_NAME_CACHE_TTL_SEC = 30.0

VIRTUAL_MARKERS = (
    "loopback", "virtual", "vmware", "virtualbox", "hyper-v", "vethernet",
    "wintun", "wireguard", "zerotier", "tailscale", "tap-", "docker", "wsl",
)
_PROCESS_NAME_CACHE: dict[int, tuple[float, str | None]] = {}


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


def _udp_owner_index() -> tuple[dict[tuple[str, int], int], dict[int, set[int]]]:
    exact: dict[tuple[str, int], int] = {}
    by_port: dict[int, set[int]] = defaultdict(set)
    if not NATIVE_AVAILABLE:
        return exact, by_port

    try:
        for binding in list_udp_bindings():
            process_id = binding.get("process_id")
            local_port = int(binding["local_port"])
            local_ip = str(binding["local_ip"])
            if process_id:
                exact[(local_ip, local_port)] = int(process_id)
                by_port[local_port].add(int(process_id))
    except Exception:
        # UDP bindings are supplemental; TCP ownership remains available.
        return {}, {}
    return exact, by_port


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


def collect_public_endpoints(limit: int = MAX_TRACKED_ENDPOINTS) -> list[dict[str, Any]]:
    """Read public remote endpoints and their owning PIDs, without packet capture."""
    max_items = max(1, int(limit))
    tcp_found: dict[tuple[str, int, str, int, int], dict[str, Any]] = {}
    udp_found: dict[tuple[str, int, str, int, int], dict[str, Any]] = {}

    if NATIVE_AVAILABLE:
        native_ok = False
        try:
            tcp_rows = list_tcp_connections(limit=max_items)
            native_ok = True
            for item in tcp_rows:
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
            udp_exact, udp_by_port = _udp_owner_index()
            try:
                udp_connections = psutil.net_connections(kind="udp")
            except psutil.Error:
                udp_connections = []

            for connection in udp_connections:
                parsed = _parse_remote(connection.raddr)
                if parsed is None:
                    continue
                ip, port = parsed
                local = _address_parts(connection.laddr)
                local_ip, local_port = local if local is not None else ("", 0)
                process_id = getattr(connection, "pid", None)
                if not process_id:
                    process_id = udp_exact.get((local_ip, local_port))
                if not process_id:
                    matches = udp_by_port.get(local_port, set())
                    if len(matches) == 1:
                        process_id = next(iter(matches))
                endpoint = _make_endpoint(
                    ip, port, "UDP", process_id, local_port, "CONNECTED"
                )
                key = (
                    ip, port, "UDP",
                    int(endpoint.get("process_id") or 0),
                    int(endpoint.get("local_port") or 0),
                )
                udp_found[key] = endpoint
                if len(udp_found) >= max_items:
                    break
            return _interleave_endpoints(tcp_found, udp_found, max_items)

    # Compatibility backend. This is also used when the native extension is
    # not built, preserving the existing first-run experience.
    for connection in psutil.net_connections(kind="inet"):
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
    return _interleave_endpoints(tcp_found, udp_found, max_items)


class NetworkWorker(QThread):
    snapshot_ready = Signal(dict)
    status_changed = Signal(str)
    startup_ready = Signal(str)

    def __init__(self, config: AppConfig):
        super().__init__()
        self._config = config
        self._lock = threading.Lock()
        self._requested_interface = config.interface
        self._requested_geoip_databases = tuple(config.resolved_geoip_databases())
        self._stop_event = threading.Event()

    def set_interface(self, name: str) -> None:
        with self._lock:
            self._requested_interface = name

    def set_geoip_databases(self, paths: list[str]) -> None:
        with self._lock:
            self._requested_geoip_databases = tuple(paths)

    def stop(self, timeout_ms: int = 10000) -> bool:
        """Request graceful shutdown; never forcibly terminate the worker."""
        self._stop_event.set()
        return self.wait(timeout_ms)

    def run(self) -> None:
        store: Store | None = None
        locator: GeoLocator | None = None
        startup_notified = False
        try:
            store = Store(database_path())
            locator = GeoLocator(self._requested_geoip_databases)
            interface = self._resolve_interface(self._requested_interface)
            status = locator.status
            self.status_changed.emit(status)
            if not interface:
                self.status_changed.emit("No active network interface found")

            # MMDB readers are opened on this worker; UI startup stays responsive.
            self.startup_ready.emit(status)
            startup_notified = True

            previous_time = time.monotonic()
            last_endpoint_scan = 0.0
            last_history_sample = 0.0
            upload_rate = 0.0
            download_rate = 0.0
            connection_status = "Waiting for first network sample"
            loaded_geoip = self._requested_geoip_databases

            while not self._stop_event.is_set():
                now_mono = time.monotonic()
                now_wall = time.time()

                with self._lock:
                    requested = self._requested_interface
                    requested_geoip = self._requested_geoip_databases

                if requested_geoip != loaded_geoip:
                    if locator is not None:
                        locator.close()
                    locator = GeoLocator(requested_geoip)
                    loaded_geoip = requested_geoip
                    self.status_changed.emit(locator.status)

                try:
                    resolved = self._resolve_interface(requested)
                except psutil.Error:
                    resolved = interface
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
                    upload_rate = totals["upload_delta"] / elapsed
                    download_rate = totals["download_delta"] / elapsed
                else:
                    upload_rate = 0.0
                    download_rate = 0.0

                if now_mono - last_endpoint_scan >= ENDPOINT_SCAN_INTERVAL_SEC:
                    try:
                        endpoints = collect_public_endpoints()
                        for endpoint in endpoints:
                            endpoint.update(locator.lookup(endpoint["ip"]))
                        store.update_destinations(endpoints, now_wall)
                        owner_count = sum(
                            1 for endpoint in endpoints if endpoint.get("process_id")
                        )
                        connection_status = (
                            f"{len(endpoints)} public endpoints observed; "
                            f"{owner_count} with process owner (scan every 15 s)"
                        )
                    except psutil.AccessDenied:
                        connection_status = "Socket table access restricted; interface totals still work"
                    except psutil.Error as exc:
                        connection_status = f"Socket table unavailable: {exc}"
                    except Exception as exc:
                        connection_status = f"Endpoint scan unavailable: {exc}"
                    last_endpoint_scan = now_mono

                if now_mono - last_history_sample >= HISTORY_SAMPLE_INTERVAL_SEC:
                    store.add_sample(now_wall, upload_rate, download_rate)
                    last_history_sample = now_mono

                if interface:
                    payload = store.summary(interface, now_wall)
                    payload.update({
                        "interface": interface,
                        "upload_bps": upload_rate,
                        "download_bps": download_rate,
                        "connection_status": connection_status,
                        "geoip_status": locator.status,
                        "native_status": backend_status(),
                        "read_only": True,
                    })
                    self.snapshot_ready.emit(payload)

                self._stop_event.wait(COUNTER_SAMPLE_INTERVAL_SEC)

        except Exception as exc:
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
