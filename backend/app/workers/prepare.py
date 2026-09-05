"""Herstellung einer Heisskopie.

Der Ausnahmefall, nicht der Regelfall: Rund 91 % aller Browsersitzungen koennen
AV1 dekodieren und bekommen die Bytes direkt aus dem Buendel. Nur der Rest -
im Wesentlichen aeltere Apple-Geraete und alte Smart-TVs - landet hier.

Zwei Wege, die sich um Groessenordnungen unterscheiden:

*Umpacken* - Video- und Tonstrom werden unveraendert in einen anderen
Behaelter kopiert. Laeuft mit weit ueber hundertfacher Echtzeit, also Sekunden
statt Minuten. Reicht immer dann, wenn nur der Behaelter das Problem war.

*Transkodieren* - Das Video wird neu kodiert. Kostet je nach Geraet das Vier-
bis Sechsfache der Echtzeit und ist nur noetig, wenn der Client den Videocodec
selbst nicht beherrscht.

Vor dem teuren Weg wird deshalb immer geprueft, ob der billige genuegt.
"""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import HotCopy, HotCopyStatus, Job, JobType, Video, VideoStatus, utcnow
from app.services import bundle, cache, jobs, media, paths, playback

log = logging.getLogger(__name__)


def _eintrag_anlegen(db: Session, video: Video, variante: str, pfad: Path) -> HotCopy:
    """Legt den Eintrag VOR der Arbeit an, mit Status 'in Vorbereitung'.

    Wichtig fuer den Reaper: Solange dieser Status steht, fasst er die Datei
    nicht an. Ohne den Eintrag koennte er die halbfertige Datei als verwaist
    einstufen und mitten im Entpacken loeschen.
    """
    hot = db.scalar(select(HotCopy).where(HotCopy.video_id == video.id, HotCopy.variant == variante))
    if hot is None:
        hot = HotCopy(video_id=video.id, variant=variante)
        db.add(hot)
    hot.path = str(pfad)
    hot.status = HotCopyStatus.PREPARING
    hot.error = None
    hot.created_at = utcnow()
    hot.last_access_at = utcnow()
    db.commit()
    return hot


@jobs.register(JobType.VIDEO_PREPARE)
def vorbereiten(db: Session, job: Job) -> None:
    video_id = job.target_id
    if not video_id:
        raise ValueError("Vorbereitungsauftrag ohne Video-ID")

    video = db.get(Video, video_id)
    if video is None or video.status != VideoStatus.ARCHIVED or not video.bundle_file:
        raise ValueError(f"Video {video_id} ist nicht archiviert")

    variante = jobs.payload_of(job).get("variant") or playback.TRANSCODE_VARIANT
    paths.component(video_id)
    channel_dir = paths.child(settings.bundle_dir, video.channel_id or "_lose")
    buendel = paths.contained(channel_dir, Path(video.bundle_file))
    settings.cache_dir.mkdir(parents=True, exist_ok=True)

    hot: HotCopy | None = None
    try:
        with bundle.BundleReader(buendel) as leser:
            manifest = leser.manifest
            quell_suffix = Path(manifest.media_name).suffix.lower()

            # --- Variante "source": unveraendert entpacken.
            if variante == "source":
                ziel = cache.hot_path_for(video_id, variante, quell_suffix)
                hot = _eintrag_anlegen(db, video, variante, ziel)
                jobs.fortschritt(db, job, 0.1, "wird entpackt")
                leser.extract_media(ziel)
                cache.register(db, video, variante, ziel, manifest.mime_type)
                jobs.erledigt(db, job, f"entpackt, {ziel.stat().st_size / 1e6:.1f} MB")
                return

            # --- Variante "h264": erst pruefen, ob Umpacken reicht.
            ziel = cache.hot_path_for(video_id, variante, playback.TRANSCODE_SUFFIX)
            hot = _eintrag_anlegen(db, video, variante, ziel)

            roh = paths.child(settings.tmp_dir, f"{video_id}.prepare{quell_suffix}")
            roh.parent.mkdir(parents=True, exist_ok=True)
            jobs.fortschritt(db, job, 0.05, "Buendel wird gelesen")
            leser.extract_media(roh)

        try:
            info = media.probe(roh)
            vcodec = playback.normalize_video_codec(info.video_codec)
            acodec = playback.normalize_audio_codec(info.audio_codec)

            # Der billige Weg: Das Video ist bereits H.264, nur der Behaelter
            # passte nicht. Dann nur umpacken - Sekunden statt Minuten.
            if vcodec == "h264" and acodec in ("aac", "mp3", None):
                jobs.fortschritt(db, job, 0.2, "wird umgepackt (kein Neukodieren noetig)")
                plan = media.ContainerPlan(".mp4", ton_umkodieren=False, ziel_ton=None, grund="nur Behaelter")
                befehl = media.build_remux_cmd(roh, ziel, plan)
                text = "umgepackt"
            elif vcodec == "h264":
                jobs.fortschritt(db, job, 0.2, "Ton wird umgesetzt, Video bleibt")
                plan = media.ContainerPlan(".mp4", ton_umkodieren=True, ziel_ton="aac", grund="Ton passt nicht")
                befehl = media.build_remux_cmd(roh, ziel, plan)
                text = "Ton umgesetzt"
            else:
                jobs.fortschritt(db, job, 0.2, f"{info.video_codec} wird nach H.264 transkodiert")
                befehl = media.build_playback_cmd(roh, ziel)
                text = "transkodiert"

            media.run_ffmpeg(
                befehl,
                dauer_s=info.duration_s,
                fortschritt=lambda a: jobs.fortschritt(db, job, 0.2 + a * 0.75),
                abbruch=lambda: _abgebrochen(db, job),
            )
            cache.register(db, video, variante, ziel, playback.TRANSCODE_MIME)
            jobs.erledigt(db, job, f"{text}, {ziel.stat().st_size / 1e6:.1f} MB")
        finally:
            roh.unlink(missing_ok=True)

    except Exception as e:
        if hot is not None:
            cache.mark_failed(db, hot, f"{type(e).__name__}: {e}")
        jobs.gescheitert(db, job, f"{type(e).__name__}: {e}")
        raise


def _abgebrochen(db: Session, job: Job) -> bool:
    """Fragt, ob der Auftrag zwischenzeitlich abgebrochen wurde.

    Ohne diese Abfrage laeuft eine abgebrochene Transkodierung im Hintergrund
    weiter und belegt einen Kern, obwohl niemand mehr auf das Ergebnis wartet.
    """
    db.refresh(job)
    return job.status == "cancelled"
