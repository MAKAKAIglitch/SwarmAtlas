from __future__ import annotations

import socket
from collections import namedtuple

from swarmatlas import collector


Address = namedtuple("Address", "ip port")


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
        lambda: [
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
    monkeypatch.setattr(
        collector,
        "list_udp_bindings",
        lambda: [],
    )
    monkeypatch.setattr(
        collector.psutil,
        "net_connections",
        lambda kind="udp": [],
    )
    collector._PROCESS_NAME_CACHE.clear()

    endpoints = collector.collect_public_endpoints()
    assert len(endpoints) == 2
    assert {item["process_id"] for item in endpoints} == {101, 202}
