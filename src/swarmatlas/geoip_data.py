from __future__ import annotations

import gzip
import hashlib
import html
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable
from urllib.request import Request, urlopen

from .config import app_data_dir


DBIP_METADATA_URL = "https://db-ip.com/db/download/ip-to-city-lite"
USER_AGENT = "SwarmAtlas/0.1 (local GeoIP database updater)"
MAX_METADATA_BYTES = 2 * 1024 * 1024
MAX_COMPRESSED_BYTES = 192 * 1024 * 1024
MAX_DATABASE_BYTES = 256 * 1024 * 1024
DATABASE_REFRESH_DAYS = 30


class _PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.text_parts: list[str] = []
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "a":
            href = dict(attrs).get("href")
            if href:
                self.links.append(href)

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.text_parts.append(data.strip())


def default_geoip_database_path() -> Path:
    return app_data_dir() / "geoip" / "dbip-city-lite.mmdb"


def _release_metadata() -> tuple[str, str]:
    request = Request(
        DBIP_METADATA_URL,
        headers={"User-Agent": USER_AGENT, "Accept": "text/html"},
    )
    with urlopen(request, timeout=30) as response:
        content = response.read(MAX_METADATA_BYTES + 1)
    if len(content) > MAX_METADATA_BYTES:
        raise ValueError("DB-IP release information exceeded the allowed size.")

    parser = _PageParser()
    parser.feed(content.decode("utf-8", errors="replace"))
    page_text = re.sub(r"\s+", " ", " ".join(parser.text_parts))
    page_text = html.unescape(page_text)

    download_url = next(
        (
            link for link in parser.links
            if re.search(
                r"dbip-city-lite-\d{4}-\d{2}\.mmdb\.gz$",
                link,
                re.IGNORECASE,
            )
        ),
        None,
    )
    if not download_url:
        raise ValueError("The official DB-IP page does not expose a City Lite MMDB download.")

    mmdb_start = page_text.find("Format MMDB")
    if mmdb_start < 0:
        raise ValueError("Could not locate the MMDB release details on the DB-IP page.")
    mmdb_section = page_text[mmdb_start:]
    download_label = mmdb_section.find("Download IP to City Lite MMDB")
    if download_label >= 0:
        mmdb_section = mmdb_section[:download_label]

    checksum_match = re.search(r"SHA1SUM\s+([0-9a-f]{40})", mmdb_section, re.IGNORECASE)
    if not checksum_match:
        raise ValueError("The DB-IP page does not contain a valid MMDB SHA-1 checksum.")

    return download_url, checksum_match.group(1).lower()


def _sha1_file(path: Path) -> str:
    digest = hashlib.sha1()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_mmdb(path: Path) -> None:
    try:
        import maxminddb
    except ImportError as exc:
        raise RuntimeError(
            "The maxminddb reader is missing. Reinstall SwarmAtlas with its GeoIP dependencies."
        ) from exc

    try:
        reader = maxminddb.open_database(str(path))
        try:
            metadata = reader.metadata()
            database_type = str(getattr(metadata, "database_type", "")).lower()
            if "city" not in database_type:
                raise ValueError(
                    f"Downloaded database has an unexpected type: {database_type or 'unknown'}"
                )
            sample = reader.get("8.8.8.8") or reader.get("1.1.1.1")
            if not isinstance(sample, dict) or not isinstance(sample.get("country"), dict):
                raise ValueError("Downloaded database does not contain a valid City record.")
        finally:
            reader.close()
    except (OSError, ValueError) as exc:
        raise ValueError(f"Downloaded DB-IP MMDB validation failed: {exc}") from exc


def _download_release(
    target: Path,
    download_url: str,
    expected_sha1: str,
    progress: Callable[[int], None] | None = None,
) -> None:
    request = Request(
        download_url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/octet-stream"},
    )

    with tempfile.TemporaryDirectory(
        prefix="swarmatlas-dbip-",
        dir=str(target.parent),
    ) as temporary:
        temporary_dir = Path(temporary)
        compressed_path = temporary_dir / "city-lite.mmdb.gz"
        extracted_path = temporary_dir / "city-lite.mmdb"

        compressed_digest = hashlib.sha1()
        received = 0
        with urlopen(request, timeout=60) as response:
            content_length = response.headers.get("Content-Length")
            expected_size = int(content_length) if content_length and content_length.isdigit() else 0
            if expected_size > MAX_COMPRESSED_BYTES:
                raise ValueError("DB-IP compressed database exceeds the allowed download size.")

            with compressed_path.open("wb") as output:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    received += len(chunk)
                    if received > MAX_COMPRESSED_BYTES:
                        raise ValueError("DB-IP compressed database exceeded the allowed size.")
                    compressed_digest.update(chunk)
                    output.write(chunk)
                    if progress and expected_size:
                        progress(min(70, int(received * 70 / expected_size)))

        uncompressed_digest = hashlib.sha1()
        extracted_size = 0
        with gzip.open(compressed_path, "rb") as source, extracted_path.open("wb") as output:
            while True:
                chunk = source.read(1024 * 1024)
                if not chunk:
                    break
                extracted_size += len(chunk)
                if extracted_size > MAX_DATABASE_BYTES:
                    raise ValueError("DB-IP database exceeded the allowed uncompressed size.")
                uncompressed_digest.update(chunk)
                output.write(chunk)
                if progress:
                    progress(70 + min(25, int(extracted_size / MAX_DATABASE_BYTES * 25)))

        # DB-IP publishes a SHA-1 beside the MMDB download. Accept the checksum
        # for either the downloaded gzip asset or the extracted MMDB file.
        if expected_sha1 not in {
            compressed_digest.hexdigest().lower(),
            uncompressed_digest.hexdigest().lower(),
        }:
            raise ValueError("DB-IP SHA-1 checksum verification failed.")

        _validate_mmdb(extracted_path)
        os.replace(extracted_path, target)
        if progress:
            progress(100)


def ensure_default_geoip_database(
    progress: Callable[[int], None] | None = None,
    force_refresh: bool = False,
) -> Path:
    """Return a validated local DB-IP City Lite database, refreshing monthly."""
    target = default_geoip_database_path()
    target.parent.mkdir(parents=True, exist_ok=True)

    existing_valid = False
    if target.is_file():
        try:
            _validate_mmdb(target)
            existing_valid = True
        except (OSError, ValueError, RuntimeError):
            existing_valid = False

    if existing_valid and not force_refresh:
        age_seconds = max(0.0, datetime.now(timezone.utc).timestamp() - target.stat().st_mtime)
        if age_seconds < DATABASE_REFRESH_DAYS * 24 * 60 * 60:
            return target

    try:
        download_url, checksum = _release_metadata()
        _download_release(target, download_url, checksum, progress)
        return target
    except Exception:
        # An expired but still valid database remains usable when the PC is
        # offline or the upstream database host is temporarily unavailable.
        if existing_valid:
            return target
        raise
