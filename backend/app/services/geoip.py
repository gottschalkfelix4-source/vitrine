"""Lokale, kurzzeitig gecachte IP-Zuordnung fuer die Administrator-Livekarte."""

from __future__ import annotations

import ipaddress
import logging
import math
import stat
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, TypedDict

from app.config import settings

log = logging.getLogger(__name__)
CACHE_ENTRIES = 1024
CACHE_TTL_SECONDS = 15 * 60
RELOAD_SECONDS = 60


class Location(TypedDict):
    status: Literal["located", "private", "unknown", "unavailable"]
    latitude: float | None
    longitude: float | None
    city: str | None
    region: str | None
    country: str | None
    country_code: str | None


class Metadata(TypedDict):
    available: bool
    database_date: str | None


def _empty(status: Literal["private", "unknown", "unavailable"]) -> Location:
    return {"status": status, "latitude": None, "longitude": None, "city": None,
            "region": None, "country": None, "country_code": None}


def _address(raw: object) -> tuple[str | None, Location | None]:
    if not isinstance(raw, str) or len(raw) > 64:
        return None, _empty("unknown")
    try:
        address = ipaddress.ip_address(raw)
    except ValueError:
        return None, _empty("unknown")
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    # is_global allein laesst z.B. Multicast in einigen Python-Versionen durch.
    if (not address.is_global or address.is_private or address.is_loopback
            or address.is_link_local or address.is_multicast or address.is_reserved
            or address.is_unspecified):
        return None, _empty("private")
    if isinstance(address, ipaddress.IPv6Address) and address.scope_id is not None:
        return None, _empty("unknown")
    return str(address), None


def _name(value: object) -> str | None:
    if not isinstance(value, Mapping) or not isinstance(value.get("names"), Mapping):
        return None
    names = value["names"]
    for language in ("de", "en"):
        name = names.get(language)
        if isinstance(name, str) and name.strip():
            return name.strip()[:160]
    return None


def _location(record: object) -> Location:
    if not isinstance(record, Mapping):
        return _empty("unknown")
    coordinates = record.get("location")
    if not isinstance(coordinates, Mapping):
        return _empty("unknown")
    latitude, longitude = coordinates.get("latitude"), coordinates.get("longitude")
    if (type(latitude) not in (float, int) or type(longitude) not in (float, int)
            or not -90 <= latitude <= 90 or not -180 <= longitude <= 180
            or not math.isfinite(latitude) or not math.isfinite(longitude)):
        return _empty("unknown")
    subdivisions = record.get("subdivisions")
    region = _name(subdivisions[0]) if isinstance(subdivisions, list) and subdivisions else None
    country = record.get("country")
    code = country.get("iso_code") if isinstance(country, Mapping) else None
    code = code.upper() if isinstance(code, str) and len(code) == 2 and code.isascii() and code.isalpha() else None
    return {"status": "located", "latitude": float(latitude), "longitude": float(longitude),
            "city": _name(record.get("city")), "region": region,
            "country": _name(country), "country_code": code}


def _database_date(epoch: object) -> str | None:
    if type(epoch) not in (int, float):
        return None
    try:
        if not math.isfinite(epoch) or epoch <= 0:
            return None
        return datetime.fromtimestamp(epoch, UTC).date().isoformat()
    except (ValueError, OverflowError, OSError):
        return None


def _open_database(path: Path):
    # Ohne optionale Reader-Bibliothek bleibt die uebrige Anwendung nutzbar.
    import maxminddb

    return maxminddb.open_database(path)


class GeoIP:
    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._reader: Any = None
        self._path: Path | None = None
        self._fingerprint: tuple[int, int, int, int] | None = None
        self._next_reload = 0.0
        self._date: str | None = None
        self._reported_unavailable = False
        self._cache: OrderedDict[str, tuple[float, Location]] = OrderedDict()

    def _discard_reader(self) -> None:
        reader, self._reader = self._reader, None
        self._date = None
        self._fingerprint = None
        self._cache.clear()
        if reader is not None:
            # Ein bereits defekter Reader darf das Dashboard nicht stoppen.
            with suppress(Exception):
                reader.close()

    def _unavailable(self) -> None:
        self._discard_reader()
        if not self._reported_unavailable:
            log.warning("Die lokale GeoIP-Datenbank ist nicht verfuegbar.")
            self._reported_unavailable = True

    def _refresh(self, now: float) -> None:
        path = settings.geoip_database
        if path == self._path and now < self._next_reload:
            return
        self._next_reload = now + RELOAD_SECONDS
        previous_path, self._path = self._path, path
        try:
            info = path.stat()
            if not stat.S_ISREG(info.st_mode):
                raise ValueError("GeoIP-Datenbank ist keine Datei")
            fingerprint = (info.st_dev, info.st_ino, info.st_mtime_ns, info.st_size)
            if self._reader is not None and previous_path == path and self._fingerprint == fingerprint:
                return
            self._discard_reader()
            reader = _open_database(path)
            self._reader = reader
            self._date = _database_date(reader.metadata().build_epoch)
            self._fingerprint = fingerprint
            self._reported_unavailable = False
        except Exception:
            self._unavailable()

    def _expire(self, now: float) -> None:
        for address, (expires, _) in list(self._cache.items()):
            if expires <= now:
                del self._cache[address]

    def _lookup(self, raw: object, now: float) -> Location:
        address, special = _address(raw)
        if special is not None:
            return special
        if self._reader is None:
            return _empty("unavailable")
        assert address is not None
        cached = self._cache.get(address)
        if cached is not None:
            self._cache.move_to_end(address)
            return dict(cached[1])
        try:
            result = _location(self._reader.get(address))
        except Exception:
            self._unavailable()
            self._next_reload = now + RELOAD_SECONDS
            return _empty("unavailable")
        self._cache[address] = (now + CACHE_TTL_SECONDS, result)
        while len(self._cache) > CACHE_ENTRIES:
            self._cache.popitem(last=False)
        return dict(result)

    def lookup(self, address: object) -> Location:
        with self._lock:
            now = self._clock()
            self._refresh(now)
            self._expire(now)
            return self._lookup(address, now)

    def metadata(self) -> Metadata:
        with self._lock:
            self._refresh(self._clock())
            return {"available": self._reader is not None, "database_date": self._date}

    def enrich(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        # Der Aufrufer hat StreamManager.snapshot() bereits abgeschlossen.
        # Dateizugriffe/Reader-Lock blockieren somit keine Wiedergabe-Leases.
        with self._lock:
            now = self._clock()
            self._refresh(now)
            self._expire(now)
            streams = [{**row, "geo": self._lookup(row.get("client_address"), now)}
                       for row in snapshot["streams"]]
            return {**snapshot, "streams": streams,
                    "geoip": {"available": self._reader is not None, "database_date": self._date}}

    def close(self) -> None:
        with self._lock:
            self._discard_reader()
            self._path = None
            self._next_reload = 0.0


locator = GeoIP()
