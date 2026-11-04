from __future__ import annotations

import pytest


native = pytest.importorskip("_swarmatlas_native")


def test_native_extension_reports_read_only_windows_capabilities() -> None:
    capabilities = set(native.capabilities())
    assert "read-only" in capabilities
    assert "tcp-owner-pid" in capabilities
    assert "udp-owner-pid" in capabilities
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
        assert 0 <= int(row[1]) <= 65535
        assert row[2] == "TCP"
        assert int(row[3]) >= 0
        assert 0 <= int(row[4]) <= 65535

    for row in udp_rows:
        assert len(row) == 3
        assert isinstance(row[0], str)
        assert 0 <= int(row[1]) <= 65535
        assert int(row[2]) >= 0
