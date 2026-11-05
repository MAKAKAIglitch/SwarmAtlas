from __future__ import annotations

import csv
import ipaddress
import json
import logging
import re
from pathlib import Path
import math
import shutil
import sqlite3
import time
from typing import Any

from PySide6.QtCore import QAbstractAnimation, QEvent, QEasingCurve, QProcess, QPropertyAnimation, Qt, QPointF, QRectF, QUrl, Signal, Slot, QObject
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QFont, QGuiApplication, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFrame,
    QGraphicsOpacityEffect, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QMainWindow, QMessageBox, QPushButton, QScrollArea, QTableWidget,
    QTableWidgetItem, QToolTip, QVBoxLayout, QWidget, QLineEdit,
)
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEngineProfile, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView

from .collector import NetworkWorker, available_interfaces, should_use_power_saver
from .config import AppConfig, app_data_dir
from .i18n import tr, translate_status
from .theme import apply_windows_mica, get_palette, resolve_language, resolve_theme
from .utils import format_bytes, format_rate


_LOGGER = logging.getLogger(__name__)


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


class SmoothScrollArea(QScrollArea):
    """A scroll area with a short, interruptible eased glide for wheel input."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scroll_animation = QPropertyAnimation(self.verticalScrollBar(), b"value", self)
        self._scroll_animation.setDuration(360)
        self._scroll_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._scroll_target = 0

    def scroll_to_value(self, value: int, duration: int = 420) -> None:
        bar = self.verticalScrollBar()
        self._scroll_target = max(bar.minimum(), min(bar.maximum(), int(value)))
        self._scroll_animation.stop()
        self._scroll_animation.setDuration(max(120, int(duration)))
        self._scroll_animation.setStartValue(bar.value())
        self._scroll_animation.setEndValue(self._scroll_target)
        self._scroll_animation.start()

    def scroll_to_widget(self, target: QWidget, margin: int = 20) -> None:
        content = self.widget()
        if content is None:
            return
        position = target.mapTo(content, target.rect().topLeft()).y()
        self.scroll_to_value(position - margin)

    def wheelEvent(self, event) -> None:
        bar = self.verticalScrollBar()
        pixel_delta = event.pixelDelta().y()
        if pixel_delta:
            movement = -pixel_delta * 1.25
            duration = 260
        else:
            angle_delta = event.angleDelta().y()
            if not angle_delta:
                super().wheelEvent(event)
                return
            movement = -(angle_delta / 120.0) * 92.0
            duration = 360

        current_target = self._scroll_target if self._scroll_animation.state() == QAbstractAnimation.State.Running else bar.value()
        self._scroll_target = max(
            bar.minimum(),
            min(bar.maximum(), round(current_target + movement)),
        )
        self._scroll_animation.stop()
        self._scroll_animation.setDuration(duration)
        self._scroll_animation.setStartValue(bar.value())
        self._scroll_animation.setEndValue(self._scroll_target)
        self._scroll_animation.start()
        event.accept()


def update_table(table: QTableWidget, rows: list[list[str]]) -> None:
    """Update table cells only when displayed values actually changed."""
    if table.rowCount() == len(rows):
        unchanged = True
        for row_index, row in enumerate(rows):
            if len(row) != table.columnCount():
                unchanged = False
                break
            for column_index, value in enumerate(row):
                item = table.item(row_index, column_index)
                if item is None or item.text() != value:
                    unchanged = False
                    break
            if not unchanged:
                break
        if unchanged:
            return

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
        self.setMinimumSize(560, 440)

        layout = QVBoxLayout(self)
        intro = make_label(tr("geoip_dialog_intro", language), 10, palette["text"])
        intro.setWordWrap(True)
        note = make_label(tr("geoip_privacy_note", language), 9, palette["muted"])
        note.setWordWrap(True)
        self.download_label = QLabel(tr("geoip_download_sources", language))
        self.download_label.setTextFormat(Qt.TextFormat.RichText)
        self.download_label.setOpenExternalLinks(True)
        self.download_label.setWordWrap(True)
        self.download_label.setStyleSheet(
            f"color: {palette['text']}; background: transparent;"
        )
        self.download_instructions = make_label(
            tr("geoip_download_instructions", language), 9, palette["muted"]
        )
        self.download_instructions.setWordWrap(True)
        layout.addWidget(intro)
        layout.addWidget(note)
        layout.addWidget(self.download_label)
        layout.addWidget(self.download_instructions)

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
        ("ip2location", "provider_ip2location"),
        ("2ip", "provider_2ip"),
    )

    def __init__(
        self,
        provider: str,
        api_keys: dict[str, str],
        language: str,
        palette: dict[str, str],
        parent=None,
    ):
        super().__init__(parent)
        self.language = language
        self.palette = palette
        self.api_keys = dict(api_keys)
        self._active_provider = provider
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
        self.api_key_input = QLineEdit(self.api_keys.get(provider, ""))
        self.api_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_input.setPlaceholderText(tr("geoip_api_key_placeholder", language))
        layout.addWidget(self.api_key_input)

        self.info_label = make_label("", 9, palette["muted"])
        self.info_label.setWordWrap(True)
        self.info_label.setTextFormat(Qt.TextFormat.RichText)
        self.info_label.setOpenExternalLinks(True)
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
        if provider != self._active_provider:
            self.api_keys[self._active_provider] = self.api_key_input.text().strip()
            self._active_provider = provider
            self.api_key_input.setText(self.api_keys.get(provider, ""))
        accepts_token = provider in {"ipdata", "ipapi-is", "ipinfo-lite", "ip2location", "2ip"}
        self.key_label.setVisible(accepts_token)
        self.api_key_input.setVisible(accepts_token)
        self.api_key_input.setEnabled(accepts_token)
        if provider == "dbip":
            message_key = "provider_note_dbip"
        else:
            message_key = f"provider_note_{provider.replace('-', '_')}"
        self.info_label.setText(tr(message_key, self.language))

    def selected_provider(self) -> str:
        return str(self.provider_combo.currentData() or "dbip")

    def selected_api_keys(self) -> dict[str, str]:
        values = dict(self.api_keys)
        current_provider = self.selected_provider()
        current_key = self.api_key_input.text().strip()
        if current_key:
            values[current_provider] = current_key
        else:
            values.pop(current_provider, None)
        return values


class TrafficChart(QWidget):
    def __init__(self, palette: dict[str, str], empty_text: str):
        super().__init__()
        self.setMinimumHeight(190)
        self.palette_colors = palette
        self.empty_text = empty_text
        self.upload: list[float] = []
        self.download: list[float] = []
        self._samples_source: list[dict[str, Any]] | None = None

    def set_theme(self, palette: dict[str, str], empty_text: str) -> None:
        self.palette_colors = palette
        self.empty_text = empty_text
        self.update()

    def set_samples(self, samples: list[dict[str, Any]]) -> None:
        if samples is self._samples_source:
            return
        upload = [float(item["upload_bps"]) for item in samples]
        download = [float(item["download_bps"]) for item in samples]
        self._samples_source = samples
        if upload == self.upload and download == self.download:
            return
        self.upload = upload
        self.download = download
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


class EndpointPingBridge(QObject):
    """Run one bounded ICMP echo only when an endpoint popup is opened."""
    pingResult = Signal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._processes: dict[int, tuple[QProcess, str]] = {}

    @Slot(str)
    def ping(self, address: str) -> None:
        try:
            normalized = str(ipaddress.ip_address(address))
        except ValueError:
            self.pingResult.emit(str(address), "Некорректный IP-адрес")
            return
        process = QProcess(self)
        process.setProgram("ping")
        process.setArguments(["-n", "1", "-w", "1200", normalized])
        key = id(process)
        self._processes[key] = (process, normalized)
        process.finished.connect(lambda _code, _status, process_key=key: self._finish(process_key))
        process.errorOccurred.connect(lambda _error, process_key=key: self._finish(process_key, failed=True))
        process.start()

    def _finish(self, key: int, failed: bool = False) -> None:
        entry = self._processes.pop(key, None)
        if entry is None:
            return
        process, address = entry
        output = bytes(process.readAllStandardOutput()).decode("utf-8", errors="replace")
        output += bytes(process.readAllStandardError()).decode("utf-8", errors="replace")
        if failed:
            result = "Не удалось запустить ping"
        else:
            match = re.search(
                r"(?:time|время)[=<]\s*([\d.,]+)\s*(?:ms|мс)",
                output,
                flags=re.IGNORECASE,
            )
            if match:
                value = match.group(1).replace(",", ".")
                result = "<1 мс" if "<" in output[match.start():match.end()] else f"{value} мс"
            elif process.exitCode() == 0:
                result = "Ответ получен"
            else:
                result = "Нет ответа (тайм-аут или фильтрация ICMP)"
        process.deleteLater()
        self.pingResult.emit(address, result)


class WorldMap(QWidget):
    """Responsive MapLibre map using OpenFreeMap's dark vector style."""

    location_selected = Signal(dict)
    MAP_STYLE_URL = "https://tiles.openfreemap.org/styles/dark"
    MAPLIBRE_VERSION = "6.16.0"
    MAX_LOCATIONS = 2000
    SIMPLE_ICONS_VERSION = "16.36.0"
    COMPANY_NETWORKS = {
        15169: ("Google", "google", "#4285F4"),
        396982: ("Google Cloud", "googlecloud", "#4285F4"),
        8075: ("Microsoft", "", "#00A4EF"),
        16509: ("Amazon Web Services", "", "#FF9900"),
        14618: ("Amazon", "", "#FF9900"),
        13335: ("Cloudflare", "cloudflare", "#F38020"),
        32934: ("Meta", "meta", "#0866FF"),
        714: ("Apple", "apple", "#A2AAAD"),
        2906: ("Netflix", "netflix", "#E50914"),
        20940: ("Akamai", "akamai", "#0099CC"),
        54113: ("Fastly", "fastly", "#FF282D"),
        14061: ("DigitalOcean", "digitalocean", "#0080FF"),
        16276: ("OVHcloud", "ovh", "#123F6D"),
        24940: ("Hetzner", "hetzner", "#D50C2D"),
        31898: ("Oracle Cloud", "", "#F80000"),
        36351: ("IBM", "", "#1261FE"),
    }
    _profile_configured = False

    def __init__(self, palette: dict[str, str], empty_text: str, cache_namespace: str = "main", language: str = "en"):
        super().__init__()
        del cache_namespace
        self.setMinimumHeight(300)
        self.palette_colors = palette
        self.empty_text = empty_text
        self.language = language
        self.requirement_text = tr("tcp_estats_requirement", language)
        self.locations: list[dict[str, Any]] = []
        self._location_source: list[dict[str, Any]] | None = None
        self._location_signature: tuple[tuple[Any, ...], ...] | None = None
        self._features: list[dict[str, Any]] = []
        self._details: dict[str, dict[str, Any]] = {}
        self._details_signature: tuple[tuple[Any, ...], ...] | None = None
        self._page_loaded = False
        self._zoom = 1.15

        if not WorldMap._profile_configured:
            profile = QWebEngineProfile.defaultProfile()
            profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.DiskHttpCache)
            profile.setHttpCacheMaximumSize(128 * 1024 * 1024)
            profile.setHttpUserAgent(profile.httpUserAgent() + " SwarmAtlas/0.3")
            WorldMap._profile_configured = True

        self.web_view = QWebEngineView(self)
        self.web_view.setMinimumHeight(300)
        self.web_view.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.web_view.settings().setAttribute(
            QWebEngineSettings.WebAttribute.JavascriptEnabled, True
        )
        self.web_view.loadFinished.connect(self._on_page_loaded)
        self._ping_bridge = EndpointPingBridge(self)
        self._web_channel = QWebChannel(self.web_view.page())
        self._web_channel.registerObject("pingBridge", self._ping_bridge)
        self.web_view.page().setWebChannel(self._web_channel)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.web_view, 1)
        html = self._map_html().replace("__ESTATS_REQUIREMENT__", json.dumps(self.requirement_text, ensure_ascii=False))
        self.web_view.setHtml(html, QUrl("https://swarmatlas.invalid/"))

    @classmethod
    def _map_html(cls) -> str:
        return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
<script src="qrc:///qtwebchannel/qwebchannel.js"></script>
<link rel="stylesheet" href="https://unpkg.com/maplibre-gl@{cls.MAPLIBRE_VERSION}/dist/maplibre-gl.css">
<style>
html,body,#map {{width:100%;height:100%;margin:0;padding:0;overflow:hidden;background:#08111f}}
.maplibregl-ctrl-group {{border:1px solid #26364b;background:#101c2b;box-shadow:0 2px 10px #0006}}
.maplibregl-ctrl-group button {{background-color:#101c2b}}
.maplibregl-ctrl-group button:hover {{background-color:#1d2d40}}
.maplibregl-ctrl button .maplibregl-ctrl-icon {{filter:invert(.88)}}
.maplibregl-ctrl-attrib {{color:#d1d9e4;background:#101c2bdc!important}}
.maplibregl-ctrl-attrib a {{color:#8ccfff}}
 .maplibregl-popup-content {{color:#e8eef6;background:#111e2e;border:1px solid #30445c;border-radius:11px;font:13px 'Segoe UI',sans-serif;padding:13px;box-shadow:0 8px 32px #0009}}
.swarm-popup {{min-width:245px;max-width:310px}}
.swarm-popup-title {{font-size:14px;font-weight:700;color:#f1f6fc;margin-bottom:3px;overflow-wrap:anywhere}}
.swarm-popup-subtitle {{font-size:11px;color:#9aadc2;margin-bottom:10px}}
.swarm-popup-section {{font-size:10px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:#6f91b5;margin:10px 0 5px}}
.swarm-popup-row {{display:flex;justify-content:space-between;gap:14px;padding:3px 0;border-bottom:1px solid #26364b}}
.swarm-popup-label {{color:#91a4ba;font-size:11px}}
.swarm-popup-value {{color:#e8eef6;font-size:11px;text-align:right;overflow-wrap:anywhere;max-width:175px}}
.swarm-popup-traffic {{font-weight:700;color:#8bdcff}}
.maplibregl-popup-tip {{border-top-color:#111e2e;border-bottom-color:#111e2e}}
#map-status {{position:absolute;z-index:5;left:12px;bottom:12px;padding:7px 10px;border-radius:7px;color:#d7e2ef;background:#101c2bdc;font:12px 'Segoe UI',sans-serif;display:none}}
#map-legend {{position:absolute;z-index:4;left:12px;top:12px;padding:8px 10px;border-radius:8px;color:#d7e2ef;background:#101c2be8;border:1px solid #26364b;font:11px 'Segoe UI',sans-serif;pointer-events:none}}
.legend-row {{display:flex;align-items:center;gap:7px;margin-top:5px}}
.legend-dot {{width:8px;height:8px;border-radius:50%;display:inline-block}}
.legend-upload {{background:#ff9854;box-shadow:0 0 9px #ff9854}}
.legend-download {{background:#35c8ff;box-shadow:0 0 9px #35c8ff}}
.legend-mixed {{background:#a78bfa;box-shadow:0 0 9px #a78bfa}}
</style>
</head><body>
<div id="map"></div><div id="map-status"></div>
<div id="map-legend">Traffic intensity
 <div class="legend-row"><span class="legend-dot legend-upload"></span>Upload-heavy</div>
 <div class="legend-row"><span class="legend-dot legend-download"></span>Download-heavy</div>
 <div class="legend-row"><span class="legend-dot legend-mixed"></span>Mixed traffic</div>
</div>
<script>
window.showMapError=function(message){{
 const status=document.getElementById('map-status');
 if(window.qt&&qt.webChannelTransport){{
  new QWebChannel(qt.webChannelTransport,function(channel){{
   window.swarmAtlasPingBridge=channel.objects.pingBridge;
   window.swarmAtlasPingBridge.pingResult.connect(function(ip,result){{
    document.querySelectorAll('[data-ping-ip]').forEach(function(el){{
     if(el.getAttribute('data-ping-ip')===ip)el.textContent=result;
    }});
   }});
  }});
 }}
 status.textContent=message;status.style.display='block';
}};
window.setTimeout(function(){{
 if(!window.swarmAtlasMapReady)window.showMapError('Map is taking too long to load. Check the connection to OpenFreeMap.');
}},15000);
window.setEndpointDetails=function(details){{
 window.swarmAtlasEndpointDetails=details||{{}};
}};
window.setSwarmEndpoints=function(features){{
 window.pendingSwarmFeatures=features||[];
 const map=window.swarmAtlasMap;
 if(window.ensureCompanyIcons)window.ensureCompanyIcons(window.pendingSwarmFeatures);
 if(!map||!map.getSource('swarm-endpoints')||window.swarmAtlasMapUpdatePending)return;
 window.swarmAtlasMapUpdatePending=true;
 window.requestAnimationFrame(function(){{
  window.swarmAtlasMapUpdatePending=false;
  const source=map.getSource('swarm-endpoints');
  if(source)source.setData({{type:'FeatureCollection',features:window.pendingSwarmFeatures}});
 }});
}};
</script>
<script type="module">
import * as maplibregl from 'https://unpkg.com/maplibre-gl@{cls.MAPLIBRE_VERSION}/dist/maplibre-gl.mjs';
window.maplibregl=maplibregl;
(function() {{
 const status=document.getElementById('map-status');
 if(!window.maplibregl){{window.showMapError('Map renderer unavailable. Check access to unpkg.com.');return;}}
 const map=new maplibregl.Map({{
  container:'map',style:'{cls.MAP_STYLE_URL}',center:[0,18],zoom:1.15,
  minZoom:1,maxZoom:12,maxPitch:0,pitchWithRotate:false,dragRotate:false,
  touchPitch:false,renderWorldCopies:true,attributionControl:false,
  fadeDuration:0,refreshExpiredTiles:false,maxTileCacheSize:64,maxParallelImageRequests:8
 }});
 window.swarmAtlasMap=map;
 map.addControl(new maplibregl.NavigationControl({{showCompass:false,visualizePitch:false}}),'top-right');
 map.addControl(new maplibregl.AttributionControl({{
  compact:true,customAttribution:'<a href="https://openfreemap.org/" target="_blank">© OpenFreeMap</a>'
 }}),'bottom-right');
 map.on('load',function(){{
  map.addSource('swarm-endpoints',{{
   type:'geojson',data:{{type:'FeatureCollection',features:window.pendingSwarmFeatures||[]}},
   cluster:true,clusterRadius:48,clusterMaxZoom:5,
   clusterProperties:{{
    traffic_bps:['+', ['get','traffic_bps']],
    traffic_upload_bps:['+', ['get','traffic_upload_bps']],
    traffic_download_bps:['+', ['get','traffic_download_bps']]
   }}
  }});
  map.addLayer({{
   id:'endpoint-cluster-glow',type:'circle',source:'swarm-endpoints',
   filter:['has','point_count'],
   paint:{{
    'circle-radius':['+',
      ['step',['get','point_count'],13,25,17,100,21],
      ['interpolate',['exponential',0.35],['get','traffic_bps'],0,0,1,1,64,3,1024,7,16384,12,262144,18,4194304,24]],
    'circle-color':['case',
      ['>', ['get','traffic_upload_bps'], ['*', ['get','traffic_download_bps'], 1.35]], '#ff9854',
      ['>', ['get','traffic_download_bps'], ['*', ['get','traffic_upload_bps'], 1.35]], '#35c8ff',
      ['>', ['get','traffic_bps'], 0], '#a78bfa',
      '#60758d'],
    'circle-opacity':['case',['>', ['get','traffic_bps'],0],0.78,0.22],
    'circle-blur':0.78
   }}
  }});
  map.addLayer({{
   id:'endpoint-clusters',type:'circle',source:'swarm-endpoints',
   filter:['has','point_count'],
   paint:{{
    'circle-color':['case',
      ['>', ['get','traffic_upload_bps'], ['*', ['get','traffic_download_bps'], 1.35]], '#ff9854',
      ['>', ['get','traffic_download_bps'], ['*', ['get','traffic_upload_bps'], 1.35]], '#35c8ff',
      ['>', ['get','traffic_bps'], 0], '#a78bfa',
      '#2563eb'],
    'circle-radius':['+',
      ['step',['get','point_count'],12,25,16,100,20],
      ['interpolate',['exponential',0.35],['get','traffic_bps'],0,0,1,0.8,64,2,1024,4,16384,7,262144,10,4194304,13]],
    'circle-stroke-color':'#071321','circle-stroke-width':1.5
   }}
  }});
  map.addLayer({{
   id:'endpoint-cluster-count',type:'symbol',source:'swarm-endpoints',
   filter:['has','point_count'],
   layout:{{'text-field':['get','point_count_abbreviated'],'text-size':11}},
   paint:{{'text-color':'#ffffff'}}
  }});
  map.addLayer({{
   id:'endpoint-glow',type:'circle',source:'swarm-endpoints',
   filter:['!', ['has','point_count']],
   paint:{{
    'circle-radius':['interpolate',['linear'],['get','traffic_bps'],
      0,7,64,11,1024,17,16384,25,262144,34,4194304,43],
    'circle-color':['case',
      ['>', ['get','traffic_upload_bps'], ['*', ['get','traffic_download_bps'], 1.35]], '#ff9854',
      ['>', ['get','traffic_download_bps'], ['*', ['get','traffic_upload_bps'], 1.35]], '#35c8ff',
      ['>', ['get','traffic_bps'], 0], '#a78bfa',
      '#60758d'],
    'circle-opacity':['case',['>', ['get','traffic_bps'],0],0.48,0.12],
    'circle-blur':0.82
   }}
  }});
  map.addLayer({{
   id:'swarm-endpoints',type:'circle',source:'swarm-endpoints',
   filter:['!', ['has','point_count']],
   paint:{{
    'circle-radius':['interpolate',['linear'],['get','traffic_bps'],
      0,3.2,64,4.5,1024,6,16384,8,262144,10,4194304,12],
    'circle-color':['case',
      ['>', ['get','traffic_upload_bps'], ['*', ['get','traffic_download_bps'], 1.35]], '#ffad70',
      ['>', ['get','traffic_download_bps'], ['*', ['get','traffic_upload_bps'], 1.35]], '#5bd6ff',
      ['>', ['get','traffic_bps'], 0], '#c3a7ff',
      '#8aa0b8'],
    'circle-opacity':['case',['>', ['get','traffic_bps'],0],1,0.72],
    'circle-stroke-color':'#071321','circle-stroke-width':1.5
   }}
  }});
  const companyIconLoads=new Set();
  window.ensureCompanyIcons=function(features){{
   const companies=new Map();
   (features||[]).forEach(function(feature){{
    const p=feature.properties||{{}};
    if(p.company_icon&&p.company_color&&!map.hasImage(p.company_icon))
     companies.set(p.company_icon,{{slug:p.company_slug||'',color:p.company_color,name:p.company_name||'Network'}});
   }});
   companies.forEach(function(meta,id){{
    if(companyIconLoads.has(id))return;
    companyIconLoads.add(id);
    const canvas=document.createElement('canvas');canvas.width=64;canvas.height=64;
    const context=canvas.getContext('2d');
    context.beginPath();context.arc(32,32,31,0,Math.PI*2);context.fillStyle=meta.color;context.fill();
    context.fillStyle='#ffffff';context.textAlign='center';context.textBaseline='middle';
    context.font='bold '+(meta.name.length>3?'21px':'25px')+' Segoe UI,sans-serif';
    context.fillText(meta.name.length>3?meta.name.slice(0,1).toUpperCase():meta.name.toUpperCase(),32,33);
    map.addImage(id,context.getImageData(0,0,64,64),{{pixelRatio:2}});
    if(!meta.slug)return;
    const url='https://cdn.jsdelivr.net/npm/simple-icons@16.36.0/icons/'+encodeURIComponent(meta.slug)+'.svg';
    fetch(url,{{mode:'cors',cache:'force-cache'}})
     .then(function(response){{if(!response.ok)throw new Error('Icon not found');return response.text();}})
     .then(function(svg){{
      if(!svg.trim().startsWith('<svg'))throw new Error('Invalid brand icon');
      const normalized=svg.replace(/<svg\\b([^>]*)>/,'<svg$1 width="64" height="64" fill="#ffffff">')
       .replace(/currentColor/g,'#ffffff');
      const blobUrl=URL.createObjectURL(new Blob([normalized],{{type:'image/svg+xml'}}));
      const image=new Image();
      image.onload=function(){{
       try{{
        const logoCanvas=document.createElement('canvas');logoCanvas.width=64;logoCanvas.height=64;
        const logoContext=logoCanvas.getContext('2d');
        logoContext.beginPath();logoContext.arc(32,32,31,0,Math.PI*2);logoContext.fillStyle=meta.color;logoContext.fill();
        logoContext.drawImage(image,15,15,34,34);
        map.updateImage(id,logoContext.getImageData(0,0,64,64));
        map.triggerRepaint();
       }}catch(error){{console.debug('Company icon render skipped',id,error);}}
       URL.revokeObjectURL(blobUrl);
      }};
      image.onerror=function(){{URL.revokeObjectURL(blobUrl);}};
      image.src=blobUrl;
     }})
     .catch(function(error){{console.debug('Company logo unavailable; keeping initials',id,error);}});
   }});
  }};
  window.ensureCompanyIcons(window.pendingSwarmFeatures||[]);
  map.addLayer({{
   id:'endpoint-company-icons',type:'symbol',source:'swarm-endpoints',minzoom:5.2,
   filter:['all',['!', ['has','point_count']],['!=',['get','company_icon'],'']],
   layout:{{
    'icon-image':['get','company_icon'],'icon-size':0.68,
    'icon-anchor':'center','icon-allow-overlap':false,'icon-ignore-placement':false,
    'icon-padding':3
   }}
  }});
  map.on('click','endpoint-clusters',async function(event){{
   const feature=event.features&&event.features[0];if(!feature)return;
   const source=map.getSource('swarm-endpoints');
   const zoom=await source.getClusterExpansionZoom(feature.properties.cluster_id);
   map.easeTo({{center:feature.geometry.coordinates,zoom:zoom}});
  }});
  map.on('mouseenter','swarm-endpoints',function(){{map.getCanvas().style.cursor='pointer';}});
  map.on('mouseleave','swarm-endpoints',function(){{map.getCanvas().style.cursor='';}});
  map.on('click','swarm-endpoints',function(event){{
   const feature=event.features&&event.features[0];if(!feature)return;
   const base=feature.properties||{{}};
   const p=Object.assign({{}},base,(window.swarmAtlasEndpointDetails||{{}})[base.endpoint_id]||{{}});
   const box=document.createElement('div');
   box.className='swarm-popup';
   const title=document.createElement('div');title.className='swarm-popup-title';
   title.textContent=p.ip||'Endpoint';box.append(title);
   const subtitle=document.createElement('div');subtitle.className='swarm-popup-subtitle';
   subtitle.textContent=[p.city,p.region,p.country].filter(Boolean).join(', ')||'Location unavailable';
   box.append(subtitle);
   if(p.company_name){{
    const company=document.createElement('div');company.className='swarm-popup-subtitle';
    company.textContent='Network owner: '+p.company_name;box.append(company);
   }}
   const bytes=function(value){{
    let n=Math.max(0,Number(value)||0),units=['B','KB','MB','GB','TB'],i=0;
    while(n>=1024&&i<units.length-1){{n/=1024;i++;}}
    return (i===0?Math.round(n):n.toFixed(1))+' '+units[i];
   }};
   const rate=function(value){{return bytes(value)+'/s';}};
   const section=function(label){{
    const el=document.createElement('div');el.className='swarm-popup-section';el.textContent=label;box.append(el);
   }};
   const row=function(label,value,traffic){{
    const line=document.createElement('div');line.className='swarm-popup-row';
    const key=document.createElement('span');key.className='swarm-popup-label';key.textContent=label;
    const val=document.createElement('span');val.className='swarm-popup-value'+(traffic?' swarm-popup-traffic':'');
    val.textContent=(value===undefined||value===null||value===''?'—':String(value));
    line.append(key,val);box.append(line);
   }};
   section('Connection');
   row('Application',p.process_name||(p.process_id?'PID '+p.process_id:'Unknown process'));
   row('PID',p.process_id||'—');
   row('Protocol',p.protocol||'—');
   row('Remote port',p.port||'—');
   row('Local port',p.local_port||'—');
   row('State',p.connection_state||'—');
   const dateText=function(value){{
    const n=Number(value)||0;return n>0?new Date(n*1000).toLocaleString():'—';
   }};
   row('First seen',dateText(p.first_seen));
   row('Last seen',dateText(p.last_seen));
   section('Network');
   row('Network owner',p.company_name||'Not identified');
   row('ASN / provider',p.organization?(p.asn?'AS'+p.asn+' · ':'')+p.organization:(p.asn?'AS'+p.asn:'—'));
   row('Country / region',[p.country,p.region].filter(Boolean).join(' / ')||'—');
   row('City',p.city||'—');
   row('Latitude / longitude',Number.isFinite(Number(p.latitude))&&Number.isFinite(Number(p.longitude))?Number(p.latitude).toFixed(4)+' / '+Number(p.longitude).toFixed(4):'—');
   section('Traffic');
   if(Number(p.traffic_known)){{
    row('Sent',bytes(p.traffic_upload_bytes),true);
    row('Received',bytes(p.traffic_download_bytes),true);
    row('Upload speed',rate(p.traffic_upload_bps),true);
    row('Download speed',rate(p.traffic_download_bps),true);
    row('Tracked flows',p.traffic_flow_count||0);
   }}else{{
    row('Traffic counters','Not collected');
    row('Requirement',__ESTATS_REQUIREMENT__);
   }}
   const pingSection=document.createElement('div');pingSection.className='swarm-popup-section';pingSection.textContent='Ping';box.append(pingSection);
   const pingLine=document.createElement('div');pingLine.className='swarm-popup-row';
   const pingLabel=document.createElement('span');pingLabel.className='swarm-popup-label';pingLabel.textContent='Задержка';
   const pingValue=document.createElement('span');pingValue.className='swarm-popup-value';pingValue.textContent='Проверка…';pingValue.setAttribute('data-ping-ip',p.ip||'');
   pingLine.append(pingLabel,pingValue);box.append(pingLine);
   if(window.swarmAtlasPingBridge&&p.ip)window.swarmAtlasPingBridge.ping(p.ip);
   else pingValue.textContent='Проверка недоступна';
   new maplibregl.Popup({{closeButton:true,closeOnClick:true,offset:12,maxWidth:'350px'}})
    .setLngLat(event.lngLat).setDOMContent(box).addTo(map);
  }});
  window.swarmAtlasMapReady=true;status.style.display='none';
  if(window.pendingSwarmFeatures)window.setSwarmEndpoints(window.pendingSwarmFeatures);
 }});
 map.on('error',function(event){{
  if(!window.swarmAtlasMapReady&&event&&event.error)
   window.showMapError('OpenFreeMap is not reachable. Check internet access.');
 }});
}})();
</script></body></html>"""

    def _on_page_loaded(self, ok: bool) -> None:
        self._page_loaded = bool(ok)
        if not ok:
            self.web_view.page().runJavaScript(
                "window.showMapError&&window.showMapError('Map page failed to load.');"
            )
        else:
            self._send_features()
            self._send_details()

    def _send_features(self) -> None:
        if self._page_loaded:
            payload = json.dumps(self._features, ensure_ascii=False, separators=(",", ":"))
            self.web_view.page().runJavaScript(
                f"window.setSwarmEndpoints&&window.setSwarmEndpoints({payload});"
            )

    def _send_details(self) -> None:
        if self._page_loaded:
            payload = json.dumps(self._details, ensure_ascii=False, separators=(",", ":"))
            self.web_view.page().runJavaScript(
                f"window.setEndpointDetails&&window.setEndpointDetails({payload});"
            )

    @classmethod
    def _company_for_network(cls, asn: int, organization: str = "") -> tuple[str, str, str] | None:
        """Return a brand only for a known network owner, never by guessing a service."""
        known = cls.COMPANY_NETWORKS.get(int(asn or 0))
        if known:
            return known
        normalized = str(organization or "").casefold()
        organization_matches = (
            ("cloudflare", ("Cloudflare", "cloudflare", "#F38020")),
            ("google", ("Google", "google", "#4285F4")),
            ("microsoft", ("Microsoft", "", "#00A4EF")),
            ("amazon", ("Amazon Web Services", "", "#FF9900")),
            ("meta", ("Meta", "meta", "#0866FF")),
            ("apple", ("Apple", "apple", "#A2AAAD")),
            ("netflix", ("Netflix", "netflix", "#E50914")),
            ("akamai", ("Akamai", "akamai", "#0099CC")),
            ("fastly", ("Fastly", "fastly", "#FF282D")),
            ("digitalocean", ("DigitalOcean", "digitalocean", "#0080FF")),
            ("ovh", ("OVHcloud", "ovh", "#123F6D")),
            ("hetzner", ("Hetzner", "hetzner", "#D50C2D")),
            ("oracle", ("Oracle Cloud", "", "#F80000")),
            ("ibm", ("IBM", "", "#1261FE")),
        )
        for token, result in organization_matches:
            if token in normalized:
                return result
        return None

    def set_locations(self, locations: list[dict[str, Any]]) -> None:
        if locations is self._location_source:
            return
        features: list[dict[str, Any]] = []
        map_signature: list[tuple[Any, ...]] = []
        valid: list[dict[str, Any]] = []
        details: dict[str, dict[str, Any]] = {}
        detail_signature: list[tuple[Any, ...]] = []
        for item in locations[:self.MAX_LOCATIONS]:
            try:
                lat, lon = float(item["latitude"]), float(item["longitude"])
            except (KeyError, TypeError, ValueError, OverflowError):
                continue
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                continue
            ip = str(item.get("ip") or "")
            city, region, country = (
                str(item.get("city") or ""),
                str(item.get("region") or ""),
                str(item.get("country") or ""),
            )
            process_id = int(item.get("process_id") or 0)
            process_name = str(item.get("process_name") or "")
            protocol = str(item.get("protocol") or "")
            port = int(item.get("port") or 0)
            local_port = int(item.get("local_port") or 0)
            connection_state = str(item.get("connection_state") or "")
            asn = int(item.get("asn") or 0)
            organization = str(item.get("organization") or item.get("isp") or "")
            company = self._company_for_network(asn, organization)
            company_name, company_slug, company_color = company if company else ("", "", "")
            company_icon = f"company-as{asn}" if company else ""
            upload_bytes = max(0, int(item.get("traffic_upload_bytes") or 0))
            download_bytes = max(0, int(item.get("traffic_download_bytes") or 0))
            upload_bps = max(0.0, float(item.get("traffic_upload_bps") or 0))
            download_bps = max(0.0, float(item.get("traffic_download_bps") or 0))
            flow_count = int(item.get("traffic_flow_count") or 0)
            endpoint_id = f"{ip}|{port}|{protocol}|{process_id}|{local_port}"
            properties = {
                "endpoint_id":endpoint_id,
                "ip":ip, "city":city, "region":region, "country":country,
                "process_id":process_id, "process_name":process_name,
                "protocol":protocol, "port":port, "local_port":local_port,
                "connection_state":connection_state, "asn":asn,
                "organization":organization, "latitude":lat, "longitude":lon,
                "company_name":company_name, "company_slug":company_slug,
                "company_icon":company_icon, "company_color":company_color,
                "traffic_upload_bps":upload_bps,
                "traffic_download_bps":download_bps,
                "traffic_bps":upload_bps + download_bps,
                "traffic_flow_count":flow_count,
                "traffic_known":1 if flow_count > 0 else 0,
            }
            detail = {
                "traffic_upload_bytes":upload_bytes,
                "traffic_download_bytes":download_bytes,
                "traffic_upload_bps":upload_bps,
                "traffic_download_bps":download_bps,
                "traffic_flow_count":flow_count,
                "traffic_known":1 if flow_count > 0 else 0,
                "company_name":company_name, "company_slug":company_slug,
                "company_icon":company_icon, "company_color":company_color,
                "first_seen":float(item.get("first_seen") or 0),
                "last_seen":float(item.get("last_seen") or 0),
            }
            details[endpoint_id] = detail
            detail_signature.append((
                endpoint_id, upload_bytes, download_bytes,
                round(upload_bps, 1), round(download_bps, 1),
                flow_count, detail["first_seen"], detail["last_seen"],
            ))
            valid.append(item)
            map_signature.append((
                endpoint_id, round(lat, 6), round(lon, 6), city, region, country,
                process_name, connection_state, asn, organization, company_icon,
                round(upload_bps, 1), round(download_bps, 1), flow_count,
            ))
            features.append({
                "type":"Feature",
                "id":endpoint_id,
                "geometry":{"type":"Point","coordinates":[lon,lat]},
                "properties":properties,
            })
        self._location_source = locations
        self.locations = valid
        new_map_signature = tuple(map_signature)
        if new_map_signature != self._location_signature:
            self._location_signature = new_map_signature
            self._features = features
            self._send_features()

        new_detail_signature = tuple(sorted(detail_signature))
        if new_detail_signature != self._details_signature:
            self._details_signature = new_detail_signature
            self._details = details
            self._send_details()

    def set_theme(self, palette: dict[str, str], empty_text: str) -> None:
        self.palette_colors, self.empty_text = palette, empty_text

    def set_boundaries(self, countries: list[list[tuple[float, float]]],
                       regions: list[list[tuple[float, float]]]) -> None:
        del countries, regions

    def set_boundary_paths(self, countries: QPainterPath, regions: QPainterPath) -> None:
        del countries, regions

    def copy_boundaries_from(self, other: "WorldMap") -> None:
        del other

    def zoom_in(self) -> None:
        self._zoom = min(12.0, self._zoom + 1.0)
        self.web_view.page().runJavaScript("window.swarmAtlasMap&&window.swarmAtlasMap.zoomIn();")

    def zoom_out(self) -> None:
        self._zoom = max(1.0, self._zoom - 1.0)
        self.web_view.page().runJavaScript("window.swarmAtlasMap&&window.swarmAtlasMap.zoomOut();")

    def reset_view(self) -> None:
        self._zoom = 1.15
        self.web_view.page().runJavaScript(
            "window.swarmAtlasMap&&window.swarmAtlasMap.flyTo({center:[0,18],zoom:1.15,duration:250});"
        )


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
        self.resize(1280, 760)
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

        self.map_widget = WorldMap(
            palette,
            source_map.empty_text,
            cache_namespace="expanded",
            language=language,
        )
        self.map_widget.copy_boundaries_from(source_map)
        self.map_widget.set_locations(source_map.locations)
        self.map_widget.setMinimumHeight(470)
        layout.addWidget(self.map_widget, 1)

        self.attribution = QLabel(attribution)
        self.attribution.setTextFormat(Qt.TextFormat.RichText)
        self.attribution.setOpenExternalLinks(True)
        self.attribution.setWordWrap(True)
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
        self._apply_theme()
        self.worker = NetworkWorker(self.config)
        self.worker.snapshot_ready.connect(self.update_snapshot)
        self.worker.status_changed.connect(self.update_geo_status)
        self.worker.startup_ready.connect(self._on_worker_startup)

        application = QGuiApplication.instance()
        if application is not None:
            application.applicationStateChanged.connect(
                self._update_collector_power_profile
            )

        self._build_ui()
        self._update_collector_power_profile()
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
        self.nav_keys = ("overview", "destinations", "history", "applications")
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

        self.app_stats_checkbox = QCheckBox(self.t("app_stats_enable"))
        self.app_stats_checkbox.setChecked(bool(self.config.tcp_flow_stats_enabled))
        self.app_stats_checkbox.setToolTip(self.t("app_traffic_note"))
        self.app_stats_checkbox.toggled.connect(self.change_app_stats)
        side.addWidget(self.app_stats_checkbox)

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
        content_layout.addLayout(header)

        self.transfer_status = make_label(self.t("transfer_waiting"), 10, self.palette["text"], 600)
        self.transfer_status.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.transfer_status.setMinimumHeight(26)
        content_layout.addWidget(self.transfer_status)

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
        self.world_map = WorldMap(self.palette, self.t("map_empty"), language=self.current_language)
        map_layout.addWidget(self.world_map)
        self.map_attribution_label = QLabel(self._map_attribution_html())
        self.map_attribution_label.setTextFormat(Qt.TextFormat.RichText)
        self.map_attribution_label.setOpenExternalLinks(True)
        self.map_attribution_label.setWordWrap(True)
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
        chart_title.addWidget(self.chart_title)
        chart_title.addStretch(1)
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
        self.export_history_button = QPushButton(self.t("export_csv"))
        self.export_history_button.setObjectName("quiet")
        self.export_history_button.clicked.connect(self.export_history_csv)
        chart_title.addWidget(self.export_history_button)
        chart_layout.addLayout(chart_title)
        self.chart = TrafficChart(self.palette, self.t("history_empty"))
        chart_layout.addWidget(self.chart)
        bottom.addWidget(chart_card, 6)
        self.chart_card = chart_card

        peers_card = card()
        peers_layout = QVBoxLayout(peers_card)
        peers_layout.setContentsMargins(18, 16, 18, 12)
        peers_heading = QHBoxLayout()
        self.endpoints_title = make_label(self.t("recent_destinations"), 13, self.palette["text"], 600)
        peers_heading.addWidget(self.endpoints_title)
        peers_heading.addStretch(1)
        self.endpoint_search = QLineEdit()
        self.endpoint_search.setPlaceholderText(self.t("search_destinations"))
        self.endpoint_search.setClearButtonEnabled(True)
        self.endpoint_search.setMaximumWidth(230)
        self.endpoint_search.textChanged.connect(self._apply_endpoint_filter)
        peers_heading.addWidget(self.endpoint_search)
        self.export_endpoints_button = QPushButton(self.t("export_csv"))
        self.export_endpoints_button.setObjectName("quiet")
        self.export_endpoints_button.clicked.connect(self.export_endpoints_csv)
        peers_heading.addWidget(self.export_endpoints_button)
        peers_layout.addLayout(peers_heading)
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
        self.peers_card = peers_card
        content_layout.addLayout(bottom)

        app_card = card()
        app_layout = QVBoxLayout(app_card)
        app_layout.setContentsMargins(18, 16, 18, 12)
        app_heading = QHBoxLayout()
        self.app_traffic_title = make_label(self.t("app_traffic_title"), 13, self.palette["text"], 600)
        app_heading.addWidget(self.app_traffic_title)
        app_heading.addStretch(1)
        self.app_traffic_status = make_label(self.t("app_stats_disabled"), 9, self.palette["muted"])
        app_heading.addWidget(self.app_traffic_status)
        app_layout.addLayout(app_heading)
        self.app_traffic_note = make_label(self.t("app_traffic_note"), 9, self.palette["muted"])
        app_layout.addWidget(self.app_traffic_note)
        self.app_table = QTableWidget(0, 6)
        self.app_table.setHorizontalHeaderLabels([
            self.t("app_name"), self.t("app_pid"), self.t("app_upload"),
            self.t("app_download"), self.t("app_total"), self.t("app_connections")
        ])
        self.app_table.horizontalHeader().setStretchLastSection(True)
        self.app_table.verticalHeader().setVisible(False)
        self.app_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.app_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.app_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.app_table.setShowGrid(False)
        self.app_table.setMinimumHeight(170)
        app_layout.addWidget(self.app_table)
        content_layout.addWidget(app_card)
        self.app_card = app_card

        self.data_note = make_label(self.t("data_note"), 9, self.palette["muted"])
        content_layout.addWidget(self.data_note)

        scroll = SmoothScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)
        self.scroll_area = scroll
        outer.addWidget(sidebar)
        outer.addWidget(scroll, 1)
        self.setCentralWidget(root)

    def _update_collector_power_profile(self, *_args: Any) -> None:
        worker = getattr(self, "worker", None)
        if worker is None:
            return
        application = QGuiApplication.instance()
        application_active = (
            application is not None
            and application.applicationState() == Qt.ApplicationState.ApplicationActive
        )
        worker.set_power_saver_mode(
            should_use_power_saver(
                application_active=application_active,
                minimized=self.isMinimized(),
            )
        )

    def showEvent(self, event: Any) -> None:
        super().showEvent(event)
        self._update_collector_power_profile()
        if not self._mica_attempted:
            self._mica_attempted = True
            self._mica_applied = apply_windows_mica(
                self, dark=self.current_theme == "dark"
            )
            if self._mica_applied:
                self._apply_theme()

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            self._update_collector_power_profile()

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
        self.page_title.setText(self.t("overview_title") if key == "overview" else self.t(key))
        if key == "history":
            self.scroll_area.scroll_to_widget(self.chart_card, 20)
        elif key == "destinations":
            self.scroll_area.scroll_to_widget(self.peers_card, 20)
        elif key == "applications":
            self.scroll_area.scroll_to_widget(self.app_card, 20)
        else:
            self.scroll_area.scroll_to_value(0)

    def change_app_stats(self, enabled: bool) -> None:
        self.config.tcp_flow_stats_enabled = bool(enabled)
        self.config.save()
        self.worker.set_tcp_flow_stats_enabled(enabled)
        self.app_traffic_status.setText(
            self.t("app_stats_enabled") if enabled else self.t("app_stats_disabled")
        )

    def change_interface(self) -> None:
        interface = str(self.interface_combo.currentData() or "auto")
        self.config.interface = interface
        self.config.save()
        self.worker.set_interface(interface)

    def configure_geoip_provider(self) -> None:
        dialog = GeoIPProviderDialog(
            self.config.geoip_provider,
            self.config.geoip_api_keys,
            self.current_language,
            self.palette,
            self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.config.geoip_provider = dialog.selected_provider()
        self.config.geoip_api_keys = dialog.selected_api_keys()
        self.config.geoip_api_key = self.config.geoip_api_keys.get(
            self.config.geoip_provider, ""
        )
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
        if target_path.exists():
            answer = QMessageBox.question(
                self,
                self.t("database_location"),
                self.t("database_existing_confirm", path=str(target_path)),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return

        if self.worker.isRunning() and not self.worker.stop():
            QMessageBox.warning(self, self.t("stopping_title"), self.t("stopping_message"))
            return

        previous_directory = self.config.database_directory
        try:
            selected_dir.mkdir(parents=True, exist_ok=True)
            if target_path.exists():
                check_db = sqlite3.connect(str(target_path), timeout=5.0)
                try:
                    check_result = check_db.execute("PRAGMA quick_check").fetchone()
                finally:
                    check_db.close()
                if not check_result or str(check_result[0]).lower() != "ok":
                    raise sqlite3.DatabaseError(
                        f"Selected database failed SQLite quick_check: {check_result!r}"
                    )
            elif current_path.is_file():
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
        self.config.geoip_provider = "dbip"
        self.config.geoip_api_key = ""
        self.config.save()
        self.worker.set_geoip_databases(paths)
        self.worker.set_geoip_provider("dbip", self.config.geoip_api_key)
        self.map_attribution_label.setText(self._map_attribution_html())
        self.statusBar().showMessage(self.t("preparing") if paths else self.t("geoip_missing"))

    def update_geo_status(self, status: str) -> None:
        self.statusBar().showMessage(translate_status(status, self.current_language))

    def _map_attribution_html(self) -> str:
        sources = {
            "dbip": ("DB-IP.com", "https://db-ip.com"),
            "ip-api": ("IP-API.com", "https://ip-api.com"),
            "ipdata": ("ipdata.co", "https://ipdata.co"),
            "ipapi-is": ("ipapi.is", "https://ipapi.is"),
            "ipinfo-lite": ("IPinfo Lite", "https://ipinfo.io/lite"),
            "ip2location": ("IP2Location.io", "https://www.ip2location.io"),
            "2ip": ("2ip.io", "https://2ip.io"),
        }
        name, url = sources.get(self.config.geoip_provider, sources["dbip"])
        source_link = f'<a href="{url}">{name}</a>'
        natural_earth = ('<a href="https://openfreemap.org/">OpenFreeMap</a> · '
                         '<a href="https://openmaptiles.org/">OpenMapTiles</a>')
        osm_attribution = (
            '<a href="https://www.openstreetmap.org/copyright">'
            "© OpenStreetMap contributors</a>"
        )
        if self.config.geoip_provider in {"ipdata", "ip2location"}:
            return self.t(
                f"map_attribution_{self.config.geoip_provider}"
            ).format(boundaries=natural_earth, osm=osm_attribution)
        return self.t("map_attribution").format(
            source=source_link,
            boundaries=natural_earth,
            osm=osm_attribution,
        )

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
        snapshot_started = time.perf_counter()
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
        live_text = self.t("monitoring", count=len(endpoints))
        if self.live_status.text() != live_text:
            self.live_status.setText(live_text)
        self.world_map.set_locations(endpoints)
        self.chart.set_samples(snapshot.get("samples", []))
        self._fill_countries(snapshot.get("countries", []))
        self._fill_regions(snapshot.get("regions", []))
        self._fill_endpoints(endpoints)
        self._fill_applications(snapshot.get("top_applications", []), snapshot.get("flow_stats_status", ""))
        if self._map_dialog is not None and self._map_dialog.isVisible():
            self._map_dialog.map_widget.set_locations(endpoints)
        live_note = self.t("data_note_live")
        if self.data_note.text() != live_note:
            self.data_note.setText(live_note)
        native_status = str(snapshot.get("native_status", ""))
        backend_label = (
            self.t("native_engine_active")
            if native_status.startswith("Rust monitor active")
            else self.t("native_engine_fallback")
        )
        geoip_label = translate_status(str(snapshot.get("geoip_status", "")), self.current_language)
        connection_status = str(snapshot.get("connection_status", "")).strip()
        status_parts = [geoip_label, backend_label]
        if connection_status:
            status_parts.append(connection_status)
        status_message = "  ·  ".join(status_parts)
        if getattr(self, "_last_status_message", None) != status_message:
            self._last_status_message = status_message
            self.statusBar().showMessage(status_message)
        snapshot_duration_ms = (time.perf_counter() - snapshot_started) * 1000.0
        if snapshot_duration_ms >= 25.0:
            _LOGGER.warning(
                "UI snapshot update took %.1f ms for %d endpoints",
                snapshot_duration_ms,
                len(endpoints),
            )

    def _fill_countries(self, countries: list[dict[str, Any]]) -> None:
        if countries is getattr(self, "_country_table_source", None):
            return
        rows: list[list[str]] = []
        if not countries:
            rows = [[self.t("geoip_missing"), "—"]]
        else:
            for item in countries:
                name = item.get("country") or item.get("country_code") or self.t("unknown")
                rows.append([str(name), str(item.get("peers", 0))])
        update_table(self.country_table, rows)
        self._country_table_source = countries

    def _fill_regions(self, regions: list[dict[str, Any]]) -> None:
        if regions is getattr(self, "_region_table_source", None):
            return
        rows: list[list[str]] = []
        for item in regions[:8]:
            region = str(item.get("region") or self.t("unknown"))
            country = str(item.get("country") or "")
            name = f"{region}, {country}" if country else region
            rows.append([name, str(item.get("peers", 0))])
        if not rows:
            rows = [[self.t("geoip_missing"), "—"]]
        update_table(self.regions_table, rows)
        self._region_table_source = regions

    def _fill_endpoints(self, endpoints: list[dict[str, Any]]) -> None:
        if endpoints is getattr(self, "_endpoint_table_source", None):
            return
        self._endpoint_table_source = endpoints
        self._apply_endpoint_filter()

    def _apply_endpoint_filter(self, *_args: Any) -> None:
        endpoints = getattr(self, "_endpoint_table_source", None) or []
        query = self.endpoint_search.text().strip().casefold() if hasattr(self, "endpoint_search") else ""
        rows: list[list[str]] = []
        for item in endpoints[:500]:
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
            searchable = " ".join(str(item.get(key) or "") for key in (
                "ip", "protocol", "port", "local_port", "process_id",
                "process_name", "asn", "organization", "isp", "city",
                "region", "country", "connection_state",
            )).casefold()
            if query and query not in searchable and query not in provider.casefold() and query not in location.casefold():
                continue
            rows.append([
                str(item.get("ip") or "—"),
                process,
                provider,
                location,
                str(item.get("protocol") or "—"),
            ])
        update_table(self.endpoint_table, rows)

    @staticmethod
    def _write_csv(path: str, headers: list[str], rows: list[list[Any]]) -> None:
        with open(path, "w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.writer(handle)
            writer.writerow(headers)
            writer.writerows(rows)

    def export_endpoints_csv(self) -> None:
        endpoints = self._last_snapshot.get("active_endpoints", [])
        if not endpoints:
            self.statusBar().showMessage(self.t("export_no_data"))
            return
        path, _ = QFileDialog.getSaveFileName(
            self, self.t("export_endpoints"), "swarmatlas-endpoints.csv", "CSV (*.csv)"
        )
        if not path:
            return
        headers = [
            "ip", "protocol", "remote_port", "local_port", "process_id",
            "process_name", "state", "country", "region", "city", "asn",
            "organization", "isp", "latitude", "longitude", "first_seen",
            "last_seen", "upload_bytes", "download_bytes", "upload_bytes_per_sec",
            "download_bytes_per_sec", "tracked_flows",
        ]
        keys = [
            "ip", "protocol", "port", "local_port", "process_id",
            "process_name", "connection_state", "country", "region", "city",
            "asn", "organization", "isp", "latitude", "longitude", "first_seen",
            "last_seen", "traffic_upload_bytes", "traffic_download_bytes",
            "traffic_upload_bps", "traffic_download_bps", "traffic_flow_count",
        ]
        try:
            self._write_csv(path, headers, [[item.get(key, "") for key in keys] for item in endpoints])
        except OSError as exc:
            QMessageBox.warning(self, self.t("export_endpoints"), str(exc))
            return
        self.statusBar().showMessage(self.t("export_saved", path=path))

    def export_history_csv(self) -> None:
        samples = self._last_snapshot.get("samples", [])
        if not samples:
            self.statusBar().showMessage(self.t("export_no_data"))
            return
        path, _ = QFileDialog.getSaveFileName(
            self, self.t("export_history"), "swarmatlas-traffic-history.csv", "CSV (*.csv)"
        )
        if not path:
            return
        try:
            self._write_csv(
                path,
                ["timestamp", "upload_bytes_per_sec", "download_bytes_per_sec"],
                [[item.get("timestamp", ""), item.get("upload_bps", 0), item.get("download_bps", 0)] for item in samples],
            )
        except OSError as exc:
            QMessageBox.warning(self, self.t("export_history"), str(exc))
            return
        self.statusBar().showMessage(self.t("export_saved", path=path))

    def _fill_applications(
        self, applications: list[dict[str, Any]], flow_status: str
    ) -> None:
        if not self.config.tcp_flow_stats_enabled:
            self.app_traffic_status.setText(self.t("app_stats_disabled"))
            update_table(self.app_table, [])
            return
        if flow_status and "unavailable" in flow_status.casefold():
            self.app_traffic_status.setText(self.t("app_stats_unavailable"))
        elif not applications:
            self.app_traffic_status.setText(self.t("app_stats_empty"))
        else:
            self.app_traffic_status.setText(flow_status)
        rows: list[list[str]] = []
        for item in applications[:100]:
            process_id = int(item.get("process_id") or 0)
            process_name = str(item.get("process_name") or "").strip()
            upload = int(item.get("upload_bytes") or 0)
            download = int(item.get("download_bytes") or 0)
            rows.append([
                process_name or (f"PID {process_id}" if process_id else self.t("unknown")),
                str(process_id or "—"),
                format_bytes(upload),
                format_bytes(download),
                format_bytes(upload + download),
                str(int(item.get("peer_count") or 0)),
            ])
        update_table(self.app_table, rows)

    def closeEvent(self, event: Any) -> None:
        if self.worker.isRunning() and not self.worker.stop():
            QMessageBox.warning(self, self.t("stopping_title"), self.t("stopping_message"))
            event.ignore()
            return
        event.accept()
