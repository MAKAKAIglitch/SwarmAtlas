from pathlib import Path

from swarmatlas.storage import Store
from swarmatlas.utils import counter_delta, format_bytes


def test_format_bytes_uses_decimal_network_units() -> None:
    assert format_bytes(1_000) == "1.00 KB"
    assert format_bytes(1_000_000) == "1.00 MB"


def test_counter_delta_handles_normal_increase_and_reset() -> None:
    assert counter_delta(150, 100) == 50
    assert counter_delta(20, 100) == 20


def test_store_accumulates_interface_counters(tmp_path: Path) -> None:
    store = Store(tmp_path / "test.sqlite3")
    first = store.record_interface("Ethernet", 10_000, 20_000, 1.0)
    second = store.record_interface("Ethernet", 12_500, 24_000, 2.0)

    assert first["upload_delta"] == 0
    assert first["download_delta"] == 0
    assert second["upload_delta"] == 2_500
    assert second["download_delta"] == 4_000
    assert second["upload_total"] == 2_500
    assert second["download_total"] == 4_000
    store.close()


def test_destination_history_is_pruned_after_five_years(tmp_path: Path) -> None:
    store = Store(tmp_path / "retention.sqlite3")
    store.update_destinations(
        [{"ip": "203.0.113.10", "port": 443, "protocol": "TCP"}],
        timestamp=1.0,
    )
    store.update_destinations([], timestamp=1.0 + 5 * 365 * 24 * 60 * 60 + 2)
    count = store._db.execute("SELECT COUNT(*) FROM destinations").fetchone()[0]
    assert count == 0
    store.close()



def test_store_persists_asn_and_organization_details(tmp_path: Path) -> None:
    store = Store(tmp_path / "asn.sqlite3")
    store.update_destinations(
        [{
            "ip": "8.8.8.8",
            "port": 443,
            "protocol": "TCP",
            "asn": 15169,
            "organization": "Google LLC",
        }],
        timestamp=100.0,
    )

    summary = store.summary("Ethernet", now=100.0)
    endpoint = summary["active_endpoints"][0]
    assert endpoint["asn"] == 15169
    assert endpoint["organization"] == "Google LLC"
    store.close()



def test_same_destination_is_kept_separately_for_each_process(tmp_path: Path) -> None:
    store = Store(tmp_path / "processes.sqlite3")
    store.update_destinations(
        [
            {
                "ip": "8.8.8.8",
                "port": 443,
                "protocol": "TCP",
                "process_id": 101,
                "process_name": "client-a.exe",
                "local_port": 51001,
            },
            {
                "ip": "8.8.8.8",
                "port": 443,
                "protocol": "TCP",
                "process_id": 202,
                "process_name": "client-b.exe",
                "local_port": 51002,
            },
        ],
        timestamp=100.0,
    )

    endpoints = store.summary("Ethernet", now=100.0)["active_endpoints"]
    assert len(endpoints) == 2
    owners = {(item["process_id"], item["process_name"]) for item in endpoints}
    assert owners == {(101, "client-a.exe"), (202, "client-b.exe")}
    store.close()



def test_store_returns_country_and_region_peer_counts(tmp_path: Path) -> None:
    store = Store(tmp_path / "regions.sqlite3")
    store.update_destinations(
        [
            {"ip": "8.8.8.8", "port": 443, "protocol": "TCP",
             "country": "United States", "region": "California"},
            {"ip": "1.1.1.1", "port": 443, "protocol": "TCP",
             "country": "United States", "region": "California"},
            {"ip": "9.9.9.9", "port": 443, "protocol": "TCP",
             "country": "United States", "region": "New York"},
            {"ip": "203.0.113.10", "port": 443, "protocol": "TCP",
             "country": "Canada", "region": "Ontario"},
        ],
        timestamp=100.0,
    )

    summary = store.summary("Ethernet", now=100.0)
    country_counts = {item["country"]: item["peers"] for item in summary["countries"]}
    region_counts = {
        (item["country"], item["region"]): item["peers"]
        for item in summary["regions"]
    }
    assert country_counts["United States"] == 3
    assert region_counts[("United States", "California")] == 2
    assert region_counts[("United States", "New York")] == 1
    assert region_counts[("Canada", "Ontario")] == 1
    store.close()

def test_legacy_destination_schema_migrates_without_losing_history(tmp_path: Path) -> None:
    import sqlite3

    path = tmp_path / "legacy.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute(
        """
        CREATE TABLE destinations (
            ip TEXT NOT NULL,
            port INTEGER NOT NULL,
            protocol TEXT NOT NULL,
            first_seen REAL NOT NULL,
            last_seen REAL NOT NULL,
            country_code TEXT,
            country TEXT,
            region TEXT,
            city TEXT,
            asn INTEGER,
            organization TEXT,
            isp TEXT,
            latitude REAL,
            longitude REAL,
            active INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (ip, port, protocol)
        )
        """
    )
    connection.execute(
        """
        INSERT INTO destinations (
            ip, port, protocol, first_seen, last_seen, country, active
        ) VALUES ('8.8.8.8', 443, 'TCP', 10, 10, 'United States', 1)
        """
    )
    connection.commit()
    connection.close()

    store = Store(path)
    migrated = store.summary("Ethernet", now=10.0)["active_endpoints"]
    assert len(migrated) == 1
    assert migrated[0]["ip"] == "8.8.8.8"
    assert migrated[0]["process_id"] == 0

    store.update_destinations(
        [{
            "ip": "8.8.8.8",
            "port": 443,
            "protocol": "TCP",
            "process_id": 101,
            "local_port": 51001,
        }],
        timestamp=11.0,
    )
    updated = store.summary("Ethernet", now=11.0)["active_endpoints"]
    assert len(updated) == 1
    assert updated[0]["process_id"] == 101
    store.close()



def test_repeated_connections_reuse_one_destination_history_row(tmp_path: Path) -> None:
    store = Store(tmp_path / "deduplicate.sqlite3")
    store.update_destinations(
        [{
            "ip": "8.8.8.8", "port": 443, "protocol": "TCP",
            "process_id": 101, "local_port": 51001, "process_name": "browser.exe",
        }],
        timestamp=100.0,
    )
    store.update_destinations(
        [{
            "ip": "8.8.8.8", "port": 443, "protocol": "TCP",
            "process_id": 101, "local_port": 51002, "process_name": "browser.exe",
        }],
        timestamp=110.0,
    )
    rows = store.summary("Ethernet", now=110.0)["active_endpoints"]
    assert len(rows) == 1
    assert rows[0]["local_port"] == 51002
    store.close()


def test_five_year_history_is_compacted_to_hourly_rows(tmp_path: Path) -> None:
    from swarmatlas.storage import HISTORY_RAW_RETENTION_SEC

    store = Store(tmp_path / "long-history.sqlite3")
    old_time = 100.0
    store.add_sample(old_time, 125.0, 350.0)
    current = old_time + HISTORY_RAW_RETENTION_SEC + 3600
    store._compact_history(current)

    raw_count = store._db.execute("SELECT COUNT(*) FROM traffic_samples").fetchone()[0]
    hourly_count = store._db.execute("SELECT COUNT(*) FROM traffic_hourly").fetchone()[0]
    assert raw_count == 0
    assert hourly_count == 1

    history = store.history(5 * 365 * 24 * 60 * 60, now=current)
    assert len(history) == 1
    assert history[0]["upload_bps"] == 125.0
    assert history[0]["download_bps"] == 350.0
    store.close()
