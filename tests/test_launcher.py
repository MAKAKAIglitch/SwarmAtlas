from __future__ import annotations

import io
import sys
from pathlib import Path

from run_windows import stream_process


def test_stream_process_duplicates_stdout_and_stderr_to_log(tmp_path: Path) -> None:
    output = io.StringIO()
    log_path = tmp_path / "launcher.log"
    command = [
        sys.executable,
        "-c",
        "import sys; print('visible stdout'); print('visible stderr', file=sys.stderr)",
    ]

    with log_path.open("w", encoding="utf-8") as log:
        result = stream_process(command, log, tmp_path, console=output)

    console_text = output.getvalue()
    log_text = log_path.read_text(encoding="utf-8")
    assert result == 0
    assert "visible stdout" in console_text
    assert "visible stderr" in console_text
    assert "visible stdout" in log_text
    assert "visible stderr" in log_text


def test_stream_process_records_nonzero_exit_code(tmp_path: Path) -> None:
    output = io.StringIO()
    log_path = tmp_path / "launcher.log"

    with log_path.open("w", encoding="utf-8") as log:
        result = stream_process(
            [sys.executable, "-c", "raise SystemExit(23)"],
            log,
            tmp_path,
            console=output,
        )

    assert result == 23
    assert "exit code 23" in output.getvalue()
    assert "exit code 23" in log_path.read_text(encoding="utf-8")
