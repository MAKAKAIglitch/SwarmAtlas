from __future__ import annotations

import math
from collections.abc import Iterable, Sequence

from PySide6.QtCore import Qt
from PySide6.QtGui import QPainterPath


MERCATOR_MAX_LAT = 85.0511287798066
# At the map's maximum zoom this tolerance remains well below one screen pixel.
DEFAULT_SIMPLIFICATION_TOLERANCE = 0.00001


def _project(longitude: float, latitude: float) -> tuple[float, float]:
    latitude = max(-MERCATOR_MAX_LAT, min(MERCATOR_MAX_LAT, latitude))
    radians = math.radians(latitude)
    x = (longitude + 180.0) / 360.0
    y = (1.0 - math.asinh(math.tan(radians)) / math.pi) / 2.0
    return x, y


def _segment_distance_squared(
    point: tuple[float, float],
    start: tuple[float, float],
    end: tuple[float, float],
) -> float:
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    if dx == 0.0 and dy == 0.0:
        px = point[0] - start[0]
        py = point[1] - start[1]
        return px * px + py * py

    projection = (
        (point[0] - start[0]) * dx + (point[1] - start[1]) * dy
    ) / (dx * dx + dy * dy)
    projection = max(0.0, min(1.0, projection))
    nearest_x = start[0] + projection * dx
    nearest_y = start[1] + projection * dy
    px = point[0] - nearest_x
    py = point[1] - nearest_y
    return px * px + py * py


def simplify_projected_ring(
    points: Sequence[tuple[float, float]],
    tolerance: float = DEFAULT_SIMPLIFICATION_TOLERANCE,
) -> list[tuple[float, float]]:
    """Douglas-Peucker simplification in projected world coordinates."""
    if len(points) <= 2 or tolerance <= 0:
        return list(points)

    # A cheap linear radial pass removes dense, nearly coincident shapefile
    # vertices before Douglas-Peucker. The smaller pre-filter tolerance keeps
    # the combined visual error below the configured simplification tolerance.
    radial_tolerance = tolerance * 0.25
    radial_threshold = radial_tolerance * radial_tolerance
    radial = [points[0]]
    last = points[0]
    for point in points[1:-1]:
        dx = point[0] - last[0]
        dy = point[1] - last[1]
        if dx * dx + dy * dy >= radial_threshold:
            radial.append(point)
            last = point
    if points[-1] != radial[-1]:
        radial.append(points[-1])
    if len(radial) <= 2:
        return radial

    points = radial
    threshold = (tolerance * 0.75) ** 2
    keep = bytearray(len(points))
    keep[0] = 1
    keep[-1] = 1
    pending = [(0, len(points) - 1)]

    while pending:
        start_index, end_index = pending.pop()
        start = points[start_index]
        end = points[end_index]
        farthest_index = -1
        farthest_distance = threshold

        for index in range(start_index + 1, end_index):
            distance = _segment_distance_squared(points[index], start, end)
            if distance > farthest_distance:
                farthest_distance = distance
                farthest_index = index

        if farthest_index >= 0:
            keep[farthest_index] = 1
            pending.append((start_index, farthest_index))
            pending.append((farthest_index, end_index))

    return [point for index, point in enumerate(points) if keep[index]]


def _append_rings(
    path: QPainterPath,
    rings: Iterable[Sequence[tuple[float, float]]],
    *,
    close: bool,
    tolerance: float,
) -> None:
    for ring in rings:
        if len(ring) < (3 if close else 2):
            continue
        projected = simplify_projected_ring(
            [_project(float(longitude), float(latitude)) for longitude, latitude in ring],
            tolerance,
        )
        if len(projected) < (3 if close else 2):
            continue

        path.moveTo(*projected[0])
        for x, y in projected[1:]:
            path.lineTo(x, y)
        if close:
            path.closeSubpath()


def build_boundary_paths(
    countries: Iterable[Sequence[tuple[float, float]]],
    regions: Iterable[Sequence[tuple[float, float]]],
    *,
    tolerance: float = DEFAULT_SIMPLIFICATION_TOLERANCE,
) -> tuple[QPainterPath, QPainterPath]:
    """Build render-ready paths off the GUI thread and discard sub-pixel vertices."""
    country_path = QPainterPath()
    country_path.setFillRule(Qt.FillRule.OddEvenFill)
    region_path = QPainterPath()

    _append_rings(country_path, countries, close=True, tolerance=tolerance)
    _append_rings(region_path, regions, close=True, tolerance=tolerance)
    return country_path, region_path
