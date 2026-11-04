from __future__ import annotations

import json
from pathlib import Path

from swarmatlas import config
from swarmatlas.config import AppConfig
from swarmatlas.i18n import tr, translate_status
from swarmatlas.theme import get_palette


def test_preferences_are_saved_and_loaded(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(config, "app_data_dir", lambda: tmp_path)

    expected = AppConfig(
        interface="Ethernet",
        geoip_database="D:/data/city.mmdb",
        theme="light",
        language="en",
    )
    expected.save()

    loaded = AppConfig.load()
    assert loaded == expected


def test_old_settings_keep_safe_ui_defaults(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(config, "app_data_dir", lambda: tmp_path)
    (tmp_path / "settings.json").write_text(
        json.dumps({"interface": "auto", "geoip_database": ""}),
        encoding="utf-8",
    )

    loaded = AppConfig.load()
    assert loaded.theme == "dark"
    assert loaded.language == "ru"


def test_invalid_preferences_fall_back_to_supported_values(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(config, "app_data_dir", lambda: tmp_path)
    (tmp_path / "settings.json").write_text(
        json.dumps({"theme": "neon", "language": "xx"}),
        encoding="utf-8",
    )

    loaded = AppConfig.load()
    assert loaded.theme == "dark"
    assert loaded.language == "ru"


def test_russian_translations_and_status_messages() -> None:
    assert tr("overview", "ru") == "Обзор"
    assert tr("destinations", "ru") == "Назначения"
    assert tr("monitoring", "ru", count=7) == "●  Мониторинг · узлов: 7"
    assert translate_status("No active network interface found", "ru") == (
        "Активный сетевой интерфейс не найден"
    )


def test_theme_palettes_have_all_required_colors() -> None:
    required = {
        "bg", "surface", "surface_alt", "text", "muted", "line",
        "accent", "accent_light", "grid", "land", "download",
        "selection", "scroll",
    }
    for theme in ("dark", "light"):
        palette = get_palette(theme)
        assert required <= palette.keys()
    assert get_palette("unknown") == get_palette("dark")
