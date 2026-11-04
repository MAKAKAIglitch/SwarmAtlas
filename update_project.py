from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath
from typing import Iterable


REPOSITORY = "MAKAKAIglitch/SwarmAtlas"
BRANCH = "main"
ARCHIVE_URL = f"https://github.com/{REPOSITORY}/archive/refs/heads/{BRANCH}.zip"
MAX_ARCHIVE_BYTES = 100 * 1024 * 1024
MAX_EXTRACTED_BYTES = 250 * 1024 * 1024

SKIP_PARTS = {
    ".git",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    "target",
    "dist",
    ".swarmatlas-update-backup",
}
SKIP_SUFFIXES = {".mmdb", ".sqlite", ".sqlite3", ".db", ".log", ".pyc"}


def say(message: str) -> None:
    print(message, flush=True)


def should_skip(relative: Path) -> bool:
    if any(part in SKIP_PARTS for part in relative.parts):
        return True
    if relative.name == ".env" or relative.name.startswith(".env."):
        return True
    if relative.suffix.lower() in SKIP_SUFFIXES:
        return True
    return False


def safe_members(archive: zipfile.ZipFile) -> tuple[str, list[zipfile.ZipInfo]]:
    files: list[zipfile.ZipInfo] = []
    roots: set[str] = set()
    extracted_total = 0

    for info in archive.infolist():
        raw = info.filename.replace("\\", "/")
        path = PurePosixPath(raw)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError(f"Unsafe path inside the update archive: {info.filename}")
        if not path.parts:
            continue
        roots.add(path.parts[0])
        mode = (info.external_attr >> 16) & 0o170000
        if mode == 0o120000:
            raise ValueError(f"Symbolic links are not accepted in update archives: {info.filename}")
        if info.is_dir():
            continue
        extracted_total += info.file_size
        if extracted_total > MAX_EXTRACTED_BYTES:
            raise ValueError("The extracted repository is larger than the allowed size.")
        files.append(info)

    if len(roots) != 1:
        raise ValueError("The update archive does not have one expected repository root.")
    return next(iter(roots)), files


def download_archive(destination: Path) -> None:
    request = urllib.request.Request(
        ARCHIVE_URL,
        headers={"User-Agent": "SwarmAtlas-Updater/1.0", "Accept": "application/zip"},
    )
    say("[Updater] Downloading the latest main branch from GitHub...")
    with urllib.request.urlopen(request, timeout=60) as response:
        length = response.headers.get("Content-Length")
        if length and int(length) > MAX_ARCHIVE_BYTES:
            raise ValueError("The repository archive is larger than the allowed download size.")
        written = 0
        with destination.open("wb") as output:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > MAX_ARCHIVE_BYTES:
                    raise ValueError("The repository archive exceeded the allowed download size.")
                output.write(chunk)
    say(f"[Updater] Download complete ({written / 1024 / 1024:.1f} MiB).")


def stage_archive(archive_path: Path, stage: Path) -> Path:
    with zipfile.ZipFile(archive_path) as archive:
        root_name, members = safe_members(archive)
        source_root = stage / "source"
        source_root.mkdir(parents=True, exist_ok=True)

        for info in members:
            raw = info.filename.replace("\\", "/")
            member_path = PurePosixPath(raw)
            relative_parts = member_path.parts[1:]
            if not relative_parts:
                continue
            relative = Path(*relative_parts)
            if should_skip(relative):
                continue

            target = source_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output)

    required = (
        source_root / "pyproject.toml",
        source_root / "run_windows.bat",
        source_root / "src" / "swarmatlas" / "__main__.py",
        source_root / "update_project.bat",
    )
    if not all(path.is_file() for path in required):
        raise ValueError("The downloaded archive is missing required SwarmAtlas files.")
    return source_root


def overlay_update(source: Path, root: Path, backup: Path) -> int:
    updated = 0
    changed: list[tuple[Path, Path | None]] = []
    files = sorted(path for path in source.rglob("*") if path.is_file())
    try:
        for source_file in files:
            relative = source_file.relative_to(source)
            if should_skip(relative):
                continue
            destination = root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)

            if destination.is_file() and destination.read_bytes() == source_file.read_bytes():
                continue

            backup_file: Path | None = None
            if destination.is_file():
                backup_file = backup / relative
                backup_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(destination, backup_file)

            temporary = destination.with_name(destination.name + ".swarmatlas-update-tmp")
            try:
                shutil.copy2(source_file, temporary)
                os.replace(temporary, destination)
            finally:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

            changed.append((destination, backup_file))
            updated += 1
    except Exception:
        # Restore files already replaced if a later file fails.
        for destination, backup_file in reversed(changed):
            try:
                if backup_file is None:
                    destination.unlink(missing_ok=True)
                else:
                    shutil.copy2(backup_file, destination)
            except OSError:
                pass
        raise
    return updated

def update_git_checkout(root: Path) -> bool:
    git_dir = root / ".git"
    if not git_dir.exists():
        return False

    git = shutil.which("git")
    if not git:
        raise RuntimeError("This folder is a Git checkout, but git.exe is not in PATH.")

    status = subprocess.run(
        [git, "-C", str(root), "status", "--porcelain", "--untracked-files=all"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if status.returncode:
        raise RuntimeError(status.stderr.strip() or "Could not inspect Git working tree.")
    unsafe_changes: list[str] = []
    for line in status.stdout.splitlines():
        if len(line) < 4:
            continue
        changed_path = line[3:].strip()
        if " -> " in changed_path:
            changed_path = changed_path.split(" -> ", 1)[1]
        changed_path = changed_path.strip('"')
        if should_skip(Path(changed_path)):
            continue
        unsafe_changes.append(line)

    if unsafe_changes:
        preview = "\\n".join(unsafe_changes[:5])
        raise RuntimeError(
            "Local Git changes were found. Commit or back up those changes before updating. "
            "The updater will not overwrite them.\\n" + preview
        )

    say("[Updater] Clean Git checkout detected; updating with fast-forward only...")
    fetch = subprocess.run(
        [git, "-C", str(root), "fetch", "origin", BRANCH],
        text=True,
    )
    if fetch.returncode:
        raise RuntimeError("Git fetch failed. Check your connection and remote configuration.")

    merge = subprocess.run(
        [git, "-C", str(root), "merge", "--ff-only", "FETCH_HEAD"],
        text=True,
    )
    if merge.returncode:
        raise RuntimeError("Fast-forward update failed. Your local Git history was not rewritten.")
    return True


def refresh_dependencies(root: Path) -> bool:
    venv_python = root / ".venv" / "Scripts" / "python.exe"
    if not venv_python.is_file():
        say("[Updater] No existing .venv found; dependency installation will happen on next launch.")
        return True

    say("[Updater] Checking and updating project dependencies...")
    process = subprocess.run(
        [str(venv_python), "-m", "pip", "install", "-e", ".[geoip]"],
        cwd=str(root),
        text=True,
    )
    return process.returncode == 0



def refresh_native_extension(root: Path) -> bool:
    """Rebuild Rust only when it was already enabled by the user."""
    venv_python = root / ".venv" / "Scripts" / "python.exe"
    scripts = root / ".venv" / "Scripts"
    maturin = scripts / "maturin.exe"
    if not venv_python.is_file():
        return True

    probe = subprocess.run(
        [
            str(venv_python),
            "-c",
            "import importlib.util,sys; "
            "sys.exit(0 if importlib.util.find_spec('_swarmatlas_native') else 1)",
        ],
        cwd=str(root),
        capture_output=True,
        text=True,
    )
    if probe.returncode != 0:
        say("[Updater] Rust core is not installed; Python collector remains selected.")
        return True

    cargo = shutil.which("cargo")
    rustc = shutil.which("rustc")
    if not cargo or not rustc:
        say("[WARNING] A Rust extension is installed, but cargo/rustc is unavailable.")
        say("[WARNING] Its existing binary was preserved; run build_native.bat to rebuild it.")
        return True

    if not maturin.is_file():
        say("[Updater] Installing the pinned Rust build frontend...")
        install = subprocess.run(
            [str(venv_python), "-m", "pip", "install", "maturin==1.16.0"],
            cwd=str(root),
            text=True,
        )
        if install.returncode != 0 or not maturin.is_file():
            say("[WARNING] Could not prepare maturin; the existing Rust binary was preserved.")
            return True

    env = os.environ.copy()
    env["VIRTUAL_ENV"] = str(root / ".venv")
    env["PATH"] = str(scripts) + os.pathsep + env.get("PATH", "")

    say("[Updater] Rebuilding the already-installed Rust monitoring core...")
    build = subprocess.run(
        [str(maturin), "develop", "--release"],
        cwd=str(root / "native"),
        env=env,
        text=True,
    )
    if build.returncode != 0:
        say("[WARNING] Rust rebuild did not complete; the previous native binary was preserved.")
        say("[WARNING] Python and the current installed Rust extension remain available.")
        return True

    say("[Updater] Rust monitoring core rebuilt successfully.")
    return True

def main() -> int:
    root = Path(__file__).resolve().parent
    if sys.version_info < (3, 11):
        say("[ERROR] The updater requires Python 3.11 or newer.")
        return 1
    say("SwarmAtlas project updater")
    say(f"[Updater] Project folder: {root}")
    say("[Updater] Close SwarmAtlas before continuing.")

    try:
        if update_git_checkout(root):
            updated_description = "Git fast-forward"
        else:
            with tempfile.TemporaryDirectory(prefix="swarmatlas-update-") as temporary:
                temp_root = Path(temporary)
                archive_path = temp_root / "main.zip"
                download_archive(archive_path)
                source = stage_archive(archive_path, temp_root / "stage")
                backup = temp_root / "backup"
                updated_count = overlay_update(source, root, backup)
                updated_description = f"{updated_count} project files"

        say(f"[Updater] Source update complete ({updated_description}).")
        if not refresh_dependencies(root):
            say("[ERROR] Source files were updated, but dependency refresh failed.")
            say("[ERROR] Check the pip output above and run the updater again when online.")
            return 2

        refresh_native_extension(root)

        say("[Updater] Update completed successfully.")
        say("[Updater] Local settings, databases, logs and .venv were preserved.")
        say("[Updater] Start SwarmAtlas with run_windows.bat.")
        return 0
    except (OSError, ValueError, zipfile.BadZipFile, urllib.error.URLError, RuntimeError) as exc:
        say(f"[ERROR] Update failed: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
