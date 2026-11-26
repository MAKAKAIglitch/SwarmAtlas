from __future__ import annotations

import pytest

from swarmatlas.internet_monitor import expand_target


def test_expand_single_ipv4_and_ipv6_addresses() -> None:
    assert expand_target("1.1.1.1") == ["1.1.1.1"]
    assert expand_target("2001:4860:4860::8888") == ["2001:4860:4860::8888"]


def test_expand_cidr_with_bounded_hosts() -> None:
    result = expand_target("192.0.2.0/29", 16)
    assert result == ["192.0.2.1", "192.0.2.2", "192.0.2.3", "192.0.2.4", "192.0.2.5", "192.0.2.6"]


def test_cidr_limit_is_enforced_before_scanning() -> None:
    with pytest.raises(ValueError, match="лимит"):
        expand_target("1.1.1.1/17", 256)


def test_hostname_is_preserved_for_dns_resolution() -> None:
    assert expand_target("cloudflare.com") == ["cloudflare.com"]


@pytest.mark.parametrize("value", ["", "999.999.999.999", "192.168.1.1/99"])
def test_invalid_targets_are_rejected(value: str) -> None:
    with pytest.raises(ValueError):
        expand_target(value)
