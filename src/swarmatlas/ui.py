from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, QPointF
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QFont
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QMainWindow,
    QMessageBox, QPushButton, QScrollArea, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from .collector import NetworkWorker, available_interfaces
from .config import AppConfig
from .utils import format_bytes, format_rate


BG = "#F5F7F8"
WHITE = "#FFFFFF"
TEXT = "#17212B"
MUTED = "#74818D"
LINE = "#E6EBEE"
ACCENT = "#159D93"
ACCENT_LIGHT = "#E6F5F3"


def card() -> QFrame:
    frame = QFrame()
    frame.setObjectName("card")
    return frame


def make_label(text: str, size: int = 10, color: str = TEXT, weight: int = 400) -> QLabel:
    label = QLabel(text)
    font = label.font()
    font.setPointSize(size)
    font.setWeight(QFont.Weight(weight))
    label.setFont(font)
    label.setStyleSheet(f"color: {color};")
    return label


class MetricCard(QFrame):
    def __init__(self, title: str, accent: str):
        super().__init__()
        self.setObjectName("card")
        self.setMinimumHeight(112)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 15, 18, 14)
        layout.setSpacing(7)
        self.title = make_label(title, 10, MUTED, 600)
        self.value = make_label("—", 23, TEXT, 600)
        self.value.setStyleSheet(f"color: {accent};")
        self.hint = make_label("Since monitoring started", 9, MUTED)
        layout.addWidget(self.title)
        layout.addWidget(self.value)
        layout.addWidget(self.hint)

    def set_value(self, value: str, hint: str | None = None) -> None:
        self.value.setText(value)
        if hint is not None:
            self.hint.setText(hint)


class TrafficChart(QWidget):
    def __init__(self):
        super().__init__()
        self.setMinimumHeight(190)
        self.upload: list[float] = []
        self.download: list[float] = []

    def set_samples(self, samples: list[dict[str, Any]]) -> None:
        self.upload = [float(item["upload_bps"]) for item in samples]
        self.download = [float(item["download_bps"]) for item in samples]
        self.update()

    def paintEvent(self, event: Any) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(10, 12, -10, -18)
        painter.setPen(QPen(QColor(LINE), 1))
        for row in range(4):
            y = rect.top() + row * rect.height() / 3
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))

        peak = max(self.upload + self.download, default=0.0)
        if peak <= 0 or len(self.upload) < 2:
            painter.setPen(QColor(MUTED))
            painter.drawText(
                rect, Qt.AlignmentFlag.AlignCenter,
                "Traffic history will appear as data is collected",
            )
            return

        def draw_series(series: list[float], color: str) -> None:
            if len(series) < 2:
                return
            path = QPainterPath()
            for index, value in enumerate(series):
                x = rect.left() + index * rect.width() / max(1, len(series) - 1)
                y = rect.bottom() - (value / peak) * rect.height()
                point = QPointF(x, y)
                if index == 0:
                    path.moveTo(point)
                else:
                    path.lineTo(point)
            painter.setPen(QPen(QColor(color), 2))
            painter.drawPath(path)

        draw_series(self.download, "#8E9AAF")
        draw_series(self.upload, ACCENT)


class WorldMap(QWidget):
    """Lightweight schematic map; points are approximate GeoIP coordinates."""

    def __init__(self):
        super().__init__()
        self.setMinimumHeight(275)
        self.locations: list[dict[str, Any]] = []

    def set_locations(self, locations: list[dict[str, Any]]) -> None:
        self.locations = [
            item for item in locations
            if item.get("latitude") is not None and item.get("longitude") is not None
        ]
        self.update()

    def _point(self, lon: float, lat: float, rect: Any) -> QPointF:
        x = rect.left() + (lon + 180.0) / 360.0 * rect.width()
        y = rect.top() + (90.0 - lat) / 180.0 * rect.height()
        return QPointF(x, y)

    def paintEvent(self, event: Any) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        area = self.rect().adjusted(12, 8, -12, -8)

        painter.setPen(QPen(QColor("#EEF1F2"), 1))
        for lon in (-120, -60, 0, 60, 120):
            x = self._point(lon, 0, area).x()
            painter.drawLine(QPointF(x, area.top()), QPointF(x, area.bottom()))
        for lat in (-45, 0, 45):
            y = self._point(0, lat, area).y()
            painter.drawLine(QPointF(area.left(), y), QPointF(area.right(), y))

        continents = [
            [(-168, 70), (-145, 72), (-125, 55), (-110, 50), (-95, 20), (-80, 8), (-78, 28), (-60, 48), (-82, 60), (-105, 72), (-135, 73)],
            [(-82, 12), (-65, 8), (-50, -5), (-45, -25), (-58, -55), (-72, -35), (-78, -5)],
            [(-12, 72), (35, 70), (70, 55), (105, 58), (145, 48), (165, 62), (178, 48), (150, 20), (120, 5), (100, 10), (80, -5), (55, 10), (42, 32), (20, 35), (5, 55)],
            [(-18, 35), (12, 37), (38, 20), (48, 0), (35, -35), (18, -35), (5, -5), (-10, 10)],
            [(112, -12), (153, -10), (155, -40), (132, -45), (114, -30)],
            [(-52, 82), (-25, 80), (-38, 60)],
        ]
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#E9EFF0"))
        for polygon in continents:
            path = QPainterPath()
            for index, (lon, lat) in enumerate(polygon):
                point = self._point(lon, lat, area)
                if index == 0:
                    path.moveTo(point)
                else:
                    path.lineTo(point)
            path.closeSubpath()
            painter.drawPath(path)

        if not self.locations:
            painter.setPen(QColor(MUTED))
            painter.drawText(
                area.adjusted(0, area.height() - 34, 0, 0),
                Qt.AlignmentFlag.AlignCenter,
                "Choose a local GeoIP database to locate active endpoints",
            )
            return

        for item in self.locations:
            point = self._point(float(item["longitude"]), float(item["latitude"]), area)
            painter.setPen(QPen(QColor(ACCENT), 1))
            painter.setBrush(QColor(ACCENT_LIGHT))
            painter.drawEllipse(point, 5.5, 5.5)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SwarmAtlas")
        self.resize(1320, 860)
        self.setMinimumSize(1050, 700)
        self.config = AppConfig.load()
        self.worker = NetworkWorker(self.config)
        self.setStyleSheet(
            f"""
            QWidget {{ background: {BG}; color: {TEXT}; font-family: 'Segoe UI'; }}
            QFrame#sidebar {{ background: {WHITE}; border-right: 1px solid {LINE}; }}
            QFrame#card {{ background: {WHITE}; border: 1px solid {LINE}; border-radius: 12px; }}
            QPushButton#nav {{ text-align: left; padding: 10px 12px; border: 0; border-radius: 8px; color: {MUTED}; }}
            QPushButton#nav:hover {{ background: {BG}; color: {TEXT}; }}
            QPushButton#nav[active="true"] {{ background: {ACCENT_LIGHT}; color: {ACCENT}; font-weight: 600; }}
            QComboBox {{ background: {WHITE}; border: 1px solid {LINE}; border-radius: 8px; padding: 8px 10px; min-width: 150px; }}
            QPushButton#quiet {{ background: {WHITE}; border: 1px solid {LINE}; border-radius: 8px; padding: 8px 12px; }}
            QTableWidget {{ background: {WHITE}; border: 0; gridline-color: {LINE}; selection-background-color: {ACCENT_LIGHT}; }}
            QHeaderView::section {{ background: {WHITE}; color: {MUTED}; border: 0; border-bottom: 1px solid {LINE}; padding: 8px; }}
            QScrollArea {{ border: 0; }}
            """
        )
        self._build_ui()
        self.worker.snapshot_ready.connect(self.update_snapshot)
        self.worker.status_changed.connect(self.update_geo_status)
        self.worker.start()

    def _build_ui(self) -> None:
        root = QWidget()
        outer = QHBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(218)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(18, 24, 18, 18)
        side.setSpacing(10)
        side.addWidget(make_label("SwarmAtlas", 18, TEXT, 700))
        side.addWidget(make_label("P2P traffic atlas", 9, MUTED))
        side.addSpacing(28)

        self.nav_buttons: list[QPushButton] = []
        for title in ("Overview", "Destinations", "History"):
            button = QPushButton(title)
            button.setObjectName("nav")
            button.setProperty("active", title == "Overview")
            button.clicked.connect(lambda checked=False, name=title: self.select_page(name))
            side.addWidget(button)
            self.nav_buttons.append(button)
        side.addStretch(1)
        settings = QPushButton("Settings  ·  GeoIP")
        settings.setObjectName("quiet")
        settings.clicked.connect(self.choose_geoip_database)
        side.addWidget(settings)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(28, 22, 28, 22)
        content_layout.setSpacing(18)

        header = QHBoxLayout()
        title_box = QVBoxLayout()
        title_box.setSpacing(3)
        title_box.addWidget(make_label("Overview", 22, TEXT, 700))
        title_box.addWidget(make_label("Your network activity, kept local.", 10, MUTED))
        header.addLayout(title_box)
        header.addStretch(1)
        self.interface_combo = QComboBox()
        self.interface_combo.addItem("Auto interface", "auto")
        for name in available_interfaces():
            self.interface_combo.addItem(name, name)
        saved_index = self.interface_combo.findData(self.config.interface)
        self.interface_combo.setCurrentIndex(max(0, saved_index))
        self.interface_combo.currentIndexChanged.connect(self.change_interface)
        header.addWidget(self.interface_combo)
        header.addWidget(make_label("READ-ONLY", 9, ACCENT, 650))
        self.live_status = make_label("●  Starting monitor", 10, ACCENT, 600)
        header.addWidget(self.live_status)
        content_layout.addLayout(header)

        metrics = QHBoxLayout()
        metrics.setSpacing(12)
        self.upload_card = MetricCard("UPLOADED", ACCENT)
        self.download_card = MetricCard("DOWNLOADED", "#7185A5")
        self.rate_card = MetricCard("LIVE TRANSFER", TEXT)
        metrics.addWidget(self.upload_card)
        metrics.addWidget(self.download_card)
        metrics.addWidget(self.rate_card)
        content_layout.addLayout(metrics)

        middle = QHBoxLayout()
        middle.setSpacing(12)

        map_card = card()
        map_layout = QVBoxLayout(map_card)
        map_layout.setContentsMargins(18, 16, 18, 14)
        map_heading = QHBoxLayout()
        map_heading.addWidget(make_label("World activity", 13, TEXT, 600))
        map_heading.addStretch(1)
        map_heading.addWidget(make_label("Approximate IP locations", 9, MUTED))
        map_layout.addLayout(map_heading)
        self.world_map = WorldMap()
        map_layout.addWidget(self.world_map)
        middle.addWidget(map_card, 7)

        countries_card = card()
        countries_layout = QVBoxLayout(countries_card)
        countries_layout.setContentsMargins(18, 16, 18, 14)
        countries_layout.addWidget(make_label("Top countries", 13, TEXT, 600))
        countries_layout.addWidget(make_label("Active public endpoints · not traffic volume", 9, MUTED))
        self.country_table = QTableWidget(0, 2)
        self.country_table.setHorizontalHeaderLabels(["Country", "Peers"])
        self.country_table.horizontalHeader().setStretchLastSection(False)
        self.country_table.setColumnWidth(0, 150)
        self.country_table.setColumnWidth(1, 55)
        self.country_table.verticalHeader().setVisible(False)
        self.country_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.country_table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.country_table.setShowGrid(False)
        countries_layout.addWidget(self.country_table)
        middle.addWidget(countries_card, 3)
        content_layout.addLayout(middle)

        bottom = QHBoxLayout()
        bottom.setSpacing(12)
        chart_card = card()
        chart_layout = QVBoxLayout(chart_card)
        chart_layout.setContentsMargins(18, 16, 18, 12)
        chart_title = QHBoxLayout()
        chart_title.addWidget(make_label("Traffic history", 13, TEXT, 600))
        chart_title.addStretch(1)
        chart_title.addWidget(make_label("Last hour", 9, MUTED))
        chart_layout.addLayout(chart_title)
        self.chart = TrafficChart()
        chart_layout.addWidget(self.chart)
        bottom.addWidget(chart_card, 6)

        peers_card = card()
        peers_layout = QVBoxLayout(peers_card)
        peers_layout.setContentsMargins(18, 16, 18, 12)
        peers_layout.addWidget(make_label("Recent destinations", 13, TEXT, 600))
        self.endpoint_table = QTableWidget(0, 3)
        self.endpoint_table.setHorizontalHeaderLabels(["IP address", "Location", "Type"])
        self.endpoint_table.setColumnWidth(0, 120)
        self.endpoint_table.setColumnWidth(1, 130)
        self.endpoint_table.horizontalHeader().setStretchLastSection(True)
        self.endpoint_table.verticalHeader().setVisible(False)
        self.endpoint_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.endpoint_table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.endpoint_table.setShowGrid(False)
        peers_layout.addWidget(self.endpoint_table)
        bottom.addWidget(peers_card, 5)
        content_layout.addLayout(bottom)

        self.data_note = make_label(
            "Read-only monitoring: interface totals are exact; destination byte attribution is not available yet.",
            9, MUTED,
        )
        content_layout.addWidget(self.data_note)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)
        outer.addWidget(sidebar)
        outer.addWidget(scroll, 1)
        self.setCentralWidget(root)

    def select_page(self, name: str) -> None:
        for button in self.nav_buttons:
            active = button.text() == name
            button.setProperty("active", active)
            button.style().unpolish(button)
            button.style().polish(button)
        if name != "Overview":
            QMessageBox.information(
                self, name,
                f"{name} view is part of the next prototype milestone. The live overview is available now.",
            )

    def change_interface(self) -> None:
        interface = str(self.interface_combo.currentData() or "auto")
        self.config.interface = interface
        self.config.save()
        self.worker.set_interface(interface)

    def choose_geoip_database(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose a local GeoIP City database", "", "MaxMind database (*.mmdb)"
        )
        if not path:
            return
        self.config.geoip_database = path
        self.config.save()
        QMessageBox.information(
            self, "GeoIP database saved",
            "The local database will be loaded when SwarmAtlas is restarted.",
        )

    def update_geo_status(self, status: str) -> None:
        self.statusBar().showMessage(status)

    def update_snapshot(self, snapshot: dict[str, Any]) -> None:
        upload = int(snapshot.get("upload_total", 0))
        download = int(snapshot.get("download_total", 0))
        up_rate = float(snapshot.get("upload_bps", 0))
        down_rate = float(snapshot.get("download_bps", 0))

        self.upload_card.set_value(format_bytes(upload), "Selected interface · saved locally")
        self.download_card.set_value(format_bytes(download), "Selected interface · saved locally")
        self.rate_card.set_value(
            f"{format_rate(up_rate)}  ↑",
            f"{format_rate(down_rate)}  ↓",
        )
        endpoints = snapshot.get("active_endpoints", [])
        self.live_status.setText(f"●  Monitoring · {len(endpoints)} endpoints")
        self.world_map.set_locations(endpoints)
        self.chart.set_samples(snapshot.get("samples", []))
        self._fill_countries(snapshot.get("countries", []))
        self._fill_endpoints(endpoints)

        self.data_note.setText(
            "Read-only: exact interface totals; endpoint locations are approximate. Bytes per IP/country are not attributed yet."
        )

    def _fill_countries(self, countries: list[dict[str, Any]]) -> None:
        self.country_table.setRowCount(len(countries) or 1)
        if not countries:
            self.country_table.setItem(0, 0, QTableWidgetItem("GeoIP not configured"))
            self.country_table.setItem(0, 1, QTableWidgetItem("—"))
            return
        for row, item in enumerate(countries):
            name = item.get("country") or item.get("country_code") or "Unknown"
            self.country_table.setItem(row, 0, QTableWidgetItem(str(name)))
            self.country_table.setItem(row, 1, QTableWidgetItem(str(item.get("peers", 0))))

    def _fill_endpoints(self, endpoints: list[dict[str, Any]]) -> None:
        rows = endpoints[:10]
        self.endpoint_table.setRowCount(len(rows))
        for row, item in enumerate(rows):
            location = ", ".join(
                str(value) for value in (item.get("city"), item.get("country")) if value
            ) or "Unknown"
            self.endpoint_table.setItem(row, 0, QTableWidgetItem(str(item["ip"])))
            self.endpoint_table.setItem(row, 1, QTableWidgetItem(location))
            self.endpoint_table.setItem(row, 2, QTableWidgetItem(str(item["protocol"])))

    def closeEvent(self, event: Any) -> None:
        if self.worker.isRunning() and not self.worker.stop():
            QMessageBox.warning(
                self, "SwarmAtlas is still stopping",
                "The read-only monitor has not finished its current OS query. Close the window again in a moment.",
            )
            event.ignore()
            return
        event.accept()
