from __future__ import annotations

import socket
from collections import namedtuple
from types import SimpleNamespace

from swarmatlas import collector
from swarmatlas.config import AppConfig


Address = namedtuple("Address", "ip port")


def test_power_saver_is_only_enabled_outside_the_foreground() -> None:
    assert not collector.should_use_power_saver(application_active=True, minimized=False)
    assert collector.should_use_power_saver(application_active=False, minimized=False)
    assert collector.should_use_power_saver(application_active=True, minimized=True)
    assert collector.should_use_power_saver(application_active=False, minimized=True)


def test_minimized_window_polling_profile_reduces_background_work() -> None:
    normal = collector.sampling_intervals(False)
    power_saver = collector.sampling_intervals(True)

    assert normal == (1.0, 5.0, 5.0)
    assert power_saver == (5.0, 20.0, 15.0)
    assert collector.HISTORY_SAMPLE_INTERVAL_SEC == 10.0


def test_power_saver_mode_wakes_worker_only_when_profile_changes() -> None:
    worker = collector.NetworkWorker(AppConfig())

    worker.set_power_saver_mode(True)
    assert worker._power_saver_mode is True
    assert worker._wake_event.is_set()

    worker._wake_event.clear()
    worker.set_power_saver_mode(True)
    assert not worker._wake_event.is_set()

    worker.set_power_saver_mode(False)
    assert worker._power_saver_mode is False
    assert worker._wake_event.is_set()


class FakeConnection:
    def __init__(
        self,
        remote_ip: str,
        remote_port: int,
        local_ip: str,
        local_port: int,
        protocol_type: int,
        pid: int | None,
    ):
        self.raddr = Address(remote_ip, remote_port)
        self.laddr = Address(local_ip, local_port)
        self.type = protocol_type
        self.pid = pid
        self.status = "ESTABLISHED"


def test_python_fallback_keeps_process_owner_metadata(monkeypatch) -> None:
    monkeypatch.setattr(collector, "NATIVE_AVAILABLE", False)
    monkeypatch.setattr(
        collector.psutil,
        "net_connections",
        lambda kind="inet": [
            FakeConnection("8.8.8.8", 443, "192.0.2.10", 51515, socket.SOCK_STREAM, 4567)
        ],
    )
    collector._PROCESS_NAME_CACHE.clear()

    endpoints = collector.collect_public_endpoints()
    assert len(endpoints) == 1
    assert endpoints[0]["ip"] == "8.8.8.8"
    assert endpoints[0]["process_id"] == 4567
    assert endpoints[0]["local_port"] == 51515
    assert endpoints[0]["protocol"] == "TCP"


def test_native_path_keeps_multiple_processes_connected_to_same_ip(monkeypatch) -> None:
    monkeypatch.setattr(collector, "NATIVE_AVAILABLE", True)
    monkeypatch.setattr(
        collector,
        "list_tcp_connections",
        lambda limit=2000: [
            {
                "ip": "8.8.8.8", "port": 443, "protocol": "TCP",
                "process_id": 101, "local_port": 51001, "connection_state": "ESTABLISHED",
            },
            {
                "ip": "8.8.8.8", "port": 443, "protocol": "TCP",
                "process_id": 202, "local_port": 51002, "connection_state": "ESTABLISHED",
            },
        ],
    )
    monkeypatch.setattr(collector, "count_udp_bindings", lambda: 0)
    monkeypatch.setattr(collector.psutil, "net_connections", lambda kind="udp": [])
    collector._PROCESS_NAME_CACHE.clear()

    endpoints = collector.collect_public_endpoints()
    assert len(endpoints) == 2
    assert {item["process_id"] for item in endpoints} == {101, 202}


def test_native_scan_counts_udp_without_reading_the_same_table_through_psutil(monkeypatch) -> None:
    monkeypatch.setattr(collector, "NATIVE_AVAILABLE", True)
    requested_limits: list[int] = []
    monkeypatch.setattr(
        collector,
        "list_tcp_connections",
        lambda limit=2000: (
            requested_limits.append(limit)
            or [
                {
                    "ip": f"8.8.8.{index}", "port": 443, "protocol": "TCP",
                    "process_id": 100 + index, "local_port": 51000 + index,
                    "connection_state": "ESTABLISHED",
                }
                for index in range(1, 4)
            ]
        ),
    )
    monkeypatch.setattr(collector, "count_udp_bindings", lambda: 11)
    monkeypatch.setattr(
        collector.psutil,
        "net_connections",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("native UDP collection must not scan the table through psutil")
        ),
    )

    endpoints, stats = collector.collect_public_endpoint_snapshot(limit=2)

    assert requested_limits == [2]
    assert len(endpoints) == 2
    assert {item["protocol"] for item in endpoints} == {"TCP"}
    assert stats["udp_local_sockets"] == 11
    assert stats["udp_public_peers"] == 0

def test_python_fallback_interleaves_tcp_and_udp_when_limit_is_reached(monkeypatch) -> None:
    monkeypatch.setattr(collector, "NATIVE_AVAILABLE", False)
    monkeypatch.setattr(
        collector.psutil,
        "net_connections",
        lambda kind="inet": [
            FakeConnection("8.8.8.8", 443, "192.0.2.10", 51001, socket.SOCK_STREAM, 1),
            FakeConnection("8.8.4.4", 443, "192.0.2.10", 51002, socket.SOCK_STREAM, 2),
            FakeConnection("1.1.1.1", 443, "192.0.2.10", 53000, socket.SOCK_DGRAM, 3),
        ],
    )

    endpoints = collector.collect_public_endpoints(limit=2)
    assert len(endpoints) == 2
    assert {item["protocol"] for item in endpoints} == {"TCP", "UDP"}


def test_remote_parser_accepts_attribute_addresses_without_tuple_indexing() -> None:
    address = SimpleNamespace(ip="8.8.8.8", port=443)
    assert collector._parse_remote(address) == ("8.8.8.8", 443)


def test_remote_parser_rejects_invalid_ports() -> None:
    assert collector._parse_remote(("8.8.8.8", -1)) is None
    assert collector._parse_remote(("8.8.8.8", 65536)) is None



def test_network_worker_resolves_auto_interface(monkeypatch) -> None:
    monkeypatch.setattr(collector, "choose_default_interface", lambda: "Wi-Fi")
    worker = collector.NetworkWorker(AppConfig(interface="auto"))

    assert worker._resolve_interface("auto") == "Wi-Fi"


def test_network_worker_preserves_selected_active_interface(monkeypatch) -> None:
    monkeypatch.setattr(
        collector.psutil,
        "net_if_stats",
        lambda: {"Ethernet": SimpleNamespace(isup=True)},
    )
    monkeypatch.setattr(
        collector,
        "choose_default_interface",
        lambda: "Wi-Fi",
    )
    worker = collector.NetworkWorker(AppConfig(interface="Ethernet"))

    assert worker._resolve_interface("Ethernet") == "Ethernet"


def test_network_worker_falls_back_when_saved_interface_is_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(
        collector.psutil,
        "net_if_stats",
        lambda: {"Ethernet": SimpleNamespace(isup=False)},
    )
    monkeypatch.setattr(
        collector,
        "choose_default_interface",
        lambda: "Wi-Fi",
    )
    worker = collector.NetworkWorker(AppConfig(interface="Ethernet"))

    assert worker._resolve_interface("Ethernet") == "Wi-Fi"



def test_native_empty_result_falls_back_to_psutil_tcp_table(monkeypatch) -> None:
    monkeypatch.setattr(collector, "NATIVE_AVAILABLE", True)
    monkeypatch.setattr(collector, "list_tcp_connections", lambda limit=2000: [])
    monkeypatch.setattr(collector, "count_udp_bindings", lambda: 0)
    monkeypatch.setattr(
        collector.psutil,
        "net_connections",
        lambda kind="inet": [
            FakeConnection(
                "8.8.8.8", 443, "192.0.2.10", 51515, socket.SOCK_STREAM, 4567
            )
        ] if kind == "tcp" else [],
    )
    collector._PROCESS_NAME_CACHE.clear()

    endpoints = collector.collect_public_endpoints()

    assert len(endpoints) == 1
    assert endpoints[0]["ip"] == "8.8.8.8"
    assert endpoints[0]["protocol"] == "TCP"
    assert endpoints[0]["process_id"] == 4567



def test_unconnected_udp_sockets_are_counted_without_inventing_remote_peers(monkeypatch) -> None:
    monkeypatch.setattr(collector, "NATIVE_AVAILABLE", True)
    monkeypatch.setattr(collector, "list_tcp_connections", lambda limit=2000: [])
    monkeypatch.setattr(collector, "count_udp_bindings", lambda: 2)
    monkeypatch.setattr(
        collector.psutil,
        "net_connections",
        lambda kind="tcp": [] if kind == "tcp" else (_ for _ in ()).throw(
            AssertionError("UDP table must not be scanned a second time")
        ),
    )

    endpoints, stats = collector.collect_public_endpoint_snapshot()

    assert endpoints == []
    assert stats["udp_local_sockets"] == 2
    assert stats["udp_public_peers"] == 0
    assert stats["udp_binding_error"] is None



def test_process_names_are_resolved_in_one_cached_process_table_pass(monkeypatch) -> None:
    class FakeProcess:
        def __init__(self, process_id: int, name: str):
            self.info = {"pid": process_id, "name": name}

    monkeypatch.setattr(
        collector.psutil,
        "process_iter",
        lambda attrs=None: [
            FakeProcess(101, "browser.exe"),
            FakeProcess(202, "game.exe"),
        ],
    )
    monkeypatch.setattr(
        collector.psutil,
        "Process",
        lambda _pid: (_ for _ in ()).throw(AssertionError("per-PID lookup was used")),
    )
    collector._PROCESS_NAME_CACHE.clear()

    collector._warm_process_name_cache([101, 202, 101])

    assert collector._process_name(101) == "browser.exe"
    assert collector._process_name(202) == "game.exe"
    assert set(collector._PROCESS_NAME_CACHE) == {101, 202}



def test_geoip_enrichment_looks_up_each_unique_ip_once() -> None:
    class FakeLocator:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def lookup(self, ip: str) -> dict[str, object]:
            self.calls.append(ip)
            return {"country": "United States", "latitude": 37.4, "longitude": -122.0}

    locator = FakeLocator()
    endpoints = [
        {"ip": "8.8.8.8", "port": 443},
        {"ip": "8.8.8.8", "port": 8443},
        {"ip": "1.1.1.1", "port": 443},
    ]

    collector._enrich_endpoints_with_geoip(endpoints, locator)  # type: ignore[arg-type]

    assert locator.calls == ["8.8.8.8", "1.1.1.1"]
    assert all(item["country"] == "United States" for item in endpoints)
