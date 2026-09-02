"""Sauberes Herunterfahren waehrend eines laufenden Downloads.

Der Anlass ist der Alltagsfall: Der Container wird aktualisiert, waehrend ein
Kanal gerade Videos holt. Vorher hiess das dreimal Verlust - der Auftrag stand
danach als Fehlschlag da, der Versuchszaehler stieg, und die halbe Datei wurde
beim naechsten Start weggeworfen.

Geprueft wird deshalb nicht "es stuerzt nicht ab", sondern die drei Zusagen,
die daraus folgen:

1. Der Auftrag kommt zurueck in die Warteschlange, nicht in die Fehlerliste.
2. Der angefangene Download bleibt liegen und wird fortgesetzt.
3. Truemmer eines echten Absturzes werden trotzdem verworfen.
"""

from __future__ import annotations

import threading

import pytest

from app.config import settings
from app.models import Channel, Job, JobStatus, Video, VideoStatus
from app.services import abbruch, jobs
from app.workers import archive
from tests.conftest import neue_sitzung


@pytest.fixture(autouse=True)
def sauberes_signal():
    """Kein Test darf das Signal fuer den naechsten stehen lassen."""
    abbruch.zuruecksetzen()
    yield
    abbruch.zuruecksetzen()


@pytest.fixture
def umgebung(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    settings.ensure_dirs()
    db = neue_sitzung()
    db.add(Channel(id="UCtest", name="Testkanal"))
    db.add(Video(id="dQw4w9WgXcQ", channel_id="UCtest", title="Langes Video",
                 status=VideoStatus.QUEUED))
    db.commit()
    return db, tmp_path


# ------------------------------------------------------------------- Signal


def test_pruefen_wirft_erst_nach_der_anforderung():
    assert abbruch.laeuft_herunter() is False
    abbruch.pruefen()  # darf nichts tun

    abbruch.anfordern()
    assert abbruch.laeuft_herunter() is True
    with pytest.raises(abbruch.Abgebrochen):
        abbruch.pruefen()


def test_signal_gilt_ueber_strangenzen_hinweg():
    """Der Downloader laeuft in einem anderen Strang als der, der stoppt."""
    gesehen: list[bool] = []

    def strang():
        gesehen.append(abbruch.laeuft_herunter())

    abbruch.anfordern()
    t = threading.Thread(target=strang)
    t.start()
    t.join()
    assert gesehen == [True]


# ---------------------------------------------------- Auftrag statt Fehlschlag


def test_unterbrochener_auftrag_wartet_wieder(umgebung):
    db, _ = umgebung
    job = jobs.enqueue_archive(db, "dQw4w9WgXcQ")
    job.status = JobStatus.RUNNING
    job.progress = 0.42
    db.commit()

    jobs.unterbrochen(db, job, "beim Herunterfahren unterbrochen")

    frisch = db.get(Job, job.id)
    assert frisch.status == JobStatus.PENDING
    assert frisch.started_at is None
    assert frisch.progress == 0.0


def test_unterbrechung_verbraucht_keinen_versuch(umgebung):
    """Der Unterschied zu einem Fehlschlag. Wer waehrend eines langen Downloads
    dreimal den Container aktualisiert, darf damit kein Video verbrennen."""
    db, _ = umgebung
    video = db.get(Video, "dQw4w9WgXcQ")
    job = jobs.enqueue_archive(db, "dQw4w9WgXcQ")

    for _ in range(3):
        job.status = JobStatus.RUNNING
        db.commit()
        jobs.unterbrochen(db, job, "beim Herunterfahren unterbrochen")

    assert video.retry_count == 0
    assert db.get(Job, job.id).status == JobStatus.PENDING


# --------------------------------------------------------- Download fortsetzen


def test_abbruch_laesst_den_halben_download_liegen(umgebung, monkeypatch):
    """Der Kern der Sache: Ohne das faengt ein 4K-Video wieder bei null an."""
    db, _ = umgebung
    job = jobs.enqueue_archive(db, "dQw4w9WgXcQ")
    ordner = settings.tmp_dir / "dQw4w9WgXcQ"

    def download_bricht_ab(video_id, ziel, **_kw):
        # So verhaelt sich yt-dlp, wenn der Fortschritts-Hook abbricht: Die
        # Teildatei liegt da, die Ausnahme kommt hoch.
        ziel.mkdir(parents=True, exist_ok=True)
        (ziel / f"{video_id}.f137.mp4.part").write_bytes(b"halb geladen")
        raise abbruch.Abgebrochen("Dienst faehrt herunter")

    monkeypatch.setattr(archive.ytdlp, "download_video", download_bricht_ab)

    with pytest.raises(abbruch.Abgebrochen):
        archive.archivieren(db, job)

    assert ordner.is_dir(), "der halbe Download wurde weggeworfen"
    assert (ordner / "dQw4w9WgXcQ.f137.mp4.part").read_bytes() == b"halb geladen"
    assert (ordner / archive.FORTSETZMARKE).is_file()
    assert db.get(Video, "dQw4w9WgXcQ").status == VideoStatus.QUEUED
    assert db.get(Job, job.id).status == JobStatus.PENDING


def test_naechster_lauf_setzt_fort_statt_neu_zu_laden(umgebung, monkeypatch):
    db, _ = umgebung
    job = jobs.enqueue_archive(db, "dQw4w9WgXcQ")
    ordner = settings.tmp_dir / "dQw4w9WgXcQ"
    ordner.mkdir(parents=True, exist_ok=True)
    (ordner / "dQw4w9WgXcQ.f137.mp4.part").write_bytes(b"halb geladen")
    (ordner / archive.FORTSETZMARKE).touch()

    vorgefunden: list[bool] = []

    def download(video_id, ziel, **_kw):
        vorgefunden.append((ziel / f"{video_id}.f137.mp4.part").is_file())
        raise abbruch.Abgebrochen("gleich nochmal Schluss")

    monkeypatch.setattr(archive.ytdlp, "download_video", download)
    with pytest.raises(abbruch.Abgebrochen):
        archive.archivieren(db, job)

    assert vorgefunden == [True], "die Teildatei war beim zweiten Lauf weg"


def test_truemmer_ohne_marke_werden_verworfen(umgebung, monkeypatch):
    """Gegenprobe. Ein Ordner ohne Marke stammt aus einem Absturz mitten im
    Umpacken - was davon brauchbar ist, laesst sich nicht feststellen."""
    db, _ = umgebung
    job = jobs.enqueue_archive(db, "dQw4w9WgXcQ")
    ordner = settings.tmp_dir / "dQw4w9WgXcQ"
    ordner.mkdir(parents=True, exist_ok=True)
    (ordner / "halbes.mp4").write_bytes(b"kaputt")

    vorgefunden: list[bool] = []

    def download(video_id, ziel, **_kw):
        vorgefunden.append((ziel / "halbes.mp4").exists())
        raise RuntimeError("egal")

    monkeypatch.setattr(archive.ytdlp, "download_video", download)
    with pytest.raises(RuntimeError):
        archive.archivieren(db, job)

    assert vorgefunden == [False], "Truemmer eines Absturzes wurden weiterverwendet"


def test_marke_wird_nach_dem_einloesen_entfernt(umgebung, monkeypatch):
    """Sonst blieben Reste eines spaeteren echten Fehlschlags fuer immer
    liegen und wuerden jeden weiteren Versuch vergiften."""
    db, _ = umgebung
    job = jobs.enqueue_archive(db, "dQw4w9WgXcQ")
    ordner = settings.tmp_dir / "dQw4w9WgXcQ"
    ordner.mkdir(parents=True, exist_ok=True)
    (ordner / archive.FORTSETZMARKE).touch()

    monkeypatch.setattr(archive.ytdlp, "download_video",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("kaputt")))
    with pytest.raises(RuntimeError):
        archive.archivieren(db, job)

    assert not ordner.exists(), "nach einem echten Fehlschlag muss der Ordner weg sein"


def test_gescheiterter_download_raeumt_weiterhin_auf(umgebung, monkeypatch):
    """Die alte Zusage darf nicht verlorengehen: Ein Fehlschlag hinterlaesst
    keine Reste, die sich sonst ueber Monate ansammeln."""
    db, _ = umgebung
    job = jobs.enqueue_archive(db, "dQw4w9WgXcQ")
    ordner = settings.tmp_dir / "dQw4w9WgXcQ"

    def download(video_id, ziel, **_kw):
        ziel.mkdir(parents=True, exist_ok=True)
        (ziel / "rest.mp4").write_bytes(b"x" * 100)
        raise RuntimeError("Netz weg")

    monkeypatch.setattr(archive.ytdlp, "download_video", download)
    with pytest.raises(RuntimeError):
        archive.archivieren(db, job)

    assert not ordner.exists()
    assert db.get(Job, job.id).status == JobStatus.FAILED


# ------------------------------------------------------------------- ffmpeg


def _langer_lauf() -> list[str]:
    """Ein ffmpeg-Aufruf, der lange genug laeuft, um abgebrochen zu werden,
    und dabei Fortschritt nach stdout meldet."""
    return [
        "ffmpeg", "-nostdin", "-loglevel", "error",
        "-f", "lavfi", "-i", "testsrc=size=640x360:rate=30:d=60",
        "-c:v", "libx264", "-preset", "veryslow",
        "-progress", "pipe:1", "-nostats",
        "-f", "null", "-",
    ]


def test_ffmpeg_abbruch_beim_herunterfahren_ist_kein_fehler():
    """Beim Herunterfahren muss run_ffmpeg Abgebrochen werfen, nicht
    MediaError - sonst wuerde die Verkleinerung als Fehlschlag vermerkt."""
    from app.services import media

    abbruch.anfordern()
    with pytest.raises(abbruch.Abgebrochen):
        media.run_ffmpeg(
            # -progress pipe:1 ist noetig: Die Abbruchpruefung sitzt in der
            # Schleife ueber stdout. Ohne Fortschrittsausgabe dort laeuft sie
            # nie - genau so, wie es die echten Kommandos aufrufen.
            _langer_lauf(),
            abbruch=abbruch.laeuft_herunter,
        )


def test_ffmpeg_abbruch_durch_den_nutzer_bleibt_ein_fehler():
    """Gegenprobe: Ein vom Nutzer abgebrochener Auftrag ist weiterhin einer."""
    from app.services import media

    with pytest.raises(media.MediaError, match="abgebrochen"):
        media.run_ffmpeg(
            _langer_lauf(),
            abbruch=lambda: True,
        )
