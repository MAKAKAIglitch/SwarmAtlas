from __future__ import annotations

import ipaddress
import math
from typing import Any, Sequence


NATIVE_TCP_LEGACY_SCAN_LIMIT = 50_000
NATIVE_UDP_SCAN_LIMIT = 50_000

_native = None
NATIVE_AVAILABLE = False
NATIVE_VERSION = ""
NATIVE_LOAD_ERROR = ""
NATIVE_CAPABILITIES: frozenset[str] = frozenset()
NATIVE_MAP_GEOMETRY = False
NATIVE_INTERFACE_COUNTERS = False
NATIVE_PROCESS_NAMES = False
NATIVE_TCP_FLOW_STATS = False
NATIVE_TRACEROUTE = False

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
    NATIVE_MAP_GEOMETRY = "map-geometry-v1" in capabilities and callable(
        getattr(candidate, "project_and_simplify_rings", None)
    )
    NATIVE_INTERFACE_COUNTERS = "network-interfaces-v1" in capabilities and callable(
        getattr(candidate, "list_network_interfaces", None)
    )
    NATIVE_PROCESS_NAMES = "process-names-v1" in capabilities and callable(
        getattr(candidate, "resolve_process_names", None)
    )
    NATIVE_TCP_FLOW_STATS = "tcp-flow-estats-v1" in capabilities and callable(
        getattr(candidate, "collect_tcp_flow_stats", None)
    )
    traceroute_capabilities = {"traceroute-icmp-v1", "traceroute-udp-v1", "traceroute-tcp-v1"}
    NATIVE_TRACEROUTE = traceroute_capabilities.issubset(capabilities) and callable(
        getattr(candidate, "traceroute_probe", None)
    )
except Exception as exc:
    # An incompatible or partially installed extension must never prevent the
    # Python collector and the rest of the desktop application from starting.
    _native = None
    NATIVE_AVAILABLE = False
    NATIVE_VERSION = ""
    NATIVE_LOAD_ERROR = type(exc).__name__
    NATIVE_CAPABILITIES = frozenset()
    NATIVE_MAP_GEOMETRY = False
    NATIVE_INTERFACE_COUNTERS = False
    NATIVE_PROCESS_NAMES = False
    NATIVE_TCP_FLOW_STATS = False
    NATIVE_TRACEROUTE = False


def backend_status() -> str:
    if NATIVE_AVAILABLE:
        features = [
            label for enabled, label in (
                (NATIVE_MAP_GEOMETRY, "map geometry"),
                (NATIVE_INTERFACE_COUNTERS, "interface counters"),
                (NATIVE_PROCESS_NAMES, "process lookup"),
                (NATIVE_TCP_FLOW_STATS, "TCP flow counters"),
            ) if enabled
        ]
        suffix = f" · {' · '.join(features)}" if features else ""
        return f"Rust monitor active ({NATIVE_VERSION}){suffix}"
    reason = f" ({NATIVE_LOAD_ERROR})" if NATIVE_LOAD_ERROR else ""
    return f"Rust monitor unavailable{reason}; using the Python compatibility collector"


def project_and_simplify_rings(
    rings: Sequence[Sequence[tuple[float, float]]],
    tolerance: float,
) -> list[list[tuple[float, float]]] | None:
    """Run a bounded batch of map rings in the Rust geometry core, if available."""
    if not NATIVE_MAP_GEOMETRY or _native is None:
        return None
    return _native.project_and_simplify_rings(rings, float(tolerance))


def list_network_interfaces() -> dict[str, dict[str, Any]] | None:
    """Read Windows interface counters in one native call, or return None for old wheels."""
    if not NATIVE_INTERFACE_COUNTERS or _native is None:
        return None

    result: dict[str, dict[str, Any]] = {}
    for row in _native.list_network_interfaces():
        if len(row) != 4:
            continue
        name = str(row[0]).strip()
        if not name:
            continue
        try:
            sent = max(0, int(row[2]))
            received = max(0, int(row[3]))
        except (TypeError, ValueError, OverflowError):
            continue
        result[name] = {
            "is_up": bool(row[1]),
            "bytes_sent": sent,
            "bytes_recv": received,
        }
    return result


def resolve_process_names(process_ids: Sequence[int]) -> dict[int, str] | None:
    """Resolve a bounded batch of PIDs in Rust, or return None for old wheels."""
    if not NATIVE_PROCESS_NAMES or _native is None:
        return None

    unique: set[int] = set()
    for value in process_ids:
        try:
            pid = int(value)
        except (TypeError, ValueError, OverflowError):
            continue
        if 0 < pid <= 0xFFFFFFFF:
            unique.add(pid)
    if not unique:
        return {}
    if len(unique) > 4096:
        raise ValueError("At most 4096 process IDs can be resolved per call")

    result: dict[int, str] = {}
    for row in _native.resolve_process_names(sorted(unique)):
        if len(row) != 2:
            continue
        try:
            pid = int(row[0])
        except (TypeError, ValueError, OverflowError):
            continue
        name = str(row[1]).strip()
        if pid in unique and name:
            result[pid] = name
    return result


def list_tcp_connections(limit: int = 2000) -> list[dict[str, Any]]:
    """Read a bounded OS table in Rust, then return only public endpoints to Python."""
    if _native is None:
        raise RuntimeError("The optional Rust extension is not installed")
    max_results = max(1, min(int(limit), 100_000))
    # New cores filter public destinations before crossing the PyO3 boundary.
    # Keep the former larger request for already-installed pre-refactor wheels.
    native_limit = (
        max_results
        if "tcp-public-endpoints" in NATIVE_CAPABILITIES
        else max(NATIVE_TCP_LEGACY_SCAN_LIMIT, max_results)
    )
    rows = _native.list_tcp_connections(native_limit)
    result: list[dict[str, Any]] = []

    for row in rows:
        if len(row) != 6:
            continue
        remote_ip, remote_port, protocol, process_id, local_port, state = row
        address_text = str(remote_ip)
        if "tcp-public-endpoints" not in NATIVE_CAPABILITIES:
            # Older wheels leave address filtering to Python. Current Rust
            # wheels already validate public ranges and normalize mapped IPv6.
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


def collect_tcp_flow_stats(limit: int = 2000) -> list[dict[str, Any]]:
    """Read opt-in Windows TCP payload counters through the Rust extension."""
    if not NATIVE_TCP_FLOW_STATS or _native is None:
        raise RuntimeError("The Rust TCP flow-statistics capability is not installed")

    max_results = max(1, min(int(limit), 100_000))
    rows = _native.collect_tcp_flow_stats(max_results)
    result: list[dict[str, Any]] = []
    for row in rows:
        if len(row) != 6:
            continue
        remote_ip, remote_port, process_id, local_port, upload_bytes, download_bytes = row
        try:
            address = ipaddress.ip_address(str(remote_ip))
            remote_port = int(remote_port)
            process_id = int(process_id)
            local_port = int(local_port)
            upload_bytes = int(upload_bytes)
            download_bytes = int(download_bytes)
        except (TypeError, ValueError, OverflowError):
            continue
        if not address.is_global or not (1 <= remote_port <= 65535):
            continue
        if not (0 <= local_port <= 65535 and 0 <= process_id <= 0xFFFFFFFF):
            continue
        if upload_bytes < 0 or download_bytes < 0:
            continue
        result.append({
            "ip": str(address),
            "port": remote_port,
            "protocol": "TCP",
            "process_id": process_id,
            "local_port": local_port,
            "upload_counter": upload_bytes,
            "download_counter": download_bytes,
        })
        if len(result) >= max_results:
            break
    return result


def count_udp_bindings() -> int:
    """Count local UDP bindings without crossing the PyO3 boundary for every row."""
    if _native is None:
        raise RuntimeError("The optional Rust extension is not installed")
    counter = getattr(_native, "count_udp_bindings", None)
    if callable(counter) and "udp-socket-count" in NATIVE_CAPABILITIES:
        return max(0, int(counter()))
    # Compatibility with older installed native wheels.
    return len(list_udp_bindings())


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


def traceroute_probe(
    target: str,
    method: str,
    ttl: int,
    timeout_ms: int = 1000,
    port: int = 443,
) -> dict[str, Any]:
    """Perform one bounded Rust traceroute probe for a single hop."""
    if not NATIVE_TRACEROUTE or _native is None:
        raise RuntimeError("The Rust ICMP/UDP/TCP traceroute capability is not installed")
    method = str(method).strip().lower()
    if method not in {"icmp", "udp", "tcp"}:
        raise ValueError("method must be icmp, udp, or tcp")
    ttl = int(ttl)
    timeout_ms = int(timeout_ms)
    port = int(port)
    if not 1 <= ttl <= 64:
        raise ValueError("ttl must be between 1 and 64")
    if not 100 <= timeout_ms <= 10_000:
        raise ValueError("timeout_ms must be between 100 and 10000")
    if not 1 <= port <= 65535:
        raise ValueError("port must be between 1 and 65535")
    row = _native.traceroute_probe(str(target), method, ttl, timeout_ms, port)
    if not isinstance(row, (tuple, list)) or len(row) != 4:
        raise RuntimeError("The Rust traceroute backend returned an invalid probe result")
    address, rtt, reached, status = row
    try:
        parsed_rtt = None if rtt is None else float(rtt)
    except (TypeError, ValueError, OverflowError) as exc:
        raise RuntimeError("The Rust traceroute backend returned an invalid RTT") from exc
    if parsed_rtt is not None and (parsed_rtt < 0 or not math.isfinite(parsed_rtt)):
        raise RuntimeError("The Rust traceroute backend returned an invalid RTT")
    return {
        "address": str(address) if address else None,
        "rtt": parsed_rtt,
        "reached": bool(reached),
        "status": str(status),
    }
