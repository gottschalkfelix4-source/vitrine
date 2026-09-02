"""Durchlauf der Archivierungskette mit echten Mediendateien.

Der Download selbst wird ersetzt - alles danach laeuft echt: ffprobe, das
Umpacken mit ffmpeg, das Buendeln, der Datenbankabgleich und zum Schluss das
Streamen aus dem entstandenen Buendel.

Damit ist genau die Naht abgedeckt, an der die Einzeltests nichts sagen: ob die
Datei, die der Archivierungsworker erzeugt, hinterher wirklich abspielbar ist.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import ArchiveCodec, settings
from app.models import Channel, Chapter, Job, JobStatus, JobType, Video, VideoStatus
from app.services import bundle, jobs, media, ytdlp
from tests.conftest import neue_sitzung

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe nicht verfuegbar",
)


@pytest.fixture(scope="session")
def quellvideo(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Ein echtes, kurzes Video im MKV-Behaelter - so, wie yt-dlp es abliefert.

    MKV ist hier der springende Punkt: Kein Browser spielt es ab, der Worker
    MUSS also umpacken.
    """
    ordner = tmp_path_factory.mktemp("quelle")
    ziel = ordner / "testvideo.mkv"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y",
         "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=15:duration=2",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
         "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30",
         "-c:a", "aac", "-b:a", "64k", "-shortest", str(ziel)],
        check=True, capture_output=True,
    )
    return ziel


@pytest.fixture
def umgebung(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "recode_min_height", 100)  # das Testvideo ist klein
    settings.ensure_dirs()

    db = neue_sitzung()
    db.add(Channel(id="UCtest", name="Testkanal", auto_archive=True))
    db.add(Video(id="vid1", channel_id="UCtest", title="Platzhalter", status=VideoStatus.QUEUED))
    db.commit()
    return db, tmp_path


def _download_ersetzen(
    monkeypatch: pytest.MonkeyPatch,
    quellvideo: Path,
    *,
    info_extra: dict | None = None,
) -> None:
    """Ersetzt nur den Netzzugriff. Alles danach laeuft echt."""

    def falscher_download(video_id, ziel, *, format_selector=None, fortschritt=None):
        ziel.mkdir(parents=True, exist_ok=True)
        kopie = ziel / f"{video_id}.mkv"
        shutil.copy2(quellvideo, kopie)
        if fortschritt:
            fortschritt(0.5, "haelfte")
            fortschritt(1.0, "fertig")
        untertitel = ziel / f"{video_id}.de.vtt"
        untertitel.write_bytes(b"WEBVTT\n\n00:00.000 --> 00:01.000\nHallo\n")
        vorschau = ziel / f"{video_id}.jpg"
        vorschau.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 500)
        info = {
            "id": video_id,
            "title": "Ein echtes Testvideo",
            "description": "Beschreibung mit Umlauten: Groesse",
            "duration": 2,
            "height": 240,
            "format_id": "137+140",
            "vcodec": "avc1",
            "acodec": "mp4a",
            "view_count": 4711,
            "like_count": 42,
            "upload_date": "20260115",
            "chapters": [
                {"start_time": 0.0, "end_time": 1.0, "title": "Anfang"},
                {"start_time": 1.0, "end_time": 2.0, "title": "Ende"},
            ],
        }
        info.update(info_extra or {})
        return ytdlp.DownloadResult(
            path=kopie, info=info, thumbnail=vorschau, subtitles=[("de", False, untertitel)]
        )

    monkeypatch.setattr(ytdlp, "download_video", falscher_download)


def _archivieren(db: Session) -> Job:
    from app.workers.archive import archivieren

    jobs.enqueue_archive(db, "vid1")
    laufend = jobs.claim_next(db, [JobType.VIDEO_ARCHIVE])
    assert laufend is not None
    archivieren(db, laufend)
    return laufend


# ------------------------------------------------------------ Der Regelfall


def test_archivierung_erzeugt_abspielbares_buendel(umgebung, quellvideo, monkeypatch):
    db, _tmp = umgebung
    _download_ersetzen(monkeypatch, quellvideo)

    job = _archivieren(db)
    assert job.status == JobStatus.DONE, job.error

    video = db.get(Video, "vid1")
    assert video.status == VideoStatus.ARCHIVED
    assert video.bundle_file and Path(video.bundle_file).is_file()
    assert video.bundle_bytes and video.bundle_bytes > 0
    assert video.source_bytes and video.source_bytes > 0

    # Das MKV muss in einen browsertauglichen Behaelter gewandert sein.
    assert video.media_name.endswith(".mp4"), f"noch immer {video.media_name}"

    ok, meldung = bundle.verify_bundle(Path(video.bundle_file))
    assert ok, meldung


def test_gebuendeltes_video_ist_byteweise_lesbar(umgebung, quellvideo, monkeypatch):
    """Der eigentliche Zweck: Nach dem Archivieren muss sich das Video direkt
    aus dem Buendel streamen lassen."""
    db, _tmp = umgebung
    _download_ersetzen(monkeypatch, quellvideo)
    _archivieren(db)

    video = db.get(Video, "vid1")
    with bundle.BundleReader(Path(video.bundle_file)) as r:
        vollstaendig = b"".join(r.media_range())
        assert len(vollstaendig) == r.media_size
        # Ein MP4 beginnt mit einem Box-Kopf; 'ftyp' steht in den ersten Bytes.
        assert b"ftyp" in vollstaendig[:64], "kein gueltiger MP4-Kopf am Anfang"
        # Sprung mitten hinein liefert dieselben Bytes wie das Vollstaendige.
        mitte = len(vollstaendig) // 2
        assert b"".join(r.media_range(mitte, 200)) == vollstaendig[mitte : mitte + 200]


def test_metadaten_landen_in_der_datenbank(umgebung, quellvideo, monkeypatch):
    db, _tmp = umgebung
    _download_ersetzen(monkeypatch, quellvideo)
    _archivieren(db)

    video = db.get(Video, "vid1")
    assert video.title == "Ein echtes Testvideo"
    assert video.view_count == 4711
    assert video.like_count == 42
    assert video.upload_date and video.upload_date.year == 2026
    assert video.width == 320 and video.height == 240

    kapitel = list(db.scalars(select(Chapter).where(Chapter.video_id == "vid1")))
    assert [k.title for k in kapitel] == ["Anfang", "Ende"]
    assert video.subtitles and video.subtitles[0].language == "de"

    # Vorschaubild liegt AUSSERHALB des Buendels, damit das Grid es ohne
    # ZIP-Zugriff rendern kann.
    assert video.thumb_file
    assert (settings.thumb_dir / video.thumb_file).is_file()


def test_recodierung_wird_nachgelagert_eingereiht(umgebung, quellvideo, monkeypatch):
    """H.264-Quelle lohnt eine Recodierung - aber erst hinterher, damit das
    Video sofort verfuegbar ist."""
    db, _tmp = umgebung
    monkeypatch.setattr(settings, "archive_codec", ArchiveCodec.AV1)
    _download_ersetzen(monkeypatch, quellvideo)
    _archivieren(db)

    assert db.get(Video, "vid1").status == VideoStatus.ARCHIVED
    offen = list(db.scalars(select(Job).where(Job.type == JobType.VIDEO_RECODE)))
    assert len(offen) == 1 and offen[0].target_id == "vid1"


def test_ohne_recodierung_kein_zusatzauftrag(umgebung, quellvideo, monkeypatch):
    db, _tmp = umgebung
    monkeypatch.setattr(settings, "archive_codec", ArchiveCodec.COPY)
    _download_ersetzen(monkeypatch, quellvideo)
    _archivieren(db)

    assert list(db.scalars(select(Job).where(Job.type == JobType.VIDEO_RECODE))) == []


# ---------------------------------------------------------------- Fehlerwege


def test_stiller_360p_rueckfall_wird_nicht_als_archiviert_verbucht(umgebung, quellvideo, monkeypatch):
    """Der wichtigste Fehlerweg. Faellt yt-dlp auf Format 18 zurueck, darf das
    Video NICHT als erledigt gelten - sonst holt es nie wieder jemand."""
    db, _tmp = umgebung
    _download_ersetzen(monkeypatch, quellvideo, info_extra={"format_id": "18", "height": 360})

    job = _archivieren(db)
    assert job.status == JobStatus.FAILED
    assert "Format 18" in (job.error or "")

    video = db.get(Video, "vid1")
    assert video.status == VideoStatus.FAILED
    assert video.bundle_file is None
    assert video.retry_count == 1


def test_geloeschtes_video_wird_nicht_endlos_wiederholt(umgebung, monkeypatch):
    db, _tmp = umgebung

    def weg(*a, **kw):
        raise ytdlp.VideoUnavailable("Video unavailable")

    monkeypatch.setattr(ytdlp, "download_video", weg)
    job = _archivieren(db)

    # Auftrag gilt als erledigt, nicht als gescheitert - es gibt nichts zu
    # wiederholen, das Video ist bei der Quelle weg.
    assert job.status == JobStatus.DONE
    assert db.get(Video, "vid1").status == VideoStatus.UNAVAILABLE


def test_arbeitsordner_wird_auch_nach_fehler_geraeumt(umgebung, quellvideo, monkeypatch):
    db, _tmp = umgebung
    _download_ersetzen(monkeypatch, quellvideo, info_extra={"format_id": "18", "height": 360})
    _archivieren(db)
    assert not (settings.tmp_dir / "vid1").exists(), "Arbeitsordner blieb liegen"


# ------------------------------------------------------------ Warteschlange


def test_auftrag_wird_nur_einmal_vergeben(umgebung):
    """Zwei Arbeiterstraenge duerfen denselben Auftrag nicht doppelt greifen."""
    db, _ = umgebung
    jobs.enqueue_archive(db, "vid1")
    erster = jobs.claim_next(db, [JobType.VIDEO_ARCHIVE])
    zweiter = jobs.claim_next(db, [JobType.VIDEO_ARCHIVE])
    assert erster is not None
    assert zweiter is None


def test_warteschlange_beachtet_die_rangfolge(umgebung):
    """Eine wartende Wiedergabe geht einer Recodierung vor - sonst sitzt
    jemand vor einem Ladebalken, waehrend im Hintergrund tagelang kodiert wird."""
    db, _ = umgebung
    db.add(Video(id="vid2", channel_id="UCtest", title="zwei", status=VideoStatus.ARCHIVED))
    db.commit()

    jobs.enqueue(db, JobType.VIDEO_RECODE, "vid2", priority=jobs.PRIO_RECODE)
    jobs.enqueue_archive(db, "vid1")
    jobs.enqueue_prepare(db, "vid2", "h264")

    reihenfolge = []
    while (j := jobs.claim_next(db)) is not None:
        reihenfolge.append(j.type)
    assert reihenfolge == [JobType.VIDEO_PREPARE, JobType.VIDEO_ARCHIVE, JobType.VIDEO_RECODE]


def test_haengende_auftraege_werden_nach_neustart_freigegeben(umgebung):
    db, _ = umgebung
    jobs.enqueue_archive(db, "vid1")
    job = jobs.claim_next(db, [JobType.VIDEO_ARCHIVE])
    assert job.status == JobStatus.RUNNING

    # Simuliert einen harten Neustart mitten im Auftrag.
    assert jobs.reset_stale(db) == 1
    db.refresh(job)
    assert job.status == JobStatus.PENDING
    assert jobs.claim_next(db, [JobType.VIDEO_ARCHIVE]) is not None


# -------------------------------------------------------------- Behaelterwahl


@pytest.mark.parametrize(
    "vcodec,acodec,erwartet_suffix,ton_neu",
    [
        ("h264", "aac", ".mp4", False),
        ("av1", "opus", ".webm", False),
        ("vp9", "opus", ".webm", False),
        ("h264", "opus", ".mp4", True),   # Opus in MP4 ginge auf Apple stumm aus
        ("av1", "aac", ".webm", True),
        ("h264", None, ".mp4", False),
    ],
)
def test_behaelterwahl(vcodec, acodec, erwartet_suffix, ton_neu):
    info = media.MediaInfo(
        duration_s=10.0, width=1920, height=1080, fps=30.0,
        video_codec=vcodec, audio_codec=acodec, bitrate=1000, size_bytes=1000,
    )
    plan = media.plan_container(info)
    assert plan.suffix == erwartet_suffix
    assert plan.ton_umkodieren is ton_neu


def test_hochkantiges_video_wird_als_short_markiert(umgebung, monkeypatch, tmp_path_factory):
    """Ein Short, das ueber die Uploads-Liste kam, war bisher nicht als solches
    gekennzeichnet - es landete unter "Videos" und wurde in eine
    16:9-Buehne gezwungen. Hochkant ist das verlaessliche Merkmal."""
    ordner = tmp_path_factory.mktemp("hochkant")
    quelle = ordner / "short.mkv"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y",
         "-f", "lavfi", "-i", "testsrc2=size=240x426:rate=15:duration=1",
         "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30", "-an", str(quelle)],
        check=True, capture_output=True,
    )
    db, _tmp = umgebung
    # Shorts hier ausdruecklich erlauben - geprueft wird die Kennzeichnung,
    # nicht die Sperre (die hat ihren eigenen Test).
    db.get(Channel, "UCtest").archive_shorts = True
    db.commit()
    _download_ersetzen(monkeypatch, quelle, info_extra={"height": 426})
    _archivieren(db)

    video = db.get(Video, "vid1")
    assert video.status == VideoStatus.ARCHIVED
    assert video.width == 240 and video.height == 426
    assert video.is_short is True


def test_medienname_im_buendel_bleibt_sauber(umgebung, quellvideo, monkeypatch):
    """Der Dateiname im Buendel ist das, was ein Mensch beim Hineinschauen
    sieht - da haben Zwischenschritt-Namen nichts verloren."""
    db, _tmp = umgebung
    _download_ersetzen(monkeypatch, quellvideo)
    _archivieren(db)

    video = db.get(Video, "vid1")
    assert video.media_name == "media/vid1.mp4", video.media_name
    with bundle.BundleReader(Path(video.bundle_file)) as r:
        assert "media/vid1.mp4" in r.names()


# ------------------------------------------------------- Kanal- und Playlistabgleich


def _liste(ids, titel="T"):
    from app.services.ytdlp import ListedVideo

    return [ListedVideo(id=i, title=f"{titel} {n}", duration_s=60, upload_date=None, view_count=1)
            for n, i in enumerate(ids)]


def test_playlist_darf_dasselbe_video_mehrfach_enthalten(umgebung, monkeypatch):
    """Regression aus einem echten Kanalabgleich.

    Naheliegend waere, ein Video je Playlist nur einmal zuzulassen. Echte
    Playlists enthalten aber Wiederholungen - einen Vorspann am Anfang und am
    Ende etwa. Mit der falschen Eindeutigkeitsregel brach der Abgleich der
    Blender-Kanalseite mitten im Lauf ab und liess den Auftrag als "laeuft"
    stehen.
    """
    from app.models import Channel, PlaylistItem
    from app.services import ytdlp
    from app.workers import sync

    db, _tmp = umgebung
    kanal = db.get(Channel, "UCtest")
    monkeypatch.setattr(ytdlp, "list_entries", lambda url, limit=None: _liste(["a", "b", "a", "c"]))

    neu = sync._sammlung_abgleichen(
        db, kanal, playlist_id="PLx", titel="Mit Wiederholung", art="playlist",
        url="egal", einreihen=False,
    )

    eintraege = list(db.scalars(select(PlaylistItem).where(PlaylistItem.playlist_id == "PLx")))
    assert [e.video_id for e in sorted(eintraege, key=lambda x: x.position)] == ["a", "b", "a", "c"]
    assert neu == 3, "drei verschiedene Videos, vier Positionen"


def test_erneuter_abgleich_ersetzt_die_reihenfolge(umgebung, monkeypatch):
    """Aendert der Kanal die Playlist, muss das Archiv nachziehen statt zu
    haeufen - entfernte Positionen sollen verschwinden."""
    from app.models import Channel, PlaylistItem
    from app.services import ytdlp
    from app.workers import sync

    db, _tmp = umgebung
    kanal = db.get(Channel, "UCtest")

    monkeypatch.setattr(ytdlp, "list_entries", lambda url, limit=None: _liste(["a", "b", "c"]))
    sync._sammlung_abgleichen(db, kanal, playlist_id="PLx", titel="V1", art="playlist",
                              url="egal", einreihen=False)

    monkeypatch.setattr(ytdlp, "list_entries", lambda url, limit=None: _liste(["c", "a"]))
    sync._sammlung_abgleichen(db, kanal, playlist_id="PLx", titel="V2", art="playlist",
                              url="egal", einreihen=False)

    eintraege = sorted(
        db.scalars(select(PlaylistItem).where(PlaylistItem.playlist_id == "PLx")),
        key=lambda x: x.position,
    )
    assert [e.video_id for e in eintraege] == ["c", "a"]
    assert db.get(__import__("app.models", fromlist=["Playlist"]).Playlist, "PLx").title == "V2"


def test_gescheiterter_auftrag_wird_trotz_blockierter_sitzung_vermerkt(umgebung, monkeypatch):
    """Der zweite Teil desselben Vorfalls: Nach einem Schreibfehler nimmt die
    Sitzung nichts mehr an. Die Fehlerbehandlung selbst darf daran nicht
    scheitern - sonst verdeckt sie die Ursache und der Auftrag bleibt fuer
    immer auf 'laeuft' stehen."""
    from sqlalchemy.exc import IntegrityError

    db, _tmp = umgebung
    job = jobs.enqueue_archive(db, "vid1")
    laufend = jobs.claim_next(db, [JobType.VIDEO_ARCHIVE])
    assert laufend is not None

    # Sitzung absichtlich in den blockierten Zustand bringen.
    db.add(Video(id="vid1", channel_id="UCtest", title="doppelt"))
    with pytest.raises(IntegrityError):
        db.flush()

    jobs.gescheitert(db, laufend, "irgendein Schreibfehler")

    db_job = db.get(Job, job.id)
    assert db_job.status == JobStatus.FAILED
    assert "Schreibfehler" in db_job.error


def test_doppelte_id_in_einer_liste_bricht_den_abgleich_nicht(umgebung, monkeypatch):
    """Regression aus dem Betrieb.

    YouTube liefert bei grossen Kanaelen gelegentlich Listen, in denen dieselbe
    Video-ID zweimal steht. Weil die Sitzung ohne Autoflush laeuft, sieht
    ``db.get()`` das eben erst angelegte, noch nicht geschriebene Video nicht -
    es entstuenden zwei Datensaetze mit derselben ID und der ganze Abgleich
    braeche mit einer IntegrityError ab.

    Der Test lief frueher gruen, weil die Testsitzung mit Autoflush gebaut
    wurde und der Betrieb ohne. Genau deshalb kommt die Sitzung jetzt aus
    conftest.neue_sitzung() - mit denselben Einstellungen wie im Betrieb.
    """
    from app.models import Channel, PlaylistItem
    from app.services import ytdlp
    from app.workers import sync

    db, _tmp = umgebung
    kanal = db.get(Channel, "UCtest")
    # "doppelt" steht zweimal drin, einmal sogar ohne Titel - so wie es
    # bei nicht mehr verfuegbaren Videos ankommt.
    from app.services.ytdlp import ListedVideo

    eintraege = [
        ListedVideo(id="doppelt", title="(ohne Titel)", duration_s=None, upload_date=None, view_count=None),
        ListedVideo(id="doppelt", title="(ohne Titel)", duration_s=None, upload_date=None, view_count=None),
        ListedVideo(id="einzeln", title="Echter Titel", duration_s=99, upload_date=None, view_count=5),
    ]
    monkeypatch.setattr(ytdlp, "list_entries", lambda url, limit=None: eintraege)

    neu = sync._sammlung_abgleichen(
        db, kanal, playlist_id="PLx", titel="Mit Doppel", art="uploads",
        url="egal", einreihen=False,
    )

    assert neu == 2, "zwei verschiedene Videos"
    assert db.get(Video, "doppelt") is not None
    assert db.get(Video, "einzeln").title == "Echter Titel"
    # Beide Positionen bleiben erhalten - die Liste wird treu abgebildet.
    pos = sorted(
        db.scalars(select(PlaylistItem).where(PlaylistItem.playlist_id == "PLx")),
        key=lambda x: x.position,
    )
    assert [p.video_id for p in pos] == ["doppelt", "doppelt", "einzeln"]


def test_testsitzung_entspricht_dem_betrieb(umgebung):
    """Wacht darueber, dass die Tests nicht wieder anders konfiguriert sind als
    der Betrieb - genau daran ist der Fehler oben vorbeigerutscht."""
    from app.db import SessionLocal

    db, _tmp = umgebung
    assert db.autoflush is False
    assert SessionLocal.kw["autoflush"] is False
    assert db.autoflush == SessionLocal.kw["autoflush"]


# ------------------------------------------------------------ Keine Shorts


def test_short_wird_trotz_uploads_liste_nicht_eingereiht(umgebung, monkeypatch):
    """Ein Short steht auch in der Uploads-Liste, dort aber ohne Kennzeichnung.
    Frueher wurde es von dort als normales Video eingereiht, bevor die
    Shorts-Liste ueberhaupt gelesen war. Jetzt wird zuerst gekennzeichnet,
    dann eingereiht."""
    from app.models import Channel
    from app.services import ytdlp
    from app.workers import sync

    db, _tmp = umgebung
    kanal = db.get(Channel, "UCtest")
    kanal.auto_archive, kanal.archive_shorts = True, False
    db.commit()
    db.get(Video, "vid1").status = VideoStatus.ARCHIVED  # aus dem Weg
    db.commit()

    listen = {
        "UUSH": _liste(["kurz1"]),
        "UULV": [],
        "UU": _liste(["lang1", "kurz1", "lang2"]),
    }

    def gefaelscht(url, limit=None):
        kennung = url.split("list=")[-1]
        # Laengste Praefixe zuerst, sonst faengt "UU" auch "UUSH..." ab.
        for praefix in sorted(listen, key=len, reverse=True):
            if kennung.startswith(praefix):
                return listen[praefix]
        return []

    monkeypatch.setattr(ytdlp, "list_entries", gefaelscht)
    monkeypatch.setattr(ytdlp, "list_channel_playlists", lambda url: [])
    monkeypatch.setattr(ytdlp, "peek_recent", lambda kanal_id: [])

    jobs.enqueue(db, JobType.CHANNEL_SYNC, "UCtest", payload={"voll": True})
    sync.kanal_abgleichen(db, jobs.claim_next(db, [JobType.CHANNEL_SYNC]))

    assert db.get(Video, "kurz1").is_short is True
    eingereiht = {j.target_id for j in db.scalars(select(Job).where(Job.type == JobType.VIDEO_ARCHIVE))}
    assert eingereiht == {"lang1", "lang2"}, "das Short darf nicht dabei sein"
    assert db.get(Video, "kurz1").status == VideoStatus.NEW


def test_hochkant_wird_vor_dem_buendeln_verworfen(umgebung, monkeypatch, tmp_path_factory):
    """Letzte Sperre direkt an der Datei: Auch wenn die Liste luegt oder jemand
    auf 'Laden' klickt - ein hochkantiges Video wird bei abgeschalteten Shorts
    nicht gebuendelt, sondern als uebersprungen vermerkt."""
    from app.models import Channel

    ordner = tmp_path_factory.mktemp("hochkant2")
    quelle = ordner / "short.mkv"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y",
         "-f", "lavfi", "-i", "testsrc2=size=240x426:rate=15:duration=1",
         "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30", "-an", str(quelle)],
        check=True, capture_output=True,
    )
    db, _tmp = umgebung
    db.get(Channel, "UCtest").archive_shorts = False
    db.commit()
    _download_ersetzen(monkeypatch, quelle, info_extra={"height": 426})

    job = _archivieren(db)
    video = db.get(Video, "vid1")
    assert video.status == VideoStatus.SKIPPED
    assert "Shorts" in (video.status_message or "")
    assert video.bundle_file is None
    assert job.status == JobStatus.DONE, "bewusst uebersprungen ist kein Fehlschlag"
    assert not list((settings.bundle_dir).rglob("*.zip")), "es darf kein Buendel entstehen"
    assert not (settings.tmp_dir / "vid1").exists(), "Arbeitsordner muss weg sein"


# --------------------------------------------- Geloeschte Playlist-Eintraege


def test_leichen_in_playlists_werden_erkannt(umgebung, monkeypatch):
    """Wird ein Video geloescht oder privat gestellt, bleibt sein Platz in
    fremden Playlists stehen - yt-dlp liefert dann nur noch die ID. Beim
    JP-Kanal waren das 156 Kacheln "(ohne Titel)" mit einem Laden-Knopf, der
    nie etwas geholt haette."""
    from app.models import Channel
    from app.services import ytdlp
    from app.services.ytdlp import ListedVideo
    from app.workers import sync

    db, _tmp = umgebung
    kanal = db.get(Channel, "UCtest")
    eintraege = [
        # So kommt ein geloeschtes Video an: nur die ID, sonst nichts.
        ListedVideo(id="tot1", title="(ohne Titel)", duration_s=None, upload_date=None, view_count=None),
        ListedVideo(id="echt1", title="Ein echtes", duration_s=300, upload_date=None, view_count=99),
        # Ein Video mit fehlendem Titel, aber vorhandener Dauer ist KEINE
        # Leiche - ein einzelnes fehlendes Feld darf nicht reichen.
        ListedVideo(id="echt2", title="(ohne Titel)", duration_s=120, upload_date=None, view_count=None),
    ]
    monkeypatch.setattr(ytdlp, "list_entries", lambda url, limit=None: eintraege)

    sync._sammlung_abgleichen(db, kanal, playlist_id="PLx", titel="Mit Leiche",
                              art="playlist", url="egal", einreihen=False)

    assert db.get(Video, "tot1").status == VideoStatus.UNAVAILABLE
    assert "geloescht" in (db.get(Video, "tot1").status_message or "")
    assert db.get(Video, "echt1").status == VideoStatus.NEW
    assert db.get(Video, "echt2").status == VideoStatus.NEW, "nur ein fehlendes Feld ist keine Leiche"


def test_bereits_archiviertes_bleibt_trotz_loeschung(umgebung, monkeypatch):
    """Der Sinn eines Archivs: Was einmal gesichert ist, bleibt spielbar -
    auch wenn die Quelle es zurueckzieht. Genau dafuer macht man das."""
    from app.models import Channel
    from app.services import ytdlp
    from app.services.ytdlp import ListedVideo
    from app.workers import sync

    db, _tmp = umgebung
    v = db.get(Video, "vid1")
    v.status = VideoStatus.ARCHIVED
    v.title = "Laengst gesichert"
    db.commit()

    monkeypatch.setattr(ytdlp, "list_entries", lambda url, limit=None: [
        ListedVideo(id="vid1", title="(ohne Titel)", duration_s=None, upload_date=None, view_count=None),
    ])
    sync._sammlung_abgleichen(db, db.get(Channel, "UCtest"), playlist_id="PLx",
                              titel="X", art="playlist", url="egal", einreihen=False)

    v = db.get(Video, "vid1")
    assert v.status == VideoStatus.ARCHIVED, "archiviertes Video darf nicht entwertet werden"
    assert v.title == "Laengst gesichert", "der Titel darf nicht verloren gehen"
