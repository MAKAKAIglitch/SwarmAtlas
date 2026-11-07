from __future__ import annotations

import os
from pathlib import Path

import pytest
from PySide6.QtCore import QObject, Signal

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication, QWidget

from swarmatlas import config
from swarmatlas.theme import get_palette
from swarmatlas.i18n import tr
from swarmatlas.diagnostics_ui import DiagnosticsDialog
from swarmatlas.native import NATIVE_TRACEROUTE
from swarmatlas.ui import EndpointPingBridge, GeoIPDatabaseDialog, GeoIPProviderDialog, MainWindow, SmoothScrollArea, TrafficChart, WorldMap


class _FakeWebEngineSettings:
    def setAttribute(self, *_args) -> None:
        pass


class _FakeWebEnginePage(QObject):
    def __init__(self):
        super().__init__()

    def runJavaScript(self, *_args) -> None:
        pass

    def setWebChannel(self, *_args) -> None:
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
    assert isinstance(window.scroll_area, SmoothScrollArea)
    assert window.nav_keys == (
        "overview", "diagnostics", "destinations", "history", "applications"
    )
    assert window.diagnostics_card is not None
    assert window.open_ping_button.text()
    assert window.open_traceroute_button.text()
    assert window.app_card is not None

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
    assert properties["company_name"] == "Google"
    assert properties["company_icon"] == "company-as15169"
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
    assert "endpoint-company-icons" in html
    assert "minzoom:5.2" in html
    assert "simple-icons@16.36.0" in html
    assert "Network owner" in html
    widget.zoom_in()
    assert widget._zoom > 1.15
    widget.zoom_out()
    widget.reset_view()
    assert widget._zoom == 1.15
    widget.deleteLater()
    app.processEvents()


def test_company_network_detection_uses_known_asn_and_safe_fallback() -> None:
    assert WorldMap._company_for_network(15169, "Google LLC") == (
        "Google", "google", "#4285F4"
    )
    assert WorldMap._company_for_network(13335, "Cloudflare, Inc.")[0] == "Cloudflare"
    assert WorldMap._company_for_network(64512, "Unknown Transit") is None
    assert WorldMap._company_for_network(16509, "Amazon.com, Inc.") == (
        "Amazon Web Services", "", "#FF9900"
    )


def test_smooth_scroll_area_uses_interruptible_eased_animation() -> None:
    app = QApplication.instance() or QApplication([])
    area = SmoothScrollArea()
    content = QWidget()
    content.setMinimumHeight(1200)
    area.setWidgetResizable(True)
    area.setWidget(content)
    area.resize(320, 240)
    area.show()
    app.processEvents()
    assert area._scroll_animation.duration() == 360
    assert area._scroll_animation.easingCurve().type().name == "OutCubic"
    area.scroll_to_value(180, 200)
    assert area._scroll_animation.endValue() == 180
    area.close()
    area.deleteLater()
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



def test_world_map_localizes_estats_requirement_and_requests_ping_only_for_open_popup() -> None:
    html = WorldMap._map_html()
    assert "window.swarmAtlasPingBridge.ping(p.ip)" in html
    assert "data-ping-ip" in html
    assert tr("tcp_estats_requirement", "ru").startswith("Для сбора этих счётчиков")
    assert "required access permissions" in tr("tcp_estats_requirement", "en")


def test_cluster_traffic_intensity_uses_aggregated_rates_and_keeps_clustering() -> None:
    html = WorldMap._map_html()
    assert "clusterProperties" in html
    assert "traffic_bps:['+', ['get','traffic_bps']]" in html
    assert "['interpolate',['exponential',0.35],['get','traffic_bps']" in html
    assert "cluster:true,clusterRadius:48,clusterMaxZoom:5" in html


def test_ping_bridge_rejects_invalid_ip_without_spawning_process() -> None:
    bridge = EndpointPingBridge()
    results = []
    bridge.pingResult.connect(lambda ip, result: results.append((ip, result)))
    bridge.ping("not-an-ip")
    assert results == [("not-an-ip", "Некорректный IP-адрес")]


def test_endpoint_filter_matches_ip_process_provider_location_and_port(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(config, "app_data_dir", lambda: tmp_path)
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    endpoints = [
        {
            "ip": "8.8.8.8", "port": 443, "local_port": 51000,
            "protocol": "TCP", "process_id": 1234, "process_name": "browser.exe",
            "asn": 15169, "organization": "Google LLC", "city": "Mountain View",
            "region": "California", "country": "United States",
        },
        {
            "ip": "1.1.1.1", "port": 53, "local_port": 53000,
            "protocol": "UDP", "process_id": 4321, "process_name": "dns.exe",
            "asn": 13335, "organization": "Cloudflare", "city": "Sydney",
            "region": "New South Wales", "country": "Australia",
        },
    ]
    window._fill_endpoints(endpoints)
    assert window.endpoint_table.rowCount() == 2
    window.endpoint_search.setText("cloudflare")
    assert window.endpoint_table.rowCount() == 1
    assert window.endpoint_table.item(0, 0).text() == "1.1.1.1"
    window.endpoint_search.setText("51000")
    assert window.endpoint_table.rowCount() == 1
    assert window.endpoint_table.item(0, 0).text() == "8.8.8.8"
    window.endpoint_search.clear()
    assert window.endpoint_table.rowCount() == 2
    window.close()
    window.deleteLater()
    app.processEvents()


def test_csv_export_writer_uses_utf8_bom_and_preserves_values(tmp_path: Path) -> None:
    path = tmp_path / "export.csv"
    MainWindow._write_csv(str(path), ["ip", "location"], [["8.8.8.8", "Москва"]])
    content = path.read_bytes()
    assert content[:3] == bytes((239, 187, 191))
    assert "Москва".encode("utf-8") in content
    assert b"8.8.8.8" in content


def test_diagnostics_quick_actions_open_the_requested_tab(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(config, "app_data_dir", lambda: tmp_path)
    app = QApplication.instance() or QApplication([])
    opened_tabs: list[str] = []

    class _DialogStub:
        def __init__(self, language, parent=None, initial_tab="ping"):
            opened_tabs.append(initial_tab)

        def exec(self) -> int:
            return 0

    monkeypatch.setattr("swarmatlas.ui.DiagnosticsDialog", _DialogStub)
    window = MainWindow()
    window.open_ping_diagnostics()
    window.open_traceroute_diagnostics()

    assert opened_tabs == ["ping", "traceroute"]
    window.close()
    window.deleteLater()
    app.processEvents()


def test_diagnostics_dialog_exposes_ping_and_icmp_traceroute() -> None:
    app = QApplication.instance() or QApplication([])
    dialog = DiagnosticsDialog("en")
    assert dialog.tabs.count() == 2
    assert dialog.ping_family.count() == 2
    assert dialog.trace_family.count() == 2
    assert dialog.trace_method.itemData(0) == "icmp"
    assert dialog.trace_method.model().item(1).isEnabled() is NATIVE_TRACEROUTE
    assert dialog.trace_method.model().item(2).isEnabled() is NATIVE_TRACEROUTE
    assert dialog.trace_probes.value() == 3
    assert dialog.trace_port.value() == 443
    assert dialog.tabs.currentIndex() == 0
    dialog.close()

    traceroute_dialog = DiagnosticsDialog("en", initial_tab="traceroute")
    assert traceroute_dialog.tabs.currentIndex() == 1
    traceroute_dialog.close()
    app.processEvents()
