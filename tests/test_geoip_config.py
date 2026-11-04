from __future__ import annotations

from pathlib import Path

from swarmatlas.config import AppConfig
from swarmatlas import config as config_module


def test_auto_geoip_database_is_used_only_when_no_custom_database_exists(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(config_module, "app_data_dir", lambda: tmp_path)
    default = tmp_path / "geoip" / "dbip-city-lite.mmdb"
    default.parent.mkdir(parents=True)
    default.write_bytes(b"local database")

    config = AppConfig()
    assert config.configured_geoip_databases() == []
    assert config.resolved_geoip_databases() == [str(default)]

    custom = tmp_path / "custom.mmdb"
    custom.write_bytes(b"custom database")
    config.geoip_databases = [str(custom)]
    assert config.resolved_geoip_databases() == [str(custom)]


def test_saving_theme_does_not_persist_automatic_database_as_user_selection(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(config_module, "app_data_dir", lambda: tmp_path)
    default = tmp_path / "geoip" / "dbip-city-lite.mmdb"
    default.parent.mkdir(parents=True)
    default.write_bytes(b"local database")

    config = AppConfig(theme="dark")
    config.save()
    loaded = AppConfig.load()
    assert loaded.theme == "dark"
    assert loaded.configured_geoip_databases() == []
    assert loaded.resolved_geoip_databases() == [str(default)]
