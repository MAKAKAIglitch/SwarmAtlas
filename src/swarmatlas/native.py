from __future__ import annotations

import ipaddress
from typing import Any


NATIVE_UDP_SCAN_LIMIT = 50_000

_native = None
NATIVE_AVAILABLE = False
NATIVE_VERSION = ""
NATIVE_LOAD_ERROR = ""
NATIVE_CAPABILITIES: frozenset[str] = frozenset()

try:
    import _swarmatlas_native as candidate

    capabilities = frozenset(str(item) for item in candidate.capabilities())
    required_capabilities = {"read-only", "tcp-owner-pid", "udp-owner-pid"}
    if not required_capabilities.issubset(capabilities):
        missing = ", ".join(sorted(required_capabilities - capabilities))
        raise RuntimeError(f"Native extension is missing required capabilities: {missing}")
    if not callable(getattr(candidate, "list_tcp_connections", None)):
        raise RuntimeError("Native extension does not expose the TCP table reader")
    if not callable(getattr(candidate, "list_udp_bindings", None)):
        raise RuntimeError("Native extension does not expose the UDP table reader")

    _native = candidate
    NATIVE_AVAILABLE = True
    NATIVE_VERSION = str(getattr(candidate, "__version__", "unknown"))
    NATIVE_CAPABILITIES = capabilities
except Exception as exc:
    # An incompatible or partially installed extension must never prevent the
    # Python collector and the rest of the desktop application from starting.
    _native = None
    NATIVE_AVAILABLE = False
    NATIVE_VERSION = ""
    NATIVE_LOAD_ERROR = type(exc).__name__
    NATIVE_CAPABILITIES = frozenset()


def backend_status() -> str:
    if NATIVE_AVAILABLE:
        return f"Rust monitor active ({NATIVE_VERSION})"
    reason = f" ({NATIVE_LOAD_ERROR})" if NATIVE_LOAD_ERROR else ""
    return f"Rust monitor unavailable{reason}; using the Python compatibility collector"


def list_tcp_connections(limit: int = 2000) -> list[dict[str, Any]]:
    """Read a bounded OS table in Rust, then return only public endpoints to Python."""
    if _native is None:
        raise RuntimeError("The optional Rust extension is not installed")
    max_results = max(1, min(int(limit), 100_000))
    # Rust scans the bounded Windows table, filters non-public addresses there,
    # and only materializes the requested number of endpoints across the PyO3 boundary.
    rows = _native.list_tcp_connections(max_results)
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

        try:
            remote_port = int(remote_port)
            local_port = int(local_port)
            process_id = int(process_id)
        except (TypeError, ValueError, OverflowError):
            continue
        if not 0 <= remote_port <= 65535 or not 0 <= local_port <= 65535:
            continue

        result.append({
            "ip": address_text,
            "port": remote_port,
            "protocol": str(protocol),
            "process_id": process_id or None,
            "local_port": local_port,
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
        try:
            local_port = int(local_port)
            process_id = int(process_id)
        except (TypeError, ValueError, OverflowError):
            continue
        if not 0 <= local_port <= 65535:
            continue
        result.append({
            "local_ip": str(local_ip),
            "local_port": local_port,
            "process_id": process_id or None,
        })
    return result
