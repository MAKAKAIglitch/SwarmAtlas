from __future__ import annotations

import pytest

from swarmatlas.diagnostics_ui import (
    normalize_target,
    parse_ping_rtt,
    parse_traceroute_line,
    ping_statistics,
)


def test_normalize_target_accepts_ipv4_ipv6_and_idna_hostname() -> None:
    assert normalize_target("  1.1.1.1 ") == "1.1.1.1"
    assert normalize_target("2001:4860:4860::8888") == "2001:4860:4860::8888"
    assert normalize_target("пример.рф") == "xn--e1afmkfd.xn--p1ai"


@pytest.mark.parametrize("value", ["", "bad host", "-n 5 127.0.0.1", "a..b", "x" * 254])
def test_normalize_target_rejects_invalid_input(value: str) -> None:
    with pytest.raises(ValueError):
        normalize_target(value)


def test_parse_ping_rtt_handles_english_and_russian_output() -> None:
    assert parse_ping_rtt("Reply from 1.1.1.1: bytes=32 time=12ms TTL=57") == 12.0
    assert parse_ping_rtt("Ответ от 1.1.1.1: число байт=32 время<1мс TTL=57") == 1.0
    assert parse_ping_rtt("Request timed out.") is None


def test_ping_statistics_reports_loss_and_mean_absolute_jitter() -> None:
    result = ping_statistics([10.0, 14.0, 12.0], 4)
    assert result == {
        "sent": 4,
        "received": 3,
        "lost": 1,
        "loss_percent": 25.0,
        "minimum": 10.0,
        "average": 12.0,
        "maximum": 14.0,
        "jitter": 3.0,
    }


def test_parse_traceroute_line_parses_ipv4_hop_and_timeout() -> None:
    reply = parse_traceroute_line("  2    3 ms    4 ms    5 ms  192.0.2.1")
    assert reply == {
        "hop": 2,
        "address": "192.0.2.1",
        "rtts": [3.0, 4.0, 5.0],
        "status": "reached",
    }
    timeout = parse_traceroute_line("  3     *        *        *     Request timed out.")
    assert timeout == {"hop": 3, "address": None, "rtts": [], "status": "timeout"}


def test_parse_traceroute_line_parses_ipv6_hop() -> None:
    reply = parse_traceroute_line("  1    1 ms    1 ms    1 ms  2001:4860:4860::8888")
    assert reply is not None
    assert reply["address"] == "2001:4860:4860::8888"
    assert reply["status"] == "reached"


def test_native_traceroute_wrapper_validates_and_normalizes_result(monkeypatch) -> None:
    from swarmatlas import native

    class FakeNative:
        @staticmethod
        def traceroute_probe(target, method, ttl, timeout_ms, port, family):
            assert (target, method, ttl, timeout_ms, port, family) == (
                "example.com", "tcp", 2, 750, 8443, 6
            )
            return ("2001:db8::1", 12.5, False, "replied")

    monkeypatch.setattr(native, "_native", FakeNative())
    monkeypatch.setattr(native, "NATIVE_TRACEROUTE", True)
    assert native.traceroute_probe(
        "example.com", "tcp", 2, 750, 8443, 6
    ) == {
        "address": "2001:db8::1",
        "rtt": 12.5,
        "reached": False,
        "status": "replied",
    }


def test_native_traceroute_wrapper_rejects_invalid_family(monkeypatch) -> None:
    from swarmatlas import native

    monkeypatch.setattr(native, "NATIVE_TRACEROUTE", True)
    monkeypatch.setattr(native, "_native", object())
    with pytest.raises(ValueError, match="family"):
        native.traceroute_probe("example.com", "icmp", 1, family=5)
