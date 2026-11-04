from __future__ import annotations

import os
import platform
import subprocess
import sys
import tempfile
import traceback
from datetime import datetime
from io import TextIOBase
from pathlib import Path
from typing import Sequence, TextIO


MIN_PYTHON = (3, 11)
MAX_LOG_FILES = 10


def _timestamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _emit(log: TextIO | None, message: str, console: TextIO = sys.stdout) -> None:
    console.write(message + "\n")
    console.flush()
    if log is not None:
        log.write(f"{_timestamp()} {message}\n")
        log.flush()


def _create_log_file() -> Path | None:
    candidates: list[Path] = []
    for variable in ("LOCALAPPDATA", "APPDATA", "TEMP"):
        value = os.environ.get(variable)
        if value:
            candidates.append(Path(value) / "SwarmAtlas" / "logs")

    candidates.append(Path(tempfile.gettempdir()) / "SwarmAtlas" / "logs")

    for directory in candidates:
        try:
            directory.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            path = directory / f"swarmatlas-{stamp}-{os.getpid()}.log"
            with path.open("a", encoding="utf-8"):
                pass
            _prune_old_logs(directory, keep=MAX_LOG_FILES, current=path)
            return path
        except OSError:
            continue
    return None


def _prune_old_logs(directory: Path, keep: int, current: Path) -> None:
    try:
        logs = sorted(
            directory.glob("swarmatlas-*.log"),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
        retained = 0
        for path in logs:
            if path == current or retained < keep - 1:
                retained += 1
                continue
            try:
                path.unlink()
            except OSError:
                pass
    except OSError:
        pass


def stream_process(
    command: Sequence[str],
    log: TextIO | None,
    cwd: Path,
    console: TextIO = sys.stdout,
    env: dict[str, str] | None = None,
) -> int:
    _emit(log, "$ " + subprocess.list2cmdline([str(part) for part in command]), console)
    process = subprocess.Popen(
        [str(part) for part in command],
        cwd=str(cwd),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    assert process.stdout is not None
    for line in process.stdout:
        _emit(log, line.rstrip("\r\n"), console)
    return_code = process.wait()
    if return_code:
        _emit(log, f"[process exited with code {return_code}]", console)
    return return_code


def _run(log: TextIO | None, root: Path) -> int:
    _emit(log, f"[INFO] SwarmAtlas launcher started")
    _emit(log, f"[INFO] Launcher Python: {sys.version.split()[0]}")
    _emit(log, f"[INFO] Platform: {platform.system()} {platform.release()} ({platform.machine()})")

    if sys.version_info < MIN_PYTHON:
        _emit(
            log,
            "[ERROR] Python 3.11 or newer is required. Install a newer Python 3 runtime "
            "and launch again.",
        )
        return 1

    venv_python = root / ".venv" / "Scripts" / "python.exe"
    if not venv_python.is_file():
        _emit(log, "[INFO] Creating the local virtual environment...")
        code = stream_process(
            [sys.executable, "-m", "venv", str(root / ".venv")],
            log,
            root,
        )
        if code:
            _emit(log, "[ERROR] Could not create .venv.")
            return code

    if not venv_python.is_file():
        _emit(log, "[ERROR] .venv was not created at the expected location.")
        return 1

    code = stream_process(
        [
            str(venv_python),
            "-c",
            "import sys; print('Environment Python:', sys.version.split()[0]); "
            "raise SystemExit(0 if sys.version_info >= (3, 11) else 1)",
        ],
        log,
        root,
    )
    if code:
        _emit(
            log,
            "[ERROR] The existing .venv uses Python older than 3.11. "
            "Rename the SwarmAtlas .venv folder and launch again.",
        )
        return code

    _emit(log, "[INFO] Checking application dependencies...")
    code = stream_process(
        [
            str(venv_python),
            "-c",
            "import PySide6, psutil, geoip2, swarmatlas",
        ],
        log,
        root,
    )
    if code:
        _emit(log, "[INFO] Dependencies are missing or incomplete; installing them...")
        code = stream_process(
            [
                str(venv_python),
                "-m",
                "pip",
                "install",
                "-e",
                ".[geoip]",
            ],
            log,
            root,
        )
        if code:
            _emit(log, "[ERROR] Dependency installation failed.")
            return code

    _emit(log, "[INFO] Starting the SwarmAtlas desktop application...")
    child_env = os.environ.copy()
    child_env["PYTHONFAULTHANDLER"] = "1"
    child_env["PYTHONUNBUFFERED"] = "1"

    code = stream_process(
        [
            str(venv_python),
            "-X",
            "faulthandler",
            "-u",
            "-m",
            "swarmatlas",
        ],
        log,
        root,
        env=child_env,
    )
    if code:
        _emit(log, f"[ERROR] SwarmAtlas terminated with exit code {code}.")
    else:
        _emit(log, "[INFO] SwarmAtlas closed normally.")
    return code


def main() -> int:
    root = Path(__file__).resolve().parent
    log_path = _create_log_file()

    if log_path is not None:
        try:
            log_context = log_path.open("a", encoding="utf-8", buffering=1)
        except OSError:
            log_context = None
    else:
        log_context = None

    if log_context is None:
        _emit(None, "[WARNING] Could not create a log file; output will remain in this console.")
        try:
            return _run(None, root)
        except Exception:
            for line in traceback.format_exc().rstrip().splitlines():
                _emit(None, line)
            return 1

    with log_context as log:
        _emit(log, f"[INFO] Log file: {log_path}")
        try:
            return _run(log, root)
        except KeyboardInterrupt:
            _emit(log, "[WARNING] Launcher interrupted by the user.")
            return 130
        except Exception:
            _emit(log, "[FATAL] Unexpected launcher exception:")
            for line in traceback.format_exc().rstrip().splitlines():
                _emit(log, line)
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
