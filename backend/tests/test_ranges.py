"""Tests der Range-Aufloesung.

Die Faelle stammen aus RFC 9110 sowie aus dem, was Browser beim Spulen
tatsaechlich schicken - Chrome eroeffnet mit ``bytes=0-``, Safari fragt gern
zuerst die letzten Bytes ab, um den Moov-Atom-Index zu finden.
"""

from __future__ import annotations

import pytest

from app.services.ranges import ByteRange, UnsatisfiableRange, parse_range

GROESSE = 1000


@pytest.mark.parametrize(
    "header",
    [None, "", "   ", "items=0-10", "bytes=abc", "bytes=", "bytes=-", "bytes=0-99,200-299"],
)
def test_ohne_verwertbaren_bereich_ganze_datei(header):
    assert parse_range(header, GROESSE) is None


@pytest.mark.parametrize(
    "header,erwartet",
    [
        ("bytes=0-499", ByteRange(0, 499)),
        ("bytes=500-999", ByteRange(500, 999)),
        ("bytes=0-", ByteRange(0, 999)),  # Chrome eroeffnet so
        ("bytes=500-", ByteRange(500, 999)),
        ("bytes=999-", ByteRange(999, 999)),
        ("bytes=-500", ByteRange(500, 999)),  # letzte 500 Bytes
        ("bytes=-1", ByteRange(999, 999)),
        ("bytes=0-0", ByteRange(0, 0)),
        ("  bytes=10-20  ", ByteRange(10, 20)),
        ("BYTES=10-20", ByteRange(10, 20)),  # Einheit ist unabhaengig von Gross/Klein
    ],
)
def test_gueltige_bereiche(header, erwartet):
    assert parse_range(header, GROESSE) == erwartet


def test_zu_grosses_ende_wird_gekuerzt():
    """Norm: Ein Ende hinter dem Dateiende ist kein Fehler, sondern wird auf
    das letzte Byte gekuerzt."""
    assert parse_range("bytes=900-99999", GROESSE) == ByteRange(900, 999)


def test_zu_grosses_suffix_liefert_ganze_datei():
    assert parse_range("bytes=-99999", GROESSE) == ByteRange(0, 999)


@pytest.mark.parametrize("header", ["bytes=1000-", "bytes=1000-1200", "bytes=5000-6000", "bytes=-0"])
def test_unerfuellbare_bereiche(header):
    with pytest.raises(UnsatisfiableRange):
        parse_range(header, GROESSE)


def test_leerdatei_ist_immer_unerfuellbar():
    with pytest.raises(UnsatisfiableRange):
        parse_range("bytes=0-", 0)


def test_verdrehter_bereich_gilt_als_unverwertbar():
    assert parse_range("bytes=500-100", GROESSE) is None


def test_laenge_und_kopfzeile():
    r = ByteRange(500, 999)
    assert r.length == 500
    assert r.content_range(GROESSE) == "bytes 500-999/1000"
    assert ByteRange(0, 0).length == 1


def test_bereiche_decken_die_datei_lueckenlos_ab():
    """Aneinandergereihte Bereiche muessen die Datei genau einmal ergeben -
    ein Off-by-one hier bedeutet fehlende oder doppelte Bytes im Videostream."""
    daten = bytes(range(256)) * 4  # 1024 Bytes
    zusammen = b""
    pos = 0
    while pos < len(daten):
        r = parse_range(f"bytes={pos}-{pos + 99}", len(daten))
        assert r is not None
        zusammen += daten[r.start : r.end + 1]
        pos = r.end + 1
    assert zusammen == daten
