from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from update_project import safe_members, should_skip


def test_updater_preserves_virtualenv_and_local_databases() -> None:
    assert should_skip(Path(".venv/Scripts/python.exe"))
    assert should_skip(Path("assets/GeoLite2-City.mmdb"))
    assert should_skip(Path("data/swarmatlas.sqlite3"))
    assert should_skip(Path("logs/latest.log"))
    assert not should_skip(Path("src/swarmatlas/ui.py"))


def test_update_archive_rejects_path_traversal(tmp_path: Path) -> None:
    archive_path = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("SwarmAtlas-main/README.md", "safe")
        archive.writestr("SwarmAtlas-main/../../outside.txt", "unsafe")

    with zipfile.ZipFile(archive_path) as archive:
        with pytest.raises(ValueError, match="Unsafe path"):
            safe_members(archive)


def test_update_archive_requires_single_repository_root(tmp_path: Path) -> None:
    archive_path = tmp_path / "two-roots.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("first/README.md", "first")
        archive.writestr("second/README.md", "second")

    with zipfile.ZipFile(archive_path) as archive:
        with pytest.raises(ValueError, match="one expected repository root"):
            safe_members(archive)
