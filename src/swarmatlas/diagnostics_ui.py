from __future__ import annotations

import ipaddress
import re
import socket
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from PySide6.QtCore import QProcess, QThread, QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .i18n import tr
from .native import NATIVE_TRACEROUTE, traceroute_probe as native_traceroute_probe


_RTT_RE = re.compile(r"(?:time|время)\s*[=<]\s*([\d.,]+)\s*(?:ms|мс)", re.IGNORECASE)
_RTT_GENERIC_RE = re.compile(r"([<>]?\s*\d+(?:[.,]\d+)?)\s*(?:ms|мс)\b", re.IGNORECASE)
_IP_RE = re.compile(r"(?<![0-9A-Fa-f:.])(?:\d{1,3}\.){3}\d{1,3}(?![0-9A-Fa-f:.])|(?<![0-9A-Fa-f:])(?:[0-9A-Fa-f]{0,4}:){2,}[0-9A-Fa-f:]{0,4}(?![0-9A-Fa-f:])")
_TRACE_TIME_RE = re.compile(r"(?:<\s*)?(\d+(?:[.,]\d+)?)\s*(?:ms|мс)?", re.IGNORECASE)
_DNS_THREADS: set[QThread] = set()


def normalize_target(value: str) -> str:
    """Validate an IP literal or DNS hostname before passing it to a process."""
    target = value.strip().rstrip(".")
    if not target or len(target) > 253 or any(char.isspace() for char in target):
        raise ValueError("Enter a valid IP address or hostname.")
    try:
        return str(ipaddress.ip_address(target))
    except ValueError:
        try:
            ascii_target = target.encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise ValueError("Enter a valid IP address or hostname.") from exc
        labels = ascii_target.split(".")
        if any(
            not label
            or len(label) > 63
            or not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?", label)
            for label in labels
        ):
            raise ValueError("Enter a valid IP address or hostname.")
        return ascii_target


def parse_ping_rtt(line: str) -> float | None:
    match = _RTT_RE.search(line) or _RTT_GENERIC_RE.search(line)
    if not match:
        return None
    value = match.group(1).replace(" ", "").replace(",", ".").lstrip("<>")
    try:
        return float(value)
    except ValueError:
        return None


def ping_statistics(samples: list[float], sent: int) -> dict[str, float | int]:
    received = len(samples)
    lost = max(0, int(sent) - received)
    differences = [abs(samples[i] - samples[i - 1]) for i in range(1, received)]
    return {
        "sent": int(sent),
        "received": received,
        "lost": lost,
        "loss_percent": (lost / sent * 100.0) if sent else 0.0,
        "minimum": min(samples) if samples else 0.0,
        "average": sum(samples) / received if received else 0.0,
        "maximum": max(samples) if samples else 0.0,
        "jitter": sum(differences) / len(differences) if differences else 0.0,
    }


def parse_traceroute_line(line: str) -> dict[str, Any] | None:
    match = re.match(r"^\s*(\d+)\s+(.*)$", line)
    if not match:
        return None
    hop = int(match.group(1))
    remainder = match.group(2)
    address = None
    for candidate in _IP_RE.findall(remainder):
        try:
            address = str(ipaddress.ip_address(candidate))
            break
        except ValueError:
            continue
    time_region = remainder.split(address, 1)[0] if address else remainder
    rtts: list[float] = []
    for value in _TRACE_TIME_RE.findall(time_region):
        try:
            rtts.append(float(value.replace(",", ".")))
        except ValueError:
            pass
    if "*" in remainder or not address:
        status = "timeout"
    else:
        status = "reached"
    return {"hop": hop, "address": address, "rtts": rtts, "status": status}


class _ReverseDnsThread(QThread):
    resolved = Signal(str, str)

    def __init__(self, address: str, parent=None):
        super().__init__(parent)
        self.address = address

    def run(self) -> None:
        try:
            hostname = socket.gethostbyaddr(self.address)[0]
        except (OSError, socket.herror, socket.gaierror):
            hostname = ""
        self.resolved.emit(self.address, hostname)


class _TracerouteWorker(QThread):
    hop_ready = Signal(int, str, list, bool, str)
    completed = Signal(bool)
    failed = Signal(str)

    def __init__(
        self,
        target: str,
        method: str,
        max_hops: int,
        timeout_ms: int,
        probes: int,
        port: int,
        family: int,
        parent=None,
    ):
        super().__init__(parent)
        self.target = target
        self.method = method
        self.max_hops = max_hops
        self.timeout_ms = timeout_ms
        self.probes = probes
        self.port = port
        self.family = family
        self._cancelled = threading.Event()

    def cancel(self) -> None:
        self._cancelled.set()

    def run(self) -> None:
        try:
            for ttl in range(1, self.max_hops + 1):
                if self._cancelled.is_set():
                    break
                address = ""
                rtts: list[float] = []
                reached = False
                status = "timeout"
                for _ in range(self.probes):
                    if self._cancelled.is_set():
                        break
                    result = native_traceroute_probe(
                        self.target, self.method, ttl, self.timeout_ms, self.port, self.family
                    )
                    if result["address"] and not address:
                        address = result["address"]
                    if result["rtt"] is not None:
                        rtts.append(result["rtt"])
                    if result["reached"]:
                        reached = True
                        status = "destination"
                        break
                    status = result["status"]
                if self._cancelled.is_set():
                    break
                self.hop_ready.emit(ttl, address, rtts, reached, status)
                if reached:
                    break
        except Exception as exc:
            self.failed.emit(str(exc))
        self.completed.emit(self._cancelled.is_set())


class DiagnosticsDialog(QDialog):
    """Cancellable Windows ping and ICMP traceroute tools."""

    def __init__(self, language: str, parent=None):
        super().__init__(parent)
        self.language = language
        self._ping_process: QProcess | None = None
        self._trace_process: QProcess | None = None
        self._trace_worker: _TracerouteWorker | None = None
        self._trace_failed = False
        self._ping_samples: list[float] = []
        self._ping_sent = 0
        self._ping_buffer = ""
        self._trace_buffer = ""
        self._dns_threads: list[_ReverseDnsThread] = []
        self.setWindowTitle(self.t("diagnostics_title"))
        self.resize(820, 620)
        self.setMinimumSize(680, 500)

        root = QVBoxLayout(self)
        self.tabs = QTabWidget()
        root.addWidget(self.tabs)

        ping_page = QWidget()
        ping_layout = QVBoxLayout(ping_page)
        ping_form = QFormLayout()
        self.ping_target = QLineEdit()
        self.ping_target.setPlaceholderText(self.t("diagnostics_target_placeholder"))
        self.ping_family = QComboBox()
        self.ping_family.addItem("IPv4", "4")
        self.ping_family.addItem("IPv6", "6")
        self.ping_count = QSpinBox()
        self.ping_count.setRange(1, 1000)
        self.ping_count.setValue(4)
        self.ping_timeout = QSpinBox()
        self.ping_timeout.setRange(250, 10000)
        self.ping_timeout.setSingleStep(250)
        self.ping_timeout.setValue(1200)
        ping_form.addRow(self.t("diagnostics_target"), self.ping_target)
        ping_form.addRow(self.t("diagnostics_family"), self.ping_family)
        ping_form.addRow(self.t("diagnostics_count"), self.ping_count)
        ping_form.addRow(self.t("diagnostics_timeout"), self.ping_timeout)
        ping_layout.addLayout(ping_form)
        ping_actions = QHBoxLayout()
        self.ping_once_button = QPushButton(self.t("diagnostics_ping_once"))
        self.ping_start_button = QPushButton(self.t("diagnostics_ping_start"))
        self.ping_stop_button = QPushButton(self.t("diagnostics_stop"))
        self.ping_stop_button.setEnabled(False)
        self.ping_once_button.clicked.connect(lambda: self.start_ping(continuous=False))
        self.ping_start_button.clicked.connect(lambda: self.start_ping(continuous=True))
        self.ping_stop_button.clicked.connect(self.stop_ping)
        ping_actions.addWidget(self.ping_once_button)
        ping_actions.addWidget(self.ping_start_button)
        ping_actions.addWidget(self.ping_stop_button)
        ping_actions.addStretch(1)
        ping_layout.addLayout(ping_actions)
        self.ping_status = QLabel(self.t("diagnostics_idle"))
        self.ping_status.setWordWrap(True)
        ping_layout.addWidget(self.ping_status)
        self.ping_stats = QLabel(self.t("diagnostics_no_samples"))
        self.ping_stats.setWordWrap(True)
        ping_layout.addWidget(self.ping_stats)
        ping_layout.addStretch(1)
        self.tabs.addTab(ping_page, self.t("diagnostics_ping_tab"))

        trace_page = QWidget()
        trace_layout = QVBoxLayout(trace_page)
        trace_form = QFormLayout()
        self.trace_target = QLineEdit()
        self.trace_target.setPlaceholderText(self.t("diagnostics_target_placeholder"))
        self.trace_family = QComboBox()
        self.trace_family.addItem("IPv4", "4")
        self.trace_family.addItem("IPv6", "6")
        self.trace_method = QComboBox()
        self.trace_method.addItem("ICMP", "icmp")
        self.trace_method.addItem("UDP", "udp")
        self.trace_method.addItem("TCP", "tcp")
        if not NATIVE_TRACEROUTE:
            for index in (1, 2):
                item = self.trace_method.model().item(index)
                if item is not None:
                    item.setEnabled(False)
                    item.setToolTip(self.t("diagnostics_method_unavailable"))
        self.trace_hops = QSpinBox()
        self.trace_hops.setRange(1, 64)
        self.trace_hops.setValue(30)
        self.trace_timeout = QSpinBox()
        self.trace_timeout.setRange(250, 10000)
        self.trace_timeout.setSingleStep(250)
        self.trace_timeout.setValue(1000)
        self.trace_probes = QSpinBox()
        self.trace_probes.setRange(1, 3)
        self.trace_probes.setValue(3)
        self.trace_port = QSpinBox()
        self.trace_port.setRange(1, 65535)
        self.trace_port.setValue(443)
        trace_form.addRow(self.t("diagnostics_target"), self.trace_target)
        trace_form.addRow(self.t("diagnostics_family"), self.trace_family)
        trace_form.addRow(self.t("diagnostics_method"), self.trace_method)
        trace_form.addRow(self.t("diagnostics_max_hops"), self.trace_hops)
        trace_form.addRow(self.t("diagnostics_timeout"), self.trace_timeout)
        trace_form.addRow(self.t("diagnostics_trace_probes"), self.trace_probes)
        trace_form.addRow(self.t("diagnostics_trace_port"), self.trace_port)
        trace_layout.addLayout(trace_form)
        trace_actions = QHBoxLayout()
        self.trace_start_button = QPushButton(self.t("diagnostics_trace_start"))
        self.trace_stop_button = QPushButton(self.t("diagnostics_stop"))
        self.trace_stop_button.setEnabled(False)
        self.trace_start_button.clicked.connect(self.start_traceroute)
        self.trace_stop_button.clicked.connect(self.stop_traceroute)
        trace_actions.addWidget(self.trace_start_button)
        trace_actions.addWidget(self.trace_stop_button)
        trace_actions.addStretch(1)
        trace_layout.addLayout(trace_actions)
        self.trace_status = QLabel(self.t("diagnostics_idle"))
        self.trace_status.setWordWrap(True)
        trace_layout.addWidget(self.trace_status)
        self.trace_table = QTableWidget(0, 5)
        self.trace_table.setHorizontalHeaderLabels([
            self.t("diagnostics_hop"), self.t("diagnostics_ip"),
            self.t("diagnostics_hostname"), self.t("diagnostics_rtt"),
            self.t("diagnostics_status"),
        ])
        self.trace_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.trace_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.trace_table.setAlternatingRowColors(True)
        self.trace_table.verticalHeader().setVisible(False)
        self.trace_table.horizontalHeader().setStretchLastSection(True)
        trace_layout.addWidget(self.trace_table, 1)
        note = QLabel(self.t("diagnostics_trace_note"))
        note.setWordWrap(True)
        trace_layout.addWidget(note)
        self.tabs.addTab(trace_page, self.t("diagnostics_trace_tab"))

    def t(self, key: str, **values: object) -> str:
        return tr(key, self.language, **values)

    def _target_and_family(self, edit: QLineEdit, family_combo: QComboBox) -> str:
        target = normalize_target(edit.text())
        try:
            address = ipaddress.ip_address(target)
        except ValueError:
            return target
        expected = int(family_combo.currentData())
        if address.version != expected:
            raise ValueError(self.t("diagnostics_family_mismatch"))
        return target

    def start_ping(self, continuous: bool) -> None:
        if self._ping_process is not None:
            return
        try:
            target = self._target_and_family(self.ping_target, self.ping_family)
        except ValueError as exc:
            self.ping_status.setText(str(exc))
            return
        args = [f"-{self.ping_family.currentData()}"]
        if continuous:
            args.extend(["-t", "-w", str(self.ping_timeout.value())])
        else:
            args.extend(["-n", str(self.ping_count.value()), "-w", str(self.ping_timeout.value())])
        args.append(target)
        process = QProcess(self)
        process.setProgram("ping")
        process.setArguments(args)
        process.readyReadStandardOutput.connect(self._read_ping_output)
        process.finished.connect(self._ping_finished)
        process.errorOccurred.connect(self._ping_error)
        self._ping_process = process
        self._ping_samples = []
        self._ping_sent = 0
        self._ping_buffer = ""
        self.ping_stats.setText(self.t("diagnostics_no_samples"))
        self.ping_status.setText(self.t("diagnostics_running", target=target))
        self.ping_once_button.setEnabled(False)
        self.ping_start_button.setEnabled(False)
        self.ping_stop_button.setEnabled(True)
        process.start()

    def _read_ping_output(self) -> None:
        process = self._ping_process
        if process is None:
            return
        chunk = bytes(process.readAllStandardOutput()).decode("utf-8", errors="replace")
        self._ping_buffer += chunk
        lines = self._ping_buffer.splitlines()
        self._ping_buffer = lines.pop() if lines else self._ping_buffer
        for line in lines:
            self._consume_ping_line(line)

    def _consume_ping_line(self, line: str) -> None:
        if not line.strip():
            return
        if "reply from" in line.casefold() or "ответ от" in line.casefold():
            self._ping_sent += 1
            rtt = parse_ping_rtt(line)
            if rtt is not None:
                self._ping_samples.append(rtt)
        elif any(token in line.casefold() for token in (
            "request timed out", "превышен интервал ожидания", "destination host unreachable",
            "узел назначения недоступен", "general failure", "сбой передачи",
        )):
            self._ping_sent += 1
        if self._ping_sent:
            self.ping_stats.setText(self._format_ping_stats())

    def _format_ping_stats(self) -> str:
        stats = ping_statistics(self._ping_samples, self._ping_sent)
        return self.t(
            "diagnostics_stats",
            sent=stats["sent"], received=stats["received"], lost=stats["lost"],
            loss=f'{stats["loss_percent"]:.1f}',
            minimum=f'{stats["minimum"]:.2f}', average=f'{stats["average"]:.2f}',
            maximum=f'{stats["maximum"]:.2f}', jitter=f'{stats["jitter"]:.2f}',
        )

    def _ping_finished(self, _code: int = 0, _status: QProcess.ExitStatus = QProcess.ExitStatus.NormalExit) -> None:
        process = self._ping_process
        if process is None:
            return
        self._read_ping_output()
        if self._ping_buffer:
            self._consume_ping_line(self._ping_buffer)
            self._ping_buffer = ""
        self._ping_process = None
        process.deleteLater()
        self._set_ping_idle()
        if self._ping_sent == 0:
            self.ping_status.setText(self.t("diagnostics_ping_no_reply"))
        else:
            self.ping_status.setText(self.t("diagnostics_finished"))

    def _ping_error(self, _error: QProcess.ProcessError) -> None:
        if self._ping_process is None:
            return
        self.ping_status.setText(self.t("diagnostics_process_error"))
        process = self._ping_process
        self._ping_process = None
        if process is not None:
            process.deleteLater()
        self._set_ping_idle()

    def _set_ping_idle(self) -> None:
        self.ping_once_button.setEnabled(True)
        self.ping_start_button.setEnabled(True)
        self.ping_stop_button.setEnabled(False)

    def stop_ping(self) -> None:
        if self._ping_process is None:
            return
        process = self._ping_process
        process.terminate()
        QTimer.singleShot(1200, lambda proc=process: proc.kill() if proc.state() != QProcess.ProcessState.NotRunning else None)

    def start_traceroute(self) -> None:
        if self._trace_process is not None or self._trace_worker is not None:
            return
        try:
            target = self._target_and_family(self.trace_target, self.trace_family)
        except ValueError as exc:
            self.trace_status.setText(str(exc))
            return
        method = str(self.trace_method.currentData())
        if NATIVE_TRACEROUTE:
            self._start_native_traceroute(target, method)
            return
        if method != "icmp":
            self.trace_status.setText(self.t("diagnostics_method_unavailable"))
            return
        args = ["-d", f"-{self.trace_family.currentData()}",
                "-h", str(self.trace_hops.value()), "-w", str(self.trace_timeout.value()), target]
        process = QProcess(self)
        process.setProgram("tracert")
        process.setArguments(args)
        process.readyReadStandardOutput.connect(self._read_trace_output)
        process.finished.connect(self._trace_finished)
        process.errorOccurred.connect(self._trace_error)
        self._trace_process = process
        self._trace_buffer = ""
        self.trace_table.setRowCount(0)
        self.trace_status.setText(self.t("diagnostics_trace_running", target=target))
        self.trace_start_button.setEnabled(False)
        self.trace_stop_button.setEnabled(True)
        process.start()


    def _start_native_traceroute(self, target: str, method: str) -> None:
        self._trace_failed = False
        self._trace_buffer = ""
        self.trace_table.setRowCount(0)
        self.trace_status.setText(
            self.t("diagnostics_trace_native_running", target=target, method=method.upper())
        )
        self.trace_start_button.setEnabled(False)
        self.trace_stop_button.setEnabled(True)
        worker = _TracerouteWorker(
            target,
            method,
            self.trace_hops.value(),
            self.trace_timeout.value(),
            self.trace_probes.value(),
            self.trace_port.value(),
            int(self.trace_family.currentData()),
            self,
        )
        worker.hop_ready.connect(self._add_native_trace_hop)
        worker.completed.connect(self._native_trace_completed)
        worker.failed.connect(self._native_trace_failed)
        worker.finished.connect(self._native_trace_thread_finished)
        self._trace_worker = worker
        worker.start()

    def _add_native_trace_hop(
        self, hop: int, address: str, rtts: list, reached: bool, status: str
    ) -> None:
        row = self.trace_table.rowCount()
        self.trace_table.insertRow(row)
        rtt_text = " / ".join(f"{float(value):.2f} ms" for value in rtts) if rtts else "—"
        if status == "timeout" and not address:
            status_text = self.t("diagnostics_hop_timeout")
        elif reached:
            status_text = self.t("diagnostics_trace_destination")
        else:
            status_text = self.t("diagnostics_hop_replied")
        values = [str(hop), address or "—", "—", rtt_text, status_text]
        for column, value in enumerate(values):
            self.trace_table.setItem(row, column, QTableWidgetItem(value))
        if address:
            worker = _ReverseDnsThread(address)
            _DNS_THREADS.add(worker)
            worker.resolved.connect(self._set_reverse_dns)
            worker.finished.connect(lambda thread=worker: self._release_dns_thread(thread))
            self._dns_threads.append(worker)
            worker.start()

    def _native_trace_completed(self, cancelled: bool) -> None:
        if self._trace_failed:
            return
        if cancelled:
            self.trace_status.setText(self.t("diagnostics_trace_cancelled"))
        elif self.trace_table.rowCount():
            self.trace_status.setText(self.t("diagnostics_trace_finished"))
        else:
            self.trace_status.setText(self.t("diagnostics_trace_no_hops"))

    def _native_trace_failed(self, message: str) -> None:
        self._trace_failed = True
        self.trace_status.setText(f"{self.t('diagnostics_process_error')} {message}")

    def _native_trace_thread_finished(self) -> None:
        worker = self._trace_worker
        self._trace_worker = None
        self.trace_start_button.setEnabled(True)
        self.trace_stop_button.setEnabled(False)
        if worker is not None:
            worker.deleteLater()

    def _read_trace_output(self) -> None:
        process = self._trace_process
        if process is None:
            return
        chunk = bytes(process.readAllStandardOutput()).decode("utf-8", errors="replace")
        self._trace_buffer += chunk
        lines = self._trace_buffer.splitlines()
        self._trace_buffer = lines.pop() if lines else self._trace_buffer
        for line in lines:
            self._add_trace_line(line)

    def _add_trace_line(self, line: str) -> None:
        parsed = parse_traceroute_line(line)
        if parsed is None:
            return
        row = self.trace_table.rowCount()
        self.trace_table.insertRow(row)
        rtts = parsed["rtts"]
        rtt_text = " / ".join(f"{value:.2f} ms" for value in rtts) if rtts else "—"
        values = [
            str(parsed["hop"]), parsed["address"] or "—", "—", rtt_text,
            self.t("diagnostics_hop_timeout") if parsed["status"] == "timeout"
            else self.t("diagnostics_hop_replied"),
        ]
        for column, value in enumerate(values):
            self.trace_table.setItem(row, column, QTableWidgetItem(value))
        address = parsed["address"]
        if address:
            worker = _ReverseDnsThread(address)
            _DNS_THREADS.add(worker)
            worker.resolved.connect(self._set_reverse_dns)
            worker.finished.connect(lambda thread=worker: self._release_dns_thread(thread))
            self._dns_threads.append(worker)
            worker.start()

    def _set_reverse_dns(self, address: str, hostname: str) -> None:
        if not hostname:
            return
        for row in range(self.trace_table.rowCount()):
            item = self.trace_table.item(row, 1)
            if item is not None and item.text() == address:
                self.trace_table.setItem(row, 2, QTableWidgetItem(hostname))

    def _release_dns_thread(self, thread: _ReverseDnsThread) -> None:
        if thread in self._dns_threads:
            self._dns_threads.remove(thread)
        _DNS_THREADS.discard(thread)
        thread.deleteLater()

    def _trace_finished(self, _code: int = 0, _status: QProcess.ExitStatus = QProcess.ExitStatus.NormalExit) -> None:
        process = self._trace_process
        if process is None:
            return
        self._read_trace_output()
        if self._trace_buffer:
            self._add_trace_line(self._trace_buffer)
            self._trace_buffer = ""
        self._trace_process = None
        process.deleteLater()
        self.trace_start_button.setEnabled(True)
        self.trace_stop_button.setEnabled(False)
        self.trace_status.setText(self.t("diagnostics_trace_finished"))

    def _trace_error(self, _error: QProcess.ProcessError) -> None:
        if self._trace_process is None:
            return
        process = self._trace_process
        self._trace_process = None
        if process is not None:
            process.deleteLater()
        self.trace_start_button.setEnabled(True)
        self.trace_stop_button.setEnabled(False)
        self.trace_status.setText(self.t("diagnostics_process_error"))

    def stop_traceroute(self) -> None:
        if self._trace_worker is not None:
            self._trace_worker.cancel()
            self.trace_stop_button.setEnabled(False)
            self.trace_status.setText(self.t("diagnostics_trace_stop_wait"))
            return
        if self._trace_process is None:
            return
        process = self._trace_process
        process.terminate()
        QTimer.singleShot(1200, lambda proc=process: proc.kill() if proc.state() != QProcess.ProcessState.NotRunning else None)

    def closeEvent(self, event) -> None:
        if self._trace_worker is not None:
            self._trace_worker.cancel()
            self._trace_worker.wait(12_000)
        for process in (self._ping_process, self._trace_process):
            if process is not None:
                process.kill()
                process.waitForFinished(500)
        for thread in list(self._dns_threads):
            thread.wait(500)
        # DNS workers are kept alive globally until their lookup finishes.
        super().closeEvent(event)
