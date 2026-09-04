"""Auftraege, die nichts mehr zu tun haben.

Der Anlass ist gemessen. Bei einem Kanal mit 3363 Videos standen 3788 wartende
Archivierungsauftraege in der Warteschlange, obwohl nur 1736 Videos ueberhaupt
noch etwas brauchten. Von 25 stichprobenartig gepruefeten Auftraegen zeigten
alle 25 auf Videos, die laengst im Archiv lagen - Altlasten aus der Zeit, bevor
"Alle laden" nur noch einreihte, was wirklich fehlt.

Der Schaden war nicht der falsche Zaehler. Der Archivierer prueft beim Start
nicht, was schon da ist: Jeder dieser Auftraege haette ein fertiges Video noch
einmal vollstaendig geholt und dafuer das IP-Budget verbrannt, das YouTube je
Adresse zuteilt - also genau die Bandbreite, fuer die die VPN-Tunnel
eingerichtet wurden.

Zwei Vorkehrungen, hier beide geprueft: ein Wachposten im Archivierer, damit
so ein Auftrag nichts kostet, und ein Aufraeumen beim Start, damit die
vorhandenen verschwinden.
"""

from __future__ import annotations

import pytest

from app.models import Channel, Job, JobStatus, JobType, Video, VideoStatus
from app.services import jobs, ytdlp
from tests.conftest import neue_sitzung


@pytest.fixture
def umgebung(tmp_path, monkeypatch):
    from app.workers import archive

    monkeypatch.setattr(archive.settings, "data_dir", tmp_path)
    archive.settings.ensure_dirs()

    db = neue_sitzung()
    db.add(Channel(id="UCtest", name="Testkanal"))
    db.commit()
    return db


def _archiviertes_video(db, tmp_path, video_id="fertig1"):
    """Ein Video, das vollstaendig im Kaltspeicher liegt."""
    buendel = tmp_path / f"{video_id}.zip"
    buendel.write_bytes(b"PK\x05\x06" + b"\0" * 18)  # leeres, gueltiges ZIP
    db.add(Video(
        id=video_id, channel_id="UCtest", title="Laengst da",
        status=VideoStatus.ARCHIVED, bundle_file=str(buendel), bundle_bytes=22,
    ))
    db.commit()
    return buendel


# ------------------------------------------------------------ Der Wachposten


def test_archiviertes_video_wird_nicht_erneut_geladen(umgebung, tmp_path, monkeypatch):
    """Der Kern: Ein Auftrag fuer ein fertiges Video kostet keinen Download.

    Ein Auftrag kann Tage in der Warteschlange stehen; in dieser Zeit kann
    dasselbe Video ueber einen anderen Auftrag hereingekommen sein. Beim
    Einreihen war er richtig und ist es jetzt nicht mehr.
    """
    from app.workers.archive import archivieren

    db = umgebung
    _archiviertes_video(db, tmp_path)

    def darf_nicht(*a, **kw):
        raise AssertionError("es wurde trotzdem heruntergeladen")

    monkeypatch.setattr(ytdlp, "download_video", darf_nicht)

    job = jobs.enqueue_archive(db, "fertig1")
    geholt = jobs.claim_next(db, [JobType.VIDEO_ARCHIVE])
    archivieren(db, geholt)

    frisch = db.get(Job, job.id)
    assert frisch.status == JobStatus.DONE
    assert "bereits" in (frisch.message or "")
    # Und das Video bleibt unangetastet archiviert.
    assert db.get(Video, "fertig1").status == VideoStatus.ARCHIVED


def test_fehlendes_buendel_wird_sehr_wohl_neu_geholt(umgebung, tmp_path, monkeypatch):
    """Geprueft wird die Datei, nicht der Zustand allein.

    Wer die Buendel von Hand aus dem Kaltspeicher raeumt, soll sie
    wiederbekommen koennen - sonst waere der Wachposten eine Falle.
    """
    from app.workers.archive import archivieren

    db = umgebung
    buendel = _archiviertes_video(db, tmp_path)
    buendel.unlink()  # der Kaltspeicher ist weg

    versucht: list[str] = []

    def merken(video_id, *a, **kw):
        versucht.append(video_id)
        raise ytdlp.YtdlpError("hier endet der Test")

    monkeypatch.setattr(ytdlp, "download_video", merken)

    jobs.enqueue_archive(db, "fertig1")
    geholt = jobs.claim_next(db, [JobType.VIDEO_ARCHIVE])
    with pytest.raises(ytdlp.YtdlpError):
        archivieren(db, geholt)
    assert versucht == ["fertig1"]


# ---------------------------------------------------------------- Aufraeumen


def test_auftraege_fuer_fertige_videos_verschwinden(umgebung, tmp_path):
    db = umgebung
    _archiviertes_video(db, tmp_path)
    db.add(Video(id="offen1", channel_id="UCtest", title="Fehlt noch",
                 status=VideoStatus.QUEUED))
    db.commit()

    jobs.enqueue_archive(db, "fertig1")
    behalten = jobs.enqueue_archive(db, "offen1")

    ergebnis = jobs.gegenstandslose_entfernen(db)
    assert ergebnis["erledigt"] == 1
    assert ergebnis["geblieben"] == 1
    assert db.get(Job, behalten.id) is not None


def test_doppelte_werden_auf_einen_zusammengezogen(umgebung):
    """``enqueue`` verhindert Doppelte eigentlich - die Pruefung ist aber ein
    SELECT vor einem INSERT ohne Schluessel darauf. Laufen zwei Einreihungen
    gleichzeitig, schluepfen beide durch."""
    db = umgebung
    db.add(Video(id="offen1", channel_id="UCtest", title="Fehlt noch",
                 status=VideoStatus.QUEUED))
    db.commit()

    erster = jobs.enqueue_archive(db, "offen1")
    # Am Schutz vorbei, so wie es das Wettrennen tut.
    zweiter = jobs.enqueue(db, JobType.VIDEO_ARCHIVE, "offen1", dedupe=False)
    assert erster.id != zweiter.id

    ergebnis = jobs.gegenstandslose_entfernen(db)
    assert ergebnis["doppelt"] == 1
    # Der aeltere bleibt: Er hat seinen Platz in der Reihenfolge schon.
    assert db.get(Job, erster.id) is not None
    assert db.get(Job, zweiter.id) is None


def test_laufende_auftraege_bleiben_unangetastet(umgebung, tmp_path):
    """Ein laufender Auftrag haelt einen halben Download in der Hand."""
    db = umgebung
    _archiviertes_video(db, tmp_path)
    jobs.enqueue_archive(db, "fertig1")
    laufend = jobs.claim_next(db, [JobType.VIDEO_ARCHIVE])
    assert laufend.status == JobStatus.RUNNING

    jobs.gegenstandslose_entfernen(db)
    assert db.get(Job, laufend.id) is not None


def test_uebersprungene_und_verschwundene_bleiben_stehen(umgebung):
    """Sie sehen gegenstandslos aus, sind es aber nicht zwingend: Ein
    geloeschtes Video kann wiederkommen, und wer die Kanalregeln aendert, will
    die uebersprungenen wiederhaben. Sie kosten einen Fehlversuch, keinen
    vollstaendigen Download."""
    db = umgebung
    db.add(Video(id="weg1", channel_id="UCtest", title="Geloescht",
                 status=VideoStatus.UNAVAILABLE))
    db.add(Video(id="skip1", channel_id="UCtest", title="Uebersprungen",
                 status=VideoStatus.SKIPPED))
    db.commit()
    a = jobs.enqueue_archive(db, "weg1")
    b = jobs.enqueue_archive(db, "skip1")

    ergebnis = jobs.gegenstandslose_entfernen(db)
    assert ergebnis["erledigt"] == 0
    assert db.get(Job, a.id) is not None
    assert db.get(Job, b.id) is not None


def test_leere_warteschlange_stoert_nicht(umgebung):
    assert jobs.gegenstandslose_entfernen(umgebung) == {
        "erledigt": 0, "doppelt": 0, "geblieben": 0
    }
