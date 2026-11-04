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


def _radial_filter_projected(
    points: Sequence[tuple[float, float]],
    tolerance: float,
) -> list[tuple[float, float]]:
    if len(points) <= 2 or tolerance <= 0:
        return list(points)

    threshold = tolerance * tolerance
    radial = [points[0]]
    last_x, last_y = points[0]
    for index in range(1, len(points) - 1):
        x, y = points[index]
        dx = x - last_x
        dy = y - last_y
        if dx * dx + dy * dy >= threshold:
            radial.append((x, y))
            last_x, last_y = x, y

    final = points[-1]
    if final != radial[-1]:
        radial.append(final)
    return radial


def _douglas_peucker(
    points: Sequence[tuple[float, float]],
    tolerance: float,
) -> list[tuple[float, float]]:
    if len(points) <= 2 or tolerance <= 0:
        return list(points)

    threshold = tolerance * tolerance
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


def simplify_projected_ring(
    points: Sequence[tuple[float, float]],
    tolerance: float = DEFAULT_SIMPLIFICATION_TOLERANCE,
) -> list[tuple[float, float]]:
    """Radial pre-filter followed by iterative Douglas-Peucker simplification."""
    if len(points) <= 2 or tolerance <= 0:
        return list(points)

    # Divide the error budget between the fast radial pass and Douglas-Peucker.
    radial = _radial_filter_projected(points, tolerance * 0.25)
    if len(radial) <= 2:
        return radial
    return _douglas_peucker(radial, tolerance * 0.75)


def _project_and_simplify_ring(
    points: Sequence[tuple[float, float]],
    tolerance: float,
) -> list[tuple[float, float]]:
    """Project geographic vertices while radial-filtering, avoiding a full extra list."""
    if len(points) <= 2 or tolerance <= 0:
        return [_project(float(lon), float(lat)) for lon, lat in points]

    radial_threshold = (tolerance * 0.25) ** 2
    first = _project(float(points[0][0]), float(points[0][1]))
    radial = [first]
    last_x, last_y = first

    for index in range(1, len(points) - 1):
        longitude, latitude = points[index]
        x, y = _project(float(longitude), float(latitude))
        dx = x - last_x
        dy = y - last_y
        if dx * dx + dy * dy >= radial_threshold:
            radial.append((x, y))
            last_x, last_y = x, y

    final = _project(float(points[-1][0]), float(points[-1][1]))
    if final != radial[-1]:
        radial.append(final)

    if len(radial) <= 2:
        return radial
    return _douglas_peucker(radial, tolerance * 0.75)

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
        projected = _project_and_simplify_ring(ring, tolerance)
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
