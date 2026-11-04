from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, Qt, QPointF, Signal, Slot
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QFont, QGuiApplication
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFrame,
    QGraphicsOpacityEffect, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QMainWindow, QMessageBox, QPushButton, QScrollArea, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from .collector import NetworkWorker, available_interfaces
from .config import AppConfig
from .i18n import tr, translate_status
from .theme import apply_windows_mica, get_palette, resolve_language, resolve_theme
from .utils import format_bytes, format_rate


def card() -> QFrame:
    frame = QFrame()
    frame.setObjectName("card")
    return frame


def make_label(
    text: str,
    size: int = 10,
    color: str = "#17212B",
    weight: int = 400,
) -> QLabel:
    label = QLabel(text)
    font = label.font()
    font.setPointSize(size)
    font.setWeight(QFont.Weight(weight))
    label.setFont(font)
    label.setStyleSheet(f"color: {color}; background: transparent;")
    return label


def update_table(table: QTableWidget, rows: list[list[str]]) -> None:
    """Update existing cells in place to avoid clearing/recreating tables every second."""
    table.setUpdatesEnabled(False)
    try:
        table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            for column_index, value in enumerate(row):
                item = table.item(row_index, column_index)
                if item is None:
                    table.setItem(row_index, column_index, QTableWidgetItem(value))
                elif item.text() != value:
                    item.setText(value)
    finally:
        table.setUpdatesEnabled(True)
        table.viewport().update()


class MetricCard(QFrame):
    def __init__(self, title: str, accent: str, hint: str, palette: dict[str, str]):
        super().__init__()
        self.setObjectName("card")
        self.setMinimumHeight(112)
        self.accent = accent
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 15, 18, 14)
        layout.setSpacing(7)
        self.title = make_label(title, 10, palette["muted"], 600)
        self.value = make_label("—", 23, accent, 600)
        self.hint = make_label(hint, 9, palette["muted"])
        self._opacity = QGraphicsOpacityEffect(self.value)
        self._opacity.setOpacity(1.0)
        self.value.setGraphicsEffect(self._opacity)
        self._fade = QPropertyAnimation(self._opacity, b"opacity", self)
        self._fade.setDuration(170)
        self._fade.setStartValue(0.76)
        self._fade.setEndValue(1.0)
        self._fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        layout.addWidget(self.title)
        layout.addWidget(self.value)
        layout.addWidget(self.hint)

    def set_value(self, value: str, hint: str | None = None) -> None:
        changed = self.value.text() != value
        if changed:
            self.value.setText(value)
            self._fade.stop()
            self._opacity.setOpacity(0.76)
            self._fade.start()
        if hint is not None and self.hint.text() != hint:
            self.hint.setText(hint)


class GeoIPDatabaseDialog(QDialog):
    def __init__(self, paths: list[str], language: str, palette: dict[str, str], parent=None):
        super().__init__(parent)
        self.language = language
        self.palette = palette
        self.setWindowTitle(tr("geoip_settings", language))
        self.setMinimumSize(540, 370)

        layout = QVBoxLayout(self)
        intro = make_label(tr("geoip_dialog_intro", language), 10, palette["text"])
        intro.setWordWrap(True)
        note = make_label(tr("geoip_privacy_note", language), 9, palette["muted"])
        note.setWordWrap(True)
        layout.addWidget(intro)
        layout.addWidget(note)

        self.empty_label = make_label(tr("geoip_empty", language), 9, palette["muted"])
        layout.addWidget(self.empty_label)
        self.items = QListWidget()
        self.items.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        layout.addWidget(self.items, 1)
        for path in paths:
            self._add_path(path)
        self._update_empty_state()
        self.items.model().rowsInserted.connect(self._update_empty_state)
        self.items.model().rowsRemoved.connect(self._update_empty_state)

        actions = QHBoxLayout()
        self.add_button = QPushButton(tr("geoip_add", language))
        self.add_button.setObjectName("quiet")
        self.remove_button = QPushButton(tr("geoip_remove", language))
        self.remove_button.setObjectName("quiet")
        self.add_button.clicked.connect(self.add_databases)
        self.remove_button.clicked.connect(self.remove_selected)
        actions.addWidget(self.add_button)
        actions.addWidget(self.remove_button)
        actions.addStretch(1)
        layout.addLayout(actions)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.setStyleSheet(
            f"QDialog {{ background: {palette['bg']}; color: {palette['text']}; }}"
            f"QListWidget {{ background: {palette['surface']}; color: {palette['text']}; "
            f"border: 1px solid {palette['line']}; border-radius: 8px; padding: 6px; }}"
            f"QPushButton {{ background: {palette['surface_alt']}; color: {palette['text']}; "
            f"border: 1px solid {palette['line']}; border-radius: 7px; padding: 7px 11px; }}"
            f"QPushButton:hover {{ border-color: {palette['accent']}; }}"
        )

    def _add_path(self, path: str) -> None:
        normalized = str(Path(path).expanduser())
        key = normalized.casefold()
        if any(
            str(self.items.item(i).data(Qt.ItemDataRole.UserRole)).casefold() == key
            for i in range(self.items.count())
        ):
            return
        item = QListWidgetItem(f"{Path(normalized).name}   ·   {normalized}")
        item.setData(Qt.ItemDataRole.UserRole, normalized)
        item.setToolTip(normalized)
        self.items.addItem(item)
        self._update_empty_state()

    def _update_empty_state(self, *_args: Any) -> None:
        self.empty_label.setVisible(self.items.count() == 0)

    def add_databases(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            tr("geoip_title", self.language),
            "",
            tr("geoip_filter", self.language),
        )
        for path in paths:
            self._add_path(path)

    def remove_selected(self) -> None:
        for item in self.items.selectedItems():
            self.items.takeItem(self.items.row(item))
        self._update_empty_state()

    def selected_paths(self) -> list[str]:
        return [
            str(self.items.item(index).data(Qt.ItemDataRole.UserRole))
            for index in range(self.items.count())
        ]


class TrafficChart(QWidget):
    def __init__(self, palette: dict[str, str], empty_text: str):
        super().__init__()
        self.setMinimumHeight(190)
        self.palette_colors = palette
        self.empty_text = empty_text
        self.upload: list[float] = []
        self.download: list[float] = []

    def set_theme(self, palette: dict[str, str], empty_text: str) -> None:
        self.palette_colors = palette
        self.empty_text = empty_text
        self.update()

    def set_samples(self, samples: list[dict[str, Any]]) -> None:
        self.upload = [float(item["upload_bps"]) for item in samples]
        self.download = [float(item["download_bps"]) for item in samples]
        self.update()

    def paintEvent(self, event: Any) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(10, 12, -10, -18)
        colors = self.palette_colors
        painter.setPen(QPen(QColor(colors["grid"]), 1))
        for row in range(4):
            y = rect.top() + row * rect.height() / 3
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))

        peak = max(self.upload + self.download, default=0.0)
        if peak <= 0 or len(self.upload) < 2:
            painter.setPen(QColor(colors["muted"]))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, self.empty_text)
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

        draw_series(self.download, colors["download"])
        draw_series(self.upload, colors["accent"])


class WorldMap(QWidget):
    """Lightweight schematic map; points are approximate GeoIP coordinates."""

    def __init__(self, palette: dict[str, str], empty_text: str):
        super().__init__()
        self.setMinimumHeight(275)
        self.palette_colors = palette
        self.empty_text = empty_text
        self.locations: list[dict[str, Any]] = []

    def set_theme(self, palette: dict[str, str], empty_text: str) -> None:
        self.palette_colors = palette
        self.empty_text = empty_text
        self.update()

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
        colors = self.palette_colors

        painter.setPen(QPen(QColor(colors["grid"]), 1))
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
        painter.setBrush(QColor(colors["land"]))
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
            painter.setPen(QColor(colors["muted"]))
            painter.drawText(
                area.adjusted(0, area.height() - 34, 0, 0),
                Qt.AlignmentFlag.AlignCenter,
                self.empty_text,
            )
            return

        for item in self.locations:
            point = self._point(float(item["longitude"]), float(item["latitude"]), area)
            painter.setPen(QPen(QColor(colors["accent"]), 1))
            painter.setBrush(QColor(colors["accent_light"]))
            painter.drawEllipse(point, 5.5, 5.5)


class MainWindow(QMainWindow):
    initialization_ready = Signal(str)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("SwarmAtlas")
        self.resize(1320, 860)
        self.setMinimumSize(1050, 700)
        self.config = AppConfig.load()
        self.current_theme = resolve_theme(self.config.theme)
        self.current_language = resolve_language(self.config.language)
        self.palette = get_palette(self.current_theme)
        self._mica_attempted = False
        self._mica_applied = False
        self._apply_theme()
        self.worker = NetworkWorker(self.config)
        self.worker.snapshot_ready.connect(self.update_snapshot)
        self.worker.status_changed.connect(self.update_geo_status)
        self.worker.startup_ready.connect(self._on_worker_startup)

        self._build_ui()
        self._style_hints = QGuiApplication.styleHints()
        color_signal = getattr(self._style_hints, "colorSchemeChanged", None)
        if color_signal is not None:
            color_signal.connect(self._on_system_theme_changed)

    @Slot(str)
    def _on_worker_startup(self, status: str) -> None:
        self.initialization_ready.emit(status)

    def t(self, key: str, **values: object) -> str:
        return tr(key, self.current_language, **values)

    def start_monitor(self) -> None:
        if not self.worker.isRunning():
            self.worker.start()

    def _apply_theme(self) -> None:
        c = self.palette
        window_bg = c["mica_window"] if self._mica_applied else c["bg"]
        surface = c["mica_surface"] if self._mica_applied else c["surface"]
        sidebar_surface = c["mica_sidebar"] if self._mica_applied else c["surface"]
        self.setStyleSheet(
            f"""
            QMainWindow {{ background: {window_bg}; }}
            QWidget {{ color: {c['text']}; font-family: 'Segoe UI'; }}
            QLabel {{ background: transparent; }}
            QWidget#appRoot {{ background: {window_bg}; }}
            QWidget#content {{ background: transparent; }}
            QFrame#sidebar {{ background: {sidebar_surface}; border-right: 1px solid {c['line']}; }}
            QFrame#card {{ background: {surface}; border: 1px solid {c['line']}; border-radius: 12px; }}
            QPushButton#nav {{ text-align: left; padding: 10px 12px; border: 0; border-radius: 8px; color: {c['muted']}; background: transparent; }}
            QPushButton#nav:hover {{ background: {c['surface_alt']}; color: {c['text']}; }}
            QPushButton#nav[active="true"] {{ background: {c['accent_light']}; color: {c['accent']}; font-weight: 600; }}
            QComboBox {{ background: {c['surface_alt']}; color: {c['text']}; border: 1px solid {c['line']}; border-radius: 8px; padding: 7px 9px; min-width: 138px; }}
            QComboBox:hover {{ border-color: {c['accent']}; }}
            QComboBox QAbstractItemView {{ background: {c['surface']}; color: {c['text']}; border: 1px solid {c['line']}; selection-background-color: {c['selection']}; outline: 0; padding: 4px; }}
            QPushButton#quiet {{ background: {c['surface_alt']}; color: {c['text']}; border: 1px solid {c['line']}; border-radius: 8px; padding: 8px 12px; }}
            QPushButton#quiet:hover {{ border-color: {c['accent']}; }}
            QTableWidget {{ background: {surface}; alternate-background-color: {c['surface_alt']}; color: {c['text']}; border: 0; gridline-color: {c['line']}; selection-background-color: {c['selection']}; }}
            QHeaderView::section {{ background: {surface}; color: {c['muted']}; border: 0; border-bottom: 1px solid {c['line']}; padding: 8px; }}
            QTableCornerButton::section {{ background: {surface}; border: 0; }}
            QScrollArea {{ border: 0; background: transparent; }}
            QScrollBar:vertical {{ background: {window_bg}; width: 9px; margin: 0; }}
            QScrollBar::handle:vertical {{ background: {c['scroll']}; min-height: 24px; border-radius: 4px; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
            QStatusBar {{ background: {surface}; color: {c['muted']}; border-top: 1px solid {c['line']}; }}
            """
        )

    def _rebuild_ui(self) -> None:
        old = self.takeCentralWidget()
        if old is not None:
            old.deleteLater()
        self.current_theme = resolve_theme(self.config.theme)
        self.current_language = resolve_language(self.config.language)
        self.palette = get_palette(self.current_theme)
        if self._mica_attempted:
            self._mica_applied = apply_windows_mica(
                self, dark=self.current_theme == "dark"
            )
        self._apply_theme()
        self._build_ui()
        self._fade_in_rebuilt_ui()

    def _fade_in_rebuilt_ui(self) -> None:
        root = self.centralWidget()
        if root is None:
            return
        previous = getattr(self, "_rebuild_fade", None)
        if previous is not None:
            previous.stop()
        effect = QGraphicsOpacityEffect(root)
        effect.setOpacity(0.84)
        root.setGraphicsEffect(effect)
        animation = QPropertyAnimation(effect, b"opacity", self)
        animation.setDuration(140)
        animation.setStartValue(0.84)
        animation.setEndValue(1.0)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._rebuild_fade = animation
        animation.start()

    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("appRoot")
        outer = QHBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(238)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(18, 24, 18, 18)
        side.setSpacing(10)
        side.addWidget(make_label("SwarmAtlas", 18, self.palette["text"], 700))
        side.addWidget(make_label(self.t("subtitle"), 9, self.palette["muted"]))
        side.addSpacing(24)

        self.nav_buttons: list[QPushButton] = []
        self.nav_keys = ("overview", "destinations", "history")
        for key in self.nav_keys:
            button = QPushButton(self.t(key))
            button.setObjectName("nav")
            button.setProperty("page_key", key)
            button.setProperty("active", key == "overview")
            button.clicked.connect(lambda checked=False, page=key: self.select_page(page))
            side.addWidget(button)
            self.nav_buttons.append(button)

        side.addStretch(1)
        self.theme_label = make_label(self.t("theme"), 9, self.palette["muted"], 600)
        side.addWidget(self.theme_label)
        self.theme_combo = QComboBox()
        self.theme_combo.addItem(self.t("system"), "system")
        self.theme_combo.addItem(self.t("dark"), "dark")
        self.theme_combo.addItem(self.t("light"), "light")
        self.theme_combo.setCurrentIndex(max(0, self.theme_combo.findData(self.config.theme)))
        self.theme_combo.currentIndexChanged.connect(self.change_theme)
        side.addWidget(self.theme_combo)

        self.language_label = make_label(self.t("language"), 9, self.palette["muted"], 600)
        side.addWidget(self.language_label)
        self.language_combo = QComboBox()
        self.language_combo.addItem(self.t("system"), "system")
        self.language_combo.addItem(self.t("russian"), "ru")
        self.language_combo.addItem(self.t("english"), "en")
        self.language_combo.setCurrentIndex(max(0, self.language_combo.findData(self.config.language)))
        self.language_combo.currentIndexChanged.connect(self.change_language)
        side.addWidget(self.language_combo)

        self.geoip_button = QPushButton(self.t("geoip_settings"))
        self.geoip_button.setObjectName("quiet")
        self.geoip_button.clicked.connect(self.choose_geoip_databases)
        side.addWidget(self.geoip_button)

        content = QWidget()
        content.setObjectName("content")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(28, 22, 28, 22)
        content_layout.setSpacing(18)

        header = QHBoxLayout()
        title_box = QVBoxLayout()
        title_box.setSpacing(3)
        self.page_title = make_label(self.t("overview_title"), 22, self.palette["text"], 700)
        self.tagline = make_label(self.t("tagline"), 10, self.palette["muted"])
        title_box.addWidget(self.page_title)
        title_box.addWidget(self.tagline)
        header.addLayout(title_box)
        header.addStretch(1)

        self.interface_combo = QComboBox()
        self.interface_combo.addItem(self.t("auto_interface"), "auto")
        for name in available_interfaces():
            self.interface_combo.addItem(name, name)
        saved_index = self.interface_combo.findData(self.config.interface)
        self.interface_combo.setCurrentIndex(max(0, saved_index))
        self.interface_combo.currentIndexChanged.connect(self.change_interface)
        header.addWidget(self.interface_combo)
        header.addWidget(make_label(self.t("read_only"), 9, self.palette["accent"], 650))
        self.live_status = make_label(self.t("starting"), 10, self.palette["accent"], 600)
        header.addWidget(self.live_status)
        content_layout.addLayout(header)

        metrics = QHBoxLayout()
        metrics.setSpacing(12)
        self.upload_card = MetricCard(
            self.t("uploaded"), self.palette["accent"], self.t("since_started"), self.palette
        )
        self.download_card = MetricCard(
            self.t("downloaded"), self.palette["download"], self.t("since_started"), self.palette
        )
        self.rate_card = MetricCard(
            self.t("live_transfer"), self.palette["text"], self.t("since_started"), self.palette
        )
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
        self.map_title = make_label(self.t("world_activity"), 13, self.palette["text"], 600)
        self.map_subtitle = make_label(self.t("approx_ip"), 9, self.palette["muted"])
        map_heading.addWidget(self.map_title)
        map_heading.addStretch(1)
        map_heading.addWidget(self.map_subtitle)
        map_layout.addLayout(map_heading)
        self.world_map = WorldMap(self.palette, self.t("map_empty"))
        map_layout.addWidget(self.world_map)
        middle.addWidget(map_card, 7)

        countries_card = card()
        countries_layout = QVBoxLayout(countries_card)
        self.countries_title = make_label(self.t("top_countries"), 13, self.palette["text"], 600)
        self.countries_subtitle = make_label(self.t("active_not_volume"), 9, self.palette["muted"])
        countries_layout.setContentsMargins(18, 16, 18, 14)
        countries_layout.addWidget(self.countries_title)
        countries_layout.addWidget(self.countries_subtitle)
        self.country_table = QTableWidget(0, 2)
        self.country_table.setHorizontalHeaderLabels([self.t("country"), self.t("peers")])
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
        self.chart_title = make_label(self.t("traffic_history"), 13, self.palette["text"], 600)
        self.chart_period = make_label(self.t("last_hour"), 9, self.palette["muted"])
        chart_title.addWidget(self.chart_title)
        chart_title.addStretch(1)
        chart_title.addWidget(self.chart_period)
        chart_layout.addLayout(chart_title)
        self.chart = TrafficChart(self.palette, self.t("history_empty"))
        chart_layout.addWidget(self.chart)
        bottom.addWidget(chart_card, 6)

        peers_card = card()
        peers_layout = QVBoxLayout(peers_card)
        peers_layout.setContentsMargins(18, 16, 18, 12)
        self.endpoints_title = make_label(self.t("recent_destinations"), 13, self.palette["text"], 600)
        peers_layout.addWidget(self.endpoints_title)
        self.endpoint_table = QTableWidget(0, 4)
        self.endpoint_table.setHorizontalHeaderLabels([
            self.t("ip_address"), self.t("provider"), self.t("location"), self.t("type")
        ])
        self.endpoint_table.setColumnWidth(0, 112)
        self.endpoint_table.setColumnWidth(1, 150)
        self.endpoint_table.setColumnWidth(2, 120)
        self.endpoint_table.horizontalHeader().setStretchLastSection(True)
        self.endpoint_table.verticalHeader().setVisible(False)
        self.endpoint_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.endpoint_table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.endpoint_table.setShowGrid(False)
        peers_layout.addWidget(self.endpoint_table)
        bottom.addWidget(peers_card, 5)
        content_layout.addLayout(bottom)

        self.data_note = make_label(self.t("data_note"), 9, self.palette["muted"])
        content_layout.addWidget(self.data_note)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)
        outer.addWidget(sidebar)
        outer.addWidget(scroll, 1)
        self.setCentralWidget(root)

    def showEvent(self, event: Any) -> None:
        super().showEvent(event)
        if not self._mica_attempted:
            self._mica_attempted = True
            self._mica_applied = apply_windows_mica(
                self, dark=self.current_theme == "dark"
            )
            if self._mica_applied:
                self._apply_theme()

    def _on_system_theme_changed(self, *_args: Any) -> None:
        if self.config.theme == "system":
            new_theme = resolve_theme("system")
            if new_theme != self.current_theme:
                self._rebuild_ui()

    def change_theme(self) -> None:
        preference = str(self.theme_combo.currentData() or "system")
        if preference == self.config.theme:
            return
        self.config.theme = preference
        self.config.save()
        self._rebuild_ui()

    def change_language(self) -> None:
        preference = str(self.language_combo.currentData() or "system")
        if preference == self.config.language:
            return
        self.config.language = preference
        self.config.save()
        self._rebuild_ui()

    def select_page(self, key: str) -> None:
        for button in self.nav_buttons:
            active = button.property("page_key") == key
            button.setProperty("active", active)
            button.style().unpolish(button)
            button.style().polish(button)
        if key != "overview":
            QMessageBox.information(self, self.t(key), self.t("page_coming", page=self.t(key)))

    def change_interface(self) -> None:
        interface = str(self.interface_combo.currentData() or "auto")
        self.config.interface = interface
        self.config.save()
        self.worker.set_interface(interface)

    def choose_geoip_databases(self) -> None:
        dialog = GeoIPDatabaseDialog(
            self.config.resolved_geoip_databases(),
            self.current_language,
            self.palette,
            self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        paths = dialog.selected_paths()
        self.config.geoip_databases = paths
        self.config.geoip_database = paths[0] if paths else ""
        self.config.save()
        self.worker.set_geoip_databases(paths)
        self.statusBar().showMessage(self.t("preparing") if paths else self.t("geoip_missing"))

    def update_geo_status(self, status: str) -> None:
        self.statusBar().showMessage(translate_status(status, self.current_language))

    def update_snapshot(self, snapshot: dict[str, Any]) -> None:
        upload = int(snapshot.get("upload_total", 0))
        download = int(snapshot.get("download_total", 0))
        up_rate = float(snapshot.get("upload_bps", 0))
        down_rate = float(snapshot.get("download_bps", 0))

        self.upload_card.set_value(format_bytes(upload), self.t("selected_saved"))
        self.download_card.set_value(format_bytes(download), self.t("selected_saved"))
        self.rate_card.set_value(
            f"{format_rate(up_rate)}  ↑",
            f"{format_rate(down_rate)}  ↓",
        )
        endpoints = snapshot.get("active_endpoints", [])
        self.live_status.setText(self.t("monitoring", count=len(endpoints)))
        self.world_map.set_locations(endpoints)
        self.chart.set_samples(snapshot.get("samples", []))
        self._fill_countries(snapshot.get("countries", []))
        self._fill_endpoints(endpoints)
        self.data_note.setText(self.t("data_note_live"))

    def _fill_countries(self, countries: list[dict[str, Any]]) -> None:
        rows: list[list[str]] = []
        if not countries:
            rows = [[self.t("geoip_missing"), "—"]]
        else:
            for item in countries:
                name = item.get("country") or item.get("country_code") or self.t("unknown")
                rows.append([str(name), str(item.get("peers", 0))])
        update_table(self.country_table, rows)

    def _fill_endpoints(self, endpoints: list[dict[str, Any]]) -> None:
        rows: list[list[str]] = []
        for item in endpoints[:10]:
            location = ", ".join(
                str(value) for value in (item.get("city"), item.get("region"), item.get("country"))
                if value
            ) or self.t("unknown")
            asn = item.get("asn")
            organization = item.get("organization") or item.get("isp")
            if asn and organization:
                provider = f"AS{asn} · {organization}"
            elif asn:
                provider = f"AS{asn}"
            else:
                provider = str(organization or "—")
            rows.append([
                str(item["ip"]),
                provider,
                location,
                str(item["protocol"]),
            ])
        update_table(self.endpoint_table, rows)

    def closeEvent(self, event: Any) -> None:
        if self.worker.isRunning() and not self.worker.stop():
            QMessageBox.warning(self, self.t("stopping_title"), self.t("stopping_message"))
            event.ignore()
            return
        event.accept()
