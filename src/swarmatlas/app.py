from __future__ import annotations

import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QSplashScreen

from .config import AppConfig
from .theme import get_palette, resolve_language, resolve_theme
from .i18n import tr
from .ui import MainWindow


def _make_splash(theme: str, language: str) -> QSplashScreen:
    palette = get_palette(theme)
    pixmap = QPixmap(480, 190)
    pixmap.fill(QColor(palette["bg"]))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    painter.setPen(QColor(palette["accent"]))
    title_font = QFont("Segoe UI", 24)
    title_font.setWeight(QFont.Weight.Bold)
    painter.setFont(title_font)
    painter.drawText(34, 75, "SwarmAtlas")

    painter.setPen(QColor(palette["muted"]))
    subtitle_font = QFont("Segoe UI", 10)
    painter.setFont(subtitle_font)
    painter.drawText(36, 111, tr("preparing", language))
    painter.setPen(QColor(palette["line"]))
    painter.drawRoundedRect(34, 144, 412, 3, 1, 1)
    painter.end()

    splash = QSplashScreen(pixmap, Qt.WindowType.WindowStaysOnTopHint)
    splash.setFont(QFont("Segoe UI", 9))
    return splash


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("SwarmAtlas")
    app.setOrganizationName("SwarmAtlas")

    config = AppConfig.load()
    theme = resolve_theme(config.theme)
    language = resolve_language(config.language)
    splash = _make_splash(theme, language)
    splash.show()
    app.processEvents()

    window = MainWindow()

    def finish_startup(_status: str) -> None:
        window.show()
        splash.finish(window)

    window.initialization_ready.connect(finish_startup)
    window.start_monitor()
    return app.exec()
