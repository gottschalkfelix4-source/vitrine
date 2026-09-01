"""Ende-zu-Ende-Test der Auslieferung.

Prueft die vollstaendige Kette: HTTP-Anfrage -> Buendel oeffnen ->
Wiedergabeentscheidung -> Offset berechnen -> Bytes ausliefern. Genau hier
wuerde ein Fehler in der Offset-Rechnung sichtbar, den die Unit-Tests der
Einzelteile nicht faenden.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import stream
from app.config import settings
from app.db import get_db
from app.models import Base, Channel, Video, VideoStatus
from app.services.bundle import BundleManifest, write_bundle

MEDIENGROESSE = 2 * 1024 * 1024 + 777
MODERN = "mp4,webm,av01,vp09,h264,opus,aac"


@pytest.fixture
def umgebung(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "hot_max_bytes", 0)
    settings.ensure_dirs()

    # StaticPool ist hier zwingend: Eine In-Memory-Datenbank gehoert der
    # jeweiligen Verbindung. Ohne geteilte Verbindung saehe der Thread des
    # TestClient eine leere Datenbank ohne Tabellen.
    eng = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(eng)
    Sitzung = sessionmaker(bind=eng, expire_on_commit=False)
    db = Sitzung()
    db.add(Channel(id="UCtest", name="Testkanal"))
    db.commit()

    app = FastAPI()
    app.include_router(stream.router)
    app.dependency_overrides[get_db] = lambda: db

    return TestClient(app), db, tmp_path


@pytest.fixture
def rohdaten() -> bytes:
    return os.urandom(MEDIENGROESSE)


def _archiviere(
    db: Session,
    tmp_path: Path,
    rohdaten: bytes,
    *,
    video_id: str = "vid1",
    dateiname: str = "film.webm",
    vcodec: str = "av1",
    acodec: str = "opus",
) -> Video:
    quelle = tmp_path / dateiname
    quelle.write_bytes(rohdaten)
    ziel = settings.bundle_dir / "UCtest" / f"{video_id}.zip"
    m = BundleManifest(
        schema_version=1, video_id=video_id, channel_id="UCtest", title="Testvideo",
        media_name="", media_bytes=0, mime_type="", video_codec=vcodec, audio_codec=acodec,
    )
    write_bundle(ziel, manifest=m, media_file=quelle, info_json={"id": video_id})
    v = Video(
        id=video_id, channel_id="UCtest", title="Testvideo",
        status=VideoStatus.ARCHIVED, bundle_file=str(ziel),
        bundle_bytes=ziel.stat().st_size, media_name=f"media/{dateiname}",
        video_codec=vcodec, audio_codec=acodec,
    )
    db.add(v)
    db.commit()
    return v


# ------------------------------------------------------------- Direktstream


def test_ganze_datei_direkt_aus_dem_buendel(umgebung, rohdaten):
    client, db, tmp = umgebung
    _archiviere(db, tmp, rohdaten)

    r = client.get("/api/videos/vid1/stream", params={"support": MODERN})
    assert r.status_code == 200
    assert r.headers["X-Wiedergabe-Modus"] == "direct"
    assert r.headers["Accept-Ranges"] == "bytes"
    assert r.headers["Content-Type"] == "video/webm"
    assert int(r.headers["Content-Length"]) == MEDIENGROESSE
    assert r.content == rohdaten, "ausgelieferte Bytes weichen von der Quelle ab"


@pytest.mark.parametrize(
    "header,start,ende",
    [
        ("bytes=0-", 0, MEDIENGROESSE - 1),
        ("bytes=0-1023", 0, 1023),
        ("bytes=1000000-1500000", 1000000, 1500000),
        ("bytes=2000000-", 2000000, MEDIENGROESSE - 1),
        ("bytes=-1000", MEDIENGROESSE - 1000, MEDIENGROESSE - 1),
    ],
)
def test_bereichsanfragen_liefern_die_richtigen_bytes(umgebung, rohdaten, header, start, ende):
    client, db, tmp = umgebung
    _archiviere(db, tmp, rohdaten)

    r = client.get("/api/videos/vid1/stream", params={"support": MODERN}, headers={"Range": header})
    assert r.status_code == 206
    assert r.headers["Content-Range"] == f"bytes {start}-{ende}/{MEDIENGROESSE}"
    assert int(r.headers["Content-Length"]) == ende - start + 1
    assert r.content == rohdaten[start : ende + 1]


def test_springen_setzt_sich_lueckenlos_zusammen(umgebung, rohdaten):
    """Simuliert, was ein Player beim Spulen macht: viele Bereiche in wirrer
    Reihenfolge. Zusammengesetzt muss exakt die Quelldatei herauskommen."""
    client, db, tmp = umgebung
    _archiviere(db, tmp, rohdaten)

    stueck = 300_000
    positionen = list(range(0, MEDIENGROESSE, stueck))
    zusammen = bytearray(MEDIENGROESSE)
    for pos in reversed(positionen):  # rueckwaerts, um Reihenfolgeeffekte auszuschliessen
        r = client.get(
            "/api/videos/vid1/stream",
            params={"support": MODERN},
            headers={"Range": f"bytes={pos}-{pos + stueck - 1}"},
        )
        assert r.status_code == 206
        zusammen[pos : pos + len(r.content)] = r.content
    assert bytes(zusammen) == rohdaten


def test_unerfuellbarer_bereich_gibt_416(umgebung, rohdaten):
    client, db, tmp = umgebung
    _archiviere(db, tmp, rohdaten)

    r = client.get(
        "/api/videos/vid1/stream",
        params={"support": MODERN},
        headers={"Range": f"bytes={MEDIENGROESSE + 100}-"},
    )
    assert r.status_code == 416
    assert r.headers["Content-Range"] == f"bytes */{MEDIENGROESSE}"


def test_h264_mp4_laeuft_auch_ohne_faehigkeitsmeldung_direkt(umgebung, rohdaten):
    client, db, tmp = umgebung
    _archiviere(db, tmp, rohdaten, dateiname="film.mp4", vcodec="h264", acodec="aac")

    r = client.get("/api/videos/vid1/stream")  # kein support-Parameter
    assert r.status_code == 200
    assert r.headers["X-Wiedergabe-Modus"] == "direct"
    assert r.content == rohdaten


# ------------------------------------------------------------ Transkodierpfad


def test_alter_client_bekommt_202_und_einen_auftrag(umgebung, rohdaten):
    """AV1-Archiv, Client kann nur H.264: Es darf kein kaputter Stream
    zurueckkommen, sondern die Ansage, dass vorbereitet wird."""
    client, db, tmp = umgebung
    _archiviere(db, tmp, rohdaten)

    r = client.get("/api/videos/vid1/stream", params={"support": "mp4,h264,aac"})
    assert r.status_code == 202
    daten = r.json()
    assert daten["status"] == "wird_vorbereitet"
    assert isinstance(daten["job_id"], int)
    # Die Begruendung soll benennen, woran es lag - welcher Pruefschritt zuerst
    # anschlaegt (hier der Container webm), ist dabei nicht festgeschrieben.
    assert daten["grund"] and "nicht" in daten["grund"]


def test_wiederholtes_nachfragen_erzeugt_nur_einen_auftrag(umgebung, rohdaten):
    """Der wartende Player fragt im Sekundentakt nach - daraus duerfen nicht
    hunderte Auftraege werden."""
    client, db, tmp = umgebung
    _archiviere(db, tmp, rohdaten)

    ids = {
        client.get("/api/videos/vid1/stream", params={"support": "mp4,h264,aac"}).json()["job_id"]
        for _ in range(5)
    }
    assert len(ids) == 1


# ---------------------------------------------------------------- Fehlerfaelle


def test_unbekanntes_video(umgebung):
    client, _, _ = umgebung
    assert client.get("/api/videos/gibtsnicht/stream").status_code == 404


def test_noch_nicht_archiviertes_video(umgebung):
    client, db, _ = umgebung
    db.add(Video(id="v2", channel_id="UCtest", title="wartet", status=VideoStatus.QUEUED))
    db.commit()
    r = client.get("/api/videos/v2/stream")
    assert r.status_code == 409
    assert "nicht archiviert" in r.json()["detail"]


def test_verschwundenes_buendel(umgebung, rohdaten):
    client, db, tmp = umgebung
    v = _archiviere(db, tmp, rohdaten)
    Path(v.bundle_file).unlink()
    assert client.get("/api/videos/vid1/stream").status_code == 410


# --------------------------------------------------------------------- Lease


def test_lease_endpunkte(umgebung, rohdaten):
    client, db, tmp = umgebung
    _archiviere(db, tmp, rohdaten)

    assert client.post("/api/videos/vid1/heartbeat").status_code == 204
    assert client.post("/api/videos/vid1/playback-ended").status_code == 204

    z = client.get("/api/videos/vid1/playback-state")
    assert z.status_code == 200
    assert z.json()["archiv_status"] == "archived"
    # Direktstream: es existiert gar keine Heisskopie, die aufzuraeumen waere.
    assert z.json()["heisskopien"] == []


def test_lease_auf_unbekanntem_video(umgebung):
    client, _, _ = umgebung
    assert client.post("/api/videos/nope/heartbeat").status_code == 404
