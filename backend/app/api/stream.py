"""Oeffentliche Videobytes, kurzlebige Player und das geschuetzte Dashboard.

Browsergeeignete Medien werden unveraendert aus dem ZIP geliefert. Fuer andere
Codecs fordert jeder Player nur die benoetigten sechssekundigen HLS-Abschnitte
an. Verwaltungsrechte fuer das Dashboard prueft die zentrale Middleware.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models import HotCopy, HotCopyStatus, Video, VideoStatus
from app.services import cache, live_streams, paths, playback
from app.services.bundle import BundleError, BundleReader
from app.services.ranges import UnsatisfiableRange, parse_range

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/videos", tags=["wiedergabe"])
playback_router = APIRouter(prefix="/api", tags=["wiedergabe"])

CHUNK = 512 * 1024


def _video_or_404(db: Session, video_id: str) -> Video:
    v = db.get(Video, video_id)
    if v is None or v.status != VideoStatus.ARCHIVED:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Video unbekannt")
    return v


def _bundle_of(video: Video) -> Path:
    if video.status != VideoStatus.ARCHIVED or not video.bundle_file:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Video ist nicht archiviert (Status: {video.status})",
        )
    try:
        p = paths.contained(settings.bundle_dir, Path(video.bundle_file))
    except paths.UnsafePath:
        raise HTTPException(status.HTTP_410_GONE, "Das Video ist nicht verfuegbar.") from None
    if not p.is_file():
        raise HTTPException(status.HTTP_410_GONE, "Buendel fehlt auf der Platte")
    return p


def _read_slice(path: Path, offset: int, laenge: int) -> Iterator[bytes]:
    """Liest ``laenge`` Bytes ab ``offset``.

    Bewusst mit einem eigenen Dateihandle je Anfrage: Mehrere Zuschauer duerfen
    sich nicht gegenseitig den Dateizeiger verstellen.
    """
    rest = laenge
    with open(path, "rb", buffering=0) as f:
        f.seek(offset)
        while rest > 0:
            stueck = f.read(min(CHUNK, rest))
            if not stueck:
                break
            rest -= len(stueck)
            yield stueck


def _resolve_source(db: Session, video: Video, support: frozenset[str]) -> playback.StreamSource | JSONResponse:
    """Ermittelt die Quelle - oder meldet, dass erst vorbereitet werden muss."""
    bundle = _bundle_of(video)

    try:
        with BundleReader(bundle) as r:
            manifest = r.manifest
            entscheidung = playback.decide(manifest, support)
            if entscheidung.mode is playback.Mode.DIRECT:
                return playback.StreamSource(
                    file=bundle,
                    base_offset=r.media_data_offset(),
                    size=r.media_size,
                    mime_type=manifest.mime_type,
                    mode=playback.Mode.DIRECT,
                )
    except (BundleError, zipfile.BadZipFile, OSError, ValueError) as e:
        log.error("Buendel %s unbrauchbar: %s", bundle, e)
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Das Videobuendel ist nicht lesbar.") from e

    # Transkodierpfad: gibt es schon eine fertige Heisskopie?
    hot = db.scalar(
        select(HotCopy).where(
            HotCopy.video_id == video.id, HotCopy.variant == entscheidung.variant
        )
    )
    if hot and hot.status == HotCopyStatus.READY:
        try:
            hot_path = paths.contained(settings.cache_dir, Path(hot.path))
        except paths.UnsafePath:
            raise HTTPException(status.HTTP_410_GONE, "Das Video ist nicht verfuegbar.") from None
        if hot_path.is_file():
            cache.touch(db, hot)
            return playback.StreamSource(
                file=hot_path,
                base_offset=0,
                size=hot.size_bytes or hot_path.stat().st_size,
                mime_type=hot.mime_type or playback.TRANSCODE_MIME,
                mode=playback.Mode.TRANSCODE,
            )

    # Oeffentliche GET-Aufrufe duerfen keine unbeschraenkten Hintergrundjobs
    # anlegen. Der neue Player fordert stattdessen begrenzte HLS-Abschnitte an.
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT,
        content={
            "detail": "Fuer diesen Browser ist Live-Transkodierung erforderlich. Bitte den Player neu laden.",
            "status": "wiedergabesitzung_erforderlich",
        },
    )


@router.get("/{video_id}/stream")
def stream(
    video_id: str,
    db: Session = Depends(get_db),
    range_header: str | None = Header(default=None, alias="Range"),
    support: str | None = Query(
        default=None,
        max_length=512,
        description="Kommaliste der Client-Faehigkeiten, z.B. 'mp4,webm,av01,opus'. "
        "Fehlt sie, wird konservativ H.264/MP4 angenommen.",
    ),
) -> Response:
    video = _video_or_404(db, video_id)
    quelle = _resolve_source(db, video, playback.parse_client_support(support))
    if isinstance(quelle, JSONResponse):
        return quelle

    kopf = {
        "Accept-Ranges": "bytes",
        "Content-Type": quelle.mime_type,
        # Geschuetzte Mediendaten sollen nach Sitzungsende nicht im Cache bleiben.
        "Cache-Control": "no-store",
        "X-Wiedergabe-Modus": quelle.mode.value,
    }

    try:
        bereich = parse_range(range_header, quelle.size)
    except UnsatisfiableRange:
        return Response(
            status_code=status.HTTP_416_RANGE_NOT_SATISFIABLE,
            headers={"Content-Range": f"bytes */{quelle.size}", "Accept-Ranges": "bytes"},
        )

    if bereich is None:
        kopf["Content-Length"] = str(quelle.size)
        return StreamingResponse(
            _read_slice(quelle.file, quelle.base_offset, quelle.size),
            status_code=status.HTTP_200_OK,
            headers=kopf,
        )

    kopf["Content-Range"] = bereich.content_range(quelle.size)
    kopf["Content-Length"] = str(bereich.length)
    return StreamingResponse(
        _read_slice(quelle.file, quelle.base_offset + bereich.start, bereich.length),
        status_code=status.HTTP_206_PARTIAL_CONTENT,
        headers=kopf,
    )


# ------------------------------------------------------------------- Lease


def _hot_copies(db: Session, video_id: str) -> list[HotCopy]:
    return list(db.scalars(select(HotCopy).where(HotCopy.video_id == video_id)))


@router.post("/{video_id}/heartbeat", status_code=status.HTTP_204_NO_CONTENT)
def heartbeat(video_id: str, db: Session = Depends(get_db)) -> Response:
    """Der Player meldet sich, solange abgespielt wird.

    Haelt eine eventuelle Heisskopie am Leben. Beim Direktstream gibt es nichts
    zu schuetzen - der Aufruf ist dann folgenlos und trotzdem erlaubt, damit das
    Frontend nicht zwischen den Faellen unterscheiden muss.
    """
    _video_or_404(db, video_id)
    for hot in _hot_copies(db, video_id):
        cache.heartbeat(db, hot)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{video_id}/playback-ended", status_code=status.HTTP_204_NO_CONTENT)
def playback_ended(video_id: str, db: Session = Depends(get_db)) -> Response:
    """Wiedergabe beendet - kurze Frist ansetzen und gleich aufraeumen."""
    _video_or_404(db, video_id)
    for hot in _hot_copies(db, video_id):
        cache.end_playback(db, hot)
    cache.reap(db)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{video_id}/playback-state")
def playback_state(video_id: str, db: Session = Depends(get_db)) -> dict[str, object]:
    """Zustand der Vorbereitung - fuer die Fortschrittsanzeige im Player."""
    video = _video_or_404(db, video_id)
    kopien = [
        {
            "variante": h.variant,
            "status": h.status,
            "groesse": h.size_bytes,
            "laeuft": cache.is_leased(h),
            "verfaellt": h.expires_at.isoformat() if h.expires_at else None,
            "fehler": "Vorbereitung fehlgeschlagen" if h.error else None,
        }
        for h in _hot_copies(db, video_id)
    ]
    return {
        "video_id": video.id,
        "archiv_status": video.status,
        "heisskopien": kopien,
    }


class PlaybackRequest(BaseModel):
    support: str = Field(default="mp4,h264,aac", max_length=512)
    force_transcode: bool = False


class PlaybackHeartbeat(BaseModel):
    position_s: float = Field(default=0, ge=0, le=live_streams.MAX_DURATION_SECONDS, allow_inf_nan=False)
    state: Literal["playing", "paused", "buffering"] = "playing"


def _playback_error(error: live_streams.PlaybackError) -> HTTPException:
    headers = {"Retry-After": "3"} if error.status_code in {429, 503} else None
    return HTTPException(error.status_code, str(error), headers=headers)


@router.post("/{video_id}/playback")
def start_playback(video_id: str, payload: PlaybackRequest, request: Request, db: Session = Depends(get_db)):
    video = _video_or_404(db, video_id)
    if video.status != VideoStatus.ARCHIVED:
        raise HTTPException(404, "Video unbekannt")
    bundle = _bundle_of(video)
    try:
        with BundleReader(bundle) as reader:
            decision = playback.decide(reader.manifest, playback.parse_client_support(payload.support))
            mode = "transcode" if payload.force_transcode or decision.mode == playback.Mode.TRANSCODE else "direct"
            viewer = live_streams.manager.create(
                video_id=video.id, video_title=video.title,
                channel_title=video.channel.name if video.channel else None,
                client_address=request.client.host if request.client else "unbekannt",
                client_name=live_streams.client_name(request.headers.get("user-agent", "")[:512]),
                mode=mode, duration_s=reader.manifest.duration_s or video.duration_s,
                source=bundle, offset=reader.media_data_offset(), size=reader.media_size,
            )
    except live_streams.PlaybackError as error:
        raise _playback_error(error) from None
    except (BundleError, zipfile.BadZipFile, OSError, ValueError, TypeError):
        raise HTTPException(409, "Das Videobuendel ist nicht lesbar.") from None
    url = (f"/api/playback/{viewer.token}/index.m3u8" if mode == "transcode" else
           f"/api/videos/{quote(video.id, safe='')}/stream?support={quote(payload.support, safe='')}")
    return {"token": viewer.token, "mode": mode, "url": url,
            "duration_s": viewer.duration_s, "segment_seconds": live_streams.SEGMENT_SECONDS,
            "reason": "Live-Transkodierung wurde angefordert" if payload.force_transcode else decision.reason}


@playback_router.get("/playback/{token}/index.m3u8")
def playback_manifest(token: str) -> Response:
    try:
        manifest = live_streams.manager.playlist(token)
    except live_streams.PlaybackError as error:
        raise _playback_error(error) from None
    return Response(manifest, media_type="application/vnd.apple.mpegurl", headers={"Cache-Control": "no-store"})


@playback_router.get("/playback/{token}/segments/{index}.ts")
async def playback_segment(token: str, index: int, request: Request) -> Response:
    cancelled = threading.Event()
    task = asyncio.create_task(asyncio.to_thread(live_streams.manager.segment, token, index, cancelled))
    try:
        while not task.done():
            if await request.is_disconnected():
                cancelled.set()
            await asyncio.wait({task}, timeout=0.2)
        data = await task
    except live_streams.PlaybackError as error:
        raise _playback_error(error) from None
    finally:
        # Ein Seek darf seinen alten Abschnitt abbrechen, ohne die Sitzung
        # anderer Zuschauer oder die neue Zielposition zu beenden.
        cancelled.set()
        if not task.done():
            task.add_done_callback(lambda result: result.exception() if not result.cancelled() else None)
    return Response(data, media_type="video/mp2t", headers={"Cache-Control": "no-store"})


@playback_router.post("/playback/{token}/heartbeat", status_code=204)
def playback_session_heartbeat(token: str, payload: PlaybackHeartbeat) -> Response:
    try:
        live_streams.manager.heartbeat(token, payload.position_s, payload.state)
    except live_streams.PlaybackError as error:
        raise _playback_error(error) from None
    return Response(status_code=204)


@playback_router.post("/playback/{token}/ended", status_code=204)
def playback_session_ended(token: str) -> Response:
    live_streams.manager.end(token)
    return Response(status_code=204)


@playback_router.get("/streams")
def active_streams() -> dict[str, object]:
    return live_streams.manager.snapshot()
