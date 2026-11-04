from __future__ import annotations

from swarmatlas import geoip_data


def test_dbip_release_metadata_finds_city_mmdb_and_sha1(monkeypatch) -> None:
    checksum = "a" * 40
    page = f"""
        <div>Format MMDB Release November 2026</div>
        <div>File size 121.0 MB</div>
        <div>MD5SUM {'b' * 32}</div>
        <div>SHA1SUM {checksum}</div>
        <a href="https://download.db-ip.com/free/dbip-city-lite-2026-11.mmdb.gz">
            Download IP to City Lite MMDB
        </a>
    """

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit: int) -> bytes:
            return page.encode("utf-8")

    monkeypatch.setattr(geoip_data, "urlopen", lambda *_args, **_kwargs: FakeResponse())
    url, digest = geoip_data._release_metadata()
    assert url.endswith("dbip-city-lite-2026-11.mmdb.gz")
    assert digest == checksum
