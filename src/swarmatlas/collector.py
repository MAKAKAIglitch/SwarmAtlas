from __future__ import annotations

import ipaddress
import socket
import threading
import time
from typing import Any

import psutil
from PySide6.QtCore import QThread, Signal

from .config import AppConfig, database_path
from .geoip import GeoLocator
from .storage import Store


# Conservative polling: socket enumeration is less frequent than byte counters.
COUNTER_SAMPLE_INTERVAL_SEC = 1.0
ENDPOINT_SCAN_INTERVAL_SEC = 15.0
HISTORY_SAMPLE_INTERVAL_SEC = 10.0
MAX_TRACKED_ENDPOINTS = 2000

VIRTUAL_MARKERS = (
    "loopback", "virtual", "vmware", "virtualbox", "hyper-v", "vethernet",
    "wintun", "wireguard", "zerotier", "tailscale", "tap-", "docker", "wsl",
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


def collect_public_endpoints(limit: int = MAX_TRACKED_ENDPOINTS) -> list[dict[str, Any]]:
    """Read the OS socket table; do not capture packets or open sockets."""
    found: dict[tuple[str, int, str], dict[str, Any]] = {}
    for connection in psutil.net_connections(kind="inet"):
        remote = connection.raddr
        if not remote:
            continue
        ip = str(getattr(remote, "ip", remote[0]))
        try:
            address = ipaddress.ip_address(ip)
        except ValueError:
            continue
        if not address.is_global:
            continue

        port = int(getattr(remote, "port", remote[1]))
        protocol = "UDP" if connection.type == socket.SOCK_DGRAM else "TCP"
        key = (ip, port, protocol)
        found[key] = {"ip": ip, "port": port, "protocol": protocol}
        if len(found) >= max(1, limit):
            break
    return list(found.values())


class NetworkWorker(QThread):
    snapshot_ready = Signal(dict)
    status_changed = Signal(str)

    def __init__(self, config: AppConfig):
        super().__init__()
        self._config = config
        self._lock = threading.Lock()
        self._requested_interface = config.interface
        self._stop_event = threading.Event()

    def set_interface(self, name: str) -> None:
        with self._lock:
            self._requested_interface = name

    def stop(self, timeout_ms: int = 10000) -> bool:
        """Request graceful shutdown; never forcibly terminate the worker."""
        self._stop_event.set()
        return self.wait(timeout_ms)

    def run(self) -> None:
        store: Store | None = None
        locator: GeoLocator | None = None
        try:
            store = Store(database_path())
            locator = GeoLocator(self._config.geoip_database)
            interface = self._resolve_interface(self._requested_interface)
            self.status_changed.emit(locator.status)
            if not interface:
                self.status_changed.emit("No active network interface found")

            previous_time = time.monotonic()
            last_endpoint_scan = 0.0
            last_history_sample = 0.0
            upload_rate = 0.0
            download_rate = 0.0
            connection_status = "Waiting for first network sample"

            while not self._stop_event.is_set():
                now_mono = time.monotonic()
                now_wall = time.time()

                with self._lock:
                    requested = self._requested_interface
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
                        connection_status = f"{len(endpoints)} public endpoints observed (scan every 15 s)"
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
                        "read_only": True,
                    })
                    self.snapshot_ready.emit(payload)

                self._stop_event.wait(COUNTER_SAMPLE_INTERVAL_SEC)

        except Exception as exc:
            self.status_changed.emit(f"Monitor stopped safely: {type(exc).__name__}: {exc}")
        finally:
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

    @staticmethod
    def _resolve_interface(requested: str) -> str:
        if requested == "auto" or not requested:
            return choose_default_interface()
        return requested if requested in psutil.net_if_stats() else choose_default_interface()
