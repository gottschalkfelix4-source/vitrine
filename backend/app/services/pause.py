"""Manuelle Pause fuer neue YouTube-Auftraege, dauerhaft in SQLite gespeichert.

Die Pause wird beim atomaren Abholen eines Auftrags in derselben SQL-Anweisung
geprueft. Dadurch kann kein Worker eine gerade bestaetigte Pause uebersehen;
nach einem Neustart ist auch kein gemeinsamer Speicherzustand nachzuladen.
Laufende Auftraege bleiben unberuehrt, ebenso die automatische IP-Drosselung.
"""

from __future__ import annotations

import logging
import math
from datetime import UTC, datetime, timedelta
from typing import TypedDict

from sqlalchemy import delete, func, or_, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from app.models import Job, JobStatus, JobType, Setting, utcnow

log = logging.getLogger(__name__)

SETTING_KEY = "_queue_pause_until"
UNBEFRISTET = "unbefristet"
MAX_MINUTEN = 7 * 24 * 60
NETZ_TYPEN = (
    JobType.CHANNEL_SYNC,
    JobType.PLAYLIST_SYNC,
    JobType.VIDEO_ARCHIVE,
    JobType.VIDEO_UPGRADE,
)


class Zustand(TypedDict):
    aktiv: bool
    bis: str | None
    rest_s: int | None
    laufend: int


def _zeittext(zeit: datetime) -> str:
    # Einheitliche UTC-Darstellung mit fester Genauigkeit: Die Werte lassen
    # sich so auch in SQLite zeitlich vergleichen, ohne lokale Zeitzone.
    return zeit.astimezone(UTC).isoformat(timespec="microseconds")


def freigegeben() -> ColumnElement[bool]:
    """SQL-Bedingung fuer SELECT *und* UPDATE beim Abholen eines Auftrags."""
    gesperrt = (
        select(Setting.key)
        .where(
            Setting.key == SETTING_KEY,
            or_(Setting.value == UNBEFRISTET, Setting.value > _zeittext(utcnow())),
        )
        .exists()
    )
    return or_(Job.type.not_in(NETZ_TYPEN), ~gesperrt)


def zustand(db: Session, *, laufend: int | None = None) -> Zustand:
    # Spalte statt ORM-Objekt lesen: Eine lange offene API-/Worker-Sitzung
    # darf keinen alten Wert aus ihrer Identity-Map behalten.
    roh = db.scalar(select(Setting.value).where(Setting.key == SETTING_KEY))
    jetzt = utcnow()
    aktiv = roh == UNBEFRISTET
    bis = None
    rest_s: int | None = None if aktiv else 0
    if roh and roh != UNBEFRISTET:
        ende = datetime.fromisoformat(roh)
        if ende > jetzt:
            aktiv = True
            bis = roh
            rest_s = math.ceil((ende - jetzt).total_seconds())
    if laufend is None:
        laufend = db.scalar(
            select(func.count(Job.id)).where(
                Job.status == JobStatus.RUNNING, Job.type.in_(NETZ_TYPEN)
            )
        ) or 0
    return {"aktiv": aktiv, "bis": bis, "rest_s": rest_s, "laufend": laufend}


def pausieren(db: Session, minuten: int | None) -> Zustand:
    if minuten is not None and (type(minuten) is not int or not 1 <= minuten <= MAX_MINUTEN):
        raise ValueError(f"Die Pause muss zwischen 1 und {MAX_MINUTEN} Minuten dauern.")
    jetzt = utcnow()
    wert = UNBEFRISTET if minuten is None else _zeittext(jetzt + timedelta(minutes=minuten))
    # UPSERT statt SELECT + INSERT: Zwei gleichzeitige Pause-Klicks duerfen
    # weder einen doppelten Schluessel noch einen verlorenen Zustand erzeugen.
    db.execute(
        insert(Setting)
        .values(key=SETTING_KEY, value=wert, updated_at=jetzt)
        .on_conflict_do_update(
            index_elements=[Setting.key], set_={"value": wert, "updated_at": jetzt}
        )
    )
    db.commit()
    log.info("YouTube-Warteschlange manuell pausiert: %s", wert)
    return zustand(db)


def fortsetzen(db: Session) -> Zustand:
    db.execute(delete(Setting).where(Setting.key == SETTING_KEY))
    db.commit()
    log.info("Manuelle Pause der YouTube-Warteschlange aufgehoben")
    return zustand(db)
