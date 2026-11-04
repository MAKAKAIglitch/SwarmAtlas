from __future__ import annotations

from pathlib import Path

import shapefile

from swarmatlas import map_data
from swarmatlas.map_data import iter_polygon_rings, read_polygon_rings, _split_antimeridian


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
        (170, 10), (175, 12), (179, 15), (-179, 15), (-175, 12), (-170, 10), (-170, -10)
    ])
    assert len(segments) == 2
    assert all(len(segment) >= 2 for segment in segments)



def test_polygon_reader_yields_rings_incrementally(tmp_path: Path) -> None:
    base = tmp_path / "streamed"
    writer = shapefile.Writer(str(base), shapeType=shapefile.POLYGON)
    writer.field("name", "C")
    writer.poly([[
        (-10, 10), (0, 10), (0, 0), (-10, 0), (-10, 10)
    ]])
    writer.record("First")
    writer.poly([[
        (10, 10), (20, 10), (20, 0), (10, 0), (10, 10)
    ]])
    writer.record("Second")
    writer.close()

    rings = iter_polygon_rings(base.with_suffix(".shp"))

    first = next(rings)
    assert first[0] == (-10.0, 10.0)
    second = next(rings)
    assert second[0] == (10.0, 10.0)


def test_streaming_map_loader_returns_renderable_paths(
    tmp_path: Path, monkeypatch
) -> None:
    countries = tmp_path / "countries.shp"
    regions = tmp_path / "regions.shp"
    for path, name, points in [
        (countries, "Country", [(-10, 10), (10, 10), (10, -10), (-10, -10), (-10, 10)]),
        (regions, "Region", [(-5, 5), (5, 5), (5, -5), (-5, -5), (-5, 5)]),
    ]:
        writer = shapefile.Writer(str(path.with_suffix("")), shapeType=shapefile.POLYGON)
        writer.field("name", "C")
        writer.poly([points])
        writer.record(name)
        writer.close()

    monkeypatch.setattr(
        map_data,
        "ensure_natural_earth_data",
        lambda progress=None: (countries, regions),
    )

    country_path, region_path = map_data.load_world_boundary_paths()

    assert country_path.elementCount() == 5
    assert region_path.elementCount() == 5
