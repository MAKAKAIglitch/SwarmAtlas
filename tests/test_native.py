from __future__ import annotations

import ipaddress
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from swarmatlas import native as native_adapter


native = pytest.importorskip("_swarmatlas_native")


def test_native_extension_reports_read_only_windows_capabilities() -> None:
    capabilities = set(native.capabilities())
    assert "read-only" in capabilities
    assert "tcp-owner-pid" in capabilities
    assert "tcp-public-endpoints" in capabilities
    assert "udp-socket-count" in capabilities
    assert "udp-owner-pid" in capabilities
    assert "map-geometry-v1" in capabilities
    assert "network-interfaces-v1" in capabilities
    assert "process-names-v1" in capabilities
    assert "ipv4" in capabilities
    assert "ipv6" in capabilities


def test_native_connection_tables_return_valid_rows() -> None:
    tcp_rows = native.list_tcp_connections()
    udp_rows = native.list_udp_bindings()
    bounded_tcp_rows = native.list_tcp_connections(10)
    bounded_udp_rows = native.list_udp_bindings(10)

    assert isinstance(tcp_rows, list)
    assert isinstance(udp_rows, list)
    assert len(bounded_tcp_rows) <= 10
    assert len(bounded_udp_rows) <= 10

    for row in tcp_rows:
        assert len(row) == 6
        assert isinstance(row[0], str)
        assert ipaddress.ip_address(row[0]).is_global
        assert 0 <= int(row[1]) <= 65535
        assert row[2] == "TCP"
        assert int(row[3]) >= 0
        assert 0 <= int(row[4]) <= 65535

    for row in udp_rows:
        assert len(row) == 3
        assert isinstance(row[0], str)
        assert 0 <= int(row[1]) <= 65535
        assert int(row[2]) >= 0


def test_native_interface_snapshot_returns_compact_valid_rows() -> None:
    rows = native.list_network_interfaces()
    assert isinstance(rows, list)
    for row in rows:
        assert len(row) == 4
        assert isinstance(row[0], str) and row[0]
        assert isinstance(row[1], bool)
        assert int(row[2]) >= 0
        assert int(row[3]) >= 0


def test_native_process_lookup_resolves_only_requested_process_ids() -> None:
    current_pid = os.getpid()
    rows = native.resolve_process_names([current_pid, current_pid])
    assert len(rows) == 1
    assert int(rows[0][0]) == current_pid
    assert rows[0][1].lower() == Path(sys.executable).name.lower()


def test_native_process_lookup_rejects_oversized_requests() -> None:
    with pytest.raises(ValueError):
        native.resolve_process_names(list(range(1, 4098)))


def test_native_geometry_matches_the_python_reference() -> None:
    if not native_adapter.NATIVE_MAP_GEOMETRY:
        pytest.skip("Installed Rust extension does not expose map geometry.")

    from swarmatlas.map_geometry import _project_and_simplify_ring

    rings = [
        [
            (-120.0 + index * 0.001, 35.0 + (0.00001 if index % 2 else 0.0))
            for index in range(500)
        ],
        [(-10.0, -10.0), (10.0, -10.0), (10.0, 10.0), (-10.0, 10.0), (-10.0, -10.0)],
        [(0.0, 90.0), (10.0, 90.0), (20.0, 90.0)],
        [(0.0, 0.0), (0.0, 0.0), (0.0, 0.0)],
    ]
    tolerance = 0.00001

    actual = native_adapter.project_and_simplify_rings(rings, tolerance)
    expected = [_project_and_simplify_ring(ring, tolerance) for ring in rings]

    assert actual is not None
    assert len(actual) == len(expected)
    for actual_ring, expected_ring in zip(actual, expected):
        assert len(actual_ring) == len(expected_ring)
        for actual_point, expected_point in zip(actual_ring, expected_ring):
            assert actual_point == pytest.approx(expected_point, abs=1e-12)


def test_native_geometry_rejects_invalid_tolerances() -> None:
    if not native_adapter.NATIVE_MAP_GEOMETRY:
        pytest.skip("Installed Rust extension does not expose map geometry.")
    with pytest.raises(ValueError):
        native_adapter.project_and_simplify_rings([[(0.0, 0.0)]], float("nan"))
    with pytest.raises(ValueError):
        native_adapter.project_and_simplify_rings([[(0.0, 0.0)]], -1.0)


def test_python_adapter_passes_display_limit_to_rust(monkeypatch) -> None:
    calls: list[int] = []
    fake = SimpleNamespace(
        list_tcp_connections=lambda limit: (
            calls.append(limit)
            or [("8.8.8.8", 443, "TCP", 42, 50000, "ESTABLISHED")]
        )
    )
    monkeypatch.setattr(native_adapter, "_native", fake)

    rows = native_adapter.list_tcp_connections(limit=7)

    assert calls == [7]
    assert len(rows) == 1
    assert rows[0]["ip"] == "8.8.8.8"


def test_python_adapter_keeps_legacy_scan_for_pre_refactor_core(monkeypatch) -> None:
    calls: list[int] = []
    fake = SimpleNamespace(
        list_tcp_connections=lambda limit: (
            calls.append(limit)
            or [("8.8.8.8", 443, "TCP", 42, 50000, "ESTABLISHED")]
        )
    )
    monkeypatch.setattr(native_adapter, "_native", fake)
    monkeypatch.setattr(native_adapter, "NATIVE_CAPABILITIES", frozenset())

    rows = native_adapter.list_tcp_connections(limit=7)

    assert calls == [50_000]
    assert len(rows) == 1


def test_current_native_adapter_skips_duplicate_ip_range_classification(monkeypatch) -> None:
    fake = SimpleNamespace(
        list_tcp_connections=lambda limit: [
            ("8.8.8.8", 443, "TCP", 42, 50000, "ESTABLISHED")
        ]
    )
    monkeypatch.setattr(native_adapter, "_native", fake)
    monkeypatch.setattr(
        native_adapter,
        "NATIVE_CAPABILITIES",
        frozenset({"tcp-public-endpoints"}),
    )
    monkeypatch.setattr(
        native_adapter.ipaddress,
        "ip_address",
        lambda _value: (_ for _ in ()).throw(AssertionError("reparsed Rust-validated IP")),
    )

    rows = native_adapter.list_tcp_connections(limit=10)

    assert rows[0]["ip"] == "8.8.8.8"


def test_native_udp_count_uses_compact_rust_counter_when_available(monkeypatch) -> None:
    fake = SimpleNamespace(
        count_udp_bindings=lambda: 23,
        list_udp_bindings=lambda _limit: (_ for _ in ()).throw(
            AssertionError("bulk UDP row conversion should not be used")
        ),
    )
    monkeypatch.setattr(native_adapter, "_native", fake)
    monkeypatch.setattr(
        native_adapter,
        "NATIVE_CAPABILITIES",
        frozenset({"udp-socket-count"}),
    )

    assert native_adapter.count_udp_bindings() == 23


def test_native_udp_count_falls_back_for_legacy_wheels(monkeypatch) -> None:
    fake = SimpleNamespace(
        list_udp_bindings=lambda limit: [
            ("0.0.0.0", 53000, 10),
            ("::", 53001, 20),
        ]
    )
    monkeypatch.setattr(native_adapter, "_native", fake)
    monkeypatch.setattr(native_adapter, "NATIVE_CAPABILITIES", frozenset())

    assert native_adapter.count_udp_bindings() == 2
