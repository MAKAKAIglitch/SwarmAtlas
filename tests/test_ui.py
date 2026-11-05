from __future__ import annotations

import os
from pathlib import Path

import pytest
from PySide6.QtCore import Signal

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication, QWidget

from swarmatlas import config
from swarmatlas.theme import get_palette
from swarmatlas.ui import GeoIPDatabaseDialog, GeoIPProviderDialog, MainWindow, TrafficChart, WorldMap


class _FakeWebEngineSettings:
    def setAttribute(self, *_args) -> None:
        pass


class _FakeWebEnginePage:
    def runJavaScript(self, *_args) -> None:
        pass


class _FakeWebEngineView(QWidget):
    loadFinished = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._settings = _FakeWebEngineSettings()
        self._page = _FakeWebEnginePage()
        self.html = ""

    def settings(self):
        return self._settings

    def page(self):
        return self._page

    def setHtml(self, html, *_args) -> None:
        self.html = html


@pytest.fixture(autouse=True)
def use_fake_webengine_for_ui_unit_tests(monkeypatch) -> None:
    # Keep UI unit tests deterministic and avoid launching a Chromium subprocess
    # on headless CI runners. The generated MapLibre page is asserted separately.
    monkeypatch.setattr("swarmatlas.ui.QWebEngineView", _FakeWebEngineView)
    monkeypatch.setattr(WorldMap, "_profile_configured", True)


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
        "process_id": 4321, "process_name": "browser.exe", "protocol": "TCP",
        "port": 443, "local_port": 51000, "connection_state": "ESTABLISHED",
        "asn": 15169, "organization": "Google LLC",
        "traffic_upload_bytes": 4096, "traffic_download_bytes": 8192,
        "traffic_upload_bps": 512.0, "traffic_download_bps": 2048.0,
        "traffic_flow_count": 1, "first_seen": 100.0, "last_seen": 200.0,
    }])
    assert WorldMap.MAP_STYLE_URL == "https://tiles.openfreemap.org/styles/dark"
    assert len(widget._features) == 1
    assert widget._features[0]["geometry"]["coordinates"] == [-122.0, 37.4]
    assert widget._features[0]["properties"]["ip"] == "8.8.8.8"
    properties = widget._features[0]["properties"]
    assert properties["process_name"] == "browser.exe"
    assert properties["process_id"] == 4321
    assert properties["traffic_upload_bps"] == 512.0
    assert properties["traffic_download_bps"] == 2048.0
    assert properties["traffic_bps"] == 2560.0
    assert properties["traffic_known"] == 1
    endpoint_id = properties["endpoint_id"]
    assert widget._details[endpoint_id]["traffic_upload_bytes"] == 4096
    assert widget._details[endpoint_id]["traffic_download_bytes"] == 8192
    html = WorldMap._map_html()
    assert "endpoint-glow" in html
    assert "swarm-popup" in html
    assert "Upload speed" in html
    assert "First seen" in html
    assert "map-legend" in html
    assert "maplibregl.Map" in html
    assert "cluster:true" in WorldMap._map_html()
    assert "getClusterExpansionZoom" in WorldMap._map_html()
    assert "clusterProperties" in WorldMap._map_html()
    assert "endpoint-cluster-glow" in WorldMap._map_html()
    widget.zoom_in()
    assert widget._zoom > 1.15
    widget.zoom_out()
    widget.reset_view()
    assert widget._zoom == 1.15
    widget.deleteLater()
    app.processEvents()


def test_world_map_refreshes_popup_details_without_rebuilding_map_geometry(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr("swarmatlas.ui.app_data_dir", lambda: tmp_path)
    app = QApplication.instance() or QApplication([])
    widget = WorldMap(get_palette("dark"), "No map data")
    endpoint = {
        "ip": "8.8.8.8", "latitude": 37.4, "longitude": -122.0,
        "country": "United States", "region": "California", "city": "Mountain View",
        "process_id": 4321, "process_name": "browser.exe", "protocol": "TCP",
        "port": 443, "local_port": 51000, "connection_state": "ESTABLISHED",
        "traffic_upload_bytes": 4096, "traffic_download_bytes": 8192,
        "traffic_upload_bps": 512.0, "traffic_download_bps": 2048.0,
        "traffic_flow_count": 1, "first_seen": 100.0, "last_seen": 200.0,
    }
    widget.set_locations([endpoint])
    features = widget._features
    endpoint["traffic_upload_bytes"] = 8192
    endpoint["last_seen"] = 205.0
    widget.set_locations([dict(endpoint)])
    assert widget._features is features
    endpoint_id = features[0]["properties"]["endpoint_id"]
    assert widget._details[endpoint_id]["traffic_upload_bytes"] == 8192
    assert widget._details[endpoint_id]["last_seen"] == 205.0
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
