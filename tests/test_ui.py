from __future__ import annotations

import math
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPointF
from PySide6.QtGui import QPainterPath, QPixmap
from PySide6.QtWidgets import QApplication

from swarmatlas import config
from swarmatlas.theme import get_palette
from swarmatlas.ui import GeoIPDatabaseDialog, GeoIPProviderDialog, MainWindow, TrafficChart, WorldMap


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


def test_world_map_uses_openfreemap_dark_and_accepts_endpoints(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("swarmatlas.ui.app_data_dir", lambda: tmp_path)
    app = QApplication.instance() or QApplication([])
    widget = WorldMap(get_palette("dark"), "No map data")
    widget.resize(900, 420)
    widget.set_locations([{
        "ip": "8.8.8.8", "latitude": 37.4, "longitude": -122.0,
        "country": "United States", "region": "California", "city": "Mountain View",
    }])
    assert WorldMap.MAP_STYLE_URL == "https://tiles.openfreemap.org/styles/dark"
    assert len(widget._features) == 1
    assert widget._features[0]["geometry"]["coordinates"] == [-122.0, 37.4]
    assert widget._features[0]["properties"]["ip"] == "8.8.8.8"
    assert "maplibregl.Map" in WorldMap._map_html()
    widget.zoom_in()
    assert widget._zoom > 1.15
    widget.zoom_out()
    widget.reset_view()
    assert widget._zoom == 1.15
    widget.deleteLater()
    app.processEvents()


def test_world_map_viewport_is_rectangular_not_forced_to_square(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("swarmatlas.ui.app_data_dir", lambda: tmp_path)
    app = QApplication.instance() or QApplication([])
    widget = WorldMap(get_palette("dark"), "No map data")
    widget.resize(900, 420)
    assert widget.width() > widget.height()
    assert "tiles.openfreemap.org/styles/dark" in WorldMap._map_html()
    widget.deleteLater()
    app.processEvents()


def test_map_attribution_includes_openfreemap_and_openstreetmap(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(config, "app_data_dir", lambda: tmp_path)
    monkeypatch.setattr("swarmatlas.ui.app_data_dir", lambda: tmp_path)
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    attribution = window._map_attribution_html()
    assert "OpenFreeMap" in attribution
    assert "OpenMapTiles" in attribution
    assert "OpenStreetMap contributors" in attribution
    window.close()
    window.deleteLater()
    app.processEvents()


def test_world_map_skips_identical_endpoint_snapshots(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("swarmatlas.ui.app_data_dir", lambda: tmp_path)
    app = QApplication.instance() or QApplication([])
    widget = WorldMap(get_palette("dark"), "No map data")
    endpoint = {
        "ip": "8.8.8.8", "latitude": 37.4, "longitude": -122.0,
        "country": "United States", "region": "California", "city": "Mountain View",
    }
    snapshot = [endpoint]
    widget.set_locations(snapshot)
    features = widget._features
    widget.set_locations(snapshot)
    assert widget._location_source is snapshot
    assert widget._features is features
    identical_snapshot = [dict(endpoint)]
    widget.set_locations(identical_snapshot)
    assert widget._features is features
    changed = [dict(endpoint, city="Sunnyvale")]
    widget.set_locations(changed)
    assert widget._features is not features
    assert widget._features[0]["properties"]["city"] == "Sunnyvale"
    widget.deleteLater()
    app.processEvents()



def test_endpoint_table_shows_ip_without_geoip_coordinates(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(config, "app_data_dir", lambda: tmp_path)
    app = QApplication.instance() or QApplication([])
    window = MainWindow()

    window._fill_endpoints([{
        "ip": "8.8.8.8",
        "port": 443,
        "protocol": "TCP",
        "process_id": 1234,
    }])

    assert window.endpoint_table.rowCount() == 1
    assert window.endpoint_table.item(0, 0).text() == "8.8.8.8"

    window.close()
    window.deleteLater()
    app.processEvents()



def test_local_geoip_dialog_links_to_compatible_official_mmdb_downloads(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(config, "app_data_dir", lambda: tmp_path)
    app = QApplication.instance() or QApplication([])
    dialog = GeoIPDatabaseDialog([], "ru", get_palette("dark"))

    links = dialog.download_label.text()
    assert "db-ip.com/db/lite.php" in links
    assert "maxmind.com/en/geolite2/signup" in links
    assert "support.maxmind.com" in links
    assert dialog.download_label.openExternalLinks()
    assert ".mmdb" in dialog.download_instructions.text().lower()

    dialog.close()
    dialog.deleteLater()
    app.processEvents()


def test_local_provider_note_has_download_links(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(config, "app_data_dir", lambda: tmp_path)
    app = QApplication.instance() or QApplication([])
    dialog = GeoIPProviderDialog("dbip", {}, "ru", get_palette("dark"))

    assert "db-ip.com/db/lite.php" in dialog.info_label.text()
    assert "maxmind.com/en/geolite2/signup" in dialog.info_label.text()
    assert dialog.info_label.openExternalLinks()

    dialog.close()
    dialog.deleteLater()
    app.processEvents()



def test_traffic_chart_skips_copying_the_same_history_snapshot(
    tmp_path: Path, monkeypatch
) -> None:
    app = QApplication.instance() or QApplication([])
    chart = TrafficChart(get_palette("dark"), "No history")
    samples = [{"upload_bps": 10.0, "download_bps": 20.0}]
    chart.set_samples(samples)
    previous_upload = chart.upload
    previous_download = chart.download

    chart.set_samples(samples)

    assert chart.upload is previous_upload
    assert chart.download is previous_download
    chart.deleteLater()
    app.processEvents()



def test_world_map_uses_spatial_hash_for_marker_hover(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr("swarmatlas.ui.app_data_dir", lambda: tmp_path)
    app = QApplication.instance() or QApplication([])
    widget = WorldMap(get_palette("dark"), "No map data")
    widget.resize(640, 320)

    endpoints = []
    for row in range(20):
        normalized_y = row / 19
        latitude = math.degrees(
            math.atan(math.sinh(math.pi * (1.0 - 2.0 * normalized_y)))
        )
        for column in range(50):
            normalized_x = column / 49
            endpoints.append({
                "ip": f"198.51.100.{(row * 50 + column) % 250 + 1}",
                "longitude": normalized_x * 360.0 - 180.0,
                "latitude": latitude,
            })
    widget.set_locations(endpoints)

    target_index = 10 * 50 + 25
    normalized = widget._location_world_points[target_index]
    rect = widget._map_rect()
    world_size = rect.width() * widget._zoom
    target = QPointF(
        rect.center().x() + widget._pan.x() + (normalized.x() - 0.5) * world_size,
        rect.center().y() + widget._pan.y() + (normalized.y() - 0.5) * world_size,
    )
    assert widget._nearest_location(target) == target_index

    assert widget._location_grid
    cell_size = widget._location_grid_cell_size
    cell_x = math.floor(normalized.x() / cell_size)
    cell_y = math.floor(normalized.y() / cell_size)
    candidates = sum(
        len(widget._location_grid.get((x, y), ()))
        for y in range(cell_y - 1, cell_y + 2)
        for x in range(cell_x - 1, cell_x + 2)
    )
    assert candidates < 100
    old_grid_key = widget._location_grid_key

    widget.zoom_in()
    rect = widget._map_rect()
    world_size = rect.width() * widget._zoom
    target = QPointF(
        rect.center().x() + widget._pan.x() + (normalized.x() - 0.5) * world_size,
        rect.center().y() + widget._pan.y() + (normalized.y() - 0.5) * world_size,
    )
    assert widget._nearest_location(target) == target_index
    assert widget._location_grid_key != old_grid_key

    widget.deleteLater()
    app.processEvents()



def test_main_window_skips_table_rebuild_for_reused_worker_lists(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(config, "app_data_dir", lambda: tmp_path)
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    countries = [{"country": "United States", "peers": 4}]
    regions = [{"region": "California", "country": "United States", "peers": 2}]
    endpoints = [{
        "ip": "8.8.8.8",
        "protocol": "TCP",
        "port": 443,
        "country": "United States",
        "city": "Mountain View",
    }]

    window._fill_countries(countries)
    window._fill_regions(regions)
    window._fill_endpoints(endpoints)

    def unexpected_rebuild(*_args) -> None:
        raise AssertionError("unchanged worker list should not rebuild its table")

    monkeypatch.setattr("swarmatlas.ui.update_table", unexpected_rebuild)
    window._fill_countries(countries)
    window._fill_regions(regions)
    window._fill_endpoints(endpoints)

    window.close()
    window.deleteLater()
    app.processEvents()
