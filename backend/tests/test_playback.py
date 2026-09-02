"""Tests der Wiedergabe-Entscheidung.

Zwei Fehlerrichtungen, beide unangenehm:
- Zu optimistisch entschieden -> der Nutzer bekommt ein schwarzes Bild.
- Zu pessimistisch entschieden -> es wird staendig unnoetig transkodiert und
  der Heissspeicher laeuft voll, obwohl direkt gestreamt werden koennte.
"""

from __future__ import annotations

import pytest

from app.services.bundle import BundleManifest
from app.services.playback import (
    FALLBACK_SUPPORT,
    Mode,
    decide,
    normalize_audio_codec,
    normalize_video_codec,
    parse_client_support,
)

MODERN = frozenset({"mp4", "webm", "av01", "vp09", "h264", "opus", "aac"})
ALT = frozenset({"mp4", "h264", "aac"})


def _m(media: str, vcodec: str | None = "av1", acodec: str | None = "opus") -> BundleManifest:
    return BundleManifest(
        schema_version=1, video_id="v", channel_id="c", title="t",
        media_name=f"media/{media}", media_bytes=1, mime_type="",
        video_codec=vcodec, audio_codec=acodec,
    )


# ---------------------------------------------------------------- Direktstream


def test_av1_webm_laeuft_direkt_auf_modernem_browser():
    d = decide(_m("v.webm", "av1", "opus"), MODERN)
    assert d.mode is Mode.DIRECT and d.variant is None


def test_h264_mp4_laeuft_ueberall_direkt():
    assert decide(_m("v.mp4", "h264", "aac"), ALT).mode is Mode.DIRECT


def test_codec_schreibweisen_werden_erkannt():
    """ffmpeg meldet 'libsvtav1', ffprobe 'av1', Manifeste teils 'av01' -
    alle drei muessen zum selben Ergebnis fuehren."""
    for schreibweise in ("av1", "av01", "libsvtav1", "AV1", "av01.0.08M"):
        assert decide(_m("v.webm", schreibweise, "opus"), MODERN).mode is Mode.DIRECT


# ------------------------------------------------------------------ Transkodieren


def test_mkv_geht_nie_direkt():
    """Der teuerste denkbare Fehler waere ein MKV-Archiv: Kein Browser spielt
    das ab, also muesste jede einzelne Wiedergabe transkodiert werden."""
    d = decide(_m("v.mkv", "h264", "aac"), MODERN)
    assert d.mode is Mode.TRANSCODE
    assert "Container" in d.reason


def test_alter_client_bekommt_av1_transkodiert():
    d = decide(_m("v.webm", "av1", "opus"), ALT)
    assert d.mode is Mode.TRANSCODE and d.variant == "h264"


def test_container_ohne_client_unterstuetzung():
    d = decide(_m("v.webm", "h264", "aac"), ALT)  # ALT kennt kein webm
    assert d.mode is Mode.TRANSCODE and "webm" in d.reason


def test_nur_der_ton_passt_nicht():
    """Video passt, Ton nicht - trotzdem transkodieren, sonst laeuft ein
    stummes Video."""
    d = decide(_m("v.mp4", "h264", "opus"), ALT)
    assert d.mode is Mode.TRANSCODE and "Ton" in d.reason


def test_unbekannter_codec_geht_auf_nummer_sicher():
    d = decide(_m("v.mp4", "irgendwas-neues", "aac"), MODERN)
    assert d.mode is Mode.TRANSCODE


def test_fehlender_codec_geht_auf_nummer_sicher():
    assert decide(_m("v.mp4", None, "aac"), MODERN).mode is Mode.TRANSCODE


def test_fehlender_tonspur_eintrag_blockiert_nicht():
    """Ein Video ohne Tonspur (oder ohne erfassten Toncodec) darf nicht
    grundlos in den Transkodierpfad rutschen."""
    assert decide(_m("v.mp4", "h264", None), ALT).mode is Mode.DIRECT


# ------------------------------------------------------- Faehigkeitsmeldung


@pytest.mark.parametrize("raw", [None, "", "   ", ","])
def test_ohne_meldung_konservative_annahme(raw):
    assert parse_client_support(raw) == FALLBACK_SUPPORT


def test_meldung_wird_normalisiert():
    assert parse_client_support(" MP4 , WebM ,AV01 ") == frozenset({"mp4", "webm", "av01"})


def test_client_ohne_meldung_bekommt_av1_transkodiert():
    """Zusammenspiel: Meldet ein Client nichts, darf er kein AV1 bekommen."""
    d = decide(_m("v.webm", "av1", "opus"), parse_client_support(None))
    assert d.mode is Mode.TRANSCODE


# ------------------------------------------------------------- Normalisierung


@pytest.mark.parametrize(
    "roh,erwartet",
    [("h264", "h264"), ("avc1", "h264"), ("libx264", "h264"), ("avc1.640028", "h264"),
     ("vp9", "vp09"), ("hevc", "hevc"), ("hvc1", "hevc"), ("bloedsinn", None), (None, None)],
)
def test_videocodec_normalisierung(roh, erwartet):
    assert normalize_video_codec(roh) == erwartet


@pytest.mark.parametrize(
    "roh,erwartet",
    [("opus", "opus"), ("libopus", "opus"), ("aac", "aac"), ("mp4a", "aac"),
     ("mp4a.40.2", "aac"), ("quatsch", None)],
)
def test_audiocodec_normalisierung(roh, erwartet):
    assert normalize_audio_codec(roh) == erwartet


# ------------------------------------------- Recodier-Entscheidung (media.py)


def test_recode_nur_wo_es_sich_lohnt():
    """Die wirtschaftlich wichtigste Entscheidung des ganzen Projekts: Eine
    VP9- oder AV1-Quelle nochmal durch SVT-AV1 zu schicken kostet rund eine
    Stunde Rechenzeit je Stunde Video und bringt fast nichts."""
    from app.config import ArchiveCodec
    from app.services.media import MediaInfo, should_recode

    def info(codec: str, hoehe: int = 1080) -> MediaInfo:
        return MediaInfo(
            duration_s=60.0, width=1920, height=hoehe, fps=30.0,
            video_codec=codec, audio_codec="aac", bitrate=3_000_000, size_bytes=10**7,
        )

    # Lohnt sich: H.264 ist der ineffizienteste der drei YouTube-Codecs.
    assert should_recode(info("h264"), ArchiveCodec.AV1)[0] is True
    assert should_recode(info("avc1"), ArchiveCodec.AV1)[0] is True

    # Lohnt sich nicht: schon effizient kodiert.
    for codec in ("vp9", "vp09", "av1", "av01"):
        lohnt, grund = should_recode(info(codec), ArchiveCodec.AV1)
        assert lohnt is False, f"{codec} sollte nicht recodiert werden"
        assert "bereits" in grund

    # Kleine Quellen ebenfalls nicht.
    assert should_recode(info("h264", hoehe=360), ArchiveCodec.AV1)[0] is False
    # Abgeschaltet heisst abgeschaltet.
    assert should_recode(info("h264"), ArchiveCodec.COPY)[0] is False


def test_kanal_sammelplaylists():
    """Der Abgleich laeuft ueber die abgeleiteten UU-Playlists, nicht ueber die
    Tab-Seiten - die sind vollstaendiger."""
    from app.services.ytdlp import YtdlpError, channel_auto_playlist

    kanal = "UCuAXFkgsw1L7xaCfnd5JJOw"
    rest = kanal[2:]
    assert channel_auto_playlist(kanal) == f"https://www.youtube.com/playlist?list=UU{rest}"
    assert channel_auto_playlist(kanal, "shorts") == f"https://www.youtube.com/playlist?list=UUSH{rest}"
    assert channel_auto_playlist(kanal, "live") == f"https://www.youtube.com/playlist?list=UULV{rest}"
    assert channel_auto_playlist(kanal, "videos") == f"https://www.youtube.com/playlist?list=UULF{rest}"

    with pytest.raises(YtdlpError):
        channel_auto_playlist("@handleStattID")


def test_stiller_360p_rueckfall_wird_erkannt():
    """Der gefaehrlichste Fehler im Betrieb: yt-dlp meldet Erfolg, hat aber nur
    die Notfassung geholt. Ohne Pruefung archiviert man wochenlang 360p."""
    from app.services.ytdlp import DegradedDownload, QualitaetVerfehlt, check_not_degraded

    def angebot(*hoehen):
        return [{"vcodec": "vp09", "height": h} for h in hoehen]

    # Guter Fall: 1080p bekommen, 1080p gewuenscht
    assert check_not_degraded(
        {"height": 1080, "format_id": "303+251", "vcodec": "vp09", "acodec": "opus",
         "formats": angebot(360, 720, 1080)}
    ) is None

    # Hochkant: yt-dlp zaehlt die lange Seite - ein Short in voller Qualitaet
    # ist 1080x1920 und muss durchgehen.
    assert check_not_degraded(
        {"height": 1920, "width": 1080, "format_id": "313+251", "vcodec": "vp09",
         "formats": angebot(1080, 1920)}
    ) is None

    # Format 18 - der klassische Rueckfall bei fehlenden PO-Tokens
    with pytest.raises(DegradedDownload, match="Format 18"):
        check_not_degraded({"height": 360, "format_id": "18", "vcodec": "avc1", "acodec": "mp4a"})

    # Unter dem absoluten Boden: immer verwerfen, auch wenn die Liste
    # behauptet, es gaebe nichts Besseres - eine gestoerte Sitzung behauptet
    # genau das.
    with pytest.raises(DegradedDownload, match="Boden"):
        check_not_degraded(
            {"height": 360, "format_id": "134+140", "vcodec": "avc1", "formats": angebot(360)}
        )

    # 720p bekommen, obwohl die Quelle 1080p anbietet. Das ist KEIN Fehlschlag
    # mehr, sondern die Aufforderung, eine Stufe tiefer erneut zu versuchen -
    # die Datei ist ja in Ordnung, nur schlechter als moeglich.
    with pytest.raises(QualitaetVerfehlt) as fund:
        check_not_degraded(
            {"height": 720, "format_id": "247+251", "vcodec": "vp09",
             "formats": angebot(360, 720, 1080)}
        )
    assert (fund.value.erhalten, fund.value.angeboten) == (720, 1080)

    # 720p bekommen, die Quelle hat auch nicht mehr: annehmen, aber vermerken.
    hinweis = check_not_degraded(
        {"height": 720, "format_id": "247+251", "vcodec": "vp09", "formats": angebot(360, 720)}
    )
    assert hinweis and "720p" in hinweis and "1080p" in hinweis

    # Nur Ton statt Video
    with pytest.raises(DegradedDownload, match="Tonspur"):
        check_not_degraded({"height": None, "format_id": "251", "vcodec": "none", "acodec": "opus"})


def test_format_selektor_setzt_minimum_statt_maximum():
    """Der Kern der Aenderung: 'mindestens 1080p' darf nicht 'hoechstens
    1080p' bedeuten. Bietet die Quelle 4K, wird 4K geladen."""
    from app.config import Settings

    s = Settings(archive_min_height=1080, archive_max_height=0)
    sel = s.format_selector()
    # Beide Seiten, nicht nur die Hoehe: "kurze Seite >= 1080" ist dasselbe wie
    # "beide Seiten >= 1080" und gilt fuer quer wie hochkant. Ein reiner
    # Hoehenfilter liess bei senkrechten Videos die 720er-Fassung durch, weil
    # deren Hoehe (1280) groesser als 1080 ist.
    assert sel.startswith("bestvideo[height>=1080][width>=1080]+bestaudio/")
    assert "<=" not in sel, "ohne Obergrenze darf nichts gedeckelt werden"
    # Rueckfall auf das Beste, was die Quelle hat
    assert "/bestvideo+bestaudio/" in sel and sel.endswith("/best")

    # Mit Obergrenze zwei Zweige: erst quer, dann hochkant. yt-dlp kennt kein
    # ODER innerhalb eines Filters, und "kurze Seite <= c" ist bei Querformat
    # die Hoehe, bei Hochkant die Breite.
    gedeckelt = Settings(archive_min_height=1080, archive_max_height=1440).format_selector()
    assert "bestvideo[height>=1080][width>=1080][height<=1440]+bestaudio" in gedeckelt
    assert "bestvideo[height>=1080][width>=1080][width<=1440]+bestaudio" in gedeckelt
    assert gedeckelt.endswith("/best")

    eigener = Settings(ytdlp_format="bestvideo[height<=720]+bestaudio").format_selector()
    assert eigener == "bestvideo[height<=720]+bestaudio"


# ------------------------------------------------- Hochkant und Rueckfall
#
# Regression aus einem echten Kanalabgleich. Ein ganzer Schwung Videos stand
# als "fehlgeschlagen" da, mit Meldungen wie "nur 1280p erhalten, obwohl die
# Quelle 1920p anbietet". Beide Zahlen sind keine Qualitaeten, sondern die
# Hoehen hochkantiger Videos: 720x1280 und 1080x1920.
#
# YouTube nennt das Format 1080x1920 selbst "1080p" - es zaehlt die kurze
# Seite. Wer die Hoehe liest, haelt ein senkrechtes 1080p-Video fuer "1920p"
# und verwirft einwandfreie Downloads.


def test_qualitaet_wird_an_der_kurzen_seite_gemessen():
    from app.services.ytdlp import guete

    assert guete({"width": 1920, "height": 1080}) == 1080   # quer
    assert guete({"width": 1080, "height": 1920}) == 1080   # hochkant, dasselbe
    assert guete({"width": 3840, "height": 2160}) == 2160
    assert guete({"width": 2160, "height": 3840}) == 2160
    assert guete({"width": 720, "height": 1280}) == 720
    # Fehlt eine Angabe, wird genommen, was da ist.
    assert guete({"height": 1080}) == 1080
    assert guete({}) is None


def test_hochkantes_video_gilt_nicht_mehr_als_verfehlt():
    """Der gemeldete Fehler, direkt nachgestellt: 'Weisswurst suess sauer!'
    mit 1080x1920. Vorher wurde das als 1920p gelesen und die 720er-Fassung
    (720x1280) als '1280p' - beides falsch."""
    from app.services.ytdlp import check_not_degraded

    formate = [{"vcodec": "vp09", "width": w, "height": h}
               for w, h in ((640, 1138), (720, 1280), (1080, 1920))]
    # Volle Qualitaet eines senkrechten Videos: geht glatt durch.
    assert check_not_degraded(
        {"width": 1080, "height": 1920, "format_id": "248+251", "vcodec": "vp09",
         "formats": formate}
    ) is None


def test_senkrechtes_4k_wird_erkannt():
    """'Warum haben wir zwei Karts?' - 2160x3840. Vorher meldete die Pruefung
    'die Quelle 3840p anbietet'."""
    from app.services.ytdlp import angebotene_guete, check_not_degraded

    formate = [{"vcodec": "vp09", "width": w, "height": h}
               for w, h in ((1080, 1920), (1440, 2560), (2160, 3840))]
    assert angebotene_guete({"formats": formate}) == 2160
    assert check_not_degraded(
        {"width": 2160, "height": 3840, "format_id": "313+251", "vcodec": "vp09",
         "formats": formate}
    ) is None


def test_stufenleiter():
    from app.services.ytdlp import naechste_stufe

    assert naechste_stufe(2160) == 1440
    assert naechste_stufe(1080) == 720
    assert naechste_stufe(1081) == 1080
    assert naechste_stufe(144) is None


def test_rueckfall_holt_die_naechste_stufe(tmp_path, monkeypatch):
    """Was der Nutzer wollte: nicht scheitern, sondern eine Stufe tiefer."""
    from app.config import settings
    from app.models import Channel, JobType, Video, VideoStatus
    from app.services import jobs, ytdlp
    from app.workers import archive
    from tests.conftest import neue_sitzung

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    settings.ensure_dirs()
    db = neue_sitzung()
    db.add(Channel(id="UCt", name="T"))
    db.add(Video(id="dQw4w9WgXcQ", channel_id="UCt", title="V", status=VideoStatus.QUEUED))
    db.commit()
    job = jobs.enqueue_archive(db, "dQw4w9WgXcQ")
    assert job.type == JobType.VIDEO_ARCHIVE

    angefordert: list[str | None] = []

    def laden(video_id, ordner, *, format_selector=None, fortschritt=None):
        angefordert.append(format_selector)
        ordner.mkdir(parents=True, exist_ok=True)
        datei = ordner / f"{video_id}.mkv"
        datei.write_bytes(b"x")
        # Erster Versuch liefert 720p, obwohl 1080p angeboten wird; der zweite
        # trifft dann die geforderte Stufe.
        hoehe = 720 if len(angefordert) == 1 else 1080
        return ytdlp.DownloadResult(
            path=datei,
            info={"width": hoehe * 16 // 9, "height": hoehe, "vcodec": "vp09",
                  "format_id": "x+y",
                  "formats": [{"vcodec": "vp09", "width": 1920, "height": 1080}]},
        )

    monkeypatch.setattr(archive.ytdlp, "download_video", laden)
    ergebnis, hinweis = archive._laden_mit_rueckfall(
        db, job, "dQw4w9WgXcQ", tmp_path / "arbeit", format_selector=None,
    )

    assert len(angefordert) == 2, "es wurde kein zweiter Versuch unternommen"
    assert "1080" in (angefordert[1] or ""), angefordert[1]
    assert ergebnis.info["height"] == 1080
    assert hinweis is None


def test_rueckfall_behaelt_am_ende_das_beste(tmp_path, monkeypatch):
    """Wenn auch die tieferen Stufen nichts bringen, wird behalten statt
    verworfen - ein 720p-Video im Archiv ist mehr wert als ein roter Eintrag."""
    from app.config import settings
    from app.models import Channel, Video, VideoStatus
    from app.services import jobs, ytdlp
    from app.workers import archive
    from tests.conftest import neue_sitzung

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    settings.ensure_dirs()
    db = neue_sitzung()
    db.add(Channel(id="UCt", name="T"))
    db.add(Video(id="dQw4w9WgXcQ", channel_id="UCt", title="V", status=VideoStatus.QUEUED))
    db.commit()
    job = jobs.enqueue_archive(db, "dQw4w9WgXcQ")

    versuche = []

    def laden(video_id, ordner, *, format_selector=None, fortschritt=None):
        versuche.append(format_selector)
        ordner.mkdir(parents=True, exist_ok=True)
        datei = ordner / f"{video_id}.mkv"
        datei.write_bytes(b"x")
        return ytdlp.DownloadResult(
            path=datei,
            info={"width": 1280, "height": 720, "vcodec": "vp09", "format_id": "x+y",
                  "formats": [{"vcodec": "vp09", "width": 1920, "height": 1080}]},
        )

    monkeypatch.setattr(archive.ytdlp, "download_video", laden)
    ergebnis, hinweis = archive._laden_mit_rueckfall(
        db, job, "dQw4w9WgXcQ", tmp_path / "arbeit", format_selector=None,
    )

    assert len(versuche) == archive.RUECKFALL_VERSUCHE + 1
    assert ergebnis.info["height"] == 720
    assert hinweis and "720p" in hinweis, hinweis


def test_gestoerte_kette_faellt_weiterhin_hart_durch(tmp_path, monkeypatch):
    """Die Gegenprobe, die wichtiger ist als der Rueckfall selbst: Format 18
    ist kein Qualitaetsproblem, sondern das Zeichen einer kaputten Sitzung.
    Ein Rueckfall waere hier genau falsch - man archivierte dauerhaft
    Notfassungen, ohne es zu merken."""
    from app.config import settings
    from app.models import Channel, Video, VideoStatus
    from app.services import jobs, ytdlp
    from app.workers import archive
    from tests.conftest import neue_sitzung

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    settings.ensure_dirs()
    db = neue_sitzung()
    db.add(Channel(id="UCt", name="T"))
    db.add(Video(id="dQw4w9WgXcQ", channel_id="UCt", title="V", status=VideoStatus.QUEUED))
    db.commit()
    job = jobs.enqueue_archive(db, "dQw4w9WgXcQ")

    def laden(video_id, ordner, *, format_selector=None, fortschritt=None):
        ordner.mkdir(parents=True, exist_ok=True)
        datei = ordner / f"{video_id}.mkv"
        datei.write_bytes(b"x")
        return ytdlp.DownloadResult(
            path=datei,
            info={"width": 640, "height": 360, "vcodec": "avc1", "acodec": "mp4a",
                  "format_id": "18", "formats": []},
        )

    monkeypatch.setattr(archive.ytdlp, "download_video", laden)
    with pytest.raises(ytdlp.DegradedDownload, match="Format 18"):
        archive._laden_mit_rueckfall(
            db, job, "dQw4w9WgXcQ", tmp_path / "arbeit", format_selector=None,
        )
