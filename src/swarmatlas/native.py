from __future__ import annotations

import ipaddress
from typing import Any


NATIVE_TCP_SCAN_LIMIT = 50_000
NATIVE_UDP_SCAN_LIMIT = 50_000

try:
    import _swarmatlas_native as _native

    NATIVE_AVAILABLE = True
    NATIVE_VERSION = str(getattr(_native, "__version__", "unknown"))
    NATIVE_CAPABILITIES = frozenset(str(item) for item in _native.capabilities())
except (ImportError, OSError):
    _native = None
    NATIVE_AVAILABLE = False
    NATIVE_VERSION = ""
    NATIVE_CAPABILITIES: frozenset[str] = frozenset()


def backend_status() -> str:
    if NATIVE_AVAILABLE:
        return f"Rust monitor active ({NATIVE_VERSION})"
    return "Rust monitor not built; using the Python compatibility collector"


def list_tcp_connections(limit: int = 2000) -> list[dict[str, Any]]:
    """Read a bounded OS table in Rust, then return only public endpoints to Python."""
    if _native is None:
        raise RuntimeError("The optional Rust extension is not installed")
    max_results = max(1, min(int(limit), 100_000))
    rows = _native.list_tcp_connections(NATIVE_TCP_SCAN_LIMIT)
    result: list[dict[str, Any]] = []

    for row in rows:
        if len(row) != 6:
            continue
        remote_ip, remote_port, protocol, process_id, local_port, state = row
        address_text = str(remote_ip)
        try:
            address = ipaddress.ip_address(address_text)
            if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
                address = address.ipv4_mapped
                address_text = str(address)
        except ValueError:
            continue
        if not address.is_global:
            continue

        result.append({
            "ip": address_text,
            "port": int(remote_port),
            "protocol": str(protocol),
            "process_id": int(process_id) or None,
            "local_port": int(local_port),
            "connection_state": str(state),
        })
        if len(result) >= max_results:
            break

    return result


def list_udp_bindings(limit: int = NATIVE_UDP_SCAN_LIMIT) -> list[dict[str, Any]]:
    if _native is None:
        raise RuntimeError("The optional Rust extension is not installed")

    max_results = max(1, min(int(limit), 100_000))
    rows = _native.list_udp_bindings(max_results)
    result: list[dict[str, Any]] = []
    for row in rows:
        if len(row) != 3:
            continue
        local_ip, local_port, process_id = row
        result.append({
            "local_ip": str(local_ip),
            "local_port": int(local_port),
            "process_id": int(process_id) or None,
        })
    return result
