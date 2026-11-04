from __future__ import annotations

from typing import Any


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


def list_tcp_connections() -> list[dict[str, Any]]:
    if _native is None:
        raise RuntimeError("The optional Rust extension is not installed")

    rows = _native.list_tcp_connections()
    result: list[dict[str, Any]] = []
    for row in rows:
        if len(row) != 6:
            continue
        remote_ip, remote_port, protocol, process_id, local_port, state = row
        result.append({
            "ip": str(remote_ip),
            "port": int(remote_port),
            "protocol": str(protocol),
            "process_id": int(process_id) or None,
            "local_port": int(local_port),
            "connection_state": str(state),
        })
    return result


def list_udp_bindings() -> list[dict[str, Any]]:
    if _native is None:
        raise RuntimeError("The optional Rust extension is not installed")

    rows = _native.list_udp_bindings()
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
