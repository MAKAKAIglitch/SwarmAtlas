from __future__ import annotations

from pathlib import Path

import shapefile

from swarmatlas.map_data import read_polygon_rings, _split_antimeridian


def test_region_shapefile_is_read_as_polygon_rings(tmp_path: Path) -> None:
    base = tmp_path / "regions"
    writer = shapefile.Writer(str(base), shapeType=shapefile.POLYGON)
    writer.field("name", "C")
    writer.poly([[
        (-10, 10), (10, 10), (10, -10), (-10, -10), (-10, 10)
    ]])
    writer.record("Test Region")
    writer.close()

    rings = read_polygon_rings(base.with_suffix(".shp"))
    assert len(rings) == 1
    assert len(rings[0]) == 5
    assert rings[0][0] == (-10.0, 10.0)


def test_map_ring_is_split_at_antimeridian() -> None:
    segments = _split_antimeridian([
        (170, 10), (179, 12), (-179, 12), (-170, 10), (-170, -10)
    ])
    assert len(segments) == 2
    assert all(len(segment) >= 3 for segment in segments)
