from __future__ import annotations

from swarmatlas.map_geometry import build_boundary_paths, simplify_projected_ring


def test_simplifier_removes_subpixel_noise_and_preserves_shape() -> None:
    flat = [(0.0, 0.0), (0.2, 0.000001), (0.4, -0.000001), (0.6, 0.0)]
    assert simplify_projected_ring(flat, tolerance=0.001) == [flat[0], flat[-1]]

    curved = [(0.0, 0.0), (0.5, 0.2), (1.0, 0.0)]
    assert simplify_projected_ring(curved, tolerance=0.01) == curved


def test_map_paths_are_built_with_country_fill_and_closed_region_rings() -> None:
    country = [[(-10.0, -10.0), (10.0, -10.0), (10.0, 10.0), (-10.0, 10.0), (-10.0, -10.0)]]
    region = [[(-5.0, -5.0), (5.0, -5.0), (5.0, 5.0), (-5.0, 5.0), (-5.0, -5.0)]]

    country_path, region_path = build_boundary_paths(country, region)

    assert not country_path.isEmpty()
    assert not region_path.isEmpty()
    assert country_path.elementCount() == 5
    assert region_path.elementCount() == 5


def test_map_path_builder_accepts_empty_datasets() -> None:
    country_path, region_path = build_boundary_paths([], [])
    assert country_path.isEmpty()
    assert region_path.isEmpty()



def test_fused_projection_and_radial_filter_match_staged_simplification() -> None:
    from swarmatlas.map_geometry import (
        _project,
        _project_and_simplify_ring,
    )

    geographic = [
        (-120.0 + index * 0.001, 35.0 + (0.00001 if index % 2 else 0.0))
        for index in range(500)
    ]
    tolerance = 0.00001
    staged = simplify_projected_ring(
        [_project(longitude, latitude) for longitude, latitude in geographic],
        tolerance,
    )

    fused = _project_and_simplify_ring(geographic, tolerance)

    assert fused == staged
    assert len(fused) < len(geographic)
