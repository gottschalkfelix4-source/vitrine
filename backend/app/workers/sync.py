"""Kanal- und Playlist-Abgleich.

Der Abgleich laeuft in zwei Geschwindigkeiten, weil YouTube-Requests knapp
sind:

*Schnellcheck* ueber den RSS-Feed. Geht an yt-dlp vorbei, zaehlt nicht gegen
das Drosselungsbudget, liefert aber nur die juengsten ~15 Eintraege. Kann
deshalb stuendlich laufen.

*Vollabgleich* ueber die ``UU``-Uploads-Playlist. Teuer, dafuer vollstaendig.
Laeuft selten.

Zur Playlist-Behandlung: Ein Video, das in der Kanaluebersicht und in drei
Playlists auftaucht, liegt trotzdem genau einmal auf der Platte. Playlists sind
reine Zuordnungen. Und sie zeigen **alle** Positionen, auch die nicht
archivierten - mit Zustandskennzeichnung. Das ist genau der Punkt, an dem
TubeArchivist scheitert ("only items archived will be able to show up"), und
der Grund, warum dort die Playlist-Treue als kaputt gilt.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import (
    Channel,
    Job,
    JobType,
    Playlist,
    PlaylistItem,
    PlaylistKind,
    Video,
    VideoStatus,
    utcnow,
)
from app.services import jobs, ytdlp

log = logging.getLogger(__name__)


def _video_anlegen(db: Session, kanal_id: str | None, eintrag: ytdlp.ListedVideo) -> tuple[Video, bool]:
    """Legt ein Video an, falls es neu ist. Liefert (Video, war_neu)."""
    v = db.get(Video, eintrag.id)
    if v is not None:
        # Vorhandene Videos nur behutsam auffrischen - Titel und Aufrufe
        # aendern sich, der Archivzustand darf dabei nicht angefasst werden.
        if eintrag.title and eintrag.title != "(ohne Titel)":
            v.title = eintrag.title
        if eintrag.view_count is not None:
            v.view_count = eintrag.view_count
        return v, False

    v = Video(
        id=eintrag.id,
        channel_id=kanal_id,
        title=eintrag.title,
        duration_s=eintrag.duration_s,
        upload_date=eintrag.upload_date,
        view_count=eintrag.view_count,
        status=VideoStatus.NEW,
    )
    db.add(v)
    return v, True


def _soll_archiviert_werden(kanal: Channel, video: Video) -> bool:
    if not kanal.auto_archive:
        return False
    if video.is_short and not kanal.archive_shorts:
        return False
    if video.was_live and not kanal.archive_live:
        return False
    if kanal.archive_since and video.upload_date and video.upload_date < kanal.archive_since:
        return False
    return True


def _sammlung_abgleichen(
    db: Session,
    kanal: Channel,
    playlist_id: str,
    titel: str,
    art: str,
    url: str,
    *,
    einreihen: bool,
) -> int:
    """Gleicht eine Sammlung ab und pflegt ihre Zuordnungen. Liefert die Zahl
    neuer Videos."""
    liste = db.get(Playlist, playlist_id)
    if liste is None:
        liste = Playlist(id=playlist_id, channel_id=kanal.id, kind=art, title=titel)
        db.add(liste)
    liste.title = titel
    liste.kind = art

    eintraege = ytdlp.list_entries(url)
    neu = 0

    # Zuordnungen vollstaendig neu setzen: Reihenfolge und Zusammensetzung einer
    # Playlist aendern sich, entfernte Positionen muessen verschwinden.
    db.execute(
        PlaylistItem.__table__.delete().where(PlaylistItem.playlist_id == playlist_id)
    )

    for position, eintrag in enumerate(eintraege):
        video, war_neu = _video_anlegen(db, kanal.id, eintrag)
        if art == PlaylistKind.SHORTS:
            video.is_short = True
        if art == PlaylistKind.LIVE:
            video.was_live = True
        neu += int(war_neu)
        db.add(PlaylistItem(playlist_id=playlist_id, video_id=eintrag.id, position=position))

    liste.item_count = len(eintraege)
    liste.last_synced_at = utcnow()
    db.commit()

    if einreihen:
        for eintrag in eintraege:
            video = db.get(Video, eintrag.id)
            if video and video.status == VideoStatus.NEW and _soll_archiviert_werden(kanal, video):
                video.status = VideoStatus.QUEUED
                jobs.enqueue_archive(db, video.id)
        db.commit()

    return neu


@jobs.register(JobType.CHANNEL_SYNC)
def kanal_abgleichen(db: Session, job: Job) -> None:
    kanal_id = job.target_id
    if not kanal_id:
        raise ValueError("Abgleichauftrag ohne Kanal-ID")

    kanal = db.get(Channel, kanal_id)
    if kanal is None:
        raise ValueError(f"Kanal {kanal_id} nicht in der Datenbank")

    voll = jobs.payload_of(job).get("voll", False)

    try:
        # ---- Schnellcheck: kostet keinen yt-dlp-Request
        jobs.fortschritt(db, job, 0.05, "Schnellcheck ueber RSS")
        neu_gesehen = 0
        try:
            for eintrag in ytdlp.peek_recent(kanal_id):
                _, war_neu = _video_anlegen(db, kanal_id, eintrag)
                neu_gesehen += int(war_neu)
            db.commit()
        except ytdlp.YtdlpError as e:
            log.info("RSS-Schnellcheck fuer %s fehlgeschlagen: %s", kanal_id, e)

        # Nichts Neues im Feed und kein Vollabgleich verlangt: fertig. Das ist
        # der Normalfall und kostet praktisch nichts.
        if not voll and neu_gesehen == 0:
            kanal.last_synced_at = utcnow()
            db.commit()
            jobs.erledigt(db, job, "keine Aenderung (RSS)")
            return

        # ---- Vollabgleich ueber die Sammelplaylists
        jobs.fortschritt(db, job, 0.2, "Uploads werden gelesen")
        neu = _sammlung_abgleichen(
            db, kanal,
            playlist_id="UU" + kanal_id[2:],
            titel="Alle Uploads",
            art=PlaylistKind.UPLOADS,
            url=ytdlp.channel_auto_playlist(kanal_id, "uploads"),
            einreihen=True,
        )

        # Shorts und Livestreams nur, wenn der Kanal sie ueberhaupt archivieren
        # soll - sonst waeren es teure Requests fuer Videos, die niemand will.
        schritt = 0.5
        for art, schluessel, praefix, titel in (
            (PlaylistKind.SHORTS, "shorts", "UUSH", "Shorts"),
            (PlaylistKind.LIVE, "live", "UULV", "Livestreams"),
        ):
            if art == PlaylistKind.SHORTS and not kanal.archive_shorts:
                continue
            if art == PlaylistKind.LIVE and not kanal.archive_live:
                continue
            schritt += 0.15
            jobs.fortschritt(db, job, schritt, f"{titel} werden gelesen")
            try:
                _sammlung_abgleichen(
                    db, kanal,
                    playlist_id=praefix + kanal_id[2:],
                    titel=titel,
                    art=art,
                    url=ytdlp.channel_auto_playlist(kanal_id, schluessel),
                    einreihen=True,
                )
            except ytdlp.YtdlpError as e:
                # Nicht jeder Kanal hat Shorts oder Streams - kein Fehler.
                log.info("%s fuer %s nicht vorhanden: %s", titel, kanal_id, e)

        # ---- Die vom Kanal angelegten Playlists
        jobs.fortschritt(db, job, 0.8, "Playlists werden gelesen")
        kanal_url = f"https://www.youtube.com/channel/{kanal_id}"
        for p in ytdlp.list_channel_playlists(kanal_url):
            try:
                _sammlung_abgleichen(
                    db, kanal,
                    playlist_id=p.id,
                    titel=p.title,
                    art=PlaylistKind.PLAYLIST,
                    url=ytdlp.playlist_url(p.id),
                    # Playlists reihen nichts zusaetzlich ein: Ihre Videos
                    # stecken bereits in den Uploads. Sie stiften nur Ordnung.
                    einreihen=False,
                )
            except ytdlp.YtdlpError as e:
                log.warning("Playlist %s (%s) nicht lesbar: %s", p.id, p.title, e)

        kanal.last_synced_at = utcnow()
        db.commit()
        jobs.erledigt(db, job, f"{neu} neue Videos gefunden")

    except Exception as e:
        jobs.gescheitert(db, job, f"{type(e).__name__}: {e}")
        raise


@jobs.register(JobType.PLAYLIST_SYNC)
def playlist_abgleichen(db: Session, job: Job) -> None:
    playlist_id = job.target_id
    if not playlist_id:
        raise ValueError("Abgleichauftrag ohne Playlist-ID")

    liste = db.get(Playlist, playlist_id)
    if liste is None:
        raise ValueError(f"Playlist {playlist_id} nicht in der Datenbank")
    kanal = db.get(Channel, liste.channel_id) if liste.channel_id else None
    if kanal is None:
        raise ValueError(f"Playlist {playlist_id} hat keinen Kanal")

    _sammlung_abgleichen(
        db, kanal,
        playlist_id=playlist_id,
        titel=liste.title,
        art=liste.kind,
        url=ytdlp.playlist_url(playlist_id),
        einreihen=jobs.payload_of(job).get("einreihen", False),
    )
    jobs.erledigt(db, job, "abgeglichen")


def faellige_kanaele_einreihen() -> int:
    """Reiht alle Kanaele ein, deren Abgleich ansteht.

    Wird vom Zeitplaner aufgerufen. Kanaele ohne eigenen Rhythmus benutzen den
    globalen Standardwert.
    """
    from app.db import session_scope

    jetzt = utcnow()
    with session_scope() as db:
        anzahl = 0
        for kanal in db.scalars(select(Channel).where(Channel.sync_enabled.is_(True))):
            stunden = kanal.sync_interval_hours or settings.default_sync_interval_hours
            letzter = kanal.last_synced_at
            if letzter is not None and letzter.tzinfo is None:
                from datetime import UTC

                letzter = letzter.replace(tzinfo=UTC)
            if letzter is None or jetzt - letzter >= timedelta(hours=stunden):
                jobs.enqueue_channel_sync(db, kanal.id)
                anzahl += 1
        return anzahl
