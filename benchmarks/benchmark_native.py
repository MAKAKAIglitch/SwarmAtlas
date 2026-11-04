from __future__ import annotations

import argparse
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
    print(f"  connected TCP rows: {row_count}")
    print(f"  median: {statistics.median(timings_ms):.3f} ms")
    print(f"  p95:    {ordered[p95_index]:.3f} ms")
    print(f"  min:    {ordered[0]:.3f} ms")
    print(f"  max:    {ordered[-1]:.3f} ms")
    return timings_ms, row_count


def rust_read():
    # Compare the raw native table path, before UI formatting or GeoIP.
    return native_backend._native.list_tcp_connections(50_000)


def psutil_read():
    # Match the native reader's connected-row semantics (exclude listeners).
    return [
        connection
        for connection in psutil.net_connections(kind="tcp")
        if connection.raddr
    ]


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
