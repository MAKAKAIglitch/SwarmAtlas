from pathlib import Path

from swarmatlas.storage import Store
from swarmatlas.utils import counter_delta, format_bytes


def test_format_bytes_uses_decimal_network_units() -> None:
    assert format_bytes(1_000) == "1.00 KB"
    assert format_bytes(1_000_000) == "1.00 MB"


def test_counter_delta_handles_normal_increase_and_reset() -> None:
    assert counter_delta(150, 100) == 50
    assert counter_delta(20, 100) == 20


def test_store_accumulates_interface_counters(tmp_path: Path) -> None:
    store = Store(tmp_path / "test.sqlite3")
    first = store.record_interface("Ethernet", 10_000, 20_000, 1.0)
    second = store.record_interface("Ethernet", 12_500, 24_000, 2.0)

    assert first["upload_delta"] == 0
    assert first["download_delta"] == 0
    assert second["upload_delta"] == 2_500
    assert second["download_delta"] == 4_000
    assert second["upload_total"] == 2_500
    assert second["download_total"] == 4_000
    store.close()
