from __future__ import annotations

import pytest

from swarmatlas.internet_monitor import expand_target


def test_expand_single_ipv4_and_ipv6_addresses() -> None:
    assert expand_target("1.1.1.1") == ["1.1.1.1"]
    assert expand_target("2001:4860:4860::8888") == ["2001:4860:4860::8888"]


def test_expand_cidr_with_bounded_hosts() -> None:
    result = expand_target("192.0.2.0/29", 16)
    assert result == ["192.0.2.1", "192.0.2.2", "192.0.2.3", "192.0.2.4", "192.0.2.5", "192.0.2.6"]


def test_large_cidr_is_safely_truncated_to_configured_limit() -> None:
    result = expand_target("1.1.1.1/17", 256)
    assert len(result) == 256
    assert result[0] == "1.1.0.1"
    assert result[-1] == "1.1.1.0"


def test_hostname_is_preserved_for_dns_resolution() -> None:
    assert expand_target("cloudflare.com") == ["cloudflare.com"]


@pytest.mark.parametrize("value", ["", "999.999.999.999", "192.168.1.1/99"])
def test_invalid_targets_are_rejected(value: str) -> None:
    with pytest.raises(ValueError):
        expand_target(value)
