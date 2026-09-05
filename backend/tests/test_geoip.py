"""Offline-GeoIP: Datenformen, Cache, Reader-Wechsel und Admin-Grenze."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from app.config import settings
from app.services import geoip, live_streams
from tests.test_auth import environment as environment
from tests.test_auth import setup

RECORD = {
    "location": {"latitude": 37.422, "longitude": -122.085},
    "city": {"names": {"en": "Mountain View"}},
    "subdivisions": [{"names": {"de": "Kalifornien", "en": "California"}}],
    "country": {"names": {"de": "Vereinigte Staaten", "en": "United States"}, "iso_code": "US"},
}


class Reader:
    def __init__(self, record=RECORD, epoch=1788220800):
        self.record = record
        self.epoch = epoch
        self.calls: list[str] = []
        self.closed = False

    def metadata(self):
        return SimpleNamespace(build_epoch=self.epoch)

    def get(self, address):
        assert not self.closed
        self.calls.append(address)
        return self.record

    def close(self):
        self.closed = True


@pytest.fixture
def locator(tmp_path, monkeypatch):
    path = tmp_path / "city.mmdb"
    path.write_bytes(b"fake database")
    monkeypatch.setattr(settings, "geoip_database", path)
    reader = Reader()
    monkeypatch.setattr(geoip, "_open_database", lambda _path: reader)
    clock = [0.0]
    instance = geoip.GeoIP(clock=lambda: clock[0])
    yield instance, reader, clock, path
    instance.close()


def test_public_lookup_names_coordinates_and_metadata(locator):
    instance, reader, _, path = locator
    result = instance.lookup("8.8.8.8")
    assert result == {"status": "located", "latitude": 37.422, "longitude": -122.085,
                      "city": "Mountain View", "region": "Kalifornien",
                      "country": "Vereinigte Staaten", "country_code": "US"}
    assert instance.metadata() == {"available": True, "database_date": "2026-09-01"}
    assert str(path) not in str(instance.metadata())
    assert reader.calls == ["8.8.8.8"]


@pytest.mark.parametrize("address", [
    "127.0.0.1", "10.1.2.3", "192.168.0.1", "172.16.0.1", "169.254.1.1", "100.64.0.1",
    "0.0.0.0", "224.0.0.1", "240.0.0.1", "255.255.255.255", "198.51.100.1",
    "::", "::1", "fd00::1", "fe80::1", "fe80::1%eth0", "ff02::1", "2001:db8::1",
    "::ffff:192.168.1.1", "::ffff:127.0.0.1", "::ffff:224.0.0.1",
])
def test_nonpublic_addresses_never_reach_reader(locator, address):
    instance, reader, _, _ = locator
    result = instance.lookup(address)
    assert result["status"] == "private"
    assert all(value is None for key, value in result.items() if key != "status")
    assert reader.calls == []


@pytest.mark.parametrize("address", [None, 1234, "", "testclient", "8.8.8.8:443", "[::1]", "x" * 1000,
                                     "2001:4860:4860::8888%interface"])
def test_invalid_addresses_are_unknown_without_lookup(locator, address):
    instance, reader, _, _ = locator
    assert instance.lookup(address)["status"] == "unknown"
    assert reader.calls == []


def test_mapped_ipv6_and_ipv4_share_one_normalized_cache_entry(locator):
    instance, reader, _, _ = locator
    assert instance.lookup("::ffff:8.8.8.8") == instance.lookup("8.8.8.8")
    assert reader.calls == ["8.8.8.8"]
    assert instance.lookup("2001:4860:4860::8888")["status"] == "located"


@pytest.mark.parametrize(("latitude", "longitude"), [
    (float("nan"), 0), (0, float("inf")), (-91, 0), (0, 181), (True, 0), (0, False),
    ("37.4", 12), (None, 12), (10**1000, 0), (0, -(10**1000)),
])
def test_unusable_coordinates_are_unknown_and_never_nan_in_json(locator, latitude, longitude):
    instance, reader, _, _ = locator
    reader.record = {**RECORD, "location": {"latitude": latitude, "longitude": longitude}}
    result = instance.lookup("8.8.8.8")
    assert result["status"] == "unknown" and result["latitude"] is None and result["longitude"] is None
    assert instance.metadata()["available"] is True


@pytest.mark.parametrize("record", [None, [], {}, {"location": []}, {"country": "invalid"}])
def test_absent_or_malformed_record_is_unknown(locator, record):
    instance, reader, _, _ = locator
    reader.record = record
    assert instance.lookup("8.8.8.8")["status"] == "unknown"


def test_missing_names_and_country_code_do_not_discard_valid_coordinates(locator):
    instance, reader, _, _ = locator
    reader.record = {"location": {"latitude": 0, "longitude": 0}, "city": {"names": []},
                     "country": {"names": {"de": " "}, "iso_code": "../"}, "subdivisions": [{}]}
    result = instance.lookup("8.8.8.8")
    assert result["status"] == "located" and result["latitude"] == result["longitude"] == 0.0
    assert result["city"] is result["region"] is result["country"] is result["country_code"] is None


def test_cache_is_bounded_lru_with_fixed_ttl_and_no_mutable_shared_result(locator, monkeypatch):
    instance, reader, clock, _ = locator
    monkeypatch.setattr(geoip, "CACHE_ENTRIES", 2)
    instance.lookup("8.8.8.8")["city"] = "client mutation"
    assert instance.lookup("8.8.8.8")["city"] == "Mountain View"
    instance.lookup("1.1.1.1")
    instance.lookup("8.8.8.8")
    instance.lookup("9.9.9.9")
    assert len(instance._cache) == 2
    instance.lookup("1.1.1.1")
    assert reader.calls == ["8.8.8.8", "1.1.1.1", "9.9.9.9", "1.1.1.1"]
    clock[0] = geoip.CACHE_TTL_SECONDS
    instance.lookup("1.1.1.1")
    assert reader.calls[-2:] == ["1.1.1.1", "1.1.1.1"]
    assert len(instance._cache) == 1


def test_missing_database_recovers_on_controlled_retry(locator):
    instance, reader, clock, path = locator
    path.unlink()
    assert instance.lookup("8.8.8.8")["status"] == "unavailable"
    assert instance.lookup("127.0.0.1")["status"] == "private"
    assert instance.lookup("invalid")["status"] == "unknown"
    assert instance.metadata() == {"available": False, "database_date": None}
    path.write_bytes(b"replacement")
    assert instance.lookup("8.8.8.8")["status"] == "unavailable"
    clock[0] = geoip.RELOAD_SECONDS
    assert instance.lookup("8.8.8.8")["status"] == "located"
    assert reader.calls == ["8.8.8.8"]


def test_corrupt_database_open_and_lookup_fail_gracefully(locator, monkeypatch):
    instance, reader, clock, _ = locator

    def broken_open(_path):
        raise ValueError("private database path")

    monkeypatch.setattr(geoip, "_open_database", broken_open)
    assert instance.lookup("8.8.8.8")["status"] == "unavailable"
    monkeypatch.setattr(geoip, "_open_database", lambda _path: reader)
    clock[0] = geoip.RELOAD_SECONDS

    def broken_get(_address):
        raise ValueError("corrupt record")

    monkeypatch.setattr(reader, "get", broken_get)
    assert instance.lookup("8.8.8.8")["status"] == "unavailable"
    assert reader.closed and instance.metadata()["available"] is False
    assert len(instance._cache) == 0


def test_real_mmdb_reader_rejects_corrupt_file_without_exposing_path(tmp_path, monkeypatch):
    path = tmp_path / "private-path.mmdb"
    path.write_bytes(b"not a MaxMind database")
    monkeypatch.setattr(settings, "geoip_database", path)
    instance = geoip.GeoIP()
    try:
        assert instance.lookup("8.8.8.8")["status"] == "unavailable"
        assert instance.metadata() == {"available": False, "database_date": None}
        assert str(path) not in str(instance.lookup("8.8.8.8"))
    finally:
        instance.close()


@pytest.mark.parametrize("epoch", [None, True, "2026-09-01", -1, float("nan"), float("inf"), 10**1000])
def test_bad_build_epoch_never_produces_invalid_json_or_breaks_lookup(locator, epoch):
    instance, reader, _, _ = locator
    reader.epoch = epoch
    assert instance.lookup("8.8.8.8")["status"] == "located"
    assert instance.metadata() == {"available": True, "database_date": None}


def test_database_replacement_closes_old_reader_and_invalidates_cache(locator, monkeypatch):
    instance, first, clock, path = locator
    instance.lookup("8.8.8.8")
    second = Reader({**RECORD, "city": {"names": {"en": "New city"}}}, epoch=1790812800)
    monkeypatch.setattr(geoip, "_open_database", lambda _path: second)
    path.write_bytes(b"replacement with a different size")
    assert instance.lookup("8.8.8.8")["city"] == "Mountain View"
    clock[0] = geoip.RELOAD_SECONDS
    assert instance.lookup("8.8.8.8")["city"] == "New city"
    assert first.closed and second.calls == ["8.8.8.8"]
    assert instance.metadata()["database_date"] == "2026-10-01"
    instance.close()
    assert second.closed and len(instance._cache) == 0


def test_close_waits_for_active_reader_lookup(locator, monkeypatch):
    instance, reader, _, _ = locator
    entered, release, close_started, closed = (threading.Event() for _ in range(4))

    def get(_address):
        entered.set()
        assert release.wait(2)
        assert not reader.closed
        return RECORD

    def close():
        close_started.set()
        instance.close()
        closed.set()

    monkeypatch.setattr(reader, "get", get)
    with ThreadPoolExecutor(max_workers=2) as pool:
        lookup = pool.submit(instance.lookup, "8.8.8.8")
        assert entered.wait(2)
        closing = pool.submit(close)
        try:
            assert close_started.wait(2)
            assert not closed.wait(0.03)
        finally:
            release.set()
        assert lookup.result(timeout=2)["status"] == "located"
        closing.result(timeout=2)
    assert reader.closed


def test_admin_dashboard_enrichment_uses_only_stored_client_address(environment, locator, monkeypatch):
    client, _, _ = environment
    instance, reader, _, path = locator
    monkeypatch.setattr(geoip, "locator", instance)
    manager = live_streams.StreamManager()
    monkeypatch.setattr(live_streams, "manager", manager)
    viewer = manager.create(video_id="video", video_title="Film", channel_title=None, client_address="8.8.8.8",
                            client_name="Browser", mode="direct", duration_s=30, source=path, offset=0, size=1)
    try:
        assert client.get("/api/streams", headers={"X-Forwarded-For": "1.1.1.1"}).status_code == 401
        assert reader.calls == []
        setup(client)
        result = client.get("/api/streams?ip=1.1.1.1", headers={"X-Forwarded-For": "1.1.1.1"})
        assert result.status_code == 200 and result.headers["cache-control"] == "no-store"
        assert result.json()["geoip"] == {"available": True, "database_date": "2026-09-01"}
        row = result.json()["streams"][0]
        assert row["client_address"] == "8.8.8.8" and row["geo"]["status"] == "located"
        assert reader.calls == ["8.8.8.8"]
        assert viewer.token not in result.text and str(path) not in result.text
        assert "geo" not in manager.snapshot()["streams"][0]
    finally:
        manager.close()
