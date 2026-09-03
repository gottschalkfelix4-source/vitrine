"""Tests der Bibliotheks-API."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

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
    # v2 ist unavailable und zaehlt nicht mit: "1 von 3" waere irrefuehrend,
    # wenn eines davon geloescht ist und nie erreichbar wird.
    assert k["videos_gesamt"] == 2
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
    assert aufrufe[0] == "v1", "v2 ist verschwunden und faellt aus der Liste"


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
    k = d["kaltspeicher"]
    assert (k["bytes"], k["videos"], k["quelle_bytes"], k["gespart_bytes"]) == (
        1_000_000, 1, 2_000_000, 1_000_000
    )
    assert d["videos_nach_status"] == {"archived": 1, "queued": 1, "unavailable": 1}
    # v0 ist 600 s lang und belegt 1 MB - daraus rechnet sich die Prognose.
    assert k["dauer_s"] == 600
    assert k["bytes_je_sekunde"] == round(1_000_000 / 600)


def test_speicher_je_kanal_und_groesste(umgebung):
    client, _ = umgebung
    d = client.get("/api/storage").json()
    (kanal,) = d["je_kanal"]
    assert kanal["name"] == "Testkanal" and kanal["videos"] == 1
    assert [g["id"] for g in d["groesste"]] == ["v0"]


def test_hochrechnung_nutzt_eigene_messwerte(umgebung):
    """Die wichtigste Zahl der Seite: Was kaeme noch dazu? Sie beruht auf dem
    eigenen Schnitt, sobald etwas archiviert ist - nicht auf einer Faustzahl."""
    client, db = umgebung
    db.add(Video(id="offen1", channel_id="UCtest", title="Offen", status=VideoStatus.NEW,
                 duration_s=1200))
    db.commit()

    h = client.get("/api/storage").json()["hochrechnung"]
    assert h["gemessen"] is True
    assert h["offene_videos"] == 2  # v1 (queued, 300 s) und offen1 (1200 s)
    # 1500 s zu je (1 MB / 600 s) = rund 2,5 MB
    assert h["offene_dauer_s"] == 1500
    assert h["bytes_geschaetzt"] == pytest.approx(2_500_000, rel=0.01)


def test_hochrechnung_ohne_messwerte_ist_als_annahme_gekennzeichnet(umgebung):
    """Ohne ein einziges archiviertes Video gibt es nichts zu messen - das
    muss dabeistehen, sonst liest man eine Hausnummer als Zusage."""
    client, db = umgebung
    v = db.get(Video, "v0")
    v.status = VideoStatus.NEW
    v.bundle_bytes = None
    db.commit()

    h = client.get("/api/storage").json()["hochrechnung"]
    assert h["gemessen"] is False


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
    # v2 fehlt bewusst: verschwundene Videos gehoeren nicht in die Liste.
    assert {"v0", "v1"} <= nur_videos and "v2" not in nur_videos


def test_kanaldetail_zaehlt_nach_art(umgebung):
    client, db = umgebung
    db.add(Video(id="s1", channel_id="UCtest", title="Short", is_short=True))
    db.add(Video(id="l1", channel_id="UCtest", title="Stream", was_live=True))
    db.commit()
    z = client.get("/api/channels/UCtest").json()["zaehler"]
    assert z == {"videos": 2, "shorts": 1, "live": 1, "verschwunden": 1}


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


# ------------------------------------------------------ Video aus dem Archiv


def test_video_aus_archiv_entfernen(umgebung):
    from app.services import suche as volltext

    client, db = umgebung
    v = db.get(Video, "v0")
    buendel = settings.bundle_dir / "UCtest" / "v0.zip"
    buendel.parent.mkdir(parents=True, exist_ok=True)
    buendel.write_bytes(b"z" * 2000)
    thumb = settings.thumb_dir / "v0.jpg"
    thumb.write_bytes(b"t" * 50)
    v.bundle_file, v.thumb_file = str(buendel), thumb.name
    db.commit()

    r = client.delete("/api/videos/v0")
    assert r.status_code == 200
    assert r.json()["bytes_freigegeben"] == 2050

    db.expire_all()
    v = db.get(Video, "v0")
    assert v is not None, "der Datensatz bleibt - das Video gehoert zum Kanal"
    assert v.status == VideoStatus.SKIPPED
    assert v.bundle_file is None and v.thumb_file is None
    assert not buendel.exists() and not thumb.exists()
    assert volltext.video_treffer(db, "video 0") == []
    # Es bleibt in der Playlist, jetzt als nicht archivierte Position.
    d = client.get("/api/playlists/PLabc").json()
    assert {p["video"]["id"]: p["video"]["status"] for p in d["positionen"]}["v0"] == "skipped"


def test_nicht_archiviertes_video_entfernen_gibt_409(umgebung):
    client, _ = umgebung
    assert client.delete("/api/videos/v1").status_code == 409
    assert client.delete("/api/videos/nixda").status_code == 404


# ------------------------------------------------- Erst erfassen, dann laden


def test_kanal_wird_standardmaessig_nur_erfasst():
    """Der Standard muss "nur erfassen" sein: Ein Kanal mit tausenden Videos
    wuerde sonst beim Aufnehmen eine tagelange Warteschlange erzeugen, bevor
    man ueberhaupt gesehen hat, was drin ist."""
    from app.api.library import KanalAnlegen

    assert KanalAnlegen(url="@test").sofort_archivieren is False
    # SQLAlchemy setzt Vorgabewerte erst beim Schreiben, nicht beim Erzeugen.
    from app.models import Channel as C

    assert C.__table__.c.auto_archive.default.arg is False


def test_offene_zaehlen_vor_dem_klick(umgebung):
    """Die Oberflaeche soll vor dem Herunterladen sagen koennen, worauf man
    sich einlaesst."""
    client, db = umgebung
    for i in range(3):
        db.add(Video(id=f"o{i}", channel_id="UCtest", title=f"Offen {i}",
                     status=VideoStatus.NEW, duration_s=600))
    db.commit()

    d = client.get("/api/channels/UCtest/downloadable").json()
    # v1 (queued) und v2 (unavailable) zaehlen nicht, v0 ist archiviert.
    assert d["anzahl"] == 3
    assert d["dauer_s"] == 1800
    assert d["bytes_geschaetzt"] > 0


def test_offene_zaehlen_beachtet_die_kanalregeln(umgebung):
    """Ein Kanal ohne Shorts darf sie auch hier nicht mitzaehlen - sonst
    verspricht die Anzeige mehr, als der Download liefert."""
    client, db = umgebung
    db.add(Video(id="s1", channel_id="UCtest", title="Short", status=VideoStatus.NEW,
                 is_short=True, duration_s=30))
    db.add(Video(id="n1", channel_id="UCtest", title="Normal", status=VideoStatus.NEW,
                 duration_s=300))
    db.commit()

    assert client.get("/api/channels/UCtest/downloadable").json()["anzahl"] == 1

    db.get(Channel, "UCtest").archive_shorts = True
    db.commit()
    assert client.get("/api/channels/UCtest/downloadable").json()["anzahl"] == 2


def test_alle_laden_reiht_ein(umgebung):
    client, db = umgebung
    db.add(Video(id="o1", channel_id="UCtest", title="Offen", status=VideoStatus.NEW))
    db.add(Video(id="f1", channel_id="UCtest", title="Fehlgeschlagen", status=VideoStatus.FAILED))
    db.add(Video(id="u1", channel_id="UCtest", title="Uebersprungen", status=VideoStatus.SKIPPED))
    db.commit()

    r = client.post("/api/channels/UCtest/download-all")
    assert r.status_code == 202
    # Fehlgeschlagene und uebersprungene werden wieder aufgenommen - wer hier
    # klickt, will alles haben.
    #
    # Vier, nicht drei: "v1" aus der Vorbereitung steht auf "wartet", hat aber
    # keinen Auftrag. Ein solches Video haengt fest - es wuerde nie geladen,
    # weil niemand mehr danach sieht. Der Knopf holt es mit zurueck, und genau
    # das ist der Sinn von "nur was wirklich fehlt".
    assert r.json()["eingereiht"] == 4

    eingereiht = {j.target_id for j in db.scalars(select(Job).where(Job.type == JobType.VIDEO_ARCHIVE))}
    assert eingereiht == {"o1", "f1", "u1", "v1"}
    assert db.get(Video, "o1").status == VideoStatus.QUEUED
    # Das archivierte und das verschwundene bleiben aussen vor.
    assert "v0" not in eingereiht and "v2" not in eingereiht


def test_alle_laden_verdoppelt_nichts(umgebung):
    client, db = umgebung
    db.add(Video(id="o1", channel_id="UCtest", title="Offen", status=VideoStatus.NEW))
    db.commit()

    client.post("/api/channels/UCtest/download-all")
    zweiter = client.post("/api/channels/UCtest/download-all").json()
    assert zweiter["eingereiht"] == 0, "bereits eingereihte duerfen nicht doppelt kommen"

    # Auf die Ziele geprueft statt auf eine feste Gesamtzahl: Der Test soll
    # sagen "nichts doppelt", nicht "genau ein Auftrag" - sonst bricht er,
    # sobald die Vorbereitung ein Video mehr anlegt, und sagt trotzdem nichts
    # ueber Dubletten aus.
    ziele = [j.target_id for j in db.scalars(select(Job).where(Job.type == JobType.VIDEO_ARCHIVE))]
    assert len(ziele) == len(set(ziele)), f"Dubletten in der Warteschlange: {ziele}"
    assert "o1" in ziele


def test_alle_laden_bei_unbekanntem_kanal(umgebung):
    client, _ = umgebung
    assert client.post("/api/channels/UCnix/download-all").status_code == 404
    assert client.get("/api/channels/UCnix/downloadable").status_code == 404


# ------------------------------------------------------- Fortschrittsanzeige


def test_aktive_auftraege_knapp_gehalten(umgebung):
    """Die Oberflaeche fragt das im Sekundentakt ab - die Antwort muss klein
    bleiben und trotzdem den Titel nennen, sonst steht dort nur eine ID."""
    client, db = umgebung
    from app.services import jobs as j

    j.enqueue_archive(db, "v1")
    laufend = j.claim_next(db, [JobType.VIDEO_ARCHIVE])
    j.fortschritt(db, laufend, 0.42, "Lade 42 %")
    j.enqueue_channel_sync(db, "UCtest")

    d = client.get("/api/jobs/aktiv").json()
    assert d["wartend"] == 1
    (eintrag,) = d["laufend"]
    assert eintrag["art"] == JobType.VIDEO_ARCHIVE
    assert eintrag["titel"] == "Video 1"
    assert eintrag["fortschritt"] == pytest.approx(0.42)
    assert eintrag["meldung"] == "Lade 42 %"


def test_aktive_auftraege_leer(umgebung):
    client, _ = umgebung
    antwort = client.get("/api/jobs/aktiv").json()
    assert antwort["laufend"] == []
    assert antwort["wartend"] == 0
    # Die Oberflaeche unterscheidet an diesem Feld eine Zwangspause von einem
    # haengenden Dienst - von aussen sehen beide gleich aus.
    assert antwort["drosselung"]["pausiert"] is False


def test_alle_gescheiterten_auf_einmal_wiederholen(umgebung):
    """Nach einer Sperre durch YouTube stehen Dutzende Auftraege rot in der
    Liste, alle mit demselben Fehler. Sie einzeln anzuklicken ist keine
    zumutbare Bedienung."""
    client, db = umgebung

    for i in (1, 2):
        db.add(
            Job(
                type=JobType.VIDEO_ARCHIVE,
                target_id=f"v{i}",
                status=JobStatus.FAILED,
                error="Sign in to confirm you're not a bot",
            )
        )
    for i in (1, 2):
        video = db.get(Video, f"v{i}")
        video.status = VideoStatus.FAILED
        video.status_message = "Sign in to confirm you're not a bot"
        video.retry_count = 3
    db.commit()

    antwort = client.post("/api/jobs/retry-failed")
    assert antwort.status_code == 202
    assert antwort.json() == {"auftraege": 2, "videos": 2}

    db.expire_all()
    for auftrag in db.scalars(select(Job)):
        assert auftrag.status == JobStatus.PENDING
        assert auftrag.error is None
    for i in (1, 2):
        video = db.get(Video, f"v{i}")
        assert video.status == VideoStatus.QUEUED
        # Die Fehlschlaege lagen an der IP-Adresse, nicht am Video - sie
        # duerfen es nicht belasten.
        assert video.retry_count == 0


# ------------------------------------------------- Verschwundene Videos


def test_verschwundene_fallen_aus_der_videoliste(umgebung):
    """Geloeschte und privat gestellte Videos liessen sich nie holen - in der
    Videoliste waeren sie nur Rauschen. Beim JP-Kanal waren das 156 Kacheln
    "(ohne Titel)" mit einem Laden-Knopf, der nichts bewirkt haette."""
    client, _ = umgebung
    alle = {v["id"] for v in client.get("/api/videos", params={"nur_archiviert": False}).json()}
    assert "v2" not in alle, "verschwundenes Video steht noch in der Liste"

    # Wer sie sehen will, fragt ausdruecklich danach.
    gezielt = {v["id"] for v in client.get("/api/videos", params={"status": "unavailable"}).json()}
    assert gezielt == {"v2"}


def test_verschwundene_bleiben_in_der_playlist(umgebung):
    """Der Gegenpol: In der Playlist zaehlt gerade die Information, dass an
    Position 3 mal etwas war. Das ist der Unterschied zu TubeArchivist."""
    client, _ = umgebung
    d = client.get("/api/playlists/PLabc").json()
    zustaende = {p["video"]["id"]: p["video"]["status"] for p in d["positionen"]}
    assert zustaende["v2"] == "unavailable"
    assert len(d["positionen"]) == 3, "die Position darf nicht verschwinden"


def test_verschwundene_zaehlen_nicht_als_ladbar(umgebung):
    """Sonst verspricht "Alle laden (N)" mehr, als es einloesen kann."""
    client, _ = umgebung
    assert client.get("/api/channels/UCtest/downloadable").json()["anzahl"] == 0


# ------------------------------------------------- Vorschaubild aus der Quelle
#
# Der Anlass: Nach dem Erfassen eines Kanals mit 3363 Videos hatten 3361 davon
# kein Vorschaubild, weil eines erst beim Herunterladen entsteht. YouTube
# liefert die Adresse aber schon beim blossen Auflisten mit.


def test_bild_zeigt_auf_die_abgelegte_datei_wenn_archiviert(umgebung):
    client, db = umgebung
    db.get(Video, "v0").thumb_file = "v0.webp"
    db.commit()
    (v,) = [x for x in client.get("/api/videos?kanal=UCtest").json() if x["id"] == "v0"]
    assert v["bild"] == "/api/thumbs/v0.webp"


def test_bild_verweist_auf_die_quelle_solange_nichts_archiviert_ist(umgebung):
    """Der eigentliche Punkt: ein noch nicht geladenes Video hat trotzdem ein
    Bild - sonst besteht eine frisch erfasste Kanalseite aus grauen Kacheln."""
    client, db = umgebung
    db.add(Video(id="dQw4w9WgXcQ", channel_id="UCtest", title="Frisch erfasst",
                 status=VideoStatus.NEW))
    db.commit()
    (v,) = [x for x in client.get("/api/videos?kanal=UCtest&nur_archiviert=false").json()
            if x["id"] == "dQw4w9WgXcQ"]
    assert v["bild"] == "/api/thumbs/quelle/dQw4w9WgXcQ"


def test_verschwundene_videos_bekommen_kein_bild_versprochen(umgebung):
    """Zu einem geloeschten Video hat auch YouTube keins - ein Abruf waere
    sicher vergeblich."""
    client, db = umgebung
    db.add(Video(id="aaaaaaaaaaa", channel_id="UCtest", title="(ohne Titel)",
                 status=VideoStatus.UNAVAILABLE))
    db.commit()
    v = client.get("/api/videos?status=unavailable").json()
    assert [x["bild"] for x in v if x["id"] == "aaaaaaaaaaa"] == [None]


@pytest.mark.parametrize("kennung", [
    "../../etc/passwd",
    "kurz",
    "evil.example.com/x",
    "aaaaaaaaaa@",
])
def test_quellbild_nimmt_nur_echte_video_ids(umgebung, kennung):
    """Aus der Kennung wird eine Adresse gebaut, die der Server selbst abruft.
    Ohne feste Form liesse er sich zu beliebigen Zielen schicken."""
    client, _ = umgebung
    assert client.get(f"/api/thumbs/quelle/{kennung}").status_code in (400, 404)


def test_quellbild_wird_nur_einmal_geholt(umgebung, monkeypatch):
    """Beim zweiten Abruf darf nichts mehr ins Netz gehen - sonst laedt jeder
    Seitenaufbau die Bilder erneut."""
    client, _ = umgebung
    import io

    abrufe = []

    class Antwort(io.BytesIO):
        status = 200
        def __enter__(self): return self
        def __exit__(self, *_): return False

    def falscher_abruf(anfrage, timeout=None):
        abrufe.append(anfrage.full_url)
        return Antwort(b"\xff\xd8\xff\xe0" + b"x" * 5000)

    monkeypatch.setattr(library, "_ohne_bild", set())
    monkeypatch.setattr("urllib.request.urlopen", falscher_abruf)

    for _ in range(3):
        r = client.get("/api/thumbs/quelle/dQw4w9WgXcQ")
        assert r.status_code == 200
        assert r.content.startswith(b"\xff\xd8")
    assert len(abrufe) == 1, f"Bild wurde {len(abrufe)}-mal geholt statt einmal"
    assert "dQw4w9WgXcQ" in abrufe[0]


def test_quellbild_faellt_auf_kleinere_groesse_zurueck(umgebung, monkeypatch):
    """Die grossen Groessen gibt es nur bei neueren Videos; hqdefault immer.

    Geprueft wird beides: dass ein 404 den naechsten Versuch ausloest, und dass
    ein Platzhalterbild nicht als Treffer durchgeht - YouTube antwortet auf
    fehlende Groessen auch mal mit einem winzigen Bild statt mit einem Fehler.
    """
    client, _ = umgebung
    import io
    from urllib.error import HTTPError

    versucht = []

    class Antwort(io.BytesIO):
        status = 200
        def __enter__(self): return self
        def __exit__(self, *_): return False

    def falscher_abruf(anfrage, timeout=None):
        versucht.append(anfrage.full_url.rsplit("/", 1)[-1])
        if "hq720" in anfrage.full_url:
            raise HTTPError(anfrage.full_url, 404, "Not Found", {}, None)
        if "maxresdefault" in anfrage.full_url:
            return Antwort(b"\xff\xd8" + b"x" * 100)  # Platzhalter, zu klein
        return Antwort(b"\xff\xd8\xff\xe0" + b"x" * 5000)

    monkeypatch.setattr(library, "_ohne_bild", set())
    monkeypatch.setattr("urllib.request.urlopen", falscher_abruf)

    assert client.get("/api/thumbs/quelle/dQw4w9WgXcQ").status_code == 200
    # Nach dem 404 und dem Platzhalter bleibt die naechste Stufe - und dort
    # wird abgebrochen, hqdefault gar nicht mehr geholt.
    assert versucht == ["maxresdefault.jpg", "hq720.jpg", "sddefault.jpg"]


def test_kein_bild_wird_nicht_bei_jedem_aufbau_neu_versucht(umgebung, monkeypatch):
    """Sonst laeuft jeder Seitenaufbau in dieselben drei Fehlschlaege."""
    client, _ = umgebung
    from urllib.error import HTTPError

    abrufe = []

    def immer_weg(anfrage, timeout=None):
        abrufe.append(anfrage.full_url)
        raise HTTPError(anfrage.full_url, 404, "Not Found", {}, None)

    monkeypatch.setattr(library, "_ohne_bild", set())
    monkeypatch.setattr("urllib.request.urlopen", immer_weg)

    for _ in range(3):
        assert client.get("/api/thumbs/quelle/dQw4w9WgXcQ").status_code == 404
    assert len(abrufe) == len(library._BILDGROESSEN), (
        f"{len(abrufe)} Abrufe - nach dem ersten Fehlschlag darf nichts mehr ins Netz gehen"
    )


# ------------------------------------------------------------- Sortierung
#
# YouTube liefert beim Auflisten kein Datum. Sortiert wird deshalb nach dem
# Rang in der Uploads-Liste, sonst steht eine frisch erfasste Kanalseite in
# Einfuegereihenfolge da.


def test_sortierung_folgt_dem_rang_auch_ohne_datum(umgebung):
    client, db = umgebung
    # Bewusst in unsortierter Reihenfolge angelegt: Ohne Sortierung nach Rang
    # kaeme genau diese Einfuegereihenfolge zurueck.
    raenge = {"mittleres00": 1, "neuestes000": 0, "aeltestes00": 2}
    for kennung, rang in raenge.items():
        db.add(Video(id=kennung, channel_id="UCtest", title=f"Ohne Datum {kennung}",
                     status=VideoStatus.NEW, upload_date=None, uploads_position=rang))
    db.commit()
    ids = [v["id"] for v in client.get(
        "/api/videos?kanal=UCtest&nur_archiviert=false&sortierung=neu").json()
        if v["id"] in raenge]
    # Rang 0 ist das neueste Video und gehoert nach vorn.
    assert ids == ["neuestes000", "mittleres00", "aeltestes00"]

    rueckwaerts = [v["id"] for v in client.get(
        "/api/videos?kanal=UCtest&nur_archiviert=false&sortierung=alt").json()
        if v["id"] in raenge]
    assert rueckwaerts == ["aeltestes00", "mittleres00", "neuestes000"]


# ------------------------------------------------------------------ Qualitaet


def test_videoliste_nennt_die_aufloesung(umgebung):
    """Die Kachel zeigt ein Qualitaetsetikett - dafuer braucht sie die Werte
    schon in der Liste und nicht erst im Detail."""
    client, db = umgebung
    v = db.get(Video, "v0")
    v.width, v.height, v.fps = 3840, 2160, 29.97
    db.commit()

    (k,) = [x for x in client.get("/api/videos").json() if x["id"] == "v0"]
    assert (k["breite"], k["hoehe"]) == (3840, 2160)
    assert k["fps"] == pytest.approx(29.97)


def test_ohne_archivierung_bleibt_die_aufloesung_leer(umgebung):
    """Vor dem Herunterladen nennt YouTube beim Auflisten keine Aufloesung.
    Ein geratener Wert waere schlimmer als keiner - die Oberflaeche zeigt dann
    schlicht kein Etikett."""
    client, _ = umgebung
    (k,) = [x for x in client.get("/api/videos?nur_archiviert=false").json()
            if x["id"] == "v1"]
    assert k["hoehe"] is None
    assert k["breite"] is None
    assert k["fps"] is None


# ------------------------------------------- Qualitaet nachtraeglich anheben


@pytest.fixture
def mit_aufloesungen(umgebung):
    """v0 ist archiviert in 1080p, dazu ein hochkantiges und ein 4K-Video."""
    client, db = umgebung
    db.get(Video, "v0").width, db.get(Video, "v0").height = 1920, 1080
    db.add(Video(id="hochkant000", channel_id="UCtest", title="Senkrecht",
                 status=VideoStatus.ARCHIVED, width=1080, height=1920,
                 bundle_bytes=200_000_000))
    db.add(Video(id="schonvierk0", channel_id="UCtest", title="Schon 4K",
                 status=VideoStatus.ARCHIVED, width=3840, height=2160,
                 bundle_bytes=800_000_000))
    db.commit()
    return client, db


def test_vorschau_zaehlt_nur_was_darunter_liegt(mit_aufloesungen):
    client, _ = mit_aufloesungen
    v = client.get("/api/upgrade/vorschau?ziel=2160").json()
    # v0 und das hochkantige liegen bei 1080, das 4K-Video nicht.
    assert v["videos"] == 2
    assert v["nach_stufe"] == {"1080": 2}


def test_vorschau_misst_die_kurze_seite(mit_aufloesungen):
    """Ein hochkantiges 1080p-Video ist 1080x1920. Nach der Hoehe gemessen
    laege es ueber 1440 und fiele faelschlich heraus."""
    client, _ = mit_aufloesungen
    v = client.get("/api/upgrade/vorschau?ziel=1440").json()
    assert v["videos"] == 2, v["nach_stufe"]


def test_vorschau_schaetzt_den_zusatzbedarf(mit_aufloesungen):
    client, _ = mit_aufloesungen
    v = client.get("/api/upgrade/vorschau?ziel=2160").json()
    # 1080 -> 2160 ist die vierfache Pixelzahl.
    assert v["geschaetzt_bytes"] == pytest.approx(v["jetzt_bytes"] * 4, rel=0.01)
    assert v["zusatz_bytes"] == v["geschaetzt_bytes"] - v["jetzt_bytes"]


def test_vorschau_nennt_die_untergrenze_der_dauer(mit_aufloesungen):
    """Bei vielen Videos ist nicht die Leitung die Grenze, sondern YouTubes
    Drosselung von rund 300 Videos je Stunde."""
    client, _ = mit_aufloesungen
    assert client.get("/api/upgrade/vorschau?ziel=2160").json()["stunden_mindestens"] >= 0


def test_einreihen_erzeugt_je_video_einen_auftrag(mit_aufloesungen):
    client, db = mit_aufloesungen
    antwort = client.post("/api/upgrade?ziel=2160")
    assert antwort.status_code == 202
    assert antwort.json()["eingereiht"] == 2

    auftraege = list(db.scalars(select(Job).where(Job.type == JobType.VIDEO_UPGRADE)))
    assert {j.target_id for j in auftraege} == {"v0", "hochkant000"}
    # Ganz hinten in der Warteschlange: Ein Hochstufen darf nie vor einem
    # Video stehen, das ueberhaupt noch nicht im Archiv liegt.
    assert all(j.priority >= 900 for j in auftraege)


def test_unsinnige_zielstufe_wird_abgelehnt(mit_aufloesungen):
    client, _ = mit_aufloesungen
    assert client.post("/api/upgrade?ziel=1234").status_code == 400


def test_einzelnes_video_hochstufen(mit_aufloesungen):
    client, _ = mit_aufloesungen
    assert client.post("/api/videos/v0/upgrade?ziel=2160").status_code == 202
    assert client.post("/api/videos/v1/upgrade?ziel=2160").status_code == 409  # nicht archiviert
    assert client.post("/api/videos/gibtsnicht/upgrade").status_code == 404
