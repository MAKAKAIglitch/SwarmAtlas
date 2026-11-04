from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from swarmatlas import config
from swarmatlas.ui import MainWindow


def test_main_window_exposes_system_theme_and_language_controls(
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

    window.close()
    window.deleteLater()
    app.processEvents()
