from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Any


DESTINATION_RETENTION_SEC = 30 * 24 * 60 * 60

DESTINATION_COLUMNS = (
    "ip",
    "port",
    "protocol",
    "process_id",
    "local_port",
    "process_name",
    "connection_state",
    "first_seen",
    "last_seen",
    "country_code",
    "country",
    "region",
    "city",
    "asn",
    "organization",
    "isp",
    "latitude",
    "longitude",
    "active",
)
DESTINATION_PRIMARY_KEY = ["ip", "port", "protocol", "process_id", "local_port"]


class Store:
    """Small local SQLite store. One instance is owned by the collector thread."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(path, timeout=5.0)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._db.execute("PRAGMA busy_timeout=5000")
        self._create_schema()

    def _create_schema(self) -> None:
        with self._lock, self._db:
            self._db.executescript(
                """
                CREATE TABLE IF NOT EXISTS interface_state (
                    interface TEXT PRIMARY KEY,
                    upload_total INTEGER NOT NULL DEFAULT 0,
                    download_total INTEGER NOT NULL DEFAULT 0,
                    last_sent INTEGER NOT NULL DEFAULT 0,
                    last_recv INTEGER NOT NULL DEFAULT 0,
                    updated_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS traffic_samples (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    upload_bps REAL NOT NULL,
                    download_bps REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_samples_timestamp
                    ON traffic_samples(timestamp);

                CREATE TABLE IF NOT EXISTS destinations (
                    ip TEXT NOT NULL,
                    port INTEGER NOT NULL,
                    protocol TEXT NOT NULL,
                    process_id INTEGER NOT NULL DEFAULT 0,
                    local_port INTEGER NOT NULL DEFAULT 0,
                    process_name TEXT,
                    connection_state TEXT,
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
                    PRIMARY KEY (ip, port, protocol, process_id, local_port)
                );
                """
            )
            self._migrate_destinations()
            self._db.execute(
                "CREATE INDEX IF NOT EXISTS idx_destinations_last_seen ON destinations(last_seen)"
            )

    def _migrate_destinations(self) -> None:
        info = list(self._db.execute("PRAGMA table_info(destinations)"))
        existing = {str(row[1]) for row in info}
        primary_key = [
            str(row[1])
            for row in sorted((row for row in info if int(row[5]) > 0), key=lambda row: int(row[5]))
        ]
        if existing >= set(DESTINATION_COLUMNS) and primary_key == DESTINATION_PRIMARY_KEY:
            return

        defaults = {
            "process_id": "0",
            "local_port": "0",
            "process_name": "NULL",
            "connection_state": "NULL",
            "asn": "NULL",
            "organization": "NULL",
            "isp": "NULL",
        }
        select_expressions: list[str] = []
        for column in DESTINATION_COLUMNS:
            if column in existing:
                if column in {"process_id", "local_port"}:
                    select_expressions.append(f"COALESCE({column}, 0)")
                else:
                    select_expressions.append(column)
            elif column in defaults:
                select_expressions.append(defaults[column])
            else:
                raise sqlite3.DatabaseError(
                    f"Cannot migrate destinations: required legacy column '{column}' is missing"
                )

        self._db.execute("DROP TABLE IF EXISTS destinations_migration")
        self._db.execute(
            """
            CREATE TABLE destinations_migration (
                ip TEXT NOT NULL,
                port INTEGER NOT NULL,
                protocol TEXT NOT NULL,
                process_id INTEGER NOT NULL DEFAULT 0,
                local_port INTEGER NOT NULL DEFAULT 0,
                process_name TEXT,
                connection_state TEXT,
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
                PRIMARY KEY (ip, port, protocol, process_id, local_port)
            )
            """
        )
        columns_sql = ", ".join(DESTINATION_COLUMNS)
        expressions_sql = ", ".join(select_expressions)
        self._db.execute(
            f"INSERT INTO destinations_migration ({columns_sql}) "
            f"SELECT {expressions_sql} FROM destinations"
        )
        self._db.execute("DROP TABLE destinations")
        self._db.execute("ALTER TABLE destinations_migration RENAME TO destinations")

    def record_interface(
        self, interface: str, sent: int, received: int, timestamp: float | None = None
    ) -> dict[str, int]:
        now = timestamp if timestamp is not None else time.time()
        sent = max(0, int(sent))
        received = max(0, int(received))

        with self._lock, self._db:
            row = self._db.execute(
                "SELECT * FROM interface_state WHERE interface = ?", (interface,)
            ).fetchone()

            if row is None:
                upload_delta = 0
                download_delta = 0
                upload_total = 0
                download_total = 0
                self._db.execute(
                    """
                    INSERT INTO interface_state
                    (interface, upload_total, download_total, last_sent, last_recv, updated_at)
                    VALUES (?, 0, 0, ?, ?, ?)
                    """,
                    (interface, sent, received, now),
                )
            else:
                upload_delta = sent - row["last_sent"] if sent >= row["last_sent"] else sent
                download_delta = received - row["last_recv"] if received >= row["last_recv"] else received
                upload_total = row["upload_total"] + upload_delta
                download_total = row["download_total"] + download_delta
                self._db.execute(
                    """
                    UPDATE interface_state
                    SET upload_total = ?, download_total = ?, last_sent = ?,
                        last_recv = ?, updated_at = ?
                    WHERE interface = ?
                    """,
                    (upload_total, download_total, sent, received, now, interface),
                )

            return {
                "upload_delta": upload_delta,
                "download_delta": download_delta,
                "upload_total": upload_total,
                "download_total": download_total,
            }

    def add_sample(self, timestamp: float, upload_bps: float, download_bps: float) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO traffic_samples(timestamp, upload_bps, download_bps) VALUES (?, ?, ?)",
                (timestamp, max(0.0, upload_bps), max(0.0, download_bps)),
            )
            self._db.execute(
                "DELETE FROM traffic_samples WHERE timestamp < ?",
                (timestamp - 60 * 60 * 24 * 90,),
            )

    def update_destinations(self, endpoints: list[dict[str, Any]], timestamp: float) -> None:
        with self._lock, self._db:
            self._db.execute("UPDATE destinations SET active = 0")
            self._db.execute(
                "DELETE FROM destinations WHERE last_seen < ?",
                (timestamp - DESTINATION_RETENTION_SEC,),
            )
            for item in endpoints:
                self._db.execute(
                    """
                    INSERT INTO destinations (
                        ip, port, protocol, process_id, local_port, process_name,
                        connection_state, first_seen, last_seen, country_code, country,
                        region, city, asn, organization, isp, latitude, longitude, active
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                    ON CONFLICT(ip, port, protocol, process_id, local_port) DO UPDATE SET
                        last_seen = excluded.last_seen,
                        process_name = COALESCE(excluded.process_name, destinations.process_name),
                        connection_state = COALESCE(excluded.connection_state, destinations.connection_state),
                        country_code = COALESCE(excluded.country_code, destinations.country_code),
                        country = COALESCE(excluded.country, destinations.country),
                        region = COALESCE(excluded.region, destinations.region),
                        city = COALESCE(excluded.city, destinations.city),
                        asn = COALESCE(excluded.asn, destinations.asn),
                        organization = COALESCE(excluded.organization, destinations.organization),
                        isp = COALESCE(excluded.isp, destinations.isp),
                        latitude = COALESCE(excluded.latitude, destinations.latitude),
                        longitude = COALESCE(excluded.longitude, destinations.longitude),
                        active = 1
                    """,
                    (
                        item["ip"],
                        item["port"],
                        item["protocol"],
                        int(item.get("process_id") or 0),
                        int(item.get("local_port") or 0),
                        item.get("process_name"),
                        item.get("connection_state"),
                        timestamp,
                        timestamp,
                        item.get("country_code"),
                        item.get("country"),
                        item.get("region"),
                        item.get("city"),
                        item.get("asn"),
                        item.get("organization"),
                        item.get("isp"),
                        item.get("latitude"),
                        item.get("longitude"),
                    ),
                )

    def summary(self, interface: str, now: float | None = None) -> dict[str, Any]:
        current = now if now is not None else time.time()
        with self._lock:
            state = self._db.execute(
                "SELECT upload_total, download_total FROM interface_state WHERE interface = ?",
                (interface,),
            ).fetchone()
            totals = dict(state) if state else {"upload_total": 0, "download_total": 0}
            destinations = self._db.execute(
                """
                SELECT ip, port, protocol, process_id, local_port, process_name,
                       connection_state, country_code, country, region, city,
                       asn, organization, isp, latitude, longitude, last_seen
                FROM destinations
                WHERE active = 1 AND last_seen >= ?
                ORDER BY last_seen DESC
                LIMIT 1000
                """,
                (current - ENDPOINT_ACTIVE_WINDOW_SEC,),
            ).fetchall()
            countries = self._db.execute(
                """
                SELECT country_code, country, COUNT(DISTINCT ip) AS peers
                FROM destinations
                WHERE active = 1 AND last_seen >= ? AND country IS NOT NULL
                GROUP BY country_code, country
                ORDER BY peers DESC
                LIMIT 12
                """,
                (current - ENDPOINT_ACTIVE_WINDOW_SEC,),
            ).fetchall()
            regions = self._db.execute(
                """
                SELECT country, region, COUNT(DISTINCT ip) AS peers
                FROM destinations
                WHERE active = 1 AND last_seen >= ?
                  AND region IS NOT NULL AND region <> ''
                GROUP BY country, region
                ORDER BY peers DESC, country, region
                LIMIT 20
                """,
                (current - ENDPOINT_ACTIVE_WINDOW_SEC,),
            ).fetchall()
            samples = self._db.execute(
                """
                SELECT timestamp, upload_bps, download_bps
                FROM traffic_samples
                WHERE timestamp >= ?
                ORDER BY timestamp
                """,
                (current - 3600,),
            ).fetchall()

        return {
            **totals,
            "active_endpoints": [dict(row) for row in destinations],
            "countries": [dict(row) for row in countries],
            "regions": [dict(row) for row in regions],
            "samples": [dict(row) for row in samples],
        }

    def close(self) -> None:
        with self._lock:
            self._db.close()


ENDPOINT_ACTIVE_WINDOW_SEC = 30
