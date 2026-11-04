from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from update_project import (
    NATIVE_WHEEL_FILENAME,
    NATIVE_REQUIRED_CAPABILITIES,
    verify_installed_native,
    install_prebuilt_native,
    refresh_native_extension,
    safe_members,
    should_skip,
    verify_native_wheel,
)


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
    installs: list[Path] = []

    def fake_install(root: Path, python_executable: Path) -> bool:
        installs.append(python_executable)
        return True

    monkeypatch.setattr("update_project.install_prebuilt_native", fake_install)

    assert refresh_native_extension(tmp_path) is True
    assert len(calls) == 1
    assert installs == [python]


def test_native_wheel_sha256_manifest_is_verified(tmp_path: Path) -> None:
    import hashlib

    wheel = tmp_path / NATIVE_WHEEL_FILENAME
    payload = b"fake wheel payload for checksum unit test"
    wheel.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()

    verify_native_wheel(wheel, f"{digest}  {NATIVE_WHEEL_FILENAME}")


def test_native_wheel_rejects_checksum_mismatch(tmp_path: Path) -> None:
    wheel = tmp_path / NATIVE_WHEEL_FILENAME
    wheel.write_bytes(b"actual contents")

    with pytest.raises(ValueError, match="SHA-256"):
        verify_native_wheel(wheel, f"{'0' * 64}  {NATIVE_WHEEL_FILENAME}")


def test_native_wheel_rejects_unexpected_asset_name(tmp_path: Path) -> None:
    wheel = tmp_path / NATIVE_WHEEL_FILENAME
    wheel.write_bytes(b"contents")

    with pytest.raises(ValueError, match="unexpected native wheel"):
        verify_native_wheel(wheel, f"{'0' * 64}  other.whl")


def test_prebuilt_native_update_restores_old_extension_after_failed_verification(
    tmp_path: Path, monkeypatch
) -> None:
    import subprocess

    root = tmp_path
    venv = root / ".venv"
    python = venv / "Scripts" / "python.exe"
    module = venv / "Lib" / "site-packages" / "_swarmatlas_native.abi3.pyd"
    python.parent.mkdir(parents=True)
    module.parent.mkdir(parents=True)
    python.write_bytes(b"python")
    module.write_bytes(b"previous-working-extension")

    def fake_download(_url: str, destination: Path, _maximum: int) -> None:
        destination.write_bytes(b"wheel-or-manifest")

    monkeypatch.setattr("update_project.download_limited_asset", fake_download)
    monkeypatch.setattr("update_project.verify_native_wheel", lambda *_args: None)

    def fake_run(command, **kwargs):
        command = [str(part) for part in command]
        if "-c" in command:
            script = command[-1]
            if "find_spec" in script:
                return subprocess.CompletedProcess(command, 0, stdout=str(module), stderr="")
            if "capabilities" in script:
                module.write_bytes(b"broken-new-extension")
                return subprocess.CompletedProcess(command, 1, stdout="", stderr="import failed")
        if "pip" in command:
            module.write_bytes(b"new-extension")
            return subprocess.CompletedProcess(command, 0)
        raise AssertionError(f"Unexpected updater command: {command}")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="did not pass its import check"):
        install_prebuilt_native(root, python)

    assert module.read_bytes() == b"previous-working-extension"



def test_native_backup_survives_when_automatic_rollback_fails(
    tmp_path: Path, monkeypatch
) -> None:
    import subprocess
    import update_project

    root = tmp_path
    venv = root / ".venv"
    python = venv / "Scripts" / "python.exe"
    module = venv / "Lib" / "site-packages" / "_swarmatlas_native.abi3.pyd"
    python.parent.mkdir(parents=True)
    module.parent.mkdir(parents=True)
    python.write_bytes(b"python")
    module.write_bytes(b"previous-working-extension")

    def fake_download(_url: str, destination: Path, _maximum: int) -> None:
        destination.write_bytes(b"wheel-or-manifest")

    monkeypatch.setattr(update_project, "download_limited_asset", fake_download)
    monkeypatch.setattr(update_project, "verify_native_wheel", lambda *_args: None)

    def fake_run(command, **kwargs):
        command = [str(part) for part in command]
        if "-c" in command:
            script = command[-1]
            if "find_spec" in script:
                return subprocess.CompletedProcess(command, 0, stdout=str(module), stderr="")
            if "capabilities" in script:
                module.write_bytes(b"broken-new-extension")
                return subprocess.CompletedProcess(command, 1, stdout="", stderr="import failed")
        if "pip" in command:
            module.write_bytes(b"new-extension")
            return subprocess.CompletedProcess(command, 0)
        raise AssertionError(f"Unexpected updater command: {command}")

    monkeypatch.setattr(subprocess, "run", fake_run)

    def fail_restore(_source, _destination):
        raise OSError("simulated restore failure")

    monkeypatch.setattr(update_project.os, "replace", fail_restore)

    with pytest.raises(RuntimeError, match="backup was preserved at"):
        install_prebuilt_native(root, python)

    backup = module.with_name(module.name + ".swarmatlas-backup")
    assert backup.is_file()
    assert backup.read_bytes() == b"previous-working-extension"

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
    assert len(calls) == 3
    assert "tcp-public-endpoints" in calls[2]["command"][-1]
    assert calls[2]["cwd"] == str(root)
    assert calls[1]["cwd"] == str(root / "native")
    assert calls[1]["env"]["VIRTUAL_ENV"] == str(root / ".venv")



def test_updater_native_capabilities_require_rust_side_endpoint_filter() -> None:
    assert "tcp-public-endpoints" in NATIVE_REQUIRED_CAPABILITIES


def test_updater_rejects_native_build_without_optimized_capability(
    tmp_path: Path, monkeypatch
) -> None:
    import subprocess

    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append([str(part) for part in command])
        return subprocess.CompletedProcess(command, 1)

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert verify_installed_native(tmp_path / "python.exe", tmp_path) is False
    assert len(calls) == 1
    assert "tcp-public-endpoints" in calls[0][-1]
