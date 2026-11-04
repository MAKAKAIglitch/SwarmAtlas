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



def test_database_directory_is_configurable_and_created(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(config_module, "app_data_dir", lambda: tmp_path / "settings")
    selected = tmp_path / "other-drive" / "SwarmAtlas"
    config = AppConfig(database_directory=str(selected))
    path = config_module.database_path(config)
    assert path == selected / "swarmatlas.sqlite3"
    assert selected.is_dir()


def test_geoip_provider_and_token_settings_round_trip(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(config_module, "app_data_dir", lambda: tmp_path)
    config = AppConfig(
        geoip_provider="2ip",
        geoip_api_key="local-test-token",
        geoip_api_keys={"ipdata": "ipdata-token", "ip2location": "ip2location-token"},
    )
    config.save()
    restored = AppConfig.load()
    assert restored.geoip_provider == "2ip"
    assert restored.geoip_api_key == "local-test-token"
    assert restored.geoip_api_keys == {
        "2ip": "local-test-token",
        "ipdata": "ipdata-token",
        "ip2location": "ip2location-token",
    }


def test_legacy_geoip_token_is_migrated_only_to_its_current_provider(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(config_module, "app_data_dir", lambda: tmp_path)
    (tmp_path / "settings.json").write_text(
        '{"geoip_provider": "ipinfo-lite", "geoip_api_key": "ipinfo-token"}',
        encoding="utf-8",
    )
    restored = AppConfig.load()
    assert restored.geoip_api_keys == {"ipinfo-lite": "ipinfo-token"}
    assert restored.geoip_api_key == "ipinfo-token"
    restored.geoip_provider = "2ip"
    restored.geoip_api_key = restored.geoip_api_keys.get("2ip", "")
    restored.save()
    reread = AppConfig.load()
    assert reread.geoip_api_key == ""
    assert reread.geoip_api_keys == {"ipinfo-lite": "ipinfo-token"}
