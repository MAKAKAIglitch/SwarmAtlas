from __future__ import annotations

import os
import shutil
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Callable

from urllib.request import Request, urlopen

from .config import app_data_dir


NATURAL_EARTH_VERSION = "5.1.1"
USER_AGENT = "SwarmAtlas/0.1 (offline world map data)"
MAX_COUNTRY_ARCHIVE_BYTES = 24 * 1024 * 1024
MAX_REGION_ARCHIVE_BYTES = 40 * 1024 * 1024
MAX_EXTRACTED_BYTES = 96 * 1024 * 1024

DATASETS = (
    (
        "countries",
        "https://naturalearth.s3.amazonaws.com/10m_cultural/ne_10m_admin_0_countries.zip",
        "ne_10m_admin_0_countries",
        MAX_COUNTRY_ARCHIVE_BYTES,
    ),
    (
        "regions",
        "https://naturalearth.s3.amazonaws.com/10m_cultural/ne_10m_admin_1_states_provinces.zip",
        "ne_10m_admin_1_states_provinces",
        MAX_REGION_ARCHIVE_BYTES,
    ),
)


def _dataset_directory() -> Path:
    return app_data_dir() / "mapdata" / f"natural-earth-{NATURAL_EARTH_VERSION}"


def _required_files(directory: Path, stem: str) -> tuple[Path, Path, Path]:
    return tuple(directory / f"{stem}.{suffix}" for suffix in ("shp", "shx", "dbf"))  # type: ignore[return-value]


def _download_dataset(
    url: str,
    stem: str,
    destination: Path,
    maximum_bytes: int,
    progress: Callable[[int], None] | None = None,
) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    request = Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/zip"},
    )

    with tempfile.TemporaryDirectory(prefix="swarmatlas-map-", dir=str(destination)) as temporary:
        archive_path = Path(temporary) / f"{stem}.zip"
        received = 0
        with urlopen(request, timeout=60) as response:
            content_length = response.headers.get("Content-Length")
            expected_size = int(content_length) if content_length and content_length.isdigit() else 0
            if expected_size > maximum_bytes:
                raise ValueError(f"Natural Earth {stem} archive exceeds the allowed size.")

            with archive_path.open("wb") as output:
                while True:
                    chunk = response.read(512 * 1024)
                    if not chunk:
                        break
                    received += len(chunk)
                    if received > maximum_bytes:
                        raise ValueError(f"Natural Earth {stem} archive exceeded the allowed size.")
                    output.write(chunk)
                    if progress and expected_size:
                        progress(min(80, int(received * 80 / expected_size)))

        with zipfile.ZipFile(archive_path) as archive:
            infos = archive.infolist()
            extracted_size = sum(info.file_size for info in infos if not info.is_dir())
            if extracted_size > MAX_EXTRACTED_BYTES:
                raise ValueError(f"Natural Earth {stem} data exceeds the allowed extracted size.")
            matching: set[str] = set()
            for info in infos:
                raw = info.filename.replace("\\", "/")
                member = PurePosixPath(raw)
                if member.is_absolute() or ".." in member.parts:
                    raise ValueError("The Natural Earth archive contains an unsafe path.")
                if info.is_dir() or member.stem != stem:
                    continue
                suffix = member.suffix.lower()
                if suffix not in {".shp", ".shx", ".dbf", ".prj", ".cpg"}:
                    continue
                target = destination / f"{stem}{suffix}"
                with archive.open(info) as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)
                matching.add(suffix)

            if not {".shp", ".shx", ".dbf"}.issubset(matching):
                raise ValueError(f"Natural Earth archive is missing shapefile components for {stem}.")

        for path in _required_files(destination, stem):
            if not path.is_file() or path.stat().st_size == 0:
                raise ValueError(f"Natural Earth map component is missing: {path.name}")
        if progress:
            progress(100)


def ensure_natural_earth_data(
    progress: Callable[[str, int], None] | None = None,
) -> tuple[Path, Path]:
    """Download country and first-level administrative boundaries once."""
    directory = _dataset_directory()
    directory.mkdir(parents=True, exist_ok=True)
    result: dict[str, Path] = {}

    for index, (key, url, stem, maximum) in enumerate(DATASETS):
        country_or_region_dir = directory / key
        components = _required_files(country_or_region_dir, stem)
        if not all(path.is_file() and path.stat().st_size > 0 for path in components):
            if progress:
                progress(key, 0)
            _download_dataset(
                url,
                stem,
                country_or_region_dir,
                maximum,
                (lambda value, dataset=key: progress(dataset, value)) if progress else None,
            )
        result[key] = components[0]

    return result["countries"], result["regions"]


def _split_antimeridian(points: list[tuple[float, float]]) -> list[list[tuple[float, float]]]:
    """Avoid drawing a long line across the whole map at the +/-180° meridian."""
    segments: list[list[tuple[float, float]]] = []
    current: list[tuple[float, float]] = []
    for longitude, latitude in points:
        if not (-180.5 <= longitude <= 180.5 and -90.5 <= latitude <= 90.5):
            continue
        point = (float(longitude), float(latitude))
        if current and abs(point[0] - current[-1][0]) > 180.0:
            if len(current) >= 2:
                segments.append(current)
            current = []
        current.append(point)
    if len(current) >= 2:
        segments.append(current)
    return segments


def read_polygon_rings(shapefile_path: Path) -> list[list[tuple[float, float]]]:
    """Read polygon rings with pyshp; no GIS runtime or network service is needed."""
    import shapefile

    reader = shapefile.Reader(str(shapefile_path), encoding="utf-8", encodingErrors="replace")
    rings: list[list[tuple[float, float]]] = []
    try:
        for shape in reader.iterShapes():
            points = shape.points
            if len(points) < 3:
                continue
            starts = list(shape.parts) + [len(points)]
            for start, end in zip(starts, starts[1:]):
                raw_points = [(float(x), float(y)) for x, y in points[start:end]]
                rings.extend(_split_antimeridian(raw_points))
    finally:
        reader.close()
    return rings


def load_world_boundaries(
    progress: Callable[[str], None] | None = None,
) -> tuple[list[list[tuple[float, float]]], list[list[tuple[float, float]]]]:
    country_file, region_file = ensure_natural_earth_data(
        lambda _dataset, value: progress(f"Downloading map data: {value}%")
        if progress else None
    )
    if progress:
        progress("Loading country and region boundaries…")
    countries = read_polygon_rings(country_file)
    regions = read_polygon_rings(region_file)
    return countries, regions
