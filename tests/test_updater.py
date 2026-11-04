from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from update_project import refresh_native_extension, safe_members, should_skip


def test_updater_preserves_virtualenv_and_local_databases() -> None:
    assert should_skip(Path(".venv/Scripts/python.exe"))
    assert should_skip(Path("assets/GeoLite2-City.mmdb"))
    assert should_skip(Path("data/swarmatlas.sqlite3"))
    assert should_skip(Path("logs/latest.log"))
    assert should_skip(Path("native/target/release/_swarmatlas_native.dll"))
    assert should_skip(Path("native/dist/swarmatlas_native.whl"))
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



def test_updater_leaves_python_fallback_when_rust_extension_is_absent(
    tmp_path: Path, monkeypatch
) -> None:
    import subprocess

    python = tmp_path / ".venv" / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"")
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append([str(part) for part in command])
        return subprocess.CompletedProcess(command, 1)

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr("update_project.shutil.which", lambda _name: None)

    assert refresh_native_extension(tmp_path) is True
    assert len(calls) == 1


def test_updater_does_not_rebuild_when_rust_toolchain_is_missing(
    tmp_path: Path, monkeypatch
) -> None:
    import subprocess

    python = tmp_path / ".venv" / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"")
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append([str(part) for part in command])
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr("update_project.shutil.which", lambda _name: None)

    assert refresh_native_extension(tmp_path) is True
    assert len(calls) == 1


def test_updater_rebuilds_installed_native_extension_when_toolchain_exists(
    tmp_path: Path, monkeypatch
) -> None:
    import subprocess

    root = tmp_path
    scripts = root / ".venv" / "Scripts"
    scripts.mkdir(parents=True)
    (scripts / "python.exe").write_bytes(b"")
    (scripts / "maturin.exe").write_bytes(b"")
    (root / "native").mkdir()

    calls: list[dict[str, object]] = []

    def fake_run(command, **kwargs):
        calls.append({"command": [str(part) for part in command], **kwargs})
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(
        "update_project.shutil.which",
        lambda name: f"C:/Rust/bin/{name}.exe",
    )

    assert refresh_native_extension(root) is True
    assert len(calls) == 2
    assert calls[1]["cwd"] == str(root / "native")
    assert calls[1]["env"]["VIRTUAL_ENV"] == str(root / ".venv")
