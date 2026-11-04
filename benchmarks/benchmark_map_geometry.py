from __future__ import annotations

import argparse
import statistics
import time

from swarmatlas import native as native_backend
from swarmatlas.map_geometry import _project_and_simplify_ring


def measure(label: str, callback, iterations: int) -> tuple[list[float], int]:
    for _ in range(2):
        callback()

    timings_ms: list[float] = []
    output_vertices = 0
    for _ in range(iterations):
        started = time.perf_counter_ns()
        result = callback()
        timings_ms.append((time.perf_counter_ns() - started) / 1_000_000)
        output_vertices = sum(len(ring) for ring in result)

    ordered = sorted(timings_ms)
    p95_index = min(len(ordered) - 1, max(0, int(len(ordered) * 0.95)))
    print(f"{label}:")
    print(f"  output vertices: {output_vertices}")
    print(f"  median: {statistics.median(timings_ms):.3f} ms")
    print(f"  p95:    {ordered[p95_index]:.3f} ms")
    print(f"  min:    {ordered[0]:.3f} ms")
    print(f"  max:    {ordered[-1]:.3f} ms")
    return timings_ms, output_vertices


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare Python and Rust map projection/simplification."
    )
    parser.add_argument("--iterations", type=int, default=15)
    parser.add_argument("--rings", type=int, default=256)
    parser.add_argument("--points", type=int, default=512)
    args = parser.parse_args()

    if not 5 <= args.iterations <= 200:
        parser.error("--iterations must be between 5 and 200")
    if not 1 <= args.rings <= 4096:
        parser.error("--rings must be between 1 and 4096")
    if not 3 <= args.points <= 100_000:
        parser.error("--points must be between 3 and 100,000")
    if not native_backend.NATIVE_MAP_GEOMETRY:
        print("Rust map geometry is not available in this Python environment.")
        return 2

    # Deterministic synthetic coast-like rings: no network or external data needed.
    rings = [
        [
            (
                -170.0 + ring_index * 0.07 + point_index * 0.0002,
                35.0 + ((point_index % 11) - 5) * 0.00002
                + ((point_index % 97) / 97.0) * 0.01,
            )
            for point_index in range(args.points)
        ]
        for ring_index in range(args.rings)
    ]
    tolerance = 0.00001

    print("SwarmAtlas map geometry benchmark")
    print("Synthetic deterministic input; no network, map files or telemetry.")
    print(f"rings: {args.rings}; points per ring: {args.points}; iterations: {args.iterations}")
    print()

    python_run = lambda: [
        _project_and_simplify_ring(ring, tolerance) for ring in rings
    ]
    rust_run = lambda: native_backend.project_and_simplify_rings(rings, tolerance)

    python_times, python_vertices = measure("Python reference", python_run, args.iterations)
    rust_times, rust_vertices = measure("Rust batch", rust_run, args.iterations)

    if python_vertices != rust_vertices:
        print("WARNING: output vertex counts differ; inspect numerical equivalence.")
        return 1

    python_median = statistics.median(python_times)
    rust_median = statistics.median(rust_times)
    print()
    if rust_median > 0:
        print(f"Median speed ratio (Python / Rust): {python_median / rust_median:.2f}x")
    print("Timing includes Python/Rust conversion; Qt path construction is not included.")
    print("Results are machine- and dataset-dependent, not a universal speed guarantee.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
