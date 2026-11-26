from __future__ import annotations

import ipaddress
import json
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import QThread, Signal, Qt
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QInputDialog,
    QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from .config import app_data_dir
from .native import ping_host


DEFAULT_PROFILES: dict[str, list[str]] = {
    "General": ["1.1.1.1", "8.8.8.8", "9.9.9.9"],
    "DNS": ["1.1.1.1", "1.0.0.1", "8.8.8.8", "8.8.4.4", "9.9.9.9"],
    "Gaming": ["1.1.1.1", "8.8.8.8"],
    "Custom": [],
}
MAX_CIDR_HOSTS = 256


def expand_target(value: str, limit: int = MAX_CIDR_HOSTS) -> list[str]:
    value = value.strip()
    if not value:
        raise ValueError("Введите IP-адрес, hostname или CIDR")
    try:
        if "/" in value:
            network = ipaddress.ip_network(value, strict=False)
            if network.num_addresses > limit:
                raise ValueError(
                    f"Подсеть содержит {network.num_addresses} адресов. "
                    f"Текущий лимит — {limit}; уменьшите диапазон."
                )
            if network.num_addresses <= 2:
                addresses = list(network)
            else:
                addresses = list(network.hosts())
            return [str(address) for address in addresses[:limit]]
        address = ipaddress.ip_address(value)
        return [str(address)]
    except ValueError as exc:
        if "/" in value or _looks_like_ip(value):
            raise ValueError(str(exc)) from exc
        if len(value) > 253 or any(not part or len(part) > 63 for part in value.split(".")):
            raise ValueError("Некорректное имя узла")
        return [value]


def _looks_like_ip(value: str) -> bool:
    return all(char.isdigit() or char in ".:" for char in value)


class PingCycleWorker(QThread):
    result_ready = Signal(dict)
    cycle_finished = Signal(int)
    worker_error = Signal(str)

    def __init__(self, targets: list[str], interval_seconds: int, timeout_ms: int = 1200):
        super().__init__()
        self.targets = list(targets)
        self.interval_seconds = max(10, int(interval_seconds))
        self.timeout_ms = max(100, min(10_000, int(timeout_ms)))
        self._stop_event = threading.Event()

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        while not self._stop_event.is_set():
            cycle_started = time.monotonic()
            completed = 0
            for target in self.targets:
                if self._stop_event.is_set():
                    break
                checked_at = datetime.now().astimezone().isoformat(timespec="seconds")
                try:
                    response = ping_host(target, self.timeout_ms)
                    record = {
                        "target": target,
                        "address": response.get("address") or target,
                        "rtt_ms": response.get("rtt_ms"),
                        "status": response.get("status", "unknown"),
                        "checked_at": checked_at,
                    }
                except Exception as exc:
                    record = {
                        "target": target,
                        "address": target,
                        "rtt_ms": None,
                        "status": f"error: {type(exc).__name__}: {exc}",
                        "checked_at": checked_at,
                    }
                self.result_ready.emit(record)
                completed += 1
            self.cycle_finished.emit(completed)
            remaining = max(0.0, self.interval_seconds - (time.monotonic() - cycle_started))
            if self._stop_event.wait(remaining):
                break


class InternetMonitorWidget(QWidget):
    """Sequential ICMP monitor with persistent per-host history and profiles."""

    def __init__(self, palette: dict[str, str], parent=None):
        super().__init__(parent)
        self.palette = palette
        self.data_dir = Path(app_data_dir())
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "internet_monitor.sqlite3"
        self.profiles_path = self.data_dir / "internet_monitor_profiles.json"
        self.profiles = self._load_profiles()
        self.targets: list[str] = []
        self.worker: PingCycleWorker | None = None
        self._init_database()
        self._build_ui()
        self.profile_combo.setCurrentText("General")
        self._apply_profile("General")
        self._refresh_table()
        self._refresh_history()

    def _load_profiles(self) -> dict[str, list[str]]:
        profiles = {key: list(value) for key, value in DEFAULT_PROFILES.items()}
        try:
            stored = json.loads(self.profiles_path.read_text(encoding="utf-8"))
            for name, values in stored.items():
                if isinstance(name, str) and isinstance(values, list):
                    profiles[name] = list(dict.fromkeys(str(v) for v in values if str(v).strip()))
        except (OSError, ValueError, TypeError):
            pass
        return profiles

    def _save_profiles(self) -> None:
        self.profiles_path.write_text(
            json.dumps(self.profiles, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def _init_database(self) -> None:
        with sqlite3.connect(self.db_path) as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS ping_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    target TEXT NOT NULL,
                    address TEXT NOT NULL,
                    checked_at TEXT NOT NULL,
                    rtt_ms REAL,
                    status TEXT NOT NULL
                )
            """)
            db.execute("CREATE INDEX IF NOT EXISTS idx_ping_history_target_time ON ping_history(target, id DESC)")
            db.commit()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        heading = QHBoxLayout()
        title = QLabel("Internet Monitor")
        title.setStyleSheet(f"color:{self.palette['text']};font-size:15px;font-weight:700;background:transparent")
        heading.addWidget(title)
        heading.addStretch(1)
        self.state_label = QLabel("Остановлен")
        self.state_label.setStyleSheet(f"color:{self.palette['muted']};background:transparent")
        heading.addWidget(self.state_label)
        root.addLayout(heading)

        controls = QHBoxLayout()
        self.profile_combo = QComboBox()
        self.profile_combo.addItems(list(self.profiles))
        self.profile_combo.currentTextChanged.connect(self._apply_profile)
        controls.addWidget(QLabel("Профиль"))
        controls.addWidget(self.profile_combo)
        self.interval_combo = QComboBox()
        for label, seconds in [("1 мин", 60), ("5 мин", 300), ("10 мин", 600), ("15 мин", 900), ("30 мин", 1800)]:
            self.interval_combo.addItem(label, seconds)
        self.interval_combo.setCurrentIndex(2)
        controls.addWidget(QLabel("Интервал"))
        controls.addWidget(self.interval_combo)
        self.start_button = QPushButton("Запустить")
        self.start_button.clicked.connect(self.start_monitoring)
        controls.addWidget(self.start_button)
        self.stop_button = QPushButton("Остановить")
        self.stop_button.clicked.connect(self.stop_monitoring)
        self.stop_button.setEnabled(False)
        controls.addWidget(self.stop_button)
        root.addLayout(controls)

        add_row = QHBoxLayout()
        self.target_input = QLineEdit()
        self.target_input.setPlaceholderText("IP, hostname или CIDR — например 1.1.1.1/24")
        self.target_input.returnPressed.connect(self.add_target)
        add_row.addWidget(self.target_input, 1)
        self.cidr_limit = QSpinBox()
        self.cidr_limit.setRange(1, 4096)
        self.cidr_limit.setValue(MAX_CIDR_HOSTS)
        self.cidr_limit.setToolTip("Максимум адресов, разворачиваемых из одной подсети")
        add_row.addWidget(self.cidr_limit)
        add_button = QPushButton("Добавить")
        add_button.clicked.connect(self.add_target)
        add_row.addWidget(add_button)
        remove_button = QPushButton("Удалить выбранные")
        remove_button.clicked.connect(self.remove_selected)
        add_row.addWidget(remove_button)
        save_profile_button = QPushButton("Сохранить профиль как…")
        save_profile_button.clicked.connect(self.save_profile_as)
        add_row.addWidget(save_profile_button)
        root.addLayout(add_row)

        self.summary_label = QLabel("Добавьте адреса для мониторинга. Проверки выполняются последовательно.")
        self.summary_label.setStyleSheet(f"color:{self.palette['muted']};background:transparent")
        root.addWidget(self.summary_label)

        self.target_table = QTableWidget(0, 6)
        self.target_table.setHorizontalHeaderLabels(["Адрес", "Состояние", "Последний RTT", "Min / Avg / Max", "Потери (10)", "Последняя проверка"])
        self.target_table.horizontalHeader().setStretchLastSection(True)
        self.target_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.target_table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self.target_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.target_table.verticalHeader().setVisible(False)
        self.target_table.setMinimumHeight(210)
        root.addWidget(self.target_table)

        history_title = QLabel("История последних проверок")
        history_title.setStyleSheet(f"color:{self.palette['text']};font-weight:600;background:transparent")
        root.addWidget(history_title)
        self.history_table = QTableWidget(0, 4)
        self.history_table.setHorizontalHeaderLabels(["Время", "Адрес", "Результат", "RTT"])
        self.history_table.horizontalHeader().setStretchLastSection(True)
        self.history_table.verticalHeader().setVisible(False)
        self.history_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.history_table.setMaximumHeight(190)
        root.addWidget(self.history_table)

    def _apply_profile(self, name: str) -> None:
        if self.worker and self.worker.isRunning():
            return
        self.targets = list(self.profiles.get(name, []))
        self._refresh_table()

    def add_target(self) -> None:
        raw = self.target_input.text().strip()
        try:
            expanded = expand_target(raw, self.cidr_limit.value())
        except ValueError as exc:
            QMessageBox.warning(self, "Некорректный адрес", str(exc))
            return
        added = [item for item in expanded if item not in self.targets]
        self.targets.extend(added)
        self.target_input.clear()
        self.profile_combo.blockSignals(True)
        if self.profile_combo.currentText() != "Custom":
            self.profile_combo.setCurrentText("Custom")
        self.profile_combo.blockSignals(False)
        self.profiles["Custom"] = list(self.targets)
        self._save_profiles()
        self._refresh_table()
        self.summary_label.setText(f"Добавлено адресов: {len(added)}. Всего в профиле: {len(self.targets)}.")

    def save_profile_as(self) -> None:
        name, accepted = QInputDialog.getText(self, "Новый профиль", "Название профиля:")
        name = name.strip()
        if not accepted or not name:
            return
        self.profiles[name] = list(self.targets)
        self._save_profiles()
        self.profile_combo.blockSignals(True)
        if self.profile_combo.findText(name) < 0:
            self.profile_combo.addItem(name)
        self.profile_combo.setCurrentText(name)
        self.profile_combo.blockSignals(False)
        self.summary_label.setText(f"Профиль «{name}» сохранён ({len(self.targets)} адресов).")

    def remove_selected(self) -> None:
        selected = {self.target_table.item(index.row(), 0).text() for index in self.target_table.selectionModel().selectedRows()}
        if not selected:
            return
        self.targets = [target for target in self.targets if target not in selected]
        self.profiles["Custom"] = list(self.targets)
        self._save_profiles()
        self._refresh_table()

    def start_monitoring(self) -> None:
        if not self.targets:
            QMessageBox.information(self, "Internet Monitor", "Сначала добавьте хотя бы один адрес.")
            return
        if self.worker and self.worker.isRunning():
            return
        self.worker = PingCycleWorker(self.targets, int(self.interval_combo.currentData() or 600))
        self.worker.result_ready.connect(self._on_result)
        self.worker.cycle_finished.connect(self._on_cycle_finished)
        self.worker.worker_error.connect(self._on_error)
        self.worker.start()
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.profile_combo.setEnabled(False)
        self.state_label.setText("Работает · последовательная проверка")
        self.state_label.setStyleSheet(f"color:{self.palette['accent']};background:transparent")

    def stop_monitoring(self) -> None:
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.worker.wait(3000)
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.profile_combo.setEnabled(True)
        self.state_label.setText("Остановлен")
        self.state_label.setStyleSheet(f"color:{self.palette['muted']};background:transparent")

    def _on_result(self, record: dict[str, Any]) -> None:
        with sqlite3.connect(self.db_path) as db:
            db.execute(
                "INSERT INTO ping_history(target,address,checked_at,rtt_ms,status) VALUES(?,?,?,?,?)",
                (record["target"], record["address"], record["checked_at"], record["rtt_ms"], record["status"]),
            )
            db.commit()
        self._refresh_table()
        self._refresh_history()

    def _refresh_history(self) -> None:
        with sqlite3.connect(self.db_path) as db:
            rows = db.execute(
                "SELECT checked_at,address,status,rtt_ms FROM ping_history ORDER BY id DESC LIMIT 100"
            ).fetchall()
        self.history_table.setRowCount(len(rows))
        for row, record in enumerate(rows):
            values = [record[0], record[1], record[2], f"{record[3]:.2f} ms" if record[3] is not None else "—"]
            for column, value in enumerate(values):
                self.history_table.setItem(row, column, QTableWidgetItem(str(value)))

    def _on_cycle_finished(self, completed: int) -> None:
        self.summary_label.setText(f"Цикл завершён: {completed} адресов. Следующая проверка через выбранный интервал.")

    def _on_error(self, message: str) -> None:
        self.summary_label.setText(message)

    def _refresh_table(self) -> None:
        self.target_table.setRowCount(len(self.targets))
        with sqlite3.connect(self.db_path) as db:
            for row, target in enumerate(self.targets):
                history = db.execute(
                    "SELECT rtt_ms,status,checked_at FROM ping_history WHERE target=? ORDER BY id DESC LIMIT 10",
                    (target,),
                ).fetchall()
                success = sum(1 for rtt, status, _ in history if rtt is not None and status == "reply")
                loss = 100.0 * (len(history) - success) / len(history) if history else 0.0
                if not history:
                    state = "Не проверено"
                elif success == 0:
                    state = "Проблема"
                elif loss > 0:
                    state = "Частично доступно"
                else:
                    state = "Норма"
                latest_rtt, _, latest_time = history[0] if history else (None, "", "")
                samples = [float(rtt) for rtt, status, _ in history if rtt is not None and status == "reply"]
                stats = (
                    f"{min(samples):.1f} / {sum(samples) / len(samples):.1f} / {max(samples):.1f} ms"
                    if samples else "—"
                )
                values = [
                    target, state,
                    f"{latest_rtt:.2f} ms" if latest_rtt is not None else "—",
                    stats,
                    f"{loss:.0f}%" if history else "—",
                    latest_time,
                ]
                for column, value in enumerate(values):
                    self.target_table.setItem(row, column, QTableWidgetItem(value))
        self.target_table.resizeColumnsToContents()

    def closeEvent(self, event) -> None:
        self.stop_monitoring()
        super().closeEvent(event)
