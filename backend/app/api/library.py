"""Die Bibliothek: Kanaele, Playlists, Videos, Warteschlange.

Ein Gestaltungsgrundsatz zieht sich durch alle Auflistungen: Eine Playlist
zeigt **alle** Positionen, auch die nicht archivierten, jeweils mit ihrem
Zustand. Genau daran scheitert TubeArchivist ("only items archived will be able
to show up") - und deshalb gilt dort die Playlist-Treue als kaputt. Wer sehen
will, was ihm fehlt, muss es sehen koennen.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

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
    utcnow,
)
from app.services import cache, jobs, ytdlp
from app.services import suche as volltext

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["bibliothek"])


# ------------------------------------------------------------------ Schemata


class VideoKurz(BaseModel):
    id: str
    titel: str
    kanal_id: str | None
    kanal_name: str | None = None
    dauer_s: int | None
    hochgeladen: str | None
    aufrufe: int | None
    thumb: str | None
    status: str
    ist_short: bool
    war_live: bool
    gesehen: bool
    fortschritt_s: float
    #: Anteil 0..1 fuer den roten Balken auf dem Vorschaubild.
    fortschritt_anteil: float | None = None
    buendel_bytes: int | None = None
    recodiert: bool = False

    @classmethod
    def aus(cls, v: Video) -> VideoKurz:
        anteil = None
        if v.duration_s and v.progress_s:
            anteil = min(1.0, v.progress_s / v.duration_s)
        return cls(
            id=v.id,
            titel=v.title,
            kanal_id=v.channel_id,
            kanal_name=v.channel.name if v.channel else None,
            dauer_s=v.duration_s,
            hochgeladen=v.upload_date.isoformat() if v.upload_date else None,
            aufrufe=v.view_count,
            thumb=v.thumb_file,
            status=v.status,
            ist_short=v.is_short,
            war_live=v.was_live,
            gesehen=v.watched,
            fortschritt_s=v.progress_s,
            fortschritt_anteil=anteil,
            buendel_bytes=v.bundle_bytes,
            recodiert=v.recoded,
        )


class KanalKurz(BaseModel):
    id: str
    name: str
    handle: str | None
    avatar: str | None
    abonniert: bool
    abgleich_aktiv: bool
    zuletzt_abgeglichen: str | None
    videos_gesamt: int = 0
    videos_archiviert: int = 0
    belegung_bytes: int = 0


class KanalAnlegen(BaseModel):
    url: str = Field(description="Kanal-URL, Handle (@name) oder Kanal-ID (UC...)")
    sofort_archivieren: bool = True
    shorts: bool = False
    livestreams: bool = False


class PlaylistPosition(BaseModel):
    """Eine Position in einer Playlist - archiviert oder nicht.

    Nicht archivierte Positionen bewusst mit ausgeben: Eine Playlist, die still
    nur die vorhandenen Videos zeigt, verschweigt genau das, was man wissen will.
    """

    position: int
    video: VideoKurz


# --------------------------------------------------------------------- Kanaele


def _kanal_kurz(db: Session, k: Channel) -> KanalKurz:
    gesamt, archiviert, bytes_ = db.execute(
        select(
            func.count(Video.id),
            func.count(Video.id).filter(Video.status == VideoStatus.ARCHIVED),
            func.coalesce(func.sum(Video.bundle_bytes), 0),
        ).where(Video.channel_id == k.id)
    ).one()
    return KanalKurz(
        id=k.id,
        name=k.name,
        handle=k.handle,
        avatar=k.avatar_file,
        abonniert=k.subscribed,
        abgleich_aktiv=k.sync_enabled,
        zuletzt_abgeglichen=k.last_synced_at.isoformat() if k.last_synced_at else None,
        videos_gesamt=gesamt,
        videos_archiviert=archiviert,
        belegung_bytes=int(bytes_ or 0),
    )


@router.get("/channels", response_model=list[KanalKurz])
def kanaele(db: Session = Depends(get_db)) -> list[KanalKurz]:
    return [_kanal_kurz(db, k) for k in db.scalars(select(Channel).order_by(Channel.name))]


@router.post("/channels", response_model=KanalKurz, status_code=status.HTTP_201_CREATED)
def kanal_anlegen(daten: KanalAnlegen, db: Session = Depends(get_db)) -> KanalKurz:
    """Nimmt einen Kanal auf und stoesst den ersten Abgleich an.

    Der Abgleich laeuft im Hintergrund - bei einem Kanal mit tausenden Videos
    dauert schon das blosse Auflisten Minuten, so lange darf keine
    HTTP-Anfrage offen stehen.
    """
    url = daten.url.strip()
    if not url:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "URL fehlt")
    if url.startswith("@"):
        url = f"https://www.youtube.com/{url}"
    elif url.startswith("UC") and "/" not in url:
        url = f"https://www.youtube.com/channel/{url}"

    try:
        info = ytdlp.fetch_channel(url)
    except ytdlp.YtdlpError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Kanal nicht lesbar: {e}") from e

    kanal = db.get(Channel, info.id)
    if kanal is None:
        kanal = Channel(id=info.id)
        db.add(kanal)
    kanal.name = info.name
    kanal.handle = info.handle
    kanal.description = info.description
    kanal.subscriber_count = info.subscriber_count
    kanal.subscribed = True
    kanal.sync_enabled = True
    kanal.auto_archive = daten.sofort_archivieren
    kanal.archive_shorts = daten.shorts
    kanal.archive_live = daten.livestreams
    db.commit()

    jobs.enqueue(db, JobType.CHANNEL_SYNC, kanal.id, priority=jobs.PRIO_SYNC, payload={"voll": True})
    return _kanal_kurz(db, kanal)


@router.get("/channels/{kanal_id}")
def kanal(kanal_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    k = db.get(Channel, kanal_id)
    if k is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Kanal unbekannt")

    listen = db.scalars(
        select(Playlist).where(Playlist.channel_id == kanal_id).order_by(Playlist.kind, Playlist.title)
    )
    return {
        "kanal": _kanal_kurz(db, k),
        "beschreibung": k.description,
        "banner": k.banner_file,
        # Die Tabs entsprechen der YouTube-Gliederung: Videos, Shorts,
        # Livestreams, Playlists.
        "sammlungen": [
            {
                "id": p.id,
                "titel": p.title,
                "art": p.kind,
                "anzahl": p.item_count,
                "thumb": p.thumb_file,
            }
            for p in listen
        ],
        "regeln": {
            "auto_archivieren": k.auto_archive,
            "shorts": k.archive_shorts,
            "livestreams": k.archive_live,
            "codec": k.archive_codec or settings.archive_codec,
            "abgleich_stunden": k.sync_interval_hours or settings.default_sync_interval_hours,
        },
    }


@router.post("/channels/{kanal_id}/sync", status_code=status.HTTP_202_ACCEPTED)
def kanal_abgleichen(
    kanal_id: str,
    voll: bool = Query(False, description="Vollabgleich statt RSS-Schnellcheck"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    if db.get(Channel, kanal_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Kanal unbekannt")
    job = jobs.enqueue(
        db, JobType.CHANNEL_SYNC, kanal_id, priority=jobs.PRIO_SYNC, payload={"voll": voll}
    )
    return {"job_id": job.id, "status": job.status}


# ------------------------------------------------------------------ Playlists


@router.get("/playlists/{playlist_id}")
def playlist(playlist_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    p = db.get(Playlist, playlist_id)
    if p is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Playlist unbekannt")

    positionen = db.scalars(
        select(PlaylistItem)
        .where(PlaylistItem.playlist_id == playlist_id)
        .order_by(PlaylistItem.position)
    )
    eintraege = [
        PlaylistPosition(position=e.position, video=VideoKurz.aus(e.video)) for e in positionen
    ]
    archiviert = sum(1 for e in eintraege if e.video.status == VideoStatus.ARCHIVED)
    return {
        "id": p.id,
        "titel": p.title,
        "art": p.kind,
        "kanal_id": p.channel_id,
        "beschreibung": p.description,
        "anzahl_quelle": p.item_count,
        "anzahl_archiviert": archiviert,
        "positionen": eintraege,
    }


# --------------------------------------------------------------------- Videos


@router.get("/videos", response_model=list[VideoKurz])
def videos(
    db: Session = Depends(get_db),
    kanal: str | None = None,
    status_filter: str | None = Query(None, alias="status"),
    suche: str | None = None,
    nur_archiviert: bool = True,
    sortierung: Literal["neu", "alt", "aufrufe", "titel"] = "neu",
    limit: int = Query(60, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> list[VideoKurz]:
    anfrage = select(Video)
    if kanal:
        anfrage = anfrage.where(Video.channel_id == kanal)
    if status_filter:
        anfrage = anfrage.where(Video.status == status_filter)
    elif nur_archiviert:
        anfrage = anfrage.where(Video.status == VideoStatus.ARCHIVED)
    if suche:
        # Der Volltextindex findet auch mitten im Wort und damit deutsche
        # Komposita. Fehlt er - alte SQLite ohne FTS5 - oder ist die Eingabe zu
        # kurz fuer Trigramme, bleibt der einfache Vergleich als Rueckfall.
        # Langsam, aber besser als gar kein Ergebnis.
        zu_kurz = len(volltext.normalisieren(suche).strip()) < volltext.MIN_LAENGE
        if zu_kurz or not volltext.verfuegbar(db):
            muster = f"%{suche}%"
            anfrage = anfrage.where(Video.title.ilike(muster) | Video.description.ilike(muster))
        else:
            treffer = volltext.video_treffer(db, suche, limit=limit, offset=offset)
            if not treffer:
                return []
            anfrage = anfrage.where(Video.id.in_(treffer))

    anfrage = anfrage.order_by(
        {
            "neu": Video.upload_date.desc(),
            "alt": Video.upload_date.asc(),
            "aufrufe": Video.view_count.desc(),
            "titel": Video.title.asc(),
        }[sortierung]
    )
    return [VideoKurz.aus(v) for v in db.scalars(anfrage.limit(limit).offset(offset))]


class Untertitelfund(BaseModel):
    video: VideoKurz
    start_s: float
    sprache: str
    zeile: str


class Suchergebnis(BaseModel):
    anfrage: str
    videos: list[VideoKurz]
    #: Fundstellen im gesprochenen Wort, je Video hoechstens eine.
    im_gesprochenen: list[Untertitelfund]
    zu_kurz: bool = False


@router.get("/search", response_model=Suchergebnis)
def volltextsuche(
    q: str = Query(description="Suchbegriff"),
    limit: int = Query(40, ge=1, le=100),
    db: Session = Depends(get_db),
) -> Suchergebnis:
    """Sucht in Titeln, Beschreibungen und im gesprochenen Wort.

    Die Untertitelfunde sind der eigentliche Mehrwert gegenueber einer
    Titelsuche: Sie liefern nicht nur das Video, sondern die Sekunde, an der
    der Begriff faellt.
    """
    if len(volltext.normalisieren(q).strip()) < volltext.MIN_LAENGE:
        return Suchergebnis(anfrage=q, videos=[], im_gesprochenen=[], zu_kurz=True)

    ids = volltext.video_treffer(db, q, limit=limit)
    gefunden = {v.id: v for v in db.scalars(select(Video).where(Video.id.in_(ids)))} if ids else {}
    # Reihenfolge des Index beibehalten - sie ist die Relevanzsortierung.
    videos = [VideoKurz.aus(gefunden[i]) for i in ids if i in gefunden]

    funde: list[Untertitelfund] = []
    for f in volltext.untertitel_treffer(db, q, limit=limit):
        v = db.get(Video, f.video_id)
        if v is None:
            continue
        funde.append(
            Untertitelfund(
                video=VideoKurz.aus(v), start_s=f.start_s, sprache=f.sprache, zeile=f.zeile
            )
        )

    return Suchergebnis(anfrage=q, videos=videos, im_gesprochenen=funde)


@router.post("/search/reindex", status_code=status.HTTP_200_OK)
def suchindex_neu_aufbauen(db: Session = Depends(get_db)) -> dict[str, Any]:
    """Baut den Suchindex aus der Datenbank und den Buendeln neu auf.

    Noetig nach einem Import bestehender Daten oder wenn die Untertitelsuche
    erst nachtraeglich eingeschaltet wurde.
    """
    from app.services.reindex import index_neu_aufbauen

    return index_neu_aufbauen(db)


@router.get("/videos/{video_id}")
def video(video_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    v = db.get(Video, video_id)
    if v is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Video unbekannt")

    in_listen = db.scalars(
        select(Playlist)
        .join(PlaylistItem, PlaylistItem.playlist_id == Playlist.id)
        .where(PlaylistItem.video_id == video_id, Playlist.kind == PlaylistKind.PLAYLIST)
    )
    return {
        "video": VideoKurz.aus(v),
        "beschreibung": v.description,
        "kapitel": [
            {"titel": k.title, "start_s": k.start_s, "ende_s": k.end_s} for k in v.chapters
        ],
        "untertitel": [
            {"sprache": u.language, "automatisch": u.is_auto} for u in v.subtitles
        ],
        "technik": {
            "videocodec": v.video_codec,
            "audiocodec": v.audio_codec,
            "breite": v.width,
            "hoehe": v.height,
            "fps": v.fps,
            "recodiert": v.recoded,
            "buendel_bytes": v.bundle_bytes,
            "quelle_bytes": v.source_bytes,
            "gespart_bytes": v.saved_bytes,
        },
        "in_playlists": [{"id": p.id, "titel": p.title} for p in in_listen],
        "statusmeldung": v.status_message,
    }


class Fortschritt(BaseModel):
    sekunden: float = Field(ge=0)
    gesehen: bool | None = None


@router.put("/videos/{video_id}/progress", status_code=status.HTTP_204_NO_CONTENT)
def fortschritt_merken(video_id: str, daten: Fortschritt, db: Session = Depends(get_db)) -> None:
    v = db.get(Video, video_id)
    if v is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Video unbekannt")
    v.progress_s = daten.sekunden
    v.last_watched_at = utcnow()
    if daten.gesehen is not None:
        v.watched = daten.gesehen
    # Ab 90 % gilt es als gesehen - dieselbe Schwelle wie bei YouTube, damit
    # der Abspann nicht als "noch offen" haengen bleibt.
    elif v.duration_s and daten.sekunden / v.duration_s >= 0.9:
        v.watched = True
        v.watch_count += 1
    db.commit()


@router.post("/videos/{video_id}/archive", status_code=status.HTTP_202_ACCEPTED)
def video_archivieren(video_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    v = db.get(Video, video_id)
    if v is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Video unbekannt")
    if v.status == VideoStatus.ARCHIVED:
        raise HTTPException(status.HTTP_409_CONFLICT, "Video ist bereits archiviert")
    v.status = VideoStatus.QUEUED
    db.commit()
    job = jobs.enqueue_archive(db, video_id)
    return {"job_id": job.id, "status": job.status}


# --------------------------------------------------------------- Warteschlange


@router.get("/jobs")
def warteschlange(
    db: Session = Depends(get_db),
    status_filter: str | None = Query(None, alias="status"),
    limit: int = Query(50, ge=1, le=200),
) -> list[dict[str, Any]]:
    anfrage = select(Job)
    if status_filter:
        anfrage = anfrage.where(Job.status == status_filter)
    else:
        # Erledigtes ist selten interessant - der Blick gilt dem, was noch aussteht.
        anfrage = anfrage.where(Job.status.in_([JobStatus.PENDING, JobStatus.RUNNING, JobStatus.FAILED]))
    anfrage = anfrage.order_by(Job.status, Job.priority, Job.created_at.desc())

    ergebnis = []
    for j in db.scalars(anfrage.limit(limit)):
        titel = None
        if j.type in (JobType.VIDEO_ARCHIVE, JobType.VIDEO_RECODE, JobType.VIDEO_PREPARE) and j.target_id:
            v = db.get(Video, j.target_id)
            titel = v.title if v else None
        elif j.type == JobType.CHANNEL_SYNC and j.target_id:
            k = db.get(Channel, j.target_id)
            titel = k.name if k else None
        ergebnis.append({
            "id": j.id,
            "art": j.type,
            "ziel": j.target_id,
            "titel": titel,
            "status": j.status,
            "fortschritt": j.progress,
            "meldung": j.message,
            "fehler": j.error,
            "erstellt": j.created_at.isoformat() if j.created_at else None,
        })
    return ergebnis


@router.post("/jobs/{job_id}/cancel", status_code=status.HTTP_204_NO_CONTENT)
def auftrag_abbrechen(job_id: int, db: Session = Depends(get_db)) -> None:
    j = db.get(Job, job_id)
    if j is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Auftrag unbekannt")
    if j.status in (JobStatus.DONE, JobStatus.CANCELLED):
        raise HTTPException(status.HTTP_409_CONFLICT, f"Auftrag ist bereits {j.status}")
    jobs.abbrechen(db, j)


@router.post("/jobs/{job_id}/retry", status_code=status.HTTP_202_ACCEPTED)
def auftrag_wiederholen(job_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    j = db.get(Job, job_id)
    if j is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Auftrag unbekannt")
    j.status = JobStatus.PENDING
    j.error = None
    j.progress = 0.0
    j.started_at = None
    j.finished_at = None
    db.commit()
    return {"job_id": j.id, "status": j.status}


# ------------------------------------------------------------------ Speicher


@router.get("/storage")
def speicher(db: Session = Depends(get_db)) -> dict[str, Any]:
    """Der Blick, den YouTube nicht hat und ein Archiv braucht."""
    kalt_bytes, quelle_bytes, archiviert = db.execute(
        select(
            func.coalesce(func.sum(Video.bundle_bytes), 0),
            func.coalesce(func.sum(Video.source_bytes), 0),
            func.count(Video.id).filter(Video.status == VideoStatus.ARCHIVED),
        )
    ).one()
    nach_status = dict(
        db.execute(select(Video.status, func.count(Video.id)).group_by(Video.status)).all()
    )
    offen_recode = db.scalar(
        select(func.count(Job.id)).where(
            Job.type == JobType.VIDEO_RECODE, Job.status == JobStatus.PENDING
        )
    )
    return {
        "kaltspeicher": {
            "bytes": int(kalt_bytes or 0),
            "videos": archiviert,
            "quelle_bytes": int(quelle_bytes or 0),
            "gespart_bytes": int((quelle_bytes or 0) - (kalt_bytes or 0)),
        },
        "heissspeicher": cache.usage(db),
        "freier_platz": cache.free_space(),
        "videos_nach_status": nach_status,
        "recodierungen_offen": offen_recode or 0,
    }


@router.get("/thumbs/{datei}")
def thumbnail(datei: str) -> FileResponse:
    """Liefert ein Vorschaubild.

    Die Bilder liegen bewusst ausserhalb der Buendel - ein Grid mit hunderten
    Kacheln wuerde sonst ebenso viele ZIP-Dateien oeffnen und auf einem Array
    mit schlafenden Platten jedes Mal die Platten wecken.
    """
    # Pfadanteile herausfiltern: Der Wert kommt aus der URL, ein "../" darf
    # nicht aus dem Verzeichnis herausfuehren.
    from pathlib import PurePosixPath

    name = PurePosixPath(datei).name
    pfad = settings.thumb_dir / name
    if not name or not pfad.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Vorschaubild unbekannt")
    return FileResponse(pfad, headers={"Cache-Control": "public, max-age=604800"})


@router.get("/videos/{video_id}/subtitles/{sprache}")
def untertitel(video_id: str, sprache: str, db: Session = Depends(get_db)) -> Any:
    """Holt eine Untertitelspur aus dem Buendel.

    Anders als Vorschaubilder werden Untertitel nur beim tatsaechlichen
    Abspielen gebraucht - sie duerfen deshalb im Buendel bleiben.
    """
    from fastapi.responses import Response

    from app.services.bundle import BundleError, BundleReader

    v = db.get(Video, video_id)
    if v is None or not v.bundle_file:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Video unbekannt")

    passend = next((u for u in v.subtitles if u.language == sprache), None)
    if passend is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"keine Untertitel in {sprache}")

    try:
        from pathlib import Path

        with BundleReader(Path(v.bundle_file)) as r:
            inhalt = r.read(passend.name_in_bundle)
    except (BundleError, KeyError) as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Untertitel nicht im Buendel: {e}") from e

    return Response(
        content=inhalt,
        media_type="text/vtt; charset=utf-8",
        headers={"Cache-Control": "private, max-age=86400"},
    )
