from __future__ import annotations

import math
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any


DESTINATION_RETENTION_SEC = 5 * 365 * 24 * 60 * 60
HISTORY_RETENTION_SEC = 5 * 365 * 24 * 60 * 60
HISTORY_RAW_RETENTION_SEC = 7 * 24 * 60 * 60
MAX_DESTINATION_ROWS = 200_000
MAX_HISTORY_POINTS = 1800

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
DESTINATION_PRIMARY_KEY = ["ip", "port", "protocol", "process_id"]


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
        self._db.execute("PRAGMA cache_size=-4096")
        self._db.execute("PRAGMA temp_store=MEMORY")
        self._db.execute("PRAGMA mmap_size=33554432")
        self._db.execute("PRAGMA journal_size_limit=4194304")
        self._last_compaction = 0.0
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

                CREATE TABLE IF NOT EXISTS traffic_hourly (
                    hour_start INTEGER PRIMARY KEY,
                    upload_bps_avg REAL NOT NULL,
                    download_bps_avg REAL NOT NULL,
                    upload_bps_max REAL NOT NULL,
                    download_bps_max REAL NOT NULL,
                    sample_count INTEGER NOT NULL
                );

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
                    PRIMARY KEY (ip, port, protocol, process_id)
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
                PRIMARY KEY (ip, port, protocol, process_id)
            )
            """
        )
        # Collapse rows that differ only by ephemeral local port. This preserves
        # per-process destinations while preventing reconnects from creating a
        # separate history row for every local TCP source port.
        aggregates = {
            "ip": "ip",
            "port": "port",
            "protocol": "protocol",
            "process_id": "COALESCE(process_id, 0)",
            "local_port": "MAX(COALESCE(local_port, 0))",
            "process_name": "MAX(process_name)",
            "connection_state": "MAX(connection_state)",
            "first_seen": "MIN(first_seen)",
            "last_seen": "MAX(last_seen)",
            "country_code": "MAX(country_code)",
            "country": "MAX(country)",
            "region": "MAX(region)",
            "city": "MAX(city)",
            "asn": "MAX(asn)",
            "organization": "MAX(organization)",
            "isp": "MAX(isp)",
            "latitude": "MAX(latitude)",
            "longitude": "MAX(longitude)",
            "active": "MAX(active)",
        }
        columns_sql = ", ".join(DESTINATION_COLUMNS)
        expressions_sql = ", ".join(aggregates[column] for column in DESTINATION_COLUMNS)
        self._db.execute(
            f"INSERT OR REPLACE INTO destinations_migration ({columns_sql}) "
            "SELECT " + expressions_sql + " FROM destinations "
            "GROUP BY ip, port, protocol, COALESCE(process_id, 0)"
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
            if timestamp - self._last_compaction >= 3600:
                self._compact_history(timestamp)
                self._last_compaction = timestamp

    def _compact_history(self, timestamp: float) -> None:
        """Fold samples older than seven days into compact hourly aggregates."""
        cutoff = timestamp - HISTORY_RAW_RETENTION_SEC
        with self._lock, self._db:
            self._db.execute(
                """
                INSERT INTO traffic_hourly (
                    hour_start, upload_bps_avg, download_bps_avg,
                    upload_bps_max, download_bps_max, sample_count
                )
                SELECT
                    CAST(timestamp / 3600 AS INTEGER) * 3600,
                    AVG(upload_bps), AVG(download_bps),
                    MAX(upload_bps), MAX(download_bps), COUNT(*)
                FROM traffic_samples
                WHERE timestamp < ?
                GROUP BY CAST(timestamp / 3600 AS INTEGER)
                ON CONFLICT(hour_start) DO UPDATE SET
                    upload_bps_avg = (
                        traffic_hourly.upload_bps_avg * traffic_hourly.sample_count
                        + excluded.upload_bps_avg * excluded.sample_count
                    ) / (traffic_hourly.sample_count + excluded.sample_count),
                    download_bps_avg = (
                        traffic_hourly.download_bps_avg * traffic_hourly.sample_count
                        + excluded.download_bps_avg * excluded.sample_count
                    ) / (traffic_hourly.sample_count + excluded.sample_count),
                    upload_bps_max = MAX(traffic_hourly.upload_bps_max, excluded.upload_bps_max),
                    download_bps_max = MAX(traffic_hourly.download_bps_max, excluded.download_bps_max),
                    sample_count = traffic_hourly.sample_count + excluded.sample_count
                """,
                (cutoff,),
            )
            self._db.execute("DELETE FROM traffic_samples WHERE timestamp < ?", (cutoff,))
            self._db.execute(
                "DELETE FROM traffic_hourly WHERE hour_start < ?",
                (timestamp - HISTORY_RETENTION_SEC,),
            )
            self._db.execute("PRAGMA incremental_vacuum(128)")

    def history(self, range_seconds: int, now: float | None = None) -> list[dict[str, float]]:
        """Return downsampled traffic history for ranges up to five years."""
        current = now if now is not None else time.time()
        seconds = max(60, min(int(range_seconds), HISTORY_RETENTION_SEC))
        start = current - seconds
        bucket = max(10, math.ceil(seconds / MAX_HISTORY_POINTS))
        with self._lock:
            if seconds <= HISTORY_RAW_RETENTION_SEC:
                rows = self._db.execute(
                    """
                    SELECT CAST(timestamp / ? AS INTEGER) * ? AS timestamp,
                           AVG(upload_bps) AS upload_bps,
                           AVG(download_bps) AS download_bps
                    FROM traffic_samples
                    WHERE timestamp >= ?
                    GROUP BY CAST(timestamp / ? AS INTEGER)
                    ORDER BY timestamp
                    """,
                    (bucket, bucket, start, bucket),
                ).fetchall()
            else:
                rows = self._db.execute(
                    """
                    SELECT CAST(timestamp / ? AS INTEGER) * ? AS timestamp,
                           AVG(upload_bps) AS upload_bps,
                           AVG(download_bps) AS download_bps
                    FROM (
                        SELECT timestamp, upload_bps, download_bps
                        FROM traffic_samples WHERE timestamp >= ?
                        UNION ALL
                        SELECT hour_start AS timestamp,
                               upload_bps_avg AS upload_bps,
                               download_bps_avg AS download_bps
                        FROM traffic_hourly WHERE hour_start >= ?
                    )
                    GROUP BY CAST(timestamp / ? AS INTEGER)
                    ORDER BY timestamp
                    """,
                    (bucket, bucket, start, start, bucket),
                ).fetchall()
        return [dict(row) for row in rows]

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
                    ON CONFLICT(ip, port, protocol, process_id) DO UPDATE SET
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
            try:
                self._db.execute("PRAGMA optimize")
                self._db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            finally:
                self._db.close()


ENDPOINT_ACTIVE_WINDOW_SEC = 30
