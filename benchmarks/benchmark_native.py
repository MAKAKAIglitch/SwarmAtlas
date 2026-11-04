from __future__ import annotations

import argparse
import os
import ipaddress
import statistics
import time

import psutil

from swarmatlas import native as native_backend


def measure(label: str, callback, iterations: int) -> tuple[list[float], int]:
    for _ in range(3):
        callback()

    timings_ms: list[float] = []
    row_count = 0
    for _ in range(iterations):
        started = time.perf_counter_ns()
        result = callback()
        elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000
        timings_ms.append(elapsed_ms)
        row_count = len(result)

    ordered = sorted(timings_ms)
    p95_index = min(len(ordered) - 1, max(0, int(len(ordered) * 0.95)))
    print(f"{label}:")
    print(f"  items returned: {row_count}")
    print(f"  median: {statistics.median(timings_ms):.3f} ms")
    print(f"  p95:    {ordered[p95_index]:.3f} ms")
    print(f"  min:    {ordered[0]:.3f} ms")
    print(f"  max:    {ordered[-1]:.3f} ms")
    return timings_ms, row_count


def rust_read():
    # Apply the same connected/public filter as the application, without
    # constructing UI dictionaries, GeoIP results, or saving any rows.
    rows = native_backend._native.list_tcp_connections(50_000)
    return [
        row for row in rows
        if ipaddress.ip_address(row[0]).is_global
    ]


def psutil_read():
    # Match the Rust path: connected public remote IP endpoints only.
    result = []
    for connection in psutil.net_connections(kind="tcp"):
        if not connection.raddr:
            continue
        try:
            if ipaddress.ip_address(connection.raddr.ip).is_global:
                result.append(connection)
        except ValueError:
            continue
    return result


def native_interface_read():
    return native_backend._native.list_network_interfaces()


def psutil_interface_read():
    stats = psutil.net_if_stats()
    counters = psutil.net_io_counters(pernic=True) or {}
    names = dict.fromkeys((*stats.keys(), *counters.keys()))
    return [
        (
            name,
            bool(getattr(stats.get(name), "isup", False)),
            int(getattr(counters.get(name), "bytes_sent", 0) or 0),
            int(getattr(counters.get(name), "bytes_recv", 0) or 0),
        )
        for name in names
    ]


def native_process_read(process_ids):
    return native_backend._native.resolve_process_names(process_ids)


def psutil_process_read(process_ids):
    requested = set(process_ids)
    result = {}
    for process in psutil.process_iter(attrs=["pid", "name"]):
        try:
            info = process.info
            pid = int(info.get("pid") or 0)
            if pid in requested:
                name = info.get("name")
                if name:
                    result[pid] = str(name)
                requested.remove(pid)
                if not requested:
                    break
        except (psutil.Error, OSError, AttributeError, TypeError, ValueError):
            continue
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare local TCP connection-table enumeration backends."
    )
    parser.add_argument("--iterations", type=int, default=25)
    args = parser.parse_args()

    if args.iterations < 5 or args.iterations > 500:
        parser.error("--iterations must be between 5 and 500")

    if not native_backend.NATIVE_AVAILABLE:
        print("Rust extension is not installed in this Python environment.")
        print("Build it with build_native.bat, then run this benchmark again.")
        return 2

    print("SwarmAtlas local TCP enumeration benchmark")
    print("Only reads the OS connection table. No traffic is generated or saved.")
    print("IP addresses and process names are not printed.")
    print(f"iterations: {args.iterations}")
    print()

    try:
        native_times, native_count = measure(
            "Rust / Windows IP Helper",
            rust_read,
            args.iterations,
        )
        psutil_times, psutil_count = measure(
            "Python / psutil",
            psutil_read,
            args.iterations,
        )

        print()
        print("Interface counter snapshot:")
        _, native_interface_count = measure(
            "Rust / GetIfTable2", native_interface_read, args.iterations
        )
        _, psutil_interface_count = measure(
            "Python / psutil", psutil_interface_read, args.iterations
        )
        print(f"  interface count: Rust={native_interface_count}, psutil={psutil_interface_count}")

        connection_rows = native_backend._native.list_tcp_connections(2000)
        process_ids = sorted({
            os.getpid(),
            *(int(row[3]) for row in connection_rows if int(row[3]) > 0),
        })[:4096]
        print()
        print(f"Process-name snapshot ({len(process_ids)} requested PIDs):")
        _, native_process_count = measure(
            "Rust / Tool Help", lambda: native_process_read(process_ids), args.iterations
        )
        _, psutil_process_count = measure(
            "Python / psutil", lambda: psutil_process_read(process_ids), args.iterations
        )
        print(f"  names returned: Rust={native_process_count}, psutil={psutil_process_count}")
    except (psutil.Error, OSError, RuntimeError) as exc:
        print(f"Benchmark could not complete: {type(exc).__name__}: {exc}")
        return 1

    native_median = statistics.median(native_times)
    psutil_median = statistics.median(psutil_times)
    print()
    print(f"Rows reported: Rust={native_count}, psutil={psutil_count}")
    if native_median > 0:
        print(f"Median speed ratio (psutil / Rust): {psutil_median / native_median:.2f}x")
    if native_count != psutil_count:
        print("Note: row counts differ; the Rust scan is bounded to 50,000 raw rows.")
    print("These are local timings, not a universal performance guarantee.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
