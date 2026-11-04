from __future__ import annotations

from pathlib import Path
import shutil
from typing import Any

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, Qt, QPointF, QRectF, Signal, Slot
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QFont, QGuiApplication
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFrame,
    QGraphicsOpacityEffect, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QMainWindow, QMessageBox, QPushButton, QScrollArea, QTableWidget,
    QTableWidgetItem, QToolTip, QVBoxLayout, QWidget, QLineEdit,
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


class GeoIPProviderDialog(QDialog):
    PROVIDERS = (
        ("dbip", "provider_dbip"),
        ("ip-api", "provider_ip_api"),
        ("ipdata", "provider_ipdata"),
        ("ipapi-is", "provider_ipapi_is"),
        ("ipinfo-lite", "provider_ipinfo"),
        ("2ip", "provider_2ip"),
    )

    def __init__(self, provider: str, api_key: str, language: str, palette: dict[str, str], parent=None):
        super().__init__(parent)
        self.language = language
        self.palette = palette
        self.setWindowTitle(tr("geoip_settings", language))
        self.setMinimumWidth(560)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        intro = make_label(tr("geoip_provider_intro", language), 10, palette["text"])
        intro.setWordWrap(True)
        privacy = make_label(tr("geoip_provider_privacy", language), 9, palette["muted"])
        privacy.setWordWrap(True)
        layout.addWidget(intro)
        layout.addWidget(privacy)

        self.provider_combo = QComboBox()
        for provider_id, label_key in self.PROVIDERS:
            self.provider_combo.addItem(tr(label_key, language), provider_id)
        selected = self.provider_combo.findData(provider)
        self.provider_combo.setCurrentIndex(max(0, selected))
        layout.addWidget(self.provider_combo)

        self.key_label = make_label(tr("geoip_api_key", language), 9, palette["muted"], 600)
        layout.addWidget(self.key_label)
        self.api_key_input = QLineEdit(api_key)
        self.api_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_input.setPlaceholderText(tr("geoip_api_key_placeholder", language))
        layout.addWidget(self.api_key_input)

        self.info_label = make_label("", 9, palette["muted"])
        self.info_label.setWordWrap(True)
        layout.addWidget(self.info_label)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.provider_combo.currentIndexChanged.connect(self._sync_provider)
        self._sync_provider()

        self.setStyleSheet(
            f"QDialog {{ background: {palette['bg']}; color: {palette['text']}; }}"
            f"QComboBox, QLineEdit {{ background: {palette['surface']}; color: {palette['text']}; "
            f"border: 1px solid {palette['line']}; border-radius: 7px; padding: 7px; }}"
            f"QPushButton {{ background: {palette['surface_alt']}; color: {palette['text']}; "
            f"border: 1px solid {palette['line']}; border-radius: 7px; padding: 7px 11px; }}"
        )

    def _sync_provider(self, *_args: Any) -> None:
        provider = str(self.provider_combo.currentData() or "dbip")
        key_required = provider in {"ipinfo-lite", "2ip"}
        self.key_label.setVisible(provider != "dbip")
        self.api_key_input.setVisible(provider != "dbip")
        self.api_key_input.setEnabled(provider != "dbip")
        if provider == "dbip":
            message_key = "provider_note_dbip"
        else:
            message_key = f"provider_note_{provider.replace('-', '_')}"
        self.info_label.setText(tr(message_key, self.language))

    def selected_provider(self) -> str:
        return str(self.provider_combo.currentData() or "dbip")

    def selected_api_key(self) -> str:
        return self.api_key_input.text().strip()


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
    """Interactive offline world map with local country and first-level region borders."""

    location_selected = Signal(dict)

    def __init__(self, palette: dict[str, str], empty_text: str):
        super().__init__()
        self.setMinimumHeight(275)
        self.setMouseTracking(True)
        self.palette_colors = palette
        self.empty_text = empty_text
        self.locations: list[dict[str, Any]] = []
        self.country_path = QPainterPath()
        self.region_path = QPainterPath()
        self._has_boundaries = False
        self._zoom = 1.0
        self._pan = QPointF(0.0, 0.0)
        self._drag_start: QPointF | None = None
        self._drag_origin = QPointF(0.0, 0.0)
        self._dragged = False
        self._hovered_index: int | None = None
        self.setCursor(Qt.CursorShape.OpenHandCursor)

    def set_theme(self, palette: dict[str, str], empty_text: str) -> None:
        self.palette_colors = palette
        self.empty_text = empty_text
        self.update()

    def set_locations(self, locations: list[dict[str, Any]]) -> None:
        valid: list[dict[str, Any]] = []
        for item in locations:
            try:
                latitude = float(item["latitude"])
                longitude = float(item["longitude"])
            except (KeyError, TypeError, ValueError, OverflowError):
                continue
            if not (-90.0 <= latitude <= 90.0 and -180.0 <= longitude <= 180.0):
                continue
            valid.append(item)
        self.locations = valid
        self._hovered_index = None
        self.update()

    @staticmethod
    def _normalized_point(longitude: float, latitude: float) -> QPointF:
        return QPointF((longitude + 180.0) / 360.0, (90.0 - latitude) / 180.0)

    def set_boundaries(
        self,
        countries: list[list[tuple[float, float]]],
        regions: list[list[tuple[float, float]]],
    ) -> None:
        country_path = QPainterPath()
        country_path.setFillRule(Qt.FillRule.OddEvenFill)
        for ring in countries:
            if len(ring) < 3:
                continue
            country_path.moveTo(self._normalized_point(*ring[0]))
            for longitude, latitude in ring[1:]:
                country_path.lineTo(self._normalized_point(longitude, latitude))
            country_path.closeSubpath()

        region_path = QPainterPath()
        for ring in regions:
            if len(ring) < 2:
                continue
            region_path.moveTo(self._normalized_point(*ring[0]))
            for longitude, latitude in ring[1:]:
                region_path.lineTo(self._normalized_point(longitude, latitude))
            region_path.closeSubpath()

        self.set_boundary_paths(country_path, region_path)

    def set_boundary_paths(self, countries: QPainterPath, regions: QPainterPath) -> None:
        self.country_path = QPainterPath(countries)
        self.region_path = QPainterPath(regions)
        self._has_boundaries = not self.country_path.isEmpty()
        self.update()

    def copy_boundaries_from(self, other: "WorldMap") -> None:
        self.set_boundary_paths(other.country_path, other.region_path)

    def _map_rect(self) -> QRectF:
        area = QRectF(self.rect()).adjusted(12, 8, -12, -8)
        width = min(area.width(), area.height() * 2.0)
        height = width / 2.0
        return QRectF(
            area.center().x() - width / 2.0,
            area.center().y() - height / 2.0,
            width,
            height,
        )

    def _screen_point(self, longitude: float, latitude: float) -> QPointF:
        rect = self._map_rect()
        normalized = self._normalized_point(longitude, latitude)
        return QPointF(
            rect.center().x() + self._pan.x()
            + (normalized.x() - 0.5) * rect.width() * self._zoom,
            rect.center().y() + self._pan.y()
            + (normalized.y() - 0.5) * rect.height() * self._zoom,
        )

    def _zoom_at(self, position: QPointF, factor: float) -> None:
        old_zoom = self._zoom
        new_zoom = max(1.0, min(16.0, old_zoom * factor))
        if abs(new_zoom - old_zoom) < 0.0001:
            return

        center = self._map_rect().center()
        anchored_x = (position.x() - center.x() - self._pan.x()) / old_zoom
        anchored_y = (position.y() - center.y() - self._pan.y()) / old_zoom
        self._zoom = new_zoom
        self._pan = QPointF(
            position.x() - center.x() - anchored_x * new_zoom,
            position.y() - center.y() - anchored_y * new_zoom,
        )
        self.update()

    def zoom_in(self) -> None:
        self._zoom_at(self._map_rect().center(), 1.35)

    def zoom_out(self) -> None:
        self._zoom_at(self._map_rect().center(), 1.0 / 1.35)

    def reset_view(self) -> None:
        self._zoom = 1.0
        self._pan = QPointF(0.0, 0.0)
        self.update()

    def _nearest_location(self, position: QPointF) -> int | None:
        nearest_index: int | None = None
        nearest_distance = 13.0
        for index, item in enumerate(self.locations):
            point = self._screen_point(float(item["longitude"]), float(item["latitude"]))
            dx = point.x() - position.x()
            dy = point.y() - position.y()
            distance = (dx * dx + dy * dy) ** 0.5
            if distance < nearest_distance:
                nearest_index = index
                nearest_distance = distance
        return nearest_index

    @staticmethod
    def _location_text(item: dict[str, Any]) -> str:
        details = [
            str(item.get("city") or ""),
            str(item.get("region") or ""),
            str(item.get("country") or ""),
        ]
        location = ", ".join(value for value in details if value)
        return f"{item.get('ip', '')} · {location}" if location else str(item.get("ip", ""))

    def paintEvent(self, event: Any) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        area = self.rect().adjusted(8, 6, -8, -6)
        colors = self.palette_colors
        painter.fillRect(area, QColor(colors["surface_alt"]))

        map_rect = self._map_rect()
        painter.save()
        painter.setClipRect(area)
        painter.translate(
            map_rect.center().x() + self._pan.x(),
            map_rect.center().y() + self._pan.y(),
        )
        painter.scale(
            map_rect.width() * self._zoom,
            map_rect.height() * self._zoom,
        )
        painter.translate(-0.5, -0.5)

        if self._has_boundaries:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(colors["land"]))
            painter.drawPath(self.country_path)

            country_pen = QPen(QColor(colors["muted"]), 0.8)
            country_pen.setCosmetic(True)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(country_pen)
            painter.drawPath(self.country_path)

            region_pen = QPen(QColor(colors["line"]), 0.55)
            region_pen.setCosmetic(True)
            painter.setPen(region_pen)
            painter.drawPath(self.region_path)
        else:
            continents = [
                [(-168, 70), (-145, 72), (-125, 55), (-110, 50), (-95, 20), (-80, 8), (-78, 28), (-60, 48), (-82, 60), (-105, 72), (-135, 73)],
                [(-82, 12), (-65, 8), (-50, -5), (-45, -25), (-58, -55), (-72, -35), (-78, -5)],
                [(-12, 72), (35, 70), (70, 55), (105, 58), (145, 48), (165, 62), (178, 48), (150, 20), (120, 5), (100, 10), (80, -5), (55, 10), (42, 32), (20, 35), (5, 55)],
                [(-18, 35), (12, 37), (38, 20), (48, 0), (35, -35), (18, -35), (5, -5), (-10, 10)],
                [(112, -12), (153, -10), (155, -40), (132, -45), (114, -30)],
                [(-52, 82), (-25, 80), (-38, 60)],
            ]
            painter.setPen(QPen(QColor(colors["line"]), 0.001))
            painter.setBrush(QColor(colors["land"]))
            for polygon in continents:
                path = QPainterPath()
                for index, (longitude, latitude) in enumerate(polygon):
                    point = self._normalized_point(longitude, latitude)
                    if index == 0:
                        path.moveTo(point)
                    else:
                        path.lineTo(point)
                path.closeSubpath()
                painter.drawPath(path)

        grid_pen = QPen(QColor(colors["grid"]), 0.8)
        grid_pen.setCosmetic(True)
        painter.setPen(grid_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for longitude in (-120, -60, 0, 60, 120):
            x = (longitude + 180.0) / 360.0
            painter.drawLine(QPointF(x, 0.0), QPointF(x, 1.0))
        for latitude in (-60, -30, 0, 30, 60):
            y = (90.0 - latitude) / 180.0
            painter.drawLine(QPointF(0.0, y), QPointF(1.0, y))
        painter.restore()

        if not self.locations:
            painter.setPen(QColor(colors["muted"]))
            painter.drawText(
                area.adjusted(0, area.height() - 32, 0, 0),
                Qt.AlignmentFlag.AlignCenter,
                self.empty_text,
            )
        else:
            for index, item in enumerate(self.locations):
                point = self._screen_point(
                    float(item["longitude"]),
                    float(item["latitude"]),
                )
                if not QRectF(area).adjusted(-8, -8, 8, 8).contains(point):
                    continue
                painter.setPen(QPen(QColor(colors["accent"]), 1))
                painter.setBrush(QColor(colors["accent_light"]))
                radius = 7.0 if index == self._hovered_index else 4.8
                painter.drawEllipse(point, radius, radius)

        if self._zoom > 1.01:
            painter.setPen(QColor(colors["muted"]))
            painter.drawText(
                area.adjusted(0, 6, -8, 0),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop,
                f"{self._zoom:.1f}×",
            )

    def wheelEvent(self, event: Any) -> None:
        delta = event.angleDelta().y()
        if delta:
            self._zoom_at(event.position(), 1.2 if delta > 0 else (1.0 / 1.2))
        event.accept()

    def mousePressEvent(self, event: Any) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start = event.position()
            self._drag_origin = QPointF(self._pan)
            self._dragged = False
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: Any) -> None:
        if self._drag_start is not None:
            delta = event.position() - self._drag_start
            if abs(delta.x()) + abs(delta.y()) > 3.0:
                self._dragged = True
            if self._dragged:
                self._pan = QPointF(
                    self._drag_origin.x() + delta.x(),
                    self._drag_origin.y() + delta.y(),
                )
                self.update()
            event.accept()
            return

        hovered = self._nearest_location(event.position())
        if hovered != self._hovered_index:
            self._hovered_index = hovered
            self.setToolTip(
                self._location_text(self.locations[hovered]) if hovered is not None else ""
            )
            self.update()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: Any) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._drag_start is not None:
            if not self._dragged:
                selected = self._nearest_location(event.position())
                if selected is not None:
                    item = self.locations[selected]
                    self.location_selected.emit(dict(item))
                    QToolTip.showText(
                        event.globalPosition().toPoint(),
                        self._location_text(item),
                        self,
                    )
            self._drag_start = None
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            event.accept()
            return
        super().mouseReleaseEvent(event)


class WorldMapDialog(QDialog):
    def __init__(
        self,
        source_map: WorldMap,
        title: str,
        attribution: str,
        language: str,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(1450, 850)
        self.setMinimumSize(900, 600)

        palette = source_map.palette_colors
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        toolbar = QHBoxLayout()
        toolbar.addWidget(make_label(title, 17, palette["text"], 700))
        toolbar.addStretch(1)

        self.zoom_out_button = QPushButton("−")
        self.zoom_in_button = QPushButton("+")
        self.reset_button = QPushButton(tr("map_reset", language))
        self.zoom_out_button.setToolTip(tr("map_zoom_out", language))
        self.zoom_in_button.setToolTip(tr("map_zoom_in", language))
        for button in (self.zoom_out_button, self.zoom_in_button, self.reset_button):
            button.setObjectName("quiet")
        toolbar.addWidget(self.zoom_out_button)
        toolbar.addWidget(self.zoom_in_button)
        toolbar.addWidget(self.reset_button)
        layout.addLayout(toolbar)

        self.map_widget = WorldMap(palette, source_map.empty_text)
        self.map_widget.copy_boundaries_from(source_map)
        self.map_widget.set_locations(source_map.locations)
        self.map_widget.setMinimumHeight(470)
        layout.addWidget(self.map_widget, 1)

        self.attribution = QLabel(attribution)
        self.attribution.setTextFormat(Qt.TextFormat.RichText)
        self.attribution.setOpenExternalLinks(True)
        self.attribution.setStyleSheet(f"color: {palette['muted']}; background: transparent;")
        layout.addWidget(self.attribution)

        self.zoom_out_button.clicked.connect(self.map_widget.zoom_out)
        self.zoom_in_button.clicked.connect(self.map_widget.zoom_in)
        self.reset_button.clicked.connect(self.map_widget.reset_view)
        self.setStyleSheet(
            f"QDialog {{ background: {palette['bg']}; color: {palette['text']}; }}"
            f"QPushButton#quiet {{ background: {palette['surface_alt']}; color: {palette['text']}; "
            f"border: 1px solid {palette['line']}; border-radius: 8px; padding: 7px 12px; }}"
            f"QPushButton#quiet:hover {{ border-color: {palette['accent']}; }}"
        )


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
        self._map_dialog: WorldMapDialog | None = None
        self._last_snapshot: dict[str, Any] = {}
        self._saved_country_path = QPainterPath()
        self._saved_region_path = QPainterPath()
        self._apply_theme()
        self.worker = NetworkWorker(self.config)
        self.worker.snapshot_ready.connect(self.update_snapshot)
        self.worker.status_changed.connect(self.update_geo_status)
        self.worker.startup_ready.connect(self._on_worker_startup)
        self.worker.map_data_ready.connect(self._set_map_boundaries)

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
        if self._map_dialog is not None:
            self._map_dialog.close()
            self._map_dialog = None
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
        if not self._saved_country_path.isEmpty():
            self.world_map.set_boundary_paths(
                self._saved_country_path,
                self._saved_region_path,
            )
        if self._last_snapshot:
            self.world_map.set_locations(self._last_snapshot.get("active_endpoints", []))
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
        self.geoip_button.clicked.connect(self.configure_geoip_provider)
        side.addWidget(self.geoip_button)

        self.geoip_database_button = QPushButton(self.t("geoip_local_database"))
        self.geoip_database_button.setObjectName("quiet")
        self.geoip_database_button.clicked.connect(self.choose_geoip_databases)
        side.addWidget(self.geoip_database_button)

        self.database_location_button = QPushButton(self.t("database_location"))
        self.database_location_button.setObjectName("quiet")
        self.database_location_button.clicked.connect(self.choose_database_directory)
        side.addWidget(self.database_location_button)

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
        self.transfer_status = make_label(self.t("transfer_waiting"), 9, self.palette["muted"], 600)
        header.addWidget(self.transfer_status)
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
        self.map_subtitle = make_label(self.t("map_country_region_subtitle"), 9, self.palette["muted"])
        map_heading.addWidget(self.map_title)
        map_heading.addStretch(1)
        map_heading.addWidget(self.map_subtitle)
        self.map_expand_button = QPushButton(self.t("expand_map"))
        self.map_expand_button.setObjectName("quiet")
        self.map_expand_button.clicked.connect(self.open_map_dialog)
        map_heading.addWidget(self.map_expand_button)
        map_layout.addLayout(map_heading)
        self.world_map = WorldMap(self.palette, self.t("map_empty"))
        map_layout.addWidget(self.world_map)
        self.map_attribution_label = QLabel(self._map_attribution_html())
        self.map_attribution_label.setTextFormat(Qt.TextFormat.RichText)
        self.map_attribution_label.setOpenExternalLinks(True)
        self.map_attribution_label.setStyleSheet(
            f"color: {self.palette['muted']}; background: transparent;"
        )
        map_layout.addWidget(self.map_attribution_label)
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
        self.country_table.setMaximumHeight(118)
        countries_layout.addWidget(self.country_table)
        self.regions_title = make_label(self.t("top_regions"), 12, self.palette["text"], 600)
        countries_layout.addWidget(self.regions_title)
        self.regions_table = QTableWidget(0, 2)
        self.regions_table.setHorizontalHeaderLabels([self.t("region"), self.t("peers")])
        self.regions_table.horizontalHeader().setStretchLastSection(False)
        self.regions_table.setColumnWidth(0, 150)
        self.regions_table.setColumnWidth(1, 55)
        self.regions_table.verticalHeader().setVisible(False)
        self.regions_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.regions_table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.regions_table.setShowGrid(False)
        countries_layout.addWidget(self.regions_table, 1)
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
        self.history_combo = QComboBox()
        for label_key, seconds in (
            ("last_hour", 3600),
            ("history_24h", 86400),
            ("history_7d", 7 * 86400),
            ("history_30d", 30 * 86400),
            ("history_1y", 365 * 86400),
            ("history_5y", 5 * 365 * 86400),
        ):
            self.history_combo.addItem(self.t(label_key), seconds)
        self.history_combo.currentIndexChanged.connect(self.change_history_range)
        chart_title.addWidget(self.history_combo)
        chart_layout.addLayout(chart_title)
        self.chart = TrafficChart(self.palette, self.t("history_empty"))
        chart_layout.addWidget(self.chart)
        bottom.addWidget(chart_card, 6)

        peers_card = card()
        peers_layout = QVBoxLayout(peers_card)
        peers_layout.setContentsMargins(18, 16, 18, 12)
        self.endpoints_title = make_label(self.t("recent_destinations"), 13, self.palette["text"], 600)
        peers_layout.addWidget(self.endpoints_title)
        self.endpoint_table = QTableWidget(0, 5)
        self.endpoint_table.setHorizontalHeaderLabels([
            self.t("ip_address"), self.t("process"), self.t("provider"),
            self.t("location"), self.t("type")
        ])
        self.endpoint_table.setColumnWidth(0, 112)
        self.endpoint_table.setColumnWidth(1, 138)
        self.endpoint_table.setColumnWidth(2, 145)
        self.endpoint_table.setColumnWidth(3, 120)
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

    def configure_geoip_provider(self) -> None:
        dialog = GeoIPProviderDialog(
            self.config.geoip_provider,
            self.config.geoip_api_key,
            self.current_language,
            self.palette,
            self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.config.geoip_provider = dialog.selected_provider()
        self.config.geoip_api_key = dialog.selected_api_key()
        self.config.save()
        self.worker.set_geoip_provider(self.config.geoip_provider, self.config.geoip_api_key)
        self.map_attribution_label.setText(self._map_attribution_html())
        self.statusBar().showMessage(self.t("geoip_provider_saved"))

    def choose_database_directory(self) -> None:
        from .config import database_path

        current_path = database_path(self.config)
        folder = QFileDialog.getExistingDirectory(
            self,
            self.t("database_location"),
            str(current_path.parent),
        )
        if not folder:
            return
        selected_dir = Path(folder).expanduser().resolve()
        if selected_dir == current_path.parent.resolve():
            return
        target_path = selected_dir / "swarmatlas.sqlite3"

        if self.worker.isRunning() and not self.worker.stop():
            QMessageBox.warning(self, self.t("stopping_title"), self.t("stopping_message"))
            return

        previous_directory = self.config.database_directory
        try:
            selected_dir.mkdir(parents=True, exist_ok=True)
            if current_path.is_file() and not target_path.exists():
                shutil.copy2(current_path, target_path)
            self.config.database_directory = str(selected_dir)
            self.config.save()
            self.worker.restart()
            self.statusBar().showMessage(
                self.t("database_location_saved", path=str(target_path))
            )
        except Exception as exc:
            self.config.database_directory = previous_directory
            self.config.save()
            if not self.worker.isRunning():
                self.worker.restart()
            QMessageBox.warning(
                self,
                self.t("database_location"),
                f"{type(exc).__name__}: {exc}",
            )

    def change_history_range(self) -> None:
        seconds = int(self.history_combo.currentData() or 3600)
        self.worker.set_history_range(seconds)
        self.chart_period.setText(self.history_combo.currentText())

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

    def _set_map_boundaries(self, countries: object, regions: object) -> None:
        if not isinstance(countries, list) or not isinstance(regions, list):
            return
        self.world_map.set_boundaries(countries, regions)
        self._saved_country_path = QPainterPath(self.world_map.country_path)
        self._saved_region_path = QPainterPath(self.world_map.region_path)
        if self._map_dialog is not None:
            self._map_dialog.map_widget.set_boundary_paths(
                self._saved_country_path,
                self._saved_region_path,
            )

    def _map_attribution_html(self) -> str:
        sources = {
            "dbip": ("DB-IP.com", "https://db-ip.com"),
            "ip-api": ("IP-API.com", "https://ip-api.com"),
            "ipdata": ("ipdata.co", "https://ipdata.co"),
            "ipapi-is": ("ipapi.is", "https://ipapi.is"),
            "ipinfo-lite": ("IPinfo Lite", "https://ipinfo.io/lite"),
            "2ip": ("2ip.io", "https://2ip.io"),
        }
        name, url = sources.get(self.config.geoip_provider, sources["dbip"])
        source_link = f'<a href="{url}">{name}</a>'
        natural_earth = '<a href="https://www.naturalearthdata.com/">Natural Earth</a>'
        return self.t("map_attribution").format(source=source_link, boundaries=natural_earth)

    def open_map_dialog(self) -> None:
        attribution = self._map_attribution_html()
        if self._map_dialog is None:
            self._map_dialog = WorldMapDialog(
                self.world_map,
                self.t("world_activity"),
                attribution,
                self.current_language,
                self,
            )
        self._map_dialog.map_widget.set_locations(
            self._last_snapshot.get("active_endpoints", [])
        )
        self._map_dialog.show()
        self._map_dialog.raise_()
        self._map_dialog.activateWindow()

    def update_snapshot(self, snapshot: dict[str, Any]) -> None:
        self._last_snapshot = snapshot
        upload = int(snapshot.get("upload_total", 0))
        download = int(snapshot.get("download_total", 0))
        up_rate = float(snapshot.get("upload_bps", 0))
        down_rate = float(snapshot.get("download_bps", 0))

        self.upload_card.set_value(format_bytes(upload), self.t("selected_saved"))
        self.download_card.set_value(format_bytes(download), self.t("selected_saved"))
        self.rate_card.set_value(
            f"↑ {format_rate(up_rate)}",
            f"↓ {format_rate(down_rate)}",
        )
        self.transfer_status.setText(
            f"↑ {self.t('sending')}: {format_rate(up_rate)}  ·  "
            f"↓ {self.t('receiving')}: {format_rate(down_rate)}"
        )
        endpoints = snapshot.get("active_endpoints", [])
        self.live_status.setText(self.t("monitoring", count=len(endpoints)))
        self.world_map.set_locations(endpoints)
        self.chart.set_samples(snapshot.get("samples", []))
        self._fill_countries(snapshot.get("countries", []))
        self._fill_regions(snapshot.get("regions", []))
        self._fill_endpoints(endpoints)
        if self._map_dialog is not None and self._map_dialog.isVisible():
            self._map_dialog.map_widget.set_locations(endpoints)
        self.data_note.setText(self.t("data_note_live"))
        native_status = str(snapshot.get("native_status", ""))
        backend_label = (
            self.t("native_engine_active")
            if native_status.startswith("Rust monitor active")
            else self.t("native_engine_fallback")
        )
        geoip_label = translate_status(str(snapshot.get("geoip_status", "")), self.current_language)
        self.statusBar().showMessage(f"{geoip_label}  ·  {backend_label}")

    def _fill_countries(self, countries: list[dict[str, Any]]) -> None:
        rows: list[list[str]] = []
        if not countries:
            rows = [[self.t("geoip_missing"), "—"]]
        else:
            for item in countries:
                name = item.get("country") or item.get("country_code") or self.t("unknown")
                rows.append([str(name), str(item.get("peers", 0))])
        update_table(self.country_table, rows)

    def _fill_regions(self, regions: list[dict[str, Any]]) -> None:
        rows: list[list[str]] = []
        for item in regions[:8]:
            region = str(item.get("region") or self.t("unknown"))
            country = str(item.get("country") or "")
            name = f"{region}, {country}" if country else region
            rows.append([name, str(item.get("peers", 0))])
        if not rows:
            rows = [[self.t("geoip_missing"), "—"]]
        update_table(self.regions_table, rows)

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
            process_id = item.get("process_id")
            process_name = item.get("process_name")
            if process_name and process_id:
                process = f"{process_name} · {process_id}"
            elif process_id:
                process = f"PID {process_id}"
            else:
                process = "—"
            rows.append([
                str(item["ip"]),
                process,
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
