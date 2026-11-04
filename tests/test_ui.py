from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication

from swarmatlas import config
from swarmatlas.theme import get_palette
from swarmatlas.ui import MainWindow, WorldMap


def test_main_window_exposes_system_theme_language_and_region_views(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(config, "app_data_dir", lambda: tmp_path)
    app = QApplication.instance() or QApplication([])

    window = MainWindow()
    assert window.theme_combo.count() == 3
    assert window.language_combo.count() == 3
    assert window.theme_combo.itemData(0) == "system"
    assert window.language_combo.itemData(0) == "system"
    assert window.theme_combo.currentData() == "system"
    assert window.language_combo.currentData() == "system"
    assert window.regions_table.columnCount() == 2
    assert window.map_expand_button.text()

    window.close()
    window.deleteLater()
    app.processEvents()


def test_world_map_zoom_controls_and_vector_boundaries_render_offscreen(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr("swarmatlas.ui.app_data_dir", lambda: tmp_path)
    app = QApplication.instance() or QApplication([])
    widget = WorldMap(get_palette("dark"), "No local GeoIP data")
    # Rendering tests stay deterministic and do not contact the public tile server.
    monkeypatch.setattr(widget, "_request_tile", lambda *_args: None)
    widget.resize(640, 320)
    country = [[(-20, 20), (20, 20), (20, -20), (-20, -20), (-20, 20)]]
    region = [[(-10, 10), (10, 10), (10, -10), (-10, -10), (-10, 10)]]
    widget.set_boundaries(country, region)

    assert widget._has_boundaries is True
    widget.zoom_in()
    assert widget._zoom > 1.0
    widget.zoom_out()
    widget.reset_view()
    assert widget._zoom == 1.0

    widget.set_locations([{
        "ip": "8.8.8.8",
        "latitude": 37.4,
        "longitude": -122.0,
        "country": "United States",
        "region": "California",
        "city": "Mountain View",
    }])
    pixmap = QPixmap(widget.size())
    pixmap.fill()
    widget.render(pixmap)
    assert not pixmap.isNull()
    assert widget._nearest_location(widget._screen_point(-122.0, 37.4)) == 0
    widget.deleteLater()
    app.processEvents()


def test_osm_world_map_uses_web_mercator_and_caps_at_city_region_zoom(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr("swarmatlas.ui.app_data_dir", lambda: tmp_path)
    app = QApplication.instance() or QApplication([])
    widget = WorldMap(get_palette("light"), "No map data")
    widget.resize(640, 320)

    center = widget._normalized_point(0.0, 0.0)
    north = widget._normalized_point(0.0, WorldMap.MERCATOR_MAX_LAT)
    south = widget._normalized_point(0.0, -WorldMap.MERCATOR_MAX_LAT)

    assert abs(center.x() - 0.5) < 1e-9
    assert abs(center.y() - 0.5) < 1e-9
    assert north.y() < 1e-6
    assert south.y() > 1.0 - 1e-6
    assert abs(widget._map_rect().width() - widget._map_rect().height()) < 1e-9

    assert WorldMap._tile_zoom_for_scale(1.0) == 2
    assert WorldMap._tile_zoom_for_scale(2.0) == 3
    assert WorldMap._tile_zoom_for_scale(64.0) == 8
    assert WorldMap._tile_zoom_for_scale(256.0) == WorldMap.TILE_MAX_ZOOM

    widget._zoom_at(widget._map_rect().center(), 1000.0)
    assert widget._zoom == 64.0
    widget.deleteLater()
    app.processEvents()


def test_map_attribution_includes_openstreetmap_contributors(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(config, "app_data_dir", lambda: tmp_path)
    monkeypatch.setattr("swarmatlas.ui.app_data_dir", lambda: tmp_path)
    app = QApplication.instance() or QApplication([])
    window = MainWindow()

    attribution = window._map_attribution_html()
    assert "OpenStreetMap contributors" in attribution
    assert "openstreetmap.org/copyright" in attribution

    window.close()
    window.deleteLater()
    app.processEvents()
