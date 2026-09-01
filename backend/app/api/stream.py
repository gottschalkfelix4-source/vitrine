"""Auslieferung der Mediendaten und die Wiedergabe-Lease.

Der Endpunkt ``/stream`` ist der einzige Ort, an dem Videobytes das Haus
verlassen. Je nach Entscheidung aus ``playback.py`` liest er entweder direkt aus
dem Buendel oder aus einer Heisskopie - fuer den Client sieht beides gleich aus.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import HotCopy, HotCopyStatus, Video, VideoStatus
from app.services import cache, playback
from app.services.bundle import BundleError, BundleReader
from app.services.ranges import UnsatisfiableRange, parse_range

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/videos", tags=["wiedergabe"])

CHUNK = 512 * 1024


def _video_or_404(db: Session, video_id: str) -> Video:
    v = db.get(Video, video_id)
    if v is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Video unbekannt")
    return v


def _bundle_of(video: Video) -> Path:
    if video.status != VideoStatus.ARCHIVED or not video.bundle_file:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Video ist nicht archiviert (Status: {video.status})",
        )
    p = Path(video.bundle_file)
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
    except BundleError as e:
        log.error("Buendel %s unbrauchbar: %s", bundle, e)
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, f"Buendel unbrauchbar: {e}") from e

    # Transkodierpfad: gibt es schon eine fertige Heisskopie?
    hot = db.scalar(
        select(HotCopy).where(
            HotCopy.video_id == video.id, HotCopy.variant == entscheidung.variant
        )
    )
    if hot and hot.status == HotCopyStatus.READY and Path(hot.path).is_file():
        cache.touch(db, hot)
        return playback.StreamSource(
            file=Path(hot.path),
            base_offset=0,
            size=hot.size_bytes or Path(hot.path).stat().st_size,
            mime_type=hot.mime_type or playback.TRANSCODE_MIME,
            mode=playback.Mode.TRANSCODE,
        )

    # Noch nicht da. Der Client soll nicht auf einer offenen Verbindung warten,
    # sondern den Fortschritt anzeigen und erneut anfragen.
    from app.services import jobs

    job = jobs.enqueue_prepare(db, video.id, entscheidung.variant or playback.TRANSCODE_VARIANT)
    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={
            "status": "wird_vorbereitet",
            "grund": entscheidung.reason,
            "job_id": job.id,
            "fortschritt": job.progress,
        },
    )


@router.get("/{video_id}/stream")
def stream(
    video_id: str,
    db: Session = Depends(get_db),
    range_header: str | None = Header(default=None, alias="Range"),
    support: str | None = Query(
        default=None,
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
        # Das Archiv ist unveraenderlich - der Browser darf beliebig lange cachen.
        "Cache-Control": "private, max-age=86400",
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
            "fehler": h.error,
        }
        for h in _hot_copies(db, video_id)
    ]
    return {
        "video_id": video.id,
        "archiv_status": video.status,
        "heisskopien": kopien,
        "fortschritt_s": video.progress_s,
    }
