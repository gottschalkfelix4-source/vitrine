"""Tests der Bibliotheks-API."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import library
from app.config import settings
from app.db import get_db
from app.models import (
    Channel,
    Job,
    JobStatus,
    JobType,
    Playlist,
    PlaylistItem,
    PlaylistKind,
    Video,
    VideoStatus,
)
from tests.conftest import neue_sitzung


@pytest.fixture
def umgebung(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    settings.ensure_dirs()

    db = neue_sitzung()

    db.add(Channel(id="UCtest", name="Testkanal", handle="@test"))
    db.add(Playlist(id="PLabc", channel_id="UCtest", kind=PlaylistKind.PLAYLIST,
                    title="Eine Reihe", item_count=3))
    for i, (status_, dauer) in enumerate(
        [(VideoStatus.ARCHIVED, 600), (VideoStatus.QUEUED, 300), (VideoStatus.UNAVAILABLE, None)]
    ):
        db.add(Video(
            id=f"v{i}", channel_id="UCtest", title=f"Video {i}", status=status_,
            duration_s=dauer, upload_date=datetime(2026, 1, i + 1, tzinfo=UTC),
            view_count=1000 * (i + 1), bundle_bytes=1_000_000 if status_ == VideoStatus.ARCHIVED else None,
            source_bytes=2_000_000 if status_ == VideoStatus.ARCHIVED else None,
        ))
        db.add(PlaylistItem(playlist_id="PLabc", video_id=f"v{i}", position=i))
    db.commit()

    # Im Betrieb indiziert der Archivierungs-Worker. Hier entstehen die Videos
    # direkt in der Datenbank, also muss der Index nachgezogen werden - das
    # prueft nebenbei den Neuaufbau-Weg mit.
    from app.services.reindex import index_neu_aufbauen

    index_neu_aufbauen(db, mit_untertiteln=False)

    app = FastAPI()
    app.include_router(library.router)
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app), db


# --------------------------------------------------------------------- Kanaele


def test_kanalliste_zaehlt_richtig(umgebung):
    client, _ = umgebung
    (k,) = client.get("/api/channels").json()
    assert k["name"] == "Testkanal"
    assert k["videos_gesamt"] == 3
    assert k["videos_archiviert"] == 1
    assert k["belegung_bytes"] == 1_000_000


def test_kanaldetail_listet_sammlungen(umgebung):
    client, _ = umgebung
    d = client.get("/api/channels/UCtest").json()
    assert d["kanal"]["handle"] == "@test"
    assert [s["titel"] for s in d["sammlungen"]] == ["Eine Reihe"]
    assert d["regeln"]["codec"] == "av1"


def test_unbekannter_kanal(umgebung):
    client, _ = umgebung
    assert client.get("/api/channels/UCgibtsnicht").status_code == 404


def test_abgleich_reiht_auftrag_ein(umgebung):
    client, db = umgebung
    r = client.post("/api/channels/UCtest/sync", params={"voll": True})
    assert r.status_code == 202
    job = db.get(Job, r.json()["job_id"])
    assert job.type == JobType.CHANNEL_SYNC and job.target_id == "UCtest"


# ------------------------------------------------------------------- Playlists


def test_playlist_zeigt_auch_nicht_archivierte_positionen(umgebung):
    """Der entscheidende Unterschied zu TubeArchivist: Eine Playlist verschweigt
    nicht, was fehlt."""
    client, _ = umgebung
    d = client.get("/api/playlists/PLabc").json()

    assert d["anzahl_quelle"] == 3
    assert d["anzahl_archiviert"] == 1
    assert len(d["positionen"]) == 3, "nicht archivierte Positionen wurden verschluckt"

    zustaende = {p["video"]["id"]: p["video"]["status"] for p in d["positionen"]}
    assert zustaende == {"v0": "archived", "v1": "queued", "v2": "unavailable"}
    # Reihenfolge muss der Playlist entsprechen, nicht dem Zufall.
    assert [p["position"] for p in d["positionen"]] == [0, 1, 2]


# ---------------------------------------------------------------------- Videos


def test_videoliste_zeigt_standardmaessig_nur_archiviertes(umgebung):
    client, _ = umgebung
    ids = [v["id"] for v in client.get("/api/videos").json()]
    assert ids == ["v0"]


def test_videoliste_mit_statusfilter(umgebung):
    client, _ = umgebung
    ids = [v["id"] for v in client.get("/api/videos", params={"status": "queued"}).json()]
    assert ids == ["v1"]


def test_videoliste_sortierung(umgebung):
    client, _ = umgebung
    p = {"nur_archiviert": False}
    neu = [v["id"] for v in client.get("/api/videos", params=p | {"sortierung": "neu"}).json()]
    alt = [v["id"] for v in client.get("/api/videos", params=p | {"sortierung": "alt"}).json()]
    assert neu == list(reversed(alt))
    aufrufe = [v["id"] for v in client.get("/api/videos", params=p | {"sortierung": "aufrufe"}).json()]
    assert aufrufe[0] == "v2"


def test_videosuche(umgebung):
    client, _ = umgebung
    treffer = client.get("/api/videos", params={"suche": "Video 0"}).json()
    assert [v["id"] for v in treffer] == ["v0"]


def test_videodetail_weist_ersparnis_aus(umgebung):
    client, _ = umgebung
    d = client.get("/api/videos/v0").json()
    assert d["technik"]["quelle_bytes"] == 2_000_000
    assert d["technik"]["buendel_bytes"] == 1_000_000
    assert d["technik"]["gespart_bytes"] == 1_000_000
    assert [p["id"] for p in d["in_playlists"]] == ["PLabc"]


def test_fortschritt_merken(umgebung):
    client, db = umgebung
    assert client.put("/api/videos/v0/progress", json={"sekunden": 120.0}).status_code == 204
    db.refresh(db.get(Video, "v0"))
    v = db.get(Video, "v0")
    assert v.progress_s == 120.0
    assert v.watched is False  # 120 von 600 Sekunden

    # Der Balken auf der Kachel folgt daraus.
    kachel = client.get("/api/videos").json()[0]
    assert kachel["fortschritt_anteil"] == pytest.approx(0.2)


def test_ab_neunzig_prozent_gilt_als_gesehen(umgebung):
    client, db = umgebung
    client.put("/api/videos/v0/progress", json={"sekunden": 550.0})  # 91,7 %
    assert db.get(Video, "v0").watched is True


def test_gesehen_laesst_sich_auch_ausdruecklich_setzen(umgebung):
    client, db = umgebung
    client.put("/api/videos/v0/progress", json={"sekunden": 5.0, "gesehen": True})
    assert db.get(Video, "v0").watched is True


def test_bereits_archiviertes_video_nochmal_einreihen(umgebung):
    client, _ = umgebung
    assert client.post("/api/videos/v0/archive").status_code == 409
    assert client.post("/api/videos/v1/archive").status_code == 202


# ---------------------------------------------------------------- Warteschlange


def test_warteschlange_zeigt_titel_statt_nur_ids(umgebung):
    client, db = umgebung
    from app.services import jobs as j

    j.enqueue_archive(db, "v1")
    (eintrag,) = client.get("/api/jobs").json()
    assert eintrag["art"] == JobType.VIDEO_ARCHIVE
    assert eintrag["titel"] == "Video 1", "ohne Titel ist die Warteschlange unlesbar"
    assert eintrag["status"] == JobStatus.PENDING


def test_auftrag_abbrechen_und_wiederholen(umgebung):
    client, db = umgebung
    from app.services import jobs as j

    job = j.enqueue_archive(db, "v1")
    assert client.post(f"/api/jobs/{job.id}/cancel").status_code == 204
    assert db.get(Job, job.id).status == JobStatus.CANCELLED
    # Ein abgebrochener Auftrag laesst sich nicht zweimal abbrechen.
    assert client.post(f"/api/jobs/{job.id}/cancel").status_code == 409
    assert client.post(f"/api/jobs/{job.id}/retry").status_code == 202
    assert db.get(Job, job.id).status == JobStatus.PENDING


# -------------------------------------------------------------------- Speicher


def test_speicheruebersicht(umgebung):
    client, _ = umgebung
    d = client.get("/api/storage").json()
    assert d["kaltspeicher"] == {
        "bytes": 1_000_000, "videos": 1, "quelle_bytes": 2_000_000, "gespart_bytes": 1_000_000
    }
    assert d["videos_nach_status"] == {"archived": 1, "queued": 1, "unavailable": 1}


# ---------------------------------------------------------------- Vorschaubild


def test_thumbnail_wird_ausgeliefert(umgebung):
    client, _ = umgebung
    (settings.thumb_dir / "v0.jpg").write_bytes(b"\xff\xd8\xff\xe0test")
    r = client.get("/api/thumbs/v0.jpg")
    assert r.status_code == 200
    assert r.content.startswith(b"\xff\xd8")


@pytest.mark.parametrize("name", ["../vitrine.db", "..%2Fvitrine.db", "unterordner/../../geheim"])
def test_thumbnail_pfad_kann_nicht_ausbrechen(umgebung, name):
    """Der Dateiname kommt aus der URL - er darf nicht aus dem
    Vorschaubild-Verzeichnis herausfuehren."""
    client, _ = umgebung
    (settings.data_dir / "geheim").write_text("nicht fuer die Oeffentlichkeit")
    r = client.get(f"/api/thumbs/{name}")
    assert r.status_code == 404
    assert b"Oeffentlichkeit" not in r.content


# ------------------------------------------------------------ Art-Filter


def test_videoliste_filtert_nach_art(umgebung):
    """Der Filter muss serverseitig greifen - sonst stimmt das Blaettern nicht,
    wenn der Client Shorts erst nach dem Laden aussiebt."""
    client, db = umgebung
    db.add(Video(id="s1", channel_id="UCtest", title="Ein Short", status=VideoStatus.ARCHIVED,
                 is_short=True))
    db.add(Video(id="l1", channel_id="UCtest", title="Ein Stream", status=VideoStatus.ARCHIVED,
                 was_live=True))
    db.commit()

    p = {"nur_archiviert": False, "kanal": "UCtest"}
    alle = {v["id"] for v in client.get("/api/videos", params=p).json()}
    nur_videos = {v["id"] for v in client.get("/api/videos", params=p | {"art": "videos"}).json()}
    shorts = {v["id"] for v in client.get("/api/videos", params=p | {"art": "shorts"}).json()}
    live = {v["id"] for v in client.get("/api/videos", params=p | {"art": "live"}).json()}

    assert "s1" in alle and "l1" in alle
    assert shorts == {"s1"}
    assert live == {"l1"}
    assert "s1" not in nur_videos and "l1" not in nur_videos
    assert {"v0", "v1", "v2"} <= nur_videos


def test_kanaldetail_zaehlt_nach_art(umgebung):
    client, db = umgebung
    db.add(Video(id="s1", channel_id="UCtest", title="Short", is_short=True))
    db.add(Video(id="l1", channel_id="UCtest", title="Stream", was_live=True))
    db.commit()
    z = client.get("/api/channels/UCtest").json()["zaehler"]
    assert z == {"videos": 3, "shorts": 1, "live": 1}


# ---------------------------------------------------------- Kanal entfernen


def _kanal_mit_dateien(db):
    """Ein zweiter Kanal mit allem Drum und Dran auf der Platte."""
    from app.models import HotCopy, Subtitle
    from app.services import suche as volltext

    db.add(Channel(id="UCweg", name="Wegwerfkanal"))
    ordner = settings.bundle_dir / "UCweg"
    ordner.mkdir(parents=True, exist_ok=True)
    for i in range(2):
        vid = f"weg{i}"
        buendel = ordner / f"{vid}.zip"
        buendel.write_bytes(b"x" * 1000)
        thumb = settings.thumb_dir / f"{vid}.jpg"
        thumb.parent.mkdir(parents=True, exist_ok=True)
        thumb.write_bytes(b"t" * 100)
        db.add(Video(id=vid, channel_id="UCweg", title=f"Weg {i}",
                     status=VideoStatus.ARCHIVED, bundle_file=str(buendel),
                     thumb_file=thumb.name))
        db.add(Subtitle(video_id=vid, language="de", is_auto=False,
                        name_in_bundle="subs/de.orig.vtt"))
        volltext.video_indizieren(db, video_id=vid, titel=f"Weg {i}",
                                  beschreibung=None, kanal="Wegwerfkanal")
    heiss = settings.cache_dir / "weg0.source.mp4"
    heiss.parent.mkdir(parents=True, exist_ok=True)
    heiss.write_bytes(b"h" * 500)
    db.add(HotCopy(video_id="weg0", variant="source", path=str(heiss)))
    db.commit()
    from app.services import jobs as j

    j.enqueue_archive(db, "weg1")
    return ordner, heiss


def test_kanal_entfernen_raeumt_alles_ab(umgebung):
    from app.services import suche as volltext

    client, db = umgebung
    ordner, heiss = _kanal_mit_dateien(db)

    r = client.delete("/api/channels/UCweg", params={"dateien": True})
    assert r.status_code == 200
    d = r.json()
    assert d["videos_entfernt"] == 2
    assert d["bytes_freigegeben"] > 0

    # Datenbank leer
    assert db.get(Channel, "UCweg") is None
    assert db.get(Video, "weg0") is None
    # Suchindex leer
    assert volltext.video_treffer(db, "wegwerfkanal") == []
    # Auftraege weg
    assert client.get("/api/jobs").json() == []
    # Platte leer
    assert not ordner.exists()
    assert not heiss.exists()
    # Der andere Kanal ist unberuehrt
    assert db.get(Channel, "UCtest") is not None
    assert db.get(Video, "v0") is not None


def test_kanal_entfernen_kann_buendel_behalten(umgebung):
    client, db = umgebung
    ordner, _ = _kanal_mit_dateien(db)

    r = client.delete("/api/channels/UCweg", params={"dateien": False})
    assert r.status_code == 200
    assert r.json()["buendel_geloescht"] is False
    assert db.get(Channel, "UCweg") is None
    assert ordner.exists(), "Buendel sollten behalten werden"
    assert (ordner / "weg0.zip").is_file()


def test_unbekannten_kanal_entfernen(umgebung):
    client, _ = umgebung
    assert client.delete("/api/channels/UCnix").status_code == 404
