"""Die Bibliothek: Kanaele, Playlists, Videos, Warteschlange.

Ein Gestaltungsgrundsatz zieht sich durch alle Auflistungen: Eine Playlist
zeigt **alle** Positionen, auch die nicht archivierten, jeweils mit ihrem
Zustand. Genau daran scheitert TubeArchivist ("only items archived will be able
to show up") - und deshalb gilt dort die Playlist-Treue als kaputt. Wer sehen
will, was ihm fehlt, muss es sehen koennen.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy import update as sa_update
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
from app.services import cache, drosselung, jobs, vpn, ytdlp
from app.services import suche as volltext

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["bibliothek"])


# ------------------------------------------------------------------ Schemata


def _video_bild(v: Video) -> str | None:
    """Adresse des Vorschaubilds - fertig, wie sie ins ``src`` gehoert.

    Bewusst hier und nicht im Browser entschieden: Wo ein Bild herkommt, weiss
    der Server. Es gibt zwei Quellen, in dieser Reihenfolge:

    Das *archivierte* Bild liegt neben dem Buendel und gehoert uns. Sobald ein
    Video geladen ist, wird nur noch dieses benutzt - vollstaendig offline.

    Vorher gibt es dieses Bild noch nicht. Bis vor Kurzem hiess das: gar kein
    Bild, und eine frisch erfasste Kanalseite bestand aus tausenden grauen
    Kacheln. Dabei liefert YouTube die Adresse des Vorschaubilds bereits beim
    Auflisten mit; sie laesst sich ausserdem allein aus der Video-ID bilden.
    ``/thumbs/quelle/<id>`` holt es beim ersten Ansehen und legt es ab.

    Verschwundene Videos bekommen nichts: Zu einem geloeschten Video gibt es
    auch bei YouTube kein Bild mehr, ein Abruf waere sicher vergeblich.
    """
    if v.thumb_file:
        return f"/api/thumbs/{v.thumb_file}"
    if v.status == VideoStatus.UNAVAILABLE or not _VIDEO_ID.match(v.id):
        # Kein Bild versprechen, das sich nicht einloesen laesst: Der Endpunkt
        # nimmt nur echte YouTube-IDs an, und zu einem geloeschten Video hat
        # auch YouTube keins mehr.
        return None
    return f"/api/thumbs/quelle/{v.id}"


class VideoKurz(BaseModel):
    id: str
    titel: str
    kanal_id: str | None
    kanal_name: str | None = None
    dauer_s: int | None
    hochgeladen: str | None
    aufrufe: int | None
    #: Vollstaendige Adresse, kein Dateiname - siehe :func:`_video_bild`.
    bild: str | None
    #: Aufloesung und Bildrate der abgelegten Datei. Erst nach dem Archivieren
    #: bekannt: Beim Auflisten eines Kanals nennt YouTube keine Aufloesung, und
    #: was am Ende im Buendel liegt, steht ohnehin erst nach dem Download fest.
    hoehe: int | None = None
    breite: int | None = None
    fps: float | None = None
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
            bild=_video_bild(v),
            hoehe=v.height,
            breite=v.width,
            fps=v.fps,
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
    #: Standard aus: Erst erfassen, dann entscheiden. Sonst laeuft bei einem
    #: grossen Kanal sofort eine tagelange Warteschlange an.
    sofort_archivieren: bool = False
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
            # Verschwundene zaehlen nicht mit: "2 von 3363" waere irrefuehrend,
            # wenn 156 davon geloescht sind und nie erreichbar werden.
            func.count(Video.id).filter(Video.status != VideoStatus.UNAVAILABLE),
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

    # Die Tab-Zaehler kommen aus der Datenbank, nicht aus der gerade geladenen
    # Seite - sonst stuende an "Videos" die Zahl der zufaellig ersten 60.
    # Verschwundene bleiben aussen vor - sonst verspricht die Zahl am Tab
    # mehr, als die Liste darunter zeigt.
    lang, shorts, live, verschwunden = db.execute(
        select(
            func.count(Video.id).filter(Video.is_short.is_(False), Video.was_live.is_(False)),
            func.count(Video.id).filter(Video.is_short.is_(True)),
            func.count(Video.id).filter(Video.was_live.is_(True)),
            func.count(Video.id).filter(Video.status == VideoStatus.UNAVAILABLE),
        ).where(Video.channel_id == kanal_id, Video.status != VideoStatus.UNAVAILABLE)
    ).one()
    verschwunden = db.scalar(
        select(func.count(Video.id)).where(
            Video.channel_id == kanal_id, Video.status == VideoStatus.UNAVAILABLE
        )
    ) or 0

    return {
        "kanal": _kanal_kurz(db, k),
        "beschreibung": k.description,
        "banner": k.banner_file,
        "zaehler": {"videos": lang, "shorts": shorts, "live": live, "verschwunden": verschwunden},
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


@router.get("/channels/{kanal_id}/downloadable")
def kanal_offene_zaehlen(kanal_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Wie viele Videos liessen sich jetzt herunterladen?

    Getrennt vom Ausloesen, damit die Oberflaeche vor dem Klick sagen kann,
    worauf man sich einlaesst - bei einem grossen Kanal sind das schnell
    Tage an Downloads.
    """
    from app.workers.sync import _soll_archiviert_werden

    kanal = db.get(Channel, kanal_id)
    if kanal is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Kanal unbekannt")

    offen = db.scalars(
        select(Video).where(
            Video.channel_id == kanal_id,
            Video.status.in_([VideoStatus.NEW, VideoStatus.FAILED, VideoStatus.SKIPPED]),
        )
    )
    passend = [v for v in offen if _soll_archiviert_werden(kanal, v)]
    dauer = sum(v.duration_s or 0 for v in passend)
    return {
        "anzahl": len(passend),
        "dauer_s": dauer,
        # Grobe Hausnummer, absichtlich vorsichtig: gemessene Buendel liegen
        # bei 1080p um 0,3 MB je Sekunde. Bei 4K ein Vielfaches - deshalb steht
        # in der Oberflaeche "grob geschaetzt" daneben.
        "bytes_geschaetzt": int(dauer * 0.3 * 1024**2),
    }


@router.post("/channels/{kanal_id}/download-all", status_code=status.HTTP_202_ACCEPTED)
def kanal_alle_laden(kanal_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Reiht alle noch nicht archivierten Videos des Kanals ein.

    Beachtet die Regeln des Kanals - ein Kanal ohne Shorts bekommt auch hier
    keine. Bereits laufende oder wartende Auftraege werden nicht verdoppelt,
    darum kuemmert sich die Warteschlange selbst.
    """
    from app.workers.sync import _soll_archiviert_werden

    kanal = db.get(Channel, kanal_id)
    if kanal is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Kanal unbekannt")

    # Welche Videos haben bereits einen offenen Auftrag? In EINER Abfrage, nicht
    # je Video: Bei dreitausend Videos waeren das sonst dreitausend Abfragen.
    schon_offen = set(
        db.scalars(
            select(Job.target_id).where(
                Job.type == JobType.VIDEO_ARCHIVE,
                Job.status.in_([JobStatus.PENDING, JobStatus.RUNNING]),
            )
        )
    )

    z = {
        "eingereiht": 0,
        "wartete_schon": 0,
        "laeuft_gerade": 0,
        "bereits_archiviert": 0,
        "nicht_verfuegbar": 0,
        "regeln": 0,
    }
    for video in db.scalars(select(Video).where(Video.channel_id == kanal_id)):
        if video.status == VideoStatus.ARCHIVED:
            z["bereits_archiviert"] += 1
            continue
        if video.status == VideoStatus.UNAVAILABLE:
            # Bei der Quelle geloescht - erneut anzufragen kostet nur ein
            # Anfragebudget, das YouTube ohnehin knapp bemisst.
            z["nicht_verfuegbar"] += 1
            continue
        if not _soll_archiviert_werden(kanal, video):
            # Bleibt uebersprungen, MIT seiner Begruendung.
            #
            # Vorher wurde hier pauschal alles Uebersprungene auf "neu"
            # gesetzt. Bei einem Kanal ohne Shorts hiess das: Die Shorts
            # wurden neu, fielen gleich darauf durch dieselbe Regel und blieben
            # als "neu" liegen - ohne Auftrag, ohne Begruendung, aber mitgezaehlt.
            # Jeder weitere Klick wiederholte das.
            z["regeln"] += 1
            continue
        if video.status in (
            VideoStatus.DOWNLOADING, VideoStatus.REMUXING,
            VideoStatus.ENCODING, VideoStatus.BUNDLING,
        ):
            z["laeuft_gerade"] += 1
            continue

        if video.id in schon_offen:
            # Auftrag steht bereits - nur den Zustand geradeziehen, damit die
            # Kachel nicht faelschlich rot bleibt.
            video.status = VideoStatus.QUEUED
            video.status_message = None
            z["wartete_schon"] += 1
            continue

        video.status = VideoStatus.QUEUED
        video.status_message = None
        jobs.enqueue_archive(db, video.id)
        schon_offen.add(video.id)
        z["eingereiht"] += 1

    db.commit()
    log.info(
        "Kanal %s: %d neu eingereiht, %d warteten schon, %d bereits archiviert",
        kanal_id, z["eingereiht"], z["wartete_schon"], z["bereits_archiviert"],
    )
    # "eingereiht" bleibt als Feld erhalten, damit aeltere Oberflaechen weiter
    # etwas Sinnvolles anzeigen.
    return {"eingereiht": z["eingereiht"], **z}


@router.delete("/channels/{kanal_id}")
def kanal_entfernen(
    kanal_id: str,
    dateien: bool = Query(
        True,
        description="Auch die Videodateien (Buendel) von der Platte loeschen. "
        "Ohne diesen Schalter verschwindet nur die Verwaltung; die Buendel "
        "blieben verwaist zurueck und waeren ohne Datenbank nicht abspielbar.",
    ),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Entfernt einen Kanal samt allem, was an ihm haengt.

    Die Reihenfolge ist bewusst: Erst werden alle Dateipfade eingesammelt und
    die Datenbank bereinigt, erst danach wird geloescht, was auf der Platte
    liegt. Scheitert der Datenbankteil, ist noch nichts unwiederbringlich weg;
    scheitert das Dateiloeschen, raeumt der Reaper die Heisskopien ohnehin als
    verwaist ab und die Buendel lassen sich von Hand entfernen.
    """
    import shutil as _shutil
    from pathlib import Path as _Path

    from sqlalchemy import delete as sa_delete

    k = db.get(Channel, kanal_id)
    if k is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Kanal unbekannt")

    video_ids = list(db.scalars(select(Video.id).where(Video.channel_id == kanal_id)))

    # ---- Dateipfade einsammeln, solange die Datenbank sie noch kennt.
    from app.models import HotCopy

    heisse = [
        _Path(p)
        for p in db.scalars(
            select(HotCopy.path).join(Video, Video.id == HotCopy.video_id).where(
                Video.channel_id == kanal_id
            )
        )
    ]
    vorschauen = [
        settings.thumb_dir / name
        for name in db.scalars(
            select(Video.thumb_file).where(
                Video.channel_id == kanal_id, Video.thumb_file.is_not(None)
            )
        )
    ]
    for name in (k.avatar_file, k.banner_file):
        if name:
            vorschauen.append(settings.thumb_dir / name)
    buendel_ordner = settings.bundle_dir / kanal_id
    buendel_bytes = (
        sum(p.stat().st_size for p in buendel_ordner.rglob("*") if p.is_file())
        if buendel_ordner.is_dir()
        else 0
    )

    # ---- Auftraege: Laufende werden abgebrochen (die Arbeiter pruefen das),
    # alles andere zu diesem Kanal fliegt raus - tote Eintraege, deren Ziel es
    # nicht mehr gibt, haetten in der Warteschlange nichts verloren.
    ziele = [kanal_id, *video_ids]
    for i in range(0, len(ziele), 500):
        block = ziele[i : i + 500]
        db.execute(
            sa_update(Job)
            .where(Job.target_id.in_(block), Job.status == JobStatus.RUNNING)
            .values(status=JobStatus.CANCELLED, finished_at=utcnow())
        )
        db.execute(
            sa_delete(Job).where(
                Job.target_id.in_(block), Job.status != JobStatus.CANCELLED
            )
        )

    # ---- Suchindex
    volltext.alle_entfernen(db, video_ids)

    # ---- Datenbank. Der Umweg ueber das DELETE-Statement statt db.delete(k)
    # ist Absicht: So kaskadiert die Datenbank selbst (Videos, Playlists,
    # Zuordnungen, Kapitel, Untertitel, Heisskopien), statt dass SQLAlchemy
    # tausende Objekte laedt und einzeln loescht.
    db.execute(sa_delete(Channel).where(Channel.id == kanal_id))
    db.commit()
    # Die Kaskade lief in der Datenbank, nicht in der Session - dort haengen
    # die geloeschten Videos noch im Zwischenspeicher und wuerden von db.get()
    # weiter ausgeliefert.
    db.expire_all()

    # ---- Platte
    geloescht_bytes = 0
    for p in heisse + vorschauen:
        try:
            if p.is_file():
                geloescht_bytes += p.stat().st_size
                p.unlink()
        except OSError as e:
            log.warning("Datei %s liess sich nicht loeschen: %s", p, e)
    if dateien and buendel_ordner.is_dir():
        try:
            _shutil.rmtree(buendel_ordner)
            geloescht_bytes += buendel_bytes
        except OSError as e:
            log.warning("Buendelordner %s liess sich nicht loeschen: %s", buendel_ordner, e)

    log.info(
        "Kanal %s entfernt: %d Videos, %.1f MB freigegeben (Buendel %s)",
        kanal_id, len(video_ids), geloescht_bytes / 1e6,
        "geloescht" if dateien else "behalten",
    )
    return {
        "videos_entfernt": len(video_ids),
        "bytes_freigegeben": geloescht_bytes,
        "buendel_geloescht": dateien,
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
    art: Literal["videos", "shorts", "live"] | None = Query(
        None,
        description="Auf eine Videoart eingrenzen. 'videos' heisst: weder Short noch Livestream.",
    ),
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
    else:
        # Auch bei "alles zeigen" bleiben verschwundene Videos draussen: Sie
        # sind bei der Quelle geloescht oder privat, liessen sich also nie
        # holen. In der Videoliste waeren sie nur Rauschen - in der Playlist
        # stehen sie weiterhin an ihrer Position, denn dort ist genau das die
        # Information, die zaehlt. Wer sie sehen will, filtert ausdruecklich
        # nach status=unavailable.
        anfrage = anfrage.where(Video.status != VideoStatus.UNAVAILABLE)
    # Die Art wird serverseitig gefiltert, nicht im Client. Sonst stimmt das
    # Blaettern nicht: Wer Seite fuer Seite laedt und erst im Browser die
    # Shorts aussiebt, bekommt mal 60, mal 3 sichtbare Videos pro Seite.
    if art == "shorts":
        anfrage = anfrage.where(Video.is_short.is_(True))
    elif art == "live":
        anfrage = anfrage.where(Video.was_live.is_(True))
    elif art == "videos":
        anfrage = anfrage.where(Video.is_short.is_(False), Video.was_live.is_(False))
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

    # Sortiert wird nach dem Rang in der Uploads-Liste, nicht nach dem Datum.
    #
    # Das Datum waere die naheliegende Wahl, taugt hier aber nicht: YouTube
    # liefert beim Auflisten eines Kanals keines mit, und ein einzelner Abruf
    # je Video verbietet sich bei mehreren tausend. In der Praxis hatten
    # deshalb 3348 von 3363 Videos kein Datum - "neueste zuerst" ordnete dann
    # 15 Videos richtig ein und haengte den Rest in Einfuegereihenfolge an.
    #
    # Der Rang in der Uploads-Playlist traegt dieselbe Ordnung und ist immer
    # da. Das Datum bleibt als zweiter Schluessel, fuer Videos, die nur ueber
    # eine fremde Playlist bekannt sind und keinen Rang haben.
    anfrage = anfrage.order_by(
        *{
            "neu": (Video.uploads_position.asc().nulls_last(), Video.upload_date.desc()),
            "alt": (Video.uploads_position.desc().nulls_last(), Video.upload_date.asc()),
            "aufrufe": (Video.view_count.desc().nulls_last(),),
            "titel": (Video.title.asc(),),
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


@router.delete("/videos/{video_id}")
def video_aus_archiv_entfernen(video_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Nimmt ein einzelnes Video wieder aus dem Archiv.

    Der Datensatz bleibt - das Video gehoert weiter zum Kanal und zu seinen
    Playlists, nur die Dateien verschwinden. Der Zustand wird auf
    "uebersprungen" gesetzt, nicht auf "neu": Ein "neu" wuerde der naechste
    Abgleich sofort wieder einreihen, und wer ein Video bewusst entfernt hat,
    will das nicht. Der "Laden"-Knopf auf der Kachel holt es bei Bedarf zurueck.
    """
    from pathlib import Path as _Path

    from sqlalchemy import delete as sa_delete

    from app.models import Chapter, HotCopy, Subtitle

    v = db.get(Video, video_id)
    if v is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Video unbekannt")
    if v.status != VideoStatus.ARCHIVED and not v.bundle_file:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Video ist nicht archiviert (Status: {v.status})")

    frei = 0
    pfade = [_Path(h.path) for h in db.scalars(select(HotCopy).where(HotCopy.video_id == video_id))]
    if v.bundle_file:
        pfade.append(_Path(v.bundle_file))
    if v.thumb_file:
        pfade.append(settings.thumb_dir / v.thumb_file)

    # Erst die Datenbank, dann die Platte - siehe kanal_entfernen.
    db.execute(
        sa_delete(Job).where(
            Job.target_id == video_id, Job.status.in_([JobStatus.PENDING, JobStatus.FAILED])
        )
    )
    db.execute(sa_delete(HotCopy).where(HotCopy.video_id == video_id))
    db.execute(sa_delete(Chapter).where(Chapter.video_id == video_id))
    db.execute(sa_delete(Subtitle).where(Subtitle.video_id == video_id))
    volltext.entfernen(db, video_id)

    v.status = VideoStatus.SKIPPED
    v.status_message = "aus dem Archiv entfernt"
    v.bundle_file = None
    v.bundle_bytes = None
    v.source_bytes = None
    v.media_name = None
    v.thumb_file = None
    v.recoded = False
    v.archived_at = None
    v.progress_s = 0.0
    db.commit()

    for p in pfade:
        try:
            if p.is_file():
                frei += p.stat().st_size
                p.unlink()
        except OSError as e:
            log.warning("Datei %s liess sich nicht loeschen: %s", p, e)

    log.info("Video %s aus dem Archiv entfernt, %.1f MB frei", video_id, frei / 1e6)
    return {"video_id": video_id, "bytes_freigegeben": frei, "status": v.status}


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


@router.get("/jobs/aktiv")
def laufende_auftraege(db: Session = Depends(get_db)) -> dict[str, Any]:
    """Knappe Auskunft fuer die Fortschrittsanzeige.

    Bewusst schmal gehalten, weil die Oberflaeche das im Sekundentakt abfragt -
    die vollstaendige Warteschlange mit Titeln waere dafuer zu schwer.
    """
    laufend = list(
        db.scalars(
            select(Job).where(Job.status == JobStatus.RUNNING).order_by(Job.priority, Job.id)
        )
    )
    wartend = db.scalar(select(func.count(Job.id)).where(Job.status == JobStatus.PENDING)) or 0

    eintraege = []
    for j in laufend:
        titel = None
        if j.target_id:
            if j.type == JobType.CHANNEL_SYNC:
                k = db.get(Channel, j.target_id)
                titel = k.name if k else None
            else:
                v = db.get(Video, j.target_id)
                titel = v.title if v else None
        eintraege.append({
            "id": j.id,
            "art": j.type,
            "ziel": j.target_id,
            "titel": titel,
            "fortschritt": j.progress,
            "meldung": j.message,
        })
    # Ohne diese Auskunft ist eine Drosselpause von einem haengenden Dienst
    # nicht zu unterscheiden: In beiden Faellen stehen tausend Auftraege auf
    # "wartet" und es laeuft keiner. Der Unterschied gehoert vor die Augen des
    # Nutzers, nicht nur ins Log.
    # Nach Art aufgeschluesselt, und das ist keine Spielerei: Ein Video
    # durchlaeuft im Laufe seines Lebens mehrere Auftraege - erst der Download,
    # spaeter die Verkleinerung, womoeglich noch ein Hochstufen. Eine einzelne
    # Zahl "4216 warten" liest sich deshalb wie "4216 Videos" und war bei einem
    # Kanal mit 3363 Videos schlicht nicht zu glauben. Sie stimmte trotzdem.
    nach_art = dict(
        db.execute(
            select(Job.type, func.count(Job.id))
            .where(Job.status == JobStatus.PENDING)
            .group_by(Job.type)
        ).all()
    )
    # Mit mehreren Ausgaengen heisst "gesperrt" nicht mehr "steht". Gefragt
    # wird deshalb nach allen Ausgaengen zusammen: Pausiert ist erst, wenn
    # keiner mehr frei ist. Sonst behauptete die Leiste Stillstand, waehrend
    # nebenan ueber den naechsten Tunnel weitergeladen wird.
    ids = vpn.ausgang_ids()
    return {
        "laufend": eintraege,
        "wartend": wartend,
        "nach_art": nach_art,
        "drosselung": drosselung.zustand(ids),
        "ausgaenge": {
            "gesamt": len(ids),
            "frei": len(drosselung.frei(ids)),
        },
    }


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


@router.post("/jobs/retry-failed", status_code=status.HTTP_202_ACCEPTED)
def gescheiterte_wiederholen(db: Session = Depends(get_db)) -> dict[str, Any]:
    """Reiht alles wieder ein, was gescheitert ist.

    Der Anlass ist die Bot-Pruefung von YouTube: Wer eine Sperre abbekommen
    hat, sitzt hinterher vor einer Liste von Dutzenden roter Eintraege, die
    alle denselben Fehler tragen und alle in Ordnung waeren. Sie einzeln
    anzuklicken ist keine zumutbare Bedienung.

    Der Versuchszaehler der Videos wird dabei mit zurueckgesetzt: Die
    Fehlschlaege lagen nicht am Video, und sie sollen es nicht belasten.
    """
    offen = list(db.scalars(select(Job).where(Job.status == JobStatus.FAILED)))
    for j in offen:
        j.status = JobStatus.PENDING
        j.error = None
        j.message = None
        j.progress = 0.0
        j.started_at = None
        j.finished_at = None

    # Auch das Video selbst zurueckholen - sonst steht es weiter auf
    # "fehlgeschlagen", waehrend sein Auftrag laengst wieder wartet.
    video_ids = [j.target_id for j in offen if j.target_id and j.type == JobType.VIDEO_ARCHIVE]
    zurueckgeholt = 0
    if video_ids:
        zurueckgeholt = db.execute(
            sa_update(Video)
            .where(Video.id.in_(video_ids), Video.status == VideoStatus.FAILED)
            .values(status=VideoStatus.QUEUED, status_message=None, retry_count=0)
        ).rowcount
    db.commit()
    log.info("%d gescheiterte Auftraege wieder eingereiht", len(offen))
    return {"auftraege": len(offen), "videos": zurueckgeholt}


# ------------------------------------------------------------------ Speicher


@router.get("/storage")
def speicher(db: Session = Depends(get_db)) -> dict[str, Any]:
    """Der Blick, den YouTube nicht hat und ein Archiv braucht.

    Beantwortet drei Fragen auf einmal: Was liegt da? Was hat die Recodierung
    gebracht? Und - die eigentlich wichtige - was kaeme noch dazu, wenn man
    alles holt? Ohne die dritte Zahl entscheidet man ueber ein Archiv im
    Blindflug.
    """
    import shutil as _shutil

    kalt_bytes, quelle_bytes, archiviert, recodiert, dauer_archiviert = db.execute(
        select(
            func.coalesce(func.sum(Video.bundle_bytes), 0),
            func.coalesce(func.sum(Video.source_bytes), 0),
            func.count(Video.id).filter(Video.status == VideoStatus.ARCHIVED),
            func.count(Video.id).filter(Video.recoded.is_(True)),
            func.coalesce(func.sum(Video.duration_s).filter(Video.status == VideoStatus.ARCHIVED), 0),
        )
    ).one()
    kalt_bytes, quelle_bytes = int(kalt_bytes or 0), int(quelle_bytes or 0)

    nach_status = dict(
        db.execute(select(Video.status, func.count(Video.id)).group_by(Video.status)).all()
    )
    offen_recode = db.scalar(
        select(func.count(Job.id)).where(
            Job.type == JobType.VIDEO_RECODE, Job.status == JobStatus.PENDING
        )
    ) or 0

    # ---- Was liegt je Kanal? Zeigt, wer den Platz belegt.
    je_kanal = [
        {
            "id": kid,
            "name": name,
            "videos": anzahl,
            "bytes": int(groesse or 0),
        }
        for kid, name, anzahl, groesse in db.execute(
            select(
                Channel.id,
                Channel.name,
                func.count(Video.id).filter(Video.status == VideoStatus.ARCHIVED),
                func.coalesce(func.sum(Video.bundle_bytes), 0),
            )
            .join(Video, Video.channel_id == Channel.id, isouter=True)
            .group_by(Channel.id)
            .order_by(func.coalesce(func.sum(Video.bundle_bytes), 0).desc())
        ).all()
    ]

    # ---- Die groessten Buendel. Bei knappem Platz die erste Stellschraube.
    groesste = [
        {"id": v.id, "titel": v.title, "bytes": v.bundle_bytes, "kanal": v.channel.name if v.channel else None}
        for v in db.scalars(
            select(Video)
            .where(Video.status == VideoStatus.ARCHIVED, Video.bundle_bytes.is_not(None))
            .order_by(Video.bundle_bytes.desc())
            .limit(8)
        )
    ]

    # ---- Hochrechnung auf das, was noch fehlt.
    # Grundlage ist der gemessene eigene Schnitt (Bytes je Sekunde), nicht eine
    # Faustzahl - sobald ein paar Videos da sind, ist das die ehrlichste
    # Schaetzung. Vorher eine vorsichtige Annahme fuer 1080p.
    je_sekunde = (kalt_bytes / dauer_archiviert) if dauer_archiviert else 0.3 * 1024**2
    offen_anzahl, offen_dauer = db.execute(
        select(
            func.count(Video.id),
            func.coalesce(func.sum(Video.duration_s), 0),
        ).where(Video.status.in_([VideoStatus.NEW, VideoStatus.QUEUED, VideoStatus.FAILED]))
    ).one()

    # ---- Der Datentraeger. Kaltspeicher und Heissspeicher koennen auf
    # verschiedenen liegen (Unraid: Array und Cache-Pool) - dann sind es zwei.
    def _traeger(pfad) -> dict[str, Any] | None:
        try:
            g, b, f = _shutil.disk_usage(pfad)
            return {"pfad": str(pfad), "gesamt": g, "belegt": b, "frei": f}
        except OSError:
            return None

    traeger = [t for t in (_traeger(settings.bundle_dir), _traeger(settings.cache_dir)) if t]
    # Gleicher Datentraeger? Dann nur einmal zeigen.
    if len(traeger) == 2 and traeger[0]["gesamt"] == traeger[1]["gesamt"]:
        traeger = [traeger[0] | {"pfad": "Daten und Videos"}]

    return {
        "kaltspeicher": {
            "bytes": kalt_bytes,
            "videos": archiviert,
            "quelle_bytes": quelle_bytes,
            "gespart_bytes": quelle_bytes - kalt_bytes,
            "recodiert": recodiert,
            "dauer_s": int(dauer_archiviert or 0),
            "bytes_je_sekunde": round(je_sekunde),
        },
        "heissspeicher": cache.usage(db),
        "freier_platz": cache.free_space(),
        "traeger": traeger,
        "videos_nach_status": nach_status,
        "recodierungen_offen": offen_recode,
        "je_kanal": je_kanal,
        "groesste": groesste,
        "hochrechnung": {
            "offene_videos": offen_anzahl,
            "offene_dauer_s": int(offen_dauer or 0),
            "bytes_geschaetzt": int((offen_dauer or 0) * je_sekunde),
            # Ob die Schaetzung auf eigenen Messwerten beruht oder nur auf einer
            # Annahme, gehoert dazu - sonst liest man eine Hausnummer als Zusage.
            "gemessen": bool(dauer_archiviert),
        },
    }


# ---------------------------------------------------------------- Einstellungen


@router.get("/settings")
def einstellungen_lesen(db: Session = Depends(get_db)) -> dict[str, Any]:
    """Alle aenderbaren Einstellungen samt Herkunft.

    Die Herkunft ist wichtig genug fuer die Oberflaeche: Wer im Unraid-Template
    eine Variable setzt, die er zuvor hier geaendert hat, wuerde sich sonst
    wundern, warum sein Eintrag dort nichts bewirkt.
    """
    from app.services import einstellungen

    felder = einstellungen.lesen(db)
    gruppen: list[str] = []
    for f in felder:
        if f["gruppe"] not in gruppen:
            gruppen.append(f["gruppe"])
    return {"gruppen": gruppen, "felder": felder}


@router.put("/settings")
def einstellungen_schreiben(
    aenderungen: dict[str, Any], db: Session = Depends(get_db)
) -> dict[str, Any]:
    from app.services import einstellungen

    try:
        ergebnis = einstellungen.schreiben(db, aenderungen)
    except einstellungen.Ungueltig as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e

    # Die Zahl der Arbeiterstraenge steht nicht nur im Einstellungsobjekt,
    # sondern haengt an laufenden Threads. Ohne diesen Aufruf wuerde eine
    # Aenderung erst beim naechsten Start wirken - und genau das soll sie
    # nicht mehr.
    from app.workers.runner import werk

    ergebnis["arbeiter"] = werk.anpassen()

    # Dasselbe fuer die Tunnel: Der Schalter "Tunnel benutzen" ist wertlos,
    # wenn danach kein Prozess startet. Beim Ausschalten werden sie beendet.
    if "vpn_aktiv" in aenderungen or "vpn_nur_tunnel" in aenderungen:
        from app.services import vpn

        vpn.laden(db)
    return ergebnis


# ------------------------------------------------------- Qualitaet anheben


#: Stufen, die zur Auswahl stehen. Mehr waere Ziererei - dazwischen liegt
#: nichts, was YouTube regelmaessig anbietet.
UPGRADE_STUFEN = (720, 1080, 1440, 2160, 4320)


def _upgrade_kandidaten(db: Session, ziel: int, kanal: str | None):
    """Archivierte Videos, die unterhalb der Zielstufe liegen.

    Gemessen an der kurzen Seite, wie ueberall - ein hochkantiges 1080p-Video
    ist 1080x1920 und darf nicht als "1920p" durchgehen.
    """
    anfrage = select(Video).where(
        Video.status == VideoStatus.ARCHIVED,
        Video.width.is_not(None),
        Video.height.is_not(None),
    )
    if kanal:
        anfrage = anfrage.where(Video.channel_id == kanal)
    return [
        v for v in db.scalars(anfrage)
        if (ytdlp.guete({"width": v.width, "height": v.height}) or 0) < ziel
    ]


@router.get("/upgrade/vorschau")
def upgrade_vorschau(
    ziel: int = Query(2160, description="Zielstufe, gemessen an der kurzen Seite"),
    kanal: str | None = None,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Was ein Hochstufen kosten wuerde - vor dem Klick, nicht danach.

    Die Schaetzung ist bewusst konservativ und wird als solche ausgewiesen: Sie
    rechnet mit dem Verhaeltnis der Pixelzahlen, also dem Quadrat des
    Stufenverhaeltnisses. In der Praxis liegt der Faktor etwas hoeher - YouTube
    gibt 4K rund das Vier- bis Fuenffache von 1080p mit, nicht genau das
    Vierfache. Wer knapp plant, plant damit zu knapp; das steht auch so in der
    Oberflaeche.
    """
    kandidaten = _upgrade_kandidaten(db, ziel, kanal)
    jetzt_bytes = sum(v.bundle_bytes or 0 for v in kandidaten)
    nachher_bytes = 0
    nach_stufe: dict[int, int] = {}
    for v in kandidaten:
        vorher = ytdlp.guete({"width": v.width, "height": v.height}) or 0
        nach_stufe[vorher] = nach_stufe.get(vorher, 0) + 1
        if vorher > 0 and v.bundle_bytes:
            nachher_bytes += int(v.bundle_bytes * (ziel / vorher) ** 2)

    frei = cache.free_space()
    return {
        "ziel": ziel,
        "videos": len(kandidaten),
        "jetzt_bytes": jetzt_bytes,
        "geschaetzt_bytes": nachher_bytes,
        "zusatz_bytes": max(0, nachher_bytes - jetzt_bytes),
        "freier_platz": frei,
        "passt": (nachher_bytes - jetzt_bytes) < frei if frei else None,
        #: Wie viele Videos je bisheriger Stufe betroffen sind.
        "nach_stufe": {str(k): v for k, v in sorted(nach_stufe.items())},
        #: Wie lange das Herunterladen mindestens dauert. YouTube drosselt bei
        #: rund 300 Videos je Stunde - bei vielen Videos ist das die Grenze,
        #: nicht die Bandbreite.
        "stunden_mindestens": round(len(kandidaten) / 300, 1),
    }


@router.post("/upgrade", status_code=status.HTTP_202_ACCEPTED)
def upgrade_einreihen(
    ziel: int = Query(2160),
    kanal: str | None = None,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Reiht alle Videos unterhalb der Zielstufe zum Hochstufen ein.

    Eingereiht wird ohne vorherige Pruefung beim Anbieter - ob es die bessere
    Fassung wirklich gibt, stellt der Auftrag selbst fest, mit einem
    Metadatenabruf statt eines Downloads. Das hier vorab fuer tausende Videos
    zu tun, wuerde die Anfrage minutenlang offen halten.
    """
    if ziel not in UPGRADE_STUFEN:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Zielstufe muss eine von {', '.join(str(s) for s in UPGRADE_STUFEN)} sein",
        )
    kandidaten = _upgrade_kandidaten(db, ziel, kanal)
    for v in kandidaten:
        jobs.enqueue(db, JobType.VIDEO_UPGRADE, v.id, priority=jobs.PRIO_RECODE,
                     payload={"ziel": ziel})
    return {"eingereiht": len(kandidaten), "ziel": ziel}


@router.post("/videos/{video_id}/upgrade", status_code=status.HTTP_202_ACCEPTED)
def video_hochstufen(
    video_id: str, ziel: int = Query(2160), db: Session = Depends(get_db)
) -> dict[str, Any]:
    v = db.get(Video, video_id)
    if v is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Video unbekannt")
    if v.status != VideoStatus.ARCHIVED:
        raise HTTPException(status.HTTP_409_CONFLICT, "Video ist nicht archiviert")
    job = jobs.enqueue(db, JobType.VIDEO_UPGRADE, video_id, priority=jobs.PRIO_RECODE,
                       payload={"ziel": ziel})
    return {"job_id": job.id, "status": job.status}


@router.post("/settings/reset")
def einstellungen_zuruecksetzen(
    namen: list[str] | None = None, db: Session = Depends(get_db)
) -> dict[str, Any]:
    """Entfernt gespeicherte Werte - danach gilt wieder Umgebung bzw. Standard."""
    from app.services import einstellungen

    return {"zurueckgesetzt": einstellungen.zuruecksetzen(db, namen)}


#: Form einer YouTube-Video-ID. Die Pruefung ist keine Formsache: Aus der ID
#: wird gleich eine URL gebaut, die der Server selbst abruft. Ohne feste Form
#: koennte ein Aufrufer ihn dazu bringen, beliebige Adressen zu kontaktieren.
_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")

#: Groessen, die YouTube unter fester Adresse anbietet, in der Reihenfolge des
#: Versuchs. Die grossen gibt es nur bei neueren Videos und sie fehlen sonst
#: mit 404; ``hqdefault`` existiert seit jeher fuer jedes Video und steht
#: deshalb als letztes.
#:
#: An drei Videos dieses Archivs gemessen (Bytes):
#:
#: ===============  =======  =======  =======
#: Groesse          Video A  Video B  Video C
#: ===============  =======  =======  =======
#: maxresdefault     80943   213323   176687
#: hq720             80943   213323   176687
#: sddefault         40594    90855    86648
#: hqdefault         10711    28913    21596
#: ===============  =======  =======  =======
#:
#: ``hq720`` liefert dieselbe Datei wie ``maxresdefault``, byteweise - es ist
#: also keine kleinere Stufe, sondern nur eine zweite Adresse fuer dasselbe
#: Bild. Es steht trotzdem drin, weil die beiden bei einzelnen Videos
#: unterschiedlich verfuegbar sind.
#:
#: Die echte Sparstufe ist ``sddefault``. Sie wird bewusst nicht bevorzugt:
#: Ueber einen Kanal mit 3363 Videos geht es um rund 200 MB Unterschied - auf
#: einem Archiv, das fuer dieselben Videos mehrere Terabyte braucht, ist das
#: kein Argument gegen ein scharfes Bild auf der Videoseite.
_BILDGROESSEN = ("maxresdefault", "hq720", "sddefault", "hqdefault")

#: Videos, fuer die YouTube gerade kein Bild hatte. Verhindert, dass eine
#: Kanalseite bei jedem Aufbau erneut in denselben Fehlschlag laeuft. Bewusst
#: nur im Arbeitsspeicher: Nach einem Neustart darf es wieder versucht werden,
#: denn der Fehlschlag kann an einer Stoerung gelegen haben.
_ohne_bild: set[str] = set()


@router.get("/thumbs/quelle/{video_id}")
def thumbnail_quelle(video_id: str) -> FileResponse:
    """Vorschaubild eines noch nicht archivierten Videos.

    Holt das Bild beim ersten Abruf von YouTube und legt es ab; jeder weitere
    Abruf kommt von der Platte. Das Archiv wird dadurch mit der Zeit von allein
    unabhaengiger von der Quelle, ohne dass beim Erfassen eines Kanals
    tausende Bilder auf Verdacht geladen werden - geholt wird nur, was jemand
    tatsaechlich ansieht.

    Der Umweg ueber den Server ist Absicht. Wuerde die Seite direkt auf
    ``i.ytimg.com`` verweisen, meldete jeder Seitenaufbau dem Betreiber, welche
    Videos im eigenen Archiv angesehen werden - und ohne Netz bliebe die Seite
    leer, auch fuer laengst archivierte Videos.
    """
    from pathlib import Path
    from urllib.error import HTTPError, URLError
    from urllib.request import Request, urlopen

    if not _VIDEO_ID.match(video_id):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "keine gueltige Video-ID")

    # Eigener Unterordner: Die archivierten Bilder heissen ebenfalls nach der
    # Video-ID und koennten je nach Endung genau gleich heissen.
    ordner = settings.thumb_dir / "quelle"
    ziel = ordner / f"{video_id}.jpg"
    if ziel.is_file():
        return FileResponse(ziel, headers={"Cache-Control": "public, max-age=604800"})
    if video_id in _ohne_bild:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "kein Vorschaubild verfuegbar")

    ordner.mkdir(parents=True, exist_ok=True)
    for groesse in _BILDGROESSEN:
        adresse = f"https://i.ytimg.com/vi/{video_id}/{groesse}.jpg"
        try:
            with urlopen(Request(adresse, headers={"User-Agent": "vitrine"}), timeout=10) as a:
                if a.status != 200:
                    continue
                daten = a.read()
        except (HTTPError, URLError, OSError):
            continue
        # YouTube antwortet auf fehlende Groessen auch mal mit einem winzigen
        # Platzhalterbild statt mit 404. Unter 2 KB ist nichts Echtes dabei.
        if len(daten) < 2048:
            continue
        # Erst vollstaendig schreiben, dann umbenennen: Bei zwei gleichzeitigen
        # Abrufen darf niemals eine halb geschriebene Datei ausgeliefert werden.
        vorlaeufig = Path(f"{ziel}.{os.getpid()}.teil")
        vorlaeufig.write_bytes(daten)
        vorlaeufig.replace(ziel)
        return FileResponse(ziel, headers={"Cache-Control": "public, max-age=604800"})

    _ohne_bild.add(video_id)
    raise HTTPException(status.HTTP_404_NOT_FOUND, "kein Vorschaubild verfuegbar")


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
