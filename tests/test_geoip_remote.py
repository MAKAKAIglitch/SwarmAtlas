from __future__ import annotations

from swarmatlas.geoip_remote import RemoteGeoLocator, _normalize


def test_ip_api_response_is_normalized() -> None:
    result = _normalize(
        "ip-api",
        {
            "status": "success",
            "country": "United States",
            "countryCode": "US",
            "regionName": "California",
            "city": "Mountain View",
            "lat": 37.4,
            "lon": -122.0,
            "org": "Google LLC",
            "as": "AS15169 Google LLC",
        },
    )
    assert result["country_code"] == "US"
    assert result["region"] == "California"
    assert result["latitude"] == 37.4
    assert result["asn"] == 15169


def test_ipdata_response_is_normalized() -> None:
    result = _normalize(
        "ipdata",
        {
            "country_name": "Germany",
            "country_code": "DE",
            "region": "Hesse",
            "city": "Frankfurt",
            "latitude": 50.1,
            "longitude": 8.7,
            "asn": {"asn": "AS680", "name": "Example Network"},
        },
    )
    assert result["country"] == "Germany"
    assert result["asn"] == 680
    assert result["organization"] == "Example Network"


def test_ipapi_is_response_is_normalized() -> None:
    result = _normalize(
        "ipapi-is",
        {
            "location": {
                "country": "France",
                "country_code": "FR",
                "state": "Île-de-France",
                "city": "Paris",
                "latitude": 48.85,
                "longitude": 2.35,
            },
            "company": {"name": "Example SAS"},
            "asn": {"asn": 12345},
        },
    )
    assert result["country_code"] == "FR"
    assert result["city"] == "Paris"
    assert result["asn"] == 12345


def test_ipinfo_lite_only_reports_country_and_asn() -> None:
    result = _normalize(
        "ipinfo-lite",
        {
            "country": "United Kingdom",
            "country_code": "GB",
            "asn": "AS62240",
            "as_name": "Clouvider",
        },
    )
    assert result == {
        "country": "United Kingdom",
        "country_code": "GB",
        "organization": "Clouvider",
        "asn": 62240,
    }
    assert "latitude" not in result


def test_2ip_response_is_normalized() -> None:
    result = _normalize(
        "2ip",
        {
            "country": "Switzerland",
            "code": "CH",
            "city": "Zürich",
            "lat": "47.3768",
            "lon": "8.5416",
            "asn": {"id": "19281", "name": "QUAD9-AS-1"},
        },
    )
    assert result["country_code"] == "CH"
    assert result["latitude"] == 47.3768
    assert result["asn"] == 19281


def test_private_addresses_are_never_queued_for_remote_lookup() -> None:
    locator = RemoteGeoLocator("ipdata")
    try:
        assert locator.lookup("192.168.0.1") == {}
        assert locator.lookup("127.0.0.1") == {}
        assert locator._queue.empty()
    finally:
        locator.close()


def test_2ip_requires_a_user_supplied_token() -> None:
    locator = RemoteGeoLocator("2ip")
    try:
        assert locator._thread is None
        assert locator._queue.empty()
        assert "token required" in locator.status.lower()
    finally:
        locator.close()



def test_ipapi_is_anonymous_response_uses_current_top_level_fields() -> None:
    result = _normalize(
        "ipapi-is",
        {
            "ip": "82.66.115.249",
            "company": "Proxad / Free SAS",
            "asn": "AS12322 Free SAS",
            "city": "Goven",
            "region": "Brittany",
            "country": "France",
            "lat": 48.0063,
            "lon": -1.84667,
        },
    )
    assert result["organization"] == "Proxad / Free SAS"
    assert result["asn"] == 12322
    assert result["city"] == "Goven"
    assert result["latitude"] == 48.0063
    assert result["longitude"] == -1.84667


def test_2ip_monthly_free_budget_has_headroom() -> None:
    locator = RemoteGeoLocator("2ip", "test-token")
    try:
        assert locator._monthly_limit() == 9500
        assert locator._minimum_interval() >= 1 / 3
    finally:
        locator.close()
