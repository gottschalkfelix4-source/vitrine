"""Der Knopf "Alle laden" - und was mehrmaliges Klicken anrichtet.

Anlass war eine Beobachtung im Betrieb: "Ich hab jetzt mehrmals auf alle wieder
hinzufuegen geklickt und jetzt sind da mehr Videos in der Warteschlange, als
auf dem Kanal ueberhaupt existieren."

Die Warteschlange enthielt tatsaechlich keine Dubletten - ein Video erzeugt im
Lauf seines Lebens nur mehrere *Auftraege* (Download, spaeter Verkleinerung,
womoeglich ein Hochstufen). Beim Nachsehen kam aber ein echter Fehler zutage,
und die Tests hier nageln beides fest.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api import library
from app.config import settings
from app.db import get_db
from app.models import Channel, Job, JobStatus, JobType, Video, VideoStatus
from tests.conftest import neue_sitzung


@pytest.fixture
def umgebung(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    settings.ensure_dirs()
    db = neue_sitzung()
    db.add(Channel(id="UCtest", name="Testkanal", auto_archive=True, archive_shorts=False))
    db.commit()
    app = FastAPI()
    app.include_router(library.router)
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app), db


def video(db, vid: str, status: str, **kw) -> Video:
    v = Video(id=vid, channel_id="UCtest", title=vid, status=status, **kw)
    db.add(v)
    db.commit()
    return v


def offene_auftraege(db) -> list[Job]:
    return list(
        db.scalars(
            select(Job).where(
                Job.type == JobType.VIDEO_ARCHIVE,
                Job.status.in_([JobStatus.PENDING, JobStatus.RUNNING]),
            )
        )
    )


# --------------------------------------------------------- Mehrfaches Klicken


def test_zweiter_klick_reiht_nichts_doppelt_ein(umgebung):
    """Der Kern der Beschwerde."""
    c, db = umgebung
    for i in range(5):
        video(db, f"v{i}", VideoStatus.NEW)

    erst = c.post("/api/channels/UCtest/download-all").json()
    assert erst["eingereiht"] == 5
    assert len(offene_auftraege(db)) == 5

    zweit = c.post("/api/channels/UCtest/download-all").json()
    assert zweit["eingereiht"] == 0
    assert zweit["wartete_schon"] == 5
    assert len(offene_auftraege(db)) == 5, "es darf kein einziger dazukommen"

    # Auch beim fuenften Mal nicht.
    for _ in range(3):
        c.post("/api/channels/UCtest/download-all")
    assert len(offene_auftraege(db)) == 5


def test_archivierte_werden_nicht_erneut_eingereiht(umgebung):
    """Genau das, wonach gefragt wurde: nur was wirklich fehlt."""
    c, db = umgebung
    video(db, "fertig", VideoStatus.ARCHIVED, bundle_file="/x.zip")
    video(db, "fehlt", VideoStatus.NEW)

    a = c.post("/api/channels/UCtest/download-all").json()
    assert a["eingereiht"] == 1
    assert a["bereits_archiviert"] == 1
    assert [j.target_id for j in offene_auftraege(db)] == ["fehlt"]
    assert db.get(Video, "fertig").status == VideoStatus.ARCHIVED


def test_verschwundene_werden_nicht_erneut_angefragt(umgebung):
    """Ein bei der Quelle geloeschtes Video erneut anzufragen kostet nur
    Anfragebudget, das YouTube ohnehin knapp bemisst."""
    c, db = umgebung
    video(db, "weg", VideoStatus.UNAVAILABLE)
    a = c.post("/api/channels/UCtest/download-all").json()
    assert a["eingereiht"] == 0
    assert a["nicht_verfuegbar"] == 1
    assert offene_auftraege(db) == []


def test_gescheiterte_bekommen_eine_neue_chance(umgebung):
    """Wer hier klickt, will die Nachzuegler von letzter Woche mitnehmen."""
    c, db = umgebung
    video(db, "kaputt", VideoStatus.FAILED, retry_count=3)
    a = c.post("/api/channels/UCtest/download-all").json()
    assert a["eingereiht"] == 1
    assert db.get(Video, "kaputt").status == VideoStatus.QUEUED


def test_laufender_download_wird_nicht_angetastet(umgebung):
    c, db = umgebung
    video(db, "laeuft", VideoStatus.DOWNLOADING)
    a = c.post("/api/channels/UCtest/download-all").json()
    assert a["laeuft_gerade"] == 1
    assert a["eingereiht"] == 0
    assert db.get(Video, "laeuft").status == VideoStatus.DOWNLOADING


# ------------------------------------------------- Uebersprungene bleiben es


def test_ausgeschlossene_shorts_bleiben_uebersprungen(umgebung):
    """Der Fehler, der beim Nachsehen zutage kam.

    Vorher wurde pauschal alles Uebersprungene auf "neu" gesetzt. Bei einem
    Kanal ohne Shorts hiess das: Die Shorts wurden neu, fielen gleich darauf
    durch dieselbe Regel und blieben als "neu" liegen - ohne Auftrag, ohne
    Begruendung, aber in jeder Zaehlung mitgefuehrt. Jeder weitere Klick
    wiederholte das.
    """
    c, db = umgebung
    video(db, "short", VideoStatus.SKIPPED, is_short=True,
          status_message="Shorts sind fuer diesen Kanal abgeschaltet")

    a = c.post("/api/channels/UCtest/download-all").json()
    assert a["regeln"] == 1
    assert a["eingereiht"] == 0

    v = db.get(Video, "short")
    assert v.status == VideoStatus.SKIPPED, "darf nicht auf 'neu' zurueckfallen"
    assert v.status_message, "die Begruendung muss erhalten bleiben"


def test_short_wird_geladen_sobald_der_kanal_sie_erlaubt(umgebung):
    """Die Kehrseite: Schaltet man Shorts ein, muessen die uebersprungenen
    nachtraeglich erreichbar sein - sonst waere das Ueberspringen endgueltig."""
    c, db = umgebung
    video(db, "short", VideoStatus.SKIPPED, is_short=True)
    db.get(Channel, "UCtest").archive_shorts = True
    db.commit()

    a = c.post("/api/channels/UCtest/download-all").json()
    assert a["eingereiht"] == 1
    assert db.get(Video, "short").status == VideoStatus.QUEUED


def test_datumsgrenze_des_kanals_wird_beachtet(umgebung):
    c, db = umgebung
    db.get(Channel, "UCtest").archive_since = datetime(2020, 1, 1, tzinfo=UTC)
    db.commit()
    video(db, "alt", VideoStatus.NEW, upload_date=datetime(2015, 5, 5, tzinfo=UTC))
    video(db, "neu", VideoStatus.NEW, upload_date=datetime(2024, 5, 5, tzinfo=UTC))

    a = c.post("/api/channels/UCtest/download-all").json()
    assert a["eingereiht"] == 1
    assert a["regeln"] == 1
    assert [j.target_id for j in offene_auftraege(db)] == ["neu"]


# ------------------------------------------------------- Ehrliche Auskunft


def test_bericht_erklaert_wo_die_videos_geblieben_sind(umgebung):
    """Beim zweiten Klick stand vorher nur "0 Videos eingereiht." - richtig,
    aber es beantwortet die Frage nicht, die man dann hat."""
    c, db = umgebung
    video(db, "fertig", VideoStatus.ARCHIVED)
    video(db, "weg", VideoStatus.UNAVAILABLE)
    video(db, "short", VideoStatus.SKIPPED, is_short=True)
    video(db, "offen", VideoStatus.NEW)

    a = c.post("/api/channels/UCtest/download-all").json()
    assert a == {
        "eingereiht": 1,
        "wartete_schon": 0,
        "laeuft_gerade": 0,
        "bereits_archiviert": 1,
        "nicht_verfuegbar": 1,
        "regeln": 1,
    }


def test_warteschlange_wird_nach_art_aufgeschluesselt(umgebung):
    """Die eigentliche Ursache des Missverstaendnisses: Ein Video erzeugt
    mehrere Auftraege, deshalb kann die Warteschlange mehr Eintraege haben, als
    der Kanal Videos hat. Ohne Aufschluesselung sieht das nach einem Fehler
    aus."""
    from app.services import jobs

    c, db = umgebung
    video(db, "v1", VideoStatus.ARCHIVED)
    video(db, "v2", VideoStatus.NEW)
    jobs.enqueue(db, JobType.VIDEO_RECODE, "v1", priority=jobs.PRIO_RECODE)
    jobs.enqueue(db, JobType.VIDEO_UPGRADE, "v1", priority=jobs.PRIO_RECODE)
    c.post("/api/channels/UCtest/download-all")

    daten = c.get("/api/jobs/aktiv").json()
    assert daten["nach_art"] == {
        JobType.VIDEO_ARCHIVE: 1,
        JobType.VIDEO_RECODE: 1,
        JobType.VIDEO_UPGRADE: 1,
    }
    assert daten["wartend"] == 3
