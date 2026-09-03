"""Archivierung eines Videos: herunterladen, umpacken, buendeln.

Bewusst NICHT enthalten: die Recodierung nach AV1. Die laeuft als eigener
Auftrag hinterher.

Der Grund ist praktisch: Ein Kanal mit 500 Stunden Material braucht zum
Recodieren rund 425 CPU-Stunden - etwa 18 Tage Dauerlast. Steckte das hier
drin, waere ein Video erst nach Tagen sichtbar. So ist es binnen Minuten
vollstaendig, abspielbar und gesichert; kleiner wird es spaeter im Hintergrund.
"""

from __future__ import annotations

import json
import logging
import shutil
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.config import settings
from app.db import session_scope
from app.models import (
    Chapter,
    Job,
    JobType,
    Subtitle,
    Video,
    VideoStatus,
    utcnow,
)
from app.services import abbruch, bundle, drosselung, jobs, media, suche, ytdlp

log = logging.getLogger(__name__)


def _status(db: Session, video: Video, status: str, nachricht: str | None = None) -> None:
    video.status = status
    video.status_message = nachricht
    db.commit()


def _fehler_vermerken(
    db: Session, video_id: str, status: str, meldung: str, *, versuch_zaehlen: bool = False
) -> None:
    """Haelt einen Fehlschlag fest, auch wenn die Sitzung blockiert ist.

    Scheitert ein Auftrag an einem Schreibfehler, nimmt SQLAlchemy bis zum
    Zuruecksetzen keine weitere Anweisung mehr an. Ein schlichtes
    ``video.status = ...; db.commit()`` wuerde dann selbst scheitern und die
    urspruengliche Ursache verdecken - das Video bliebe auf "wird geladen"
    stehen und niemand wuesste, warum.
    """
    db.rollback()
    video = db.get(Video, video_id)
    if video is None:
        return
    if versuch_zaehlen:
        video.retry_count += 1
    video.status = status
    video.status_message = meldung[:1000]
    db.commit()


def _thumbnail_ablegen(video_id: str, quelle: Path | None) -> str | None:
    """Legt das Vorschaubild ausserhalb des Buendels ab.

    Das Grid im UI zeigt hunderte Vorschaubilder gleichzeitig. Muesste dafuer
    jedes Mal ein ZIP geoeffnet werden, waere schon das Blaettern zaeh - und auf
    einem Array mit schlafenden Platten wuerde es die Platten aufwecken.
    """
    if quelle is None or not quelle.is_file():
        return None
    settings.thumb_dir.mkdir(parents=True, exist_ok=True)
    ziel = settings.thumb_dir / f"{video_id}{quelle.suffix.lower()}"
    shutil.copy2(quelle, ziel)
    return ziel.name


def _metadaten_uebernehmen(db: Session, video: Video, info: dict[str, Any]) -> None:
    """Uebertraegt die yt-dlp-Metadaten in die Datenbank."""
    video.title = info.get("title") or video.title
    video.description = info.get("description")
    video.duration_s = int(info["duration"]) if info.get("duration") else None
    video.view_count = info.get("view_count")
    video.like_count = info.get("like_count")
    video.was_live = bool(info.get("was_live"))
    if info.get("tags"):
        video.tags = json.dumps(info["tags"], ensure_ascii=False)

    ts = info.get("timestamp")
    if ts:
        video.upload_date = datetime.fromtimestamp(ts, tz=UTC)
    elif info.get("upload_date"):
        with suppress(ValueError):
            video.upload_date = datetime.strptime(str(info["upload_date"]), "%Y%m%d").replace(
                tzinfo=UTC
            )

    # Kapitel neu setzen statt zusammenfuehren - bei einer Neuarchivierung
    # sollen alte Kapitel nicht danebenstehen bleiben.
    db.execute(delete(Chapter).where(Chapter.video_id == video.id))
    for k in info.get("chapters") or []:
        if k.get("start_time") is None:
            continue
        db.add(
            Chapter(
                video_id=video.id,
                title=k.get("title") or "",
                start_s=float(k["start_time"]),
                end_s=float(k["end_time"]) if k.get("end_time") is not None else None,
            )
        )
    db.commit()


def _untertitel_uebernehmen(
    db: Session, video: Video, eintraege: list[bundle.SubtitleEntry]
) -> None:
    db.execute(delete(Subtitle).where(Subtitle.video_id == video.id))
    for e in eintraege:
        db.add(
            Subtitle(
                video_id=video.id,
                language=e.language,
                is_auto=e.is_auto,
                name_in_bundle=e.name_in_bundle,
            )
        )
    db.commit()


def _in_suche_aufnehmen(
    db: Session, video: Video, untertitel: list[tuple[str, bool, Path]]
) -> None:
    """Nimmt Titel, Beschreibung und gesprochenen Text in den Volltextindex auf.

    Die Untertitel sind dabei der eigentliche Gewinn: Damit findet man nicht nur
    das Video, sondern die Stelle darin. Ein Fehler hier darf die Archivierung
    aber nicht scheitern lassen - das Video ist bereits sicher gespeichert, ein
    fehlender Indexeintrag laesst sich jederzeit nachholen.
    """
    try:
        suche.video_indizieren(
            db,
            video_id=video.id,
            titel=video.title,
            beschreibung=video.description,
            kanal=video.channel.name if video.channel else None,
        )
        zeilen = 0
        for sprache, _ist_auto, pfad in untertitel:
            if not pfad.is_file():
                continue
            zeilen += suche.untertitel_indizieren(
                db, video.id, sprache, pfad.read_text(encoding="utf-8", errors="replace")
            )
        db.commit()
        if zeilen:
            log.info("%s: %d Untertitelzeilen durchsuchbar", video.id, zeilen)
    except Exception:
        db.rollback()
        log.warning("%s konnte nicht in den Suchindex aufgenommen werden", video.id, exc_info=True)


class ShortUebersprungen(Exception):
    """Das Video ist ein Short, der Kanal will aber keine. Kein Fehler -
    eine bewusste Entscheidung, die als 'uebersprungen' vermerkt wird."""


#: Liegt diese Datei im Arbeitsordner, wurde der letzte Lauf durch das
#: Herunterfahren unterbrochen und der Ordner darf weiterverwendet werden.
FORTSETZMARKE = ".unterbrochen"


def _fortsetzmarke_setzen(ordner: Path) -> None:
    """Haelt fest, dass hier nichts kaputt ist, sondern nur Schluss war.

    Ohne diese Marke waere nicht unterscheidbar, ob der Ordner von einem
    sauberen Abbruch stammt oder von einem Absturz mitten im Umpacken. Im
    zweiten Fall darf nichts davon weiterverwendet werden.
    """
    try:
        ordner.mkdir(parents=True, exist_ok=True)
        (ordner / FORTSETZMARKE).touch()
    except OSError:
        # Ohne Marke wird der Ordner beim naechsten Lauf verworfen und der
        # Download beginnt von vorn. Aergerlich, aber unschaedlich - kein Grund,
        # das Herunterfahren daran scheitern zu lassen.
        log.warning("Fortsetzmarke in %s liess sich nicht setzen", ordner, exc_info=True)


def _fortsetzmarke_einloesen(ordner: Path) -> bool:
    """Liefert True, wenn hier fortgesetzt werden darf, und raeumt die Marke ab.

    Die Marke wird sofort entfernt: Bricht dieser Lauf anders als durch ein
    Herunterfahren ab, soll der naechste den Ordner verwerfen statt auf
    Truemmern aufzubauen.
    """
    marke = ordner / FORTSETZMARKE
    if not marke.is_file():
        return False
    marke.unlink(missing_ok=True)
    log.info("Setze unterbrochenen Download in %s fort", ordner)
    return True


#: Wie oft nach einem verfehlten Qualitaetsziel eine Stufe tiefer erneut
#: versucht wird. Zwei reichen: Von 4K aus liegen damit 1440p und 1080p im
#: Zugriff, und jeder Versuch kostet einen vollen Download.
RUECKFALL_VERSUCHE = 2


def _laden_mit_rueckfall(
    db: Session,
    job: Job,
    video_id: str,
    ordner: Path,
    *,
    format_selector: str | None,
) -> tuple[ytdlp.DownloadResult, str | None]:
    """Laedt ein Video und geht bei verfehlter Qualitaet eine Stufe tiefer.

    Bisher endete dieser Fall als Fehlschlag: "nur X erhalten, obwohl die
    Quelle Y anbietet - Video wurde NICHT als archiviert verbucht". Das ist
    richtig, wenn die Kette gestoert ist, aber falsch, wenn die gewuenschte
    Stufe schlicht nicht zusammen mit einer passenden Tonspur zu haben ist.
    Der Nutzer sieht dann eine Liste roter Fehler und hat trotzdem nichts.

    Jetzt wird die naechste Stufe unterhalb des Angebots ausdruecklich
    angefordert. Bleibt auch die aus, wird das Beste behalten, was angekommen
    ist, und der Unterschied als Hinweis vermerkt - ein 720p-Video im Archiv
    ist mehr wert als ein roter Eintrag in der Warteschlange.

    Der harte Abbruch bleibt, wo er hingehoert: Format 18, reine Tonspur und
    alles unterhalb des absoluten Bodens sind Zeichen einer gestoerten
    Sitzung. Dort waere ein Rueckfall genau das Falsche - man archivierte
    dauerhaft Notfassungen, ohne es zu merken.
    """
    ziel = format_selector
    letzter: ytdlp.DownloadResult | None = None
    letzter_fehler: ytdlp.QualitaetVerfehlt | None = None

    for versuch in range(RUECKFALL_VERSUCHE + 1):
        ergebnis = ytdlp.download_video(
            video_id, ordner,
            format_selector=ziel,
            fortschritt=lambda anteil, text: jobs.fortschritt(db, job, anteil * 0.6, text),
        )
        try:
            hinweis = ytdlp.check_not_degraded(
                ergebnis.info,
                mindesthoehe=settings.archive_min_height,
                boden=settings.recode_min_height,
            )
            return ergebnis, hinweis
        except ytdlp.QualitaetVerfehlt as e:
            letzter, letzter_fehler = ergebnis, e
            stufe = ytdlp.naechste_stufe(e.angeboten + 1)  # das Angebot selbst zuerst
            if versuch >= RUECKFALL_VERSUCHE or stufe is None:
                break
            ziel = f"bestvideo[height>={stufe}][width>={stufe}]+bestaudio/bestvideo+bestaudio/best"
            log.info(
                "%s: %dp erhalten, Quelle bietet %dp - neuer Versuch auf %dp",
                video_id, e.erhalten, e.angeboten, stufe,
            )
            jobs.fortschritt(db, job, 0.02, f"Erneuter Versuch mit {stufe}p")
            # Sauber neu anfangen: Die Teildateien gehoeren zum alten Format
            # und wuerden sonst mit dem neuen vermischt.
            shutil.rmtree(ordner, ignore_errors=True)

    # Aufgeben heisst hier: behalten, was da ist, und es benennen.
    assert letzter is not None and letzter_fehler is not None
    return letzter, (
        f"{letzter_fehler.erhalten}p statt der gewuenschten "
        f"{settings.archive_min_height}p - die Quelle gibt hier nicht mehr her"
    )


def _ablegen(
    db: Session,
    job: Job,
    video: Video,
    ergebnis: ytdlp.DownloadResult,
    *,
    arbeitsordner: Path,
    info_medien: media.Medieninfo,
    quelle_bytes: int,
    qualitaetshinweis: str | None,
    zustaende_pflegen: bool = True,
) -> media.Medieninfo:
    """Packt um, buendelt und zieht die Datenbank nach. Liefert die Medieninfo
    der abgelegten Datei - nach dem Umpacken kann sie sich geaendert haben.

    Wird von zwei Wegen benutzt: vom erstmaligen Archivieren und vom
    nachtraeglichen Hochstufen. ``zustaende_pflegen`` unterscheidet sie: Beim
    Hochstufen liegt bereits ein spielbares Buendel vor, und der Zustand des
    Videos bleibt die ganze Zeit "archiviert". Wuerde er hier auf "wird
    umgepackt" gesetzt, verschwaende das Video fuer die Dauer des Vorgangs aus
    der Oberflaeche, obwohl die alte Fassung durchgehend abspielbar ist.
    """
    video_id = video.id

    # ---- Umpacken in einen browsertauglichen Behaelter (60 bis 80 %)
    plan = media.plan_container(info_medien)
    medien_datei = ergebnis.path
    if ergebnis.path.suffix.lower() != plan.suffix:
        if zustaende_pflegen:
            _status(db, video, VideoStatus.REMUXING, plan.grund)
        jobs.fortschritt(db, job, 0.62, f"Umpacken: {plan.grund}")
        # Eigenes Unterverzeichnis, damit der Dateiname sauber bleibt: Er
        # landet unveraendert als Medienname im Buendel, und dort will
        # niemand ein "demo1.zwischenschritt.mp4" wiederfinden.
        fertig_ordner = arbeitsordner / "fertig"
        fertig_ordner.mkdir(parents=True, exist_ok=True)
        umgepackt = fertig_ordner / f"{video_id}{plan.suffix}"
        media.run_ffmpeg(
            media.build_remux_cmd(ergebnis.path, umgepackt, plan),
            dauer_s=info_medien.duration_s,
            fortschritt=lambda a: jobs.fortschritt(db, job, 0.62 + a * 0.18),
            abbruch=abbruch.laeuft_herunter,
        )
        medien_datei = umgepackt
        info_medien = media.probe(medien_datei)

    # ---- Buendeln (80 bis 100 %)
    if zustaende_pflegen:
        _status(db, video, VideoStatus.BUNDLING)
    jobs.fortschritt(db, job, 0.82, "Buendel wird geschrieben")

    ziel = bundle.bundle_path_for(settings.bundle_dir, video.channel_id, video_id)
    manifest = bundle.BundleManifest(
        schema_version=bundle.SCHEMA_VERSION,
        video_id=video_id,
        channel_id=video.channel_id,
        title=ergebnis.info.get("title") or video.title,
        media_name="",
        media_bytes=0,
        mime_type="",
        source_bytes=quelle_bytes,
        recoded=False,
        video_codec=info_medien.video_codec,
        audio_codec=info_medien.audio_codec,
        width=info_medien.width,
        height=info_medien.height,
        fps=info_medien.fps,
        duration_s=info_medien.duration_s,
        created_at=utcnow().isoformat(),
    )
    # Schreibt nach <name>.part und benennt erst am Ende um. Deshalb bleibt die
    # bisherige Fassung bis zur letzten Sekunde lesbar - beim Hochstufen ist
    # genau das die Zusage: Geht etwas schief, hat man weiterhin das alte Video.
    bundle.write_bundle(
        ziel,
        manifest=manifest,
        media_file=medien_datei,
        info_json=ergebnis.info,
        thumbnail=ergebnis.thumbnail,
        subtitles=ergebnis.subtitles,
    )

    # ---- Datenbank nachziehen
    _metadaten_uebernehmen(db, video, ergebnis.info)
    _untertitel_uebernehmen(db, video, manifest.subtitles)
    video.thumb_file = _thumbnail_ablegen(video_id, ergebnis.thumbnail)
    video.bundle_file = str(ziel)
    video.bundle_bytes = ziel.stat().st_size
    video.source_bytes = quelle_bytes
    video.media_name = manifest.media_name
    video.video_codec = info_medien.video_codec
    video.audio_codec = info_medien.audio_codec
    video.width = info_medien.width
    video.height = info_medien.height
    video.fps = info_medien.fps
    video.recoded = False
    video.archived_at = utcnow()
    video.retry_count = 0
    # Ein Hinweis wie "Quelle bietet hoechstens 720p" bleibt sichtbar -
    # sonst wundert man sich spaeter, warum ein Video nicht in 1080p da ist.
    _status(db, video, VideoStatus.ARCHIVED, qualitaetshinweis)

    _in_suche_aufnehmen(db, video, ergebnis.subtitles)
    return info_medien


@jobs.register(JobType.VIDEO_ARCHIVE)
def archivieren(db: Session, job: Job) -> None:
    video_id = job.target_id
    if not video_id:
        raise ValueError("Archivierungsauftrag ohne Video-ID")

    video = db.get(Video, video_id)
    if video is None:
        raise ValueError(f"Video {video_id} nicht in der Datenbank")

    arbeitsordner = settings.tmp_dir / video_id
    fortsetzbar = _fortsetzmarke_einloesen(arbeitsordner)
    if arbeitsordner.exists() and not fortsetzbar:
        # Reste eines abgestuerzten oder gescheiterten Laufs. Was davon
        # brauchbar ist, laesst sich nicht feststellen - also weg damit.
        shutil.rmtree(arbeitsordner, ignore_errors=True)

    try:
        # ---- 1. Herunterladen (0 bis 60 % des Auftrags)
        _status(db, video, VideoStatus.DOWNLOADING)
        jobs.fortschritt(db, job, 0.02, "Download beginnt")

        kanal = video.channel
        ergebnis, qualitaetshinweis = _laden_mit_rueckfall(
            db, job, video_id, arbeitsordner,
            format_selector=(kanal.format_selector if kanal else None),
        )

        # YouTube antwortet also wieder. Eine noch stehende Pause samt Stufe
        # faellt damit weg - sonst schleppte eine einmalige Abweisung ihre
        # Eskalationsstufe wochenlang mit und die naechste begaenne bei einer
        # Stunde.
        drosselung.entwarnung()

        quelle_bytes = ergebnis.path.stat().st_size
        info_medien = media.probe(ergebnis.path)

        # Hochkant heisst Short. Die Kennzeichnung ueber die UUSH-Playlist
        # greift nur, wenn Shorts beim Kanal eingeschaltet sind - ein Short,
        # das ueber die Uploads-Liste kam, landete sonst unter "Videos" und
        # wurde dort in eine 16:9-Buehne gezwungen.
        if info_medien.width and info_medien.height and info_medien.height > info_medien.width:
            video.is_short = True
            db.commit()

        # Letzte Sperre gegen Shorts, direkt an der Datei statt an der Liste:
        # Die Kennzeichnung beim Abgleich kann luecken (YouTube liefert die
        # Shorts-Liste nicht immer vollstaendig), und der "Laden"-Knopf auf der
        # Kachel kennt die Kanalregeln nicht. Hier ist der Punkt, an dem das
        # Video nachweislich hochkant ist - und wenn der Kanal keine Shorts
        # will, wird es jetzt verworfen, nicht erst gebuendelt.
        if video.is_short and kanal is not None and not kanal.archive_shorts:
            raise ShortUebersprungen(
                f"hochkantiges Video ({info_medien.width}x{info_medien.height}) - "
                "Shorts sind fuer diesen Kanal abgeschaltet"
            )

        # ---- 3. bis 5.: umpacken, buendeln, Datenbank nachziehen
        info_medien = _ablegen(
            db, job, video, ergebnis,
            arbeitsordner=arbeitsordner,
            info_medien=info_medien,
            quelle_bytes=quelle_bytes,
            qualitaetshinweis=qualitaetshinweis,
        )

        # ---- 6. Recodierung nachgelagert einreihen, falls sie sich lohnt
        codec = media.ArchiveCodec(kanal.archive_codec) if kanal and kanal.archive_codec else settings.archive_codec
        lohnt, grund = media.should_recode(info_medien, codec)
        if lohnt:
            jobs.enqueue(db, JobType.VIDEO_RECODE, video_id, priority=jobs.PRIO_RECODE)
            log.info("%s archiviert, Recodierung eingereiht (%s)", video_id, grund)
        else:
            log.info("%s archiviert, keine Recodierung (%s)", video_id, grund)

        jobs.erledigt(db, job, f"archiviert, {video.bundle_bytes / 1e6:.1f} MB")

    except abbruch.Abgebrochen:
        # Kein Fehlschlag, sondern das Herunterfahren. Der Auftrag geht zurueck
        # in die Warteschlange, das Video zurueck auf "wartet", und der
        # angefangene Download bleibt liegen - der naechste Start setzt ihn
        # fort, statt hunderte Megabyte erneut zu holen.
        _fortsetzmarke_setzen(arbeitsordner)
        _status(db, video, VideoStatus.QUEUED, "beim Herunterfahren unterbrochen")
        jobs.unterbrochen(db, job, "beim Herunterfahren unterbrochen")
        raise
    except ShortUebersprungen as e:
        # Bewusst uebersprungen, kein Fehlschlag: Auftrag gilt als erledigt,
        # das Video bleibt mit Begruendung sichtbar und laesst sich holen,
        # sobald der Kanal Shorts erlaubt.
        _fehler_vermerken(db, video_id, VideoStatus.SKIPPED, str(e))
        jobs.erledigt(db, job, f"uebersprungen: {e}")
    except ytdlp.Gedrosselt as e:
        # Kein Fehlschlag dieses Videos, sondern eine Abweisung unserer
        # IP-Adresse. Behandelt wie das Herunterfahren: Der Auftrag geht
        # unbewertet zurueck in die Warteschlange, der Versuchszaehler bleibt
        # unberuehrt, der halbe Download bleibt liegen.
        #
        # Wuerde er stattdessen als gescheitert gelten, waere der Schaden
        # betraechtlich: Bei 1800 offenen Videos brennt eine einzige Sperre die
        # ganze Warteschlange ab, weil jedes folgende Video binnen Sekunden auf
        # dieselbe Wand laeuft und sie mit jedem Versuch verlaengert.
        hinweis = drosselung.hinweis(drosselung.melden(str(e)))
        _fortsetzmarke_setzen(arbeitsordner)
        _status(db, video, VideoStatus.QUEUED, hinweis)
        jobs.unterbrochen(db, job, hinweis)
    except ytdlp.VideoUnavailable as e:
        # Kein Grund zum Wiederholen - das Video ist bei der Quelle weg.
        _fehler_vermerken(db, video_id, VideoStatus.UNAVAILABLE, str(e))
        jobs.erledigt(db, job, "Video bei der Quelle nicht mehr verfuegbar")
    except ytdlp.DegradedDownload as e:
        # Bewusst NICHT als archiviert verbuchen: Sobald die PO-Token- oder
        # JavaScript-Kette wieder steht, soll dieses Video erneut geholt werden.
        _fehler_vermerken(db, video_id, VideoStatus.FAILED, str(e), versuch_zaehlen=True)
        jobs.gescheitert(db, job, str(e))
    except Exception as e:
        _fehler_vermerken(
            db, video_id, VideoStatus.FAILED, f"{type(e).__name__}: {e}", versuch_zaehlen=True
        )
        jobs.gescheitert(db, job, f"{type(e).__name__}: {e}")
        raise
    finally:
        # Beim Herunterfahren bleibt der Ordner stehen - er ist der halbe
        # Download, aus dem der naechste Start fortsetzt.
        if not (arbeitsordner / FORTSETZMARKE).is_file():
            shutil.rmtree(arbeitsordner, ignore_errors=True)


def _kodieren_mit_rueckfall(
    db: Session,
    job: Job,
    quelle: Path,
    ziel: Path,
    codec: media.ArchiveCodec,
    *,
    dauer_s: float | None,
) -> None:
    """Kodiert und faellt auf die CPU zurueck, wenn die Grafikkarte streikt.

    Ohne diesen Rueckfall waere ein eingestellter, aber nicht funktionierender
    Hardware-Encoder genauso verheerend wie eine Sperre durch YouTube: Jede
    einzelne der tausenden wartenden Recodierungen liefe in denselben Fehler
    und waere rot. Und die Ursachen liegen ausserhalb dieses Programms - ein
    Treiberwechsel auf dem Wirt, eine nicht mehr durchgereichte Karte, ein
    Video in einem Format, das die Karte nicht annimmt.

    Der Rueckfall gilt bewusst nur je Auftrag und schaltet die Einstellung
    nicht dauerhaft um: Ein einzelnes sperriges Video soll nicht dazu fuehren,
    dass der Rest des Archivs still auf der CPU landet.
    """
    hw = settings.hwaccel
    try:
        media.run_ffmpeg(
            media.build_archive_cmd(quelle, ziel, codec),
            dauer_s=dauer_s,
            fortschritt=lambda a: jobs.fortschritt(db, job, 0.1 + a * 0.8),
            abbruch=abbruch.laeuft_herunter,
        )
        return
    except abbruch.Abgebrochen:
        raise  # Herunterfahren ist kein Grund, es nochmal auf der CPU zu versuchen
    except media.MediaError:
        if hw is media.HardwareAccel.NONE:
            raise
        log.warning(
            "%s: Hardware-Encoder %s hat versagt, neuer Versuch auf der CPU",
            job.target_id, hw.value, exc_info=True,
        )

    ziel.unlink(missing_ok=True)  # Bruchstueck des gescheiterten Laufs
    jobs.fortschritt(db, job, 0.1, f"{hw.value} hat versagt - wird auf der CPU kodiert")
    media.run_ffmpeg(
        media.build_archive_cmd(quelle, ziel, codec, hwaccel=media.HardwareAccel.NONE),
        dauer_s=dauer_s,
        fortschritt=lambda a: jobs.fortschritt(db, job, 0.1 + a * 0.8),
        abbruch=abbruch.laeuft_herunter,
    )


@jobs.register(JobType.VIDEO_RECODE)
def recodieren(db: Session, job: Job) -> None:
    """Verkleinert ein bereits archiviertes Video.

    Laeuft auf einem eigenen, schmalen Arbeiterstrang, damit die lange
    Rechenzeit weder Downloads noch die Wiedergabe ausbremst. Das Buendel wird
    erst am Ende ersetzt - bricht der Vorgang ab, bleibt das alte unversehrt und
    das Video durchgehend abspielbar.
    """
    video_id = job.target_id
    if not video_id:
        raise ValueError("Recodierauftrag ohne Video-ID")

    video = db.get(Video, video_id)
    if video is None or not video.bundle_file:
        raise ValueError(f"Video {video_id} ist nicht archiviert")

    quelle_buendel = Path(video.bundle_file)
    arbeitsordner = settings.tmp_dir / f"{video_id}.recode"
    shutil.rmtree(arbeitsordner, ignore_errors=True)
    arbeitsordner.mkdir(parents=True, exist_ok=True)

    kanal = video.channel
    codec = media.ArchiveCodec(kanal.archive_codec) if kanal and kanal.archive_codec else settings.archive_codec

    try:
        with bundle.BundleReader(quelle_buendel) as leser:
            manifest = leser.manifest
            info_json = leser.info_json()
            entpackt = arbeitsordner / Path(manifest.media_name).name
            jobs.fortschritt(db, job, 0.05, "Buendel wird gelesen")
            leser.extract_media(entpackt)
            # Beiwerk mitnehmen, damit das neue Buendel vollstaendig bleibt.
            beiwerk: list[tuple[str, bool, Path]] = []
            for eintrag in manifest.subtitles:
                p = arbeitsordner / Path(eintrag.name_in_bundle).name
                p.write_bytes(leser.read(eintrag.name_in_bundle))
                beiwerk.append((eintrag.language, eintrag.is_auto, p))
            vorschau: Path | None = None
            if manifest.thumbnail_name:
                vorschau = arbeitsordner / manifest.thumbnail_name
                vorschau.write_bytes(leser.read(manifest.thumbnail_name))

        info_vorher = media.probe(entpackt)
        lohnt, grund = media.should_recode(info_vorher, codec)
        if not lohnt:
            jobs.erledigt(db, job, f"uebersprungen: {grund}")
            return

        _status(db, video, VideoStatus.ENCODING, grund)
        # Wie beim Umpacken: eigenes Verzeichnis, damit der Medienname im
        # Buendel schlicht "<video-id>.<endung>" bleibt.
        fertig_ordner = arbeitsordner / "fertig"
        fertig_ordner.mkdir(parents=True, exist_ok=True)
        ziel_datei = fertig_ordner / f"{video_id}{media.archive_container(codec)}"
        _kodieren_mit_rueckfall(
            db, job, entpackt, ziel_datei, codec, dauer_s=info_vorher.duration_s
        )

        neu = ziel_datei.stat().st_size
        alt = entpackt.stat().st_size
        if settings.keep_original_if_larger and neu >= alt:
            # Kommt bei schon dichtem Quellmaterial vor. Dann waere der Encode
            # reiner Qualitaetsverlust ohne Gegenwert.
            _status(db, video, VideoStatus.ARCHIVED)
            jobs.erledigt(db, job, f"verworfen: Encode war groesser ({neu / 1e6:.1f} statt {alt / 1e6:.1f} MB)")
            return

        info_nachher = media.probe(ziel_datei)
        jobs.fortschritt(db, job, 0.92, "neues Buendel wird geschrieben")

        manifest.recoded = True
        manifest.video_codec = info_nachher.video_codec
        manifest.audio_codec = info_nachher.audio_codec
        manifest.width = info_nachher.width
        manifest.height = info_nachher.height
        manifest.fps = info_nachher.fps
        bundle.write_bundle(
            quelle_buendel,
            manifest=manifest,
            media_file=ziel_datei,
            info_json=info_json,
            thumbnail=vorschau,
            subtitles=beiwerk,
        )

        video.bundle_bytes = quelle_buendel.stat().st_size
        video.media_name = manifest.media_name
        video.video_codec = info_nachher.video_codec
        video.audio_codec = info_nachher.audio_codec
        video.recoded = True
        _status(db, video, VideoStatus.ARCHIVED)

        ersparnis = 100 * (alt - neu) / alt if alt else 0
        jobs.erledigt(db, job, f"recodiert, {ersparnis:.1f} % kleiner")
        log.info("%s recodiert: %.1f MB -> %.1f MB (%.1f %%)", video_id, alt / 1e6, neu / 1e6, ersparnis)

    except abbruch.Abgebrochen:
        # Anders als beim Download gibt es hier nichts fortzusetzen: ffmpeg
        # kann einen halben Encode nicht wiederaufnehmen. Der Auftrag wird
        # aber wieder eingereiht statt als gescheitert vermerkt, und das alte
        # Buendel steht unangetastet - das Video bleibt abspielbar.
        _status(db, video, VideoStatus.ARCHIVED, "Verkleinerung beim Herunterfahren unterbrochen")
        jobs.unterbrochen(db, job, "beim Herunterfahren unterbrochen")
        raise
    except Exception as e:
        # Das alte Buendel steht noch - das Video bleibt abspielbar.
        _fehler_vermerken(db, video_id, VideoStatus.ARCHIVED, f"Recodierung fehlgeschlagen: {e}")
        jobs.gescheitert(db, job, f"{type(e).__name__}: {e}")
        raise
    finally:
        shutil.rmtree(arbeitsordner, ignore_errors=True)


def enqueue_alle_offenen(begrenzung: int = 500) -> int:
    """Reiht alles ein, was noch nicht archiviert ist. Fuer Start und UI-Knopf."""
    from sqlalchemy import select

    with session_scope() as db:
        offen = db.scalars(
            select(Video)
            .where(Video.status.in_([VideoStatus.NEW, VideoStatus.QUEUED]))
            .order_by(Video.upload_date.desc())
            .limit(begrenzung)
        )
        anzahl = 0
        for v in offen:
            jobs.enqueue_archive(db, v.id)
            anzahl += 1
        return anzahl


@jobs.register(JobType.VIDEO_UPGRADE)
def hochstufen(db: Session, job: Job) -> None:
    """Holt ein bereits archiviertes Video in besserer Qualitaet neu.

    "Hochstufen" ist die freundliche Bezeichnung fuer einen vollstaendigen
    Neu-Download. Qualitaet laesst sich einer vorhandenen Datei nicht
    hinzufuegen - was in 1080p gespeichert ist, enthaelt die fehlenden Pixel
    nicht irgendwo versteckt. Der Vorgang kostet also dieselbe Bandbreite und
    dieselbe Zeit wie das erste Archivieren, und er zaehlt genauso gegen
    YouTubes Drosselung von rund 300 Videos je Stunde.

    Drei Vorsichtsmassnahmen machen den Unterschied zum blossen Neuladen:

    Erstens wird **vorher nachgesehen**, ob es ueberhaupt etwas Besseres gibt.
    Das ist ein Metadatenabruf statt eines Downloads. Ohne diesen Schritt
    laedt man bei einem Kanal, der ueberwiegend in 1080p produziert, hunderte
    Videos erneut, um am Ende dieselbe Qualitaet zu haben.

    Zweitens bleibt das **alte Buendel bis zuletzt unangetastet**. Das Video
    ist waehrend des gesamten Vorgangs abspielbar, sein Zustand bleibt
    "archiviert", und der Austausch geschieht in einem Schritt. Geht etwas
    schief, hat man weiterhin die bisherige Fassung - ein Hochstufen darf
    niemals ein vorhandenes Video kosten.

    Drittens wird **nach dem Download geprueft**, ob wirklich mehr angekommen
    ist. Kommt dieselbe oder eine schlechtere Qualitaet, wird das Ergebnis
    verworfen statt eingesetzt.
    """
    video_id = job.target_id
    if not video_id:
        raise ValueError("Hochstufungsauftrag ohne Video-ID")

    video = db.get(Video, video_id)
    if video is None:
        raise ValueError(f"Video {video_id} nicht in der Datenbank")
    if video.status != VideoStatus.ARCHIVED or not video.bundle_file:
        raise ValueError(f"Video {video_id} ist nicht archiviert - nichts zum Hochstufen")

    ziel_guete = int(jobs.payload_of(job).get("ziel") or settings.archive_min_height)
    vorher = ytdlp.guete({"width": video.width, "height": video.height}) or 0

    # ---- 1. Lohnt es sich ueberhaupt? Ein Metadatenabruf, kein Download.
    jobs.fortschritt(db, job, 0.03, "Angebot wird geprueft")
    try:
        angebot_info = ytdlp.fetch_video_info(video_id)
    except ytdlp.VideoUnavailable as e:
        # Das Video ist bei der Quelle weg. Das archivierte bleibt, wie es ist -
        # genau dafuer gibt es ein Archiv.
        jobs.erledigt(db, job, f"bei der Quelle nicht mehr verfuegbar, Buendel bleibt: {e}")
        return
    except ytdlp.Gedrosselt as e:
        jobs.unterbrochen(db, job, drosselung.hinweis(drosselung.melden(str(e))))
        return
    except Exception as e:
        # Muss aufgefangen werden, obwohl hier noch nichts angefasst wurde:
        # Diese Vorpruefung liegt vor dem grossen try weiter unten, und ein
        # Fehler entkam damit jeder Behandlung. Der Auftrag blieb dann auf
        # "laeuft" stehen, bis der naechste Neustart ihn einsammelte.
        jobs.gescheitert(db, job, f"{type(e).__name__}: {e}")
        return

    angebot = ytdlp.angebotene_guete(angebot_info) or 0
    erreichbar = min(angebot, ziel_guete)
    if erreichbar <= vorher:
        jobs.erledigt(
            db, job,
            f"schon {vorher}p - die Quelle bietet hoechstens {angebot}p",
        )
        return

    # ---- 2. Herunterladen, in einen eigenen Ordner.
    arbeitsordner = settings.tmp_dir / f"{video_id}.hochstufen"
    shutil.rmtree(arbeitsordner, ignore_errors=True)
    waehler = (
        f"bestvideo[height>={erreichbar}][width>={erreichbar}]+bestaudio"
        f"/bestvideo+bestaudio/best"
    )
    try:
        jobs.fortschritt(db, job, 0.05, f"{vorher}p → {erreichbar}p wird geladen")
        ergebnis = ytdlp.download_video(
            video_id, arbeitsordner,
            format_selector=waehler,
            fortschritt=lambda anteil, text: jobs.fortschritt(db, job, 0.05 + anteil * 0.55, text),
        )

        # ---- 3. Ist wirklich mehr angekommen?
        info_medien = media.probe(ergebnis.path)
        nachher = ytdlp.guete({"width": info_medien.width, "height": info_medien.height}) or 0
        if nachher <= vorher:
            jobs.erledigt(
                db, job,
                f"kein Gewinn: wieder {nachher}p - das bisherige Buendel bleibt",
            )
            return

        # ---- 4. Erst jetzt wird ausgetauscht.
        alt_bytes = video.bundle_bytes or 0
        _ablegen(
            db, job, video, ergebnis,
            arbeitsordner=arbeitsordner,
            info_medien=info_medien,
            quelle_bytes=ergebnis.path.stat().st_size,
            qualitaetshinweis=None,
            # Der Zustand bleibt "archiviert": Die bisherige Fassung ist bis
            # zum Austausch abspielbar, und ein "wird umgepackt" liesse das
            # Video solange aus der Oberflaeche verschwinden.
            zustaende_pflegen=False,
        )

        # Eine Recodierung des alten Standes ist gegenstandslos geworden.
        if media.should_recode(info_medien, settings.archive_codec)[0]:
            jobs.enqueue(db, JobType.VIDEO_RECODE, video_id, priority=jobs.PRIO_RECODE)

        zuwachs = (video.bundle_bytes or 0) - alt_bytes
        jobs.erledigt(
            db, job,
            f"{vorher}p → {nachher}p, {zuwachs / 1e6:+.1f} MB",
        )
        log.info("%s hochgestuft: %dp -> %dp (%+.1f MB)", video_id, vorher, nachher, zuwachs / 1e6)

    except abbruch.Abgebrochen:
        jobs.unterbrochen(db, job, "beim Herunterfahren unterbrochen")
        raise
    except ytdlp.Gedrosselt as e:
        # Wie beim Archivieren: kein Fehlschlag, sondern eine Abweisung der
        # IP-Adresse. Das Video bleibt in seiner bisherigen Qualitaet
        # archiviert, der Auftrag wartet auf ruhigere Zeiten.
        jobs.unterbrochen(db, job, drosselung.hinweis(drosselung.melden(str(e))))
    except Exception as e:
        # Nichts am Video anfassen: Es ist weiterhin archiviert und spielbar,
        # nur eben in der bisherigen Qualitaet.
        jobs.gescheitert(db, job, f"{type(e).__name__}: {e}")
        raise
    finally:
        shutil.rmtree(arbeitsordner, ignore_errors=True)
