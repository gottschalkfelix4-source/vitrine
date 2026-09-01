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
from app.services import bundle, jobs, media, suche, ytdlp

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


@jobs.register(JobType.VIDEO_ARCHIVE)
def archivieren(db: Session, job: Job) -> None:
    video_id = job.target_id
    if not video_id:
        raise ValueError("Archivierungsauftrag ohne Video-ID")

    video = db.get(Video, video_id)
    if video is None:
        raise ValueError(f"Video {video_id} nicht in der Datenbank")

    arbeitsordner = settings.tmp_dir / video_id
    if arbeitsordner.exists():
        shutil.rmtree(arbeitsordner, ignore_errors=True)

    try:
        # ---- 1. Herunterladen (0 bis 60 % des Auftrags)
        _status(db, video, VideoStatus.DOWNLOADING)
        jobs.fortschritt(db, job, 0.02, "Download beginnt")

        kanal = video.channel
        ergebnis = ytdlp.download_video(
            video_id,
            arbeitsordner,
            format_selector=(kanal.format_selector if kanal else None),
            fortschritt=lambda anteil, text: jobs.fortschritt(db, job, anteil * 0.6, text),
        )

        # ---- 2. Pruefen, ob wirklich die gewuenschte Qualitaet ankam
        # Muss vor allem anderen passieren: Ein stiller 360p-Rueckfall darf
        # niemals als archiviert verbucht werden.
        ytdlp.check_not_degraded(ergebnis.info, mindesthoehe=settings.recode_min_height)

        quelle_bytes = ergebnis.path.stat().st_size
        info_medien = media.probe(ergebnis.path)

        # Hochkant heisst Short. Die Kennzeichnung ueber die UUSH-Playlist
        # greift nur, wenn Shorts beim Kanal eingeschaltet sind - ein Short,
        # das ueber die Uploads-Liste kam, landete sonst unter "Videos" und
        # wurde dort in eine 16:9-Buehne gezwungen.
        if info_medien.width and info_medien.height and info_medien.height > info_medien.width:
            video.is_short = True
            db.commit()

        # ---- 3. In einen browsertauglichen Behaelter umpacken (60 bis 80 %)
        plan = media.plan_container(info_medien)
        medien_datei = ergebnis.path
        if ergebnis.path.suffix.lower() != plan.suffix:
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
            )
            medien_datei = umgepackt
            info_medien = media.probe(medien_datei)

        # ---- 4. Buendeln (80 bis 100 %)
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
        bundle.write_bundle(
            ziel,
            manifest=manifest,
            media_file=medien_datei,
            info_json=ergebnis.info,
            thumbnail=ergebnis.thumbnail,
            subtitles=ergebnis.subtitles,
        )

        # ---- 5. Datenbank nachziehen
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
        _status(db, video, VideoStatus.ARCHIVED)

        _in_suche_aufnehmen(db, video, ergebnis.subtitles)

        # ---- 6. Recodierung nachgelagert einreihen, falls sie sich lohnt
        codec = media.ArchiveCodec(kanal.archive_codec) if kanal and kanal.archive_codec else settings.archive_codec
        lohnt, grund = media.should_recode(info_medien, codec)
        if lohnt:
            jobs.enqueue(db, JobType.VIDEO_RECODE, video_id, priority=jobs.PRIO_RECODE)
            log.info("%s archiviert, Recodierung eingereiht (%s)", video_id, grund)
        else:
            log.info("%s archiviert, keine Recodierung (%s)", video_id, grund)

        jobs.erledigt(db, job, f"archiviert, {video.bundle_bytes / 1e6:.1f} MB")

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
        shutil.rmtree(arbeitsordner, ignore_errors=True)


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
        media.run_ffmpeg(
            media.build_archive_cmd(entpackt, ziel_datei, codec),
            dauer_s=info_vorher.duration_s,
            fortschritt=lambda a: jobs.fortschritt(db, job, 0.1 + a * 0.8),
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
