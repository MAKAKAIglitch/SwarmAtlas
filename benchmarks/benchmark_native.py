from __future__ import annotations

import argparse
import statistics
import time

import psutil

from swarmatlas.native import NATIVE_AVAILABLE, list_tcp_connections


def measure(label: str, callback, iterations: int) -> tuple[list[float], int]:
    # Warm up both code paths before recording timings.
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
    print(f"  rows:   {row_count}")
    print(f"  median: {statistics.median(timings_ms):.3f} ms")
    print(f"  p95:    {ordered[p95_index]:.3f} ms")
    print(f"  min:    {ordered[0]:.3f} ms")
    print(f"  max:    {ordered[-1]:.3f} ms")
    return timings_ms, row_count


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare local TCP connection-table enumeration backends."
    )
    parser.add_argument("--iterations", type=int, default=25)
    args = parser.parse_args()

    if args.iterations < 5 or args.iterations > 500:
        parser.error("--iterations must be between 5 and 500")

    if not NATIVE_AVAILABLE:
        print("Rust extension is not installed in this Python environment.")
        print("Build it with build_native.bat, then run this benchmark again.")
        return 2

    print("SwarmAtlas local TCP enumeration benchmark")
    print("Only reads the OS connection table. No traffic is generated or saved.")
    print(f"iterations: {args.iterations}")
    print()

    native_times, native_count = measure(
        "Rust / Windows IP Helper",
        list_tcp_connections,
        args.iterations,
    )
    psutil_times, psutil_count = measure(
        "Python / psutil",
        lambda: psutil.net_connections(kind="tcp"),
        args.iterations,
    )

    native_median = statistics.median(native_times)
    psutil_median = statistics.median(psutil_times)
    print()
    print(f"TCP rows reported: Rust={native_count}, psutil={psutil_count}")
    if native_median > 0:
        print(f"Median speed ratio (psutil / Rust): {psutil_median / native_median:.2f}x")
    if native_count != psutil_count:
        print("Note: row counts differ; compare timings with that difference in mind.")
    print("These are local timings, not a universal performance guarantee.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
