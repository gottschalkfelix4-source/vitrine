"""Auftragswarteschlange.

Bewusst SQLite statt Redis/Celery: Das Archiv soll ein einziger Container sein,
den man auf einem NAS startet und vergisst. Eine zweite Middleware dafuer
einzufuehren waere fuer die erwartete Last (einstellige Zahl paralleler
Downloads) nicht zu rechtfertigen.

Wichtig ist trotzdem, dass zwei Worker nie denselben Auftrag greifen. Das
uebernimmt :func:`claim_next` mit einem bedingten UPDATE - der gewinnt genau
einmal, weil SQLite die Schreiboperation serialisiert.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models import Job, JobStatus, JobType, utcnow

log = logging.getLogger(__name__)

#: Auftragstyp -> Bearbeiter. Die Worker-Module tragen sich beim Import ein,
#: damit dieses Modul nichts ueber Downloads oder ffmpeg wissen muss.
HANDLERS: dict[str, Callable[[Session, Job], None]] = {}

#: Prioritaeten - kleiner heisst wichtiger. Eine Vorbereitung laesst einen
#: Menschen warten und geht deshalb allem anderen vor.
PRIO_PREPARE = 10
PRIO_ARCHIVE = 100
PRIO_SYNC = 200
#: Recodierung ganz hinten. Sie darf beliebig lange warten - das Video ist
#: bereits archiviert und abspielbar, es wird nur noch kleiner.
PRIO_RECODE = 900


def register(job_type: str) -> Callable[[Callable[[Session, Job], None]], Callable[[Session, Job], None]]:
    def deko(fn: Callable[[Session, Job], None]) -> Callable[[Session, Job], None]:
        HANDLERS[job_type] = fn
        return fn

    return deko


# ------------------------------------------------------------------ Einreihen


def enqueue(
    db: Session,
    job_type: str,
    target_id: str | None = None,
    *,
    priority: int = PRIO_ARCHIVE,
    payload: dict[str, Any] | None = None,
    dedupe: bool = True,
) -> Job:
    """Reiht einen Auftrag ein.

    Bei ``dedupe`` wird ein bereits offener, gleichartiger Auftrag
    zurueckgegeben statt eines neuen. Ohne das erzeugt ein Player, der beim
    Warten alle zwei Sekunden nachfragt, im Handumdrehen hunderte Auftraege.
    """
    if dedupe:
        vorhanden = db.scalar(
            select(Job).where(
                Job.type == job_type,
                Job.target_id == target_id,
                Job.status.in_([JobStatus.PENDING, JobStatus.RUNNING]),
            )
        )
        if vorhanden is not None:
            return vorhanden

    job = Job(
        type=job_type,
        target_id=target_id,
        priority=priority,
        payload=json.dumps(payload, ensure_ascii=False) if payload else None,
    )
    db.add(job)
    db.commit()
    log.info("Auftrag %s eingereiht: %s %s", job.id, job_type, target_id or "")
    return job


def enqueue_prepare(db: Session, video_id: str, variant: str) -> Job:
    return enqueue(
        db,
        JobType.VIDEO_PREPARE,
        video_id,
        priority=PRIO_PREPARE,
        payload={"variant": variant},
    )


def enqueue_archive(db: Session, video_id: str) -> Job:
    return enqueue(db, JobType.VIDEO_ARCHIVE, video_id, priority=PRIO_ARCHIVE)


def enqueue_channel_sync(db: Session, channel_id: str) -> Job:
    return enqueue(db, JobType.CHANNEL_SYNC, channel_id, priority=PRIO_SYNC)


# -------------------------------------------------------------------- Abholen


def claim_next(db: Session, types: list[str] | None = None) -> Job | None:
    """Nimmt den naechsten offenen Auftrag entgegen - genau einmal.

    Der bedingte UPDATE auf ``status == PENDING`` ist der Sperrmechanismus:
    Greifen zwei Worker gleichzeitig zu, aendert nur einer eine Zeile.
    """
    for _ in range(5):  # ein paar Versuche, falls ein anderer schneller war
        anfrage = select(Job).where(Job.status == JobStatus.PENDING)
        if types:
            anfrage = anfrage.where(Job.type.in_(types))
        kandidat = db.scalar(anfrage.order_by(Job.priority, Job.created_at).limit(1))
        if kandidat is None:
            return None

        ergebnis = db.execute(
            update(Job)
            .where(Job.id == kandidat.id, Job.status == JobStatus.PENDING)
            .values(status=JobStatus.RUNNING, started_at=utcnow(), progress=0.0)
        )
        db.commit()
        if ergebnis.rowcount == 1:
            db.refresh(kandidat)
            return kandidat
    return None


def fortschritt(db: Session, job: Job, wert: float, nachricht: str | None = None) -> None:
    job.progress = max(0.0, min(1.0, wert))
    if nachricht is not None:
        job.message = nachricht[:1000]
    db.commit()


def erledigt(db: Session, job: Job, nachricht: str | None = None) -> None:
    job.status = JobStatus.DONE
    job.progress = 1.0
    job.finished_at = utcnow()
    if nachricht:
        job.message = nachricht[:1000]
    db.commit()
    log.info("Auftrag %s erledigt (%s %s)", job.id, job.type, job.target_id or "")


def gescheitert(db: Session, job: Job, fehler: str) -> None:
    job.status = JobStatus.FAILED
    job.finished_at = utcnow()
    job.error = fehler[:4000]
    db.commit()
    log.warning("Auftrag %s gescheitert (%s %s): %s", job.id, job.type, job.target_id or "", fehler)


def abbrechen(db: Session, job: Job) -> None:
    job.status = JobStatus.CANCELLED
    job.finished_at = utcnow()
    db.commit()


def payload_of(job: Job) -> dict[str, Any]:
    if not job.payload:
        return {}
    try:
        return json.loads(job.payload)
    except json.JSONDecodeError:
        return {}


def reset_stale(db: Session) -> int:
    """Setzt Auftraege zurueck, die beim letzten Absturz mitten im Lauf waren.

    Ohne das bleibt nach einem harten Neustart alles auf RUNNING stehen und die
    Warteschlange arbeitet nie wieder etwas ab.
    """
    ergebnis = db.execute(
        update(Job)
        .where(Job.status == JobStatus.RUNNING)
        .values(
            status=JobStatus.PENDING,
            started_at=None,
            progress=0.0,
            message="nach Neustart erneut eingereiht",
        )
    )
    db.commit()
    if ergebnis.rowcount:
        log.info("%d haengengebliebene Auftraege zurueckgesetzt", ergebnis.rowcount)
    return ergebnis.rowcount
