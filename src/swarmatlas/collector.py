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


VIRTUAL_MARKERS = (
    "loopback", "virtual", "vmware", "virtualbox", "hyper-v", "vethernet",
    "wintun", "wireguard", "zerotier", "tailscale", "tap-", "docker", "wsl",
)


def available_interfaces() -> list[str]:
    stats = psutil.net_if_stats()
    return sorted(name for name, item in stats.items() if item.isup)


def choose_default_interface() -> str:
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


def collect_public_endpoints() -> list[dict[str, Any]]:
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

    def stop(self) -> None:
        self._stop_event.set()
        self.wait(4000)

    def run(self) -> None:
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

        try:
            while not self._stop_event.is_set():
                now_mono = time.monotonic()
                now_wall = time.time()

                with self._lock:
                    requested = self._requested_interface
                resolved = self._resolve_interface(requested)
                if resolved != interface:
                    interface = resolved
                    previous_time = now_mono

                counters = psutil.net_io_counters(pernic=True) or {}
                current = counters.get(interface) if interface else None
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

                if now_mono - last_endpoint_scan >= 5.0:
                    try:
                        endpoints = collect_public_endpoints()
                        for endpoint in endpoints:
                            endpoint.update(locator.lookup(endpoint["ip"]))
                        store.update_destinations(endpoints, now_wall)
                        connection_status = f"{len(endpoints)} public endpoints observed"
                    except psutil.AccessDenied:
                        connection_status = "Windows restricted the socket table; run elevated for fuller endpoint visibility"
                    except Exception as exc:
                        connection_status = f"Endpoint scan unavailable: {exc}"
                    last_endpoint_scan = now_mono

                if now_mono - last_history_sample >= 10.0:
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
                    })
                    self.snapshot_ready.emit(payload)

                self._stop_event.wait(1.0)
        finally:
            locator.close()
            store.close()

    @staticmethod
    def _resolve_interface(requested: str) -> str:
        if requested == "auto" or not requested:
            return choose_default_interface()
        return requested if requested in psutil.net_if_stats() else choose_default_interface()
