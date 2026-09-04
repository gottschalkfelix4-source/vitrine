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
import threading
import time
from collections.abc import Callable
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy import inspect as sa_inspect
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


#: Serialisiert das Schreiben des Fortschritts.
#:
#: Noetig, weil der Fortschritt nicht aus dem Arbeiterstrang gemeldet wird,
#: sondern aus den Fragment-Threads von yt-dlp - standardmaessig vier Stueck.
#: Die teilen sich die SQLAlchemy-Sitzung des Auftrags, und die ist
#: ausdruecklich nicht threadsicher: Schliesst ein Thread die Transaktion,
#: waehrend ein anderer mitten im Schreiben steckt, endet das mit
#: "ResourceClosedError: This transaction is closed" - und der Auftrag gilt als
#: gescheitert, obwohl der Download in Ordnung war.
#:
#: Aufgefallen ist das erst, als drei Downloads gleichzeitig liefen: Zwei von
#: dreien brachen bei 16 % und 14 % ab. Der Fehler war vorher schon da, nur
#: seltener.
_fortschritt_sperre = threading.Lock()

#: Zeitpunkt des letzten Schreibens je Auftrag.
_zuletzt_geschrieben: dict[int, float] = {}

#: Mindestabstand zwischen zwei Schreibvorgaengen. yt-dlp meldet mehrmals je
#: Sekunde und Fragment; jede Meldung einzeln zu schreiben waere Dutzende
#: Transaktionen je Sekunde fuer eine Zahl, die niemand so schnell abliest.
FORTSCHRITT_ABSTAND_S = 1.0


def fortschritt(db: Session, job: Job, wert: float, nachricht: str | None = None) -> None:
    """Haelt den Fortschritt eines Auftrags fest.

    Darf aus fremden Threads aufgerufen werden - siehe :data:`_fortschritt_sperre`.
    """
    with _fortschritt_sperre:
        job.progress = max(0.0, min(1.0, wert))
        if nachricht is not None:
            job.message = nachricht[:1000]

        # Nur der Schreibvorgang wird gedrosselt, nicht die Zuweisung: Der
        # Wert im Speicher bleibt aktuell und wird beim naechsten Durchlauf
        # oder spaetestens beim Abschluss des Auftrags mitgeschrieben.
        jetzt = time.monotonic()
        schluessel = job.id
        if jetzt - _zuletzt_geschrieben.get(schluessel, 0.0) < FORTSCHRITT_ABSTAND_S:
            return
        _zuletzt_geschrieben[schluessel] = jetzt
        db.commit()


def _fortschritt_vergessen(job_id: int) -> None:
    """Raeumt den Drosselungsvermerk ab, wenn ein Auftrag endet."""
    with _fortschritt_sperre:
        _zuletzt_geschrieben.pop(job_id, None)


def erledigt(db: Session, job: Job, nachricht: str | None = None) -> None:
    _fortschritt_vergessen(job.id)
    job.status = JobStatus.DONE
    job.progress = 1.0
    job.finished_at = utcnow()
    if nachricht:
        job.message = nachricht[:1000]
    db.commit()
    log.info("Auftrag %s erledigt (%s %s)", job.id, job.type, job.target_id or "")


def unterbrochen(db: Session, job: Job, nachricht: str) -> None:
    """Legt einen Auftrag zurueck in die Warteschlange, ohne ihn zu bewerten.

    Fuer das Herunterfahren gedacht und nur dafuer. Der Unterschied zu
    :func:`gescheitert` ist nicht kosmetisch: Ein gescheiterter Auftrag zaehlt
    einen Versuch hoch und wird nach genug Versuchen aufgegeben. Ein Update des
    Containers waehrend eines langen Downloads darf aber kein Video verbrennen -
    und der Nutzer soll danach keine Liste roter Fehlschlaege sehen, die keine
    sind.

    Der Versuchszaehler bleibt deshalb unberuehrt, und der Auftrag steht sofort
    wieder auf "wartet". Beim naechsten Start nimmt ihn der erste freie Strang.
    """
    _fortschritt_vergessen(job.id)
    job.status = JobStatus.PENDING
    job.started_at = None
    job.progress = 0.0
    job.message = nachricht[:1000]
    db.commit()
    log.info("Auftrag %s unterbrochen und wieder eingereiht (%s)", job.id, job.type)


def gescheitert(db: Session, job: Job, fehler: str) -> None:
    """Vermerkt einen Fehlschlag - auch dann, wenn die Sitzung blockiert ist.

    Das Zuruecksetzen am Anfang ist der springende Punkt: Ist der Auftrag an
    einem Schreibfehler gescheitert (etwa an einer verletzten Eindeutigkeit),
    nimmt SQLAlchemy keine weitere Anweisung mehr entgegen, bis zurueckgesetzt
    wurde. Ohne das wirft ausgerechnet die Fehlerbehandlung selbst eine
    PendingRollbackError - und die verdeckt die eigentliche Ursache. Genau so
    blieb ein abgebrochener Kanalabgleich frueher als "laeuft" stehen.
    """
    # Den Schluessel ueber den Objektzustand lesen, nicht ueber job.id: Nach
    # einem fehlgeschlagenen Flush sind die Felder abgelaufen, und schon ein
    # lesender Zugriff wuerde ein Nachladen ausloesen - das scheitert dann
    # genauso. identity kommt dagegen ohne Datenbankzugriff aus.
    kennung = sa_inspect(job).identity
    if kennung is None:
        db.rollback()
        log.warning("Auftrag ohne Kennung gescheitert: %s", fehler)
        return
    job_id = kennung[0]

    db.rollback()
    frisch = db.get(Job, job_id)
    if frisch is None:
        log.warning("Auftrag %s gescheitert, ist aber verschwunden: %s", job_id, fehler)
        return
    frisch.status = JobStatus.FAILED
    frisch.finished_at = utcnow()
    frisch.error = fehler[:4000]
    db.commit()
    log.warning(
        "Auftrag %s gescheitert (%s %s): %s", job_id, frisch.type, frisch.target_id or "", fehler
    )


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


def gegenstandslose_entfernen(db: Session) -> dict[str, int]:
    """Nimmt Archivierungsauftraege aus der Warteschlange, die nichts mehr tun.

    Zwei Sorten, und beide entstanden real bei einem Kanal mit 3363 Videos:
    Dort standen 3788 wartende Archivierungsauftraege, obwohl nur 1736 Videos
    ueberhaupt noch etwas brauchten.

    **Erledigte Ziele.** Auftraege fuer Videos, die laengst im Archiv liegen.
    Sie stammen aus der Zeit, bevor "Alle laden" nur noch einreihte, was
    wirklich fehlt. Der Fix von damals verhindert neue; die vorhandenen blieben
    liegen, denn ein Fehler im Einreihen raeumt nichts weg, was er frueher
    angerichtet hat.

    **Doppelte.** Zwei wartende Auftraege fuer dasselbe Video. ``enqueue``
    verhindert das eigentlich, aber die Pruefung ist ein SELECT vor einem
    INSERT ohne Datenbankschluessel darauf - laufen zwei Einreihungen
    gleichzeitig, schluepfen beide durch. Der aelteste bleibt stehen, weil er
    seinen Platz in der Reihenfolge schon hat.

    Angefasst werden ausschliesslich **wartende** Auftraege. Ein laufender wird
    nicht angeruehrt: Er haelt gerade einen halben Download in der Hand.

    Ebenso wenig angefasst werden Auftraege fuer uebersprungene oder bei der
    Quelle verschwundene Videos. Die sehen zwar auch gegenstandslos aus, sind
    es aber nicht zwingend - ein geloeschtes Video kann wiederkommen, und wer
    die Kanalregeln aendert, will die uebersprungenen wiederhaben. Sie kosten
    einen Fehlversuch, keinen vollstaendigen Download.

    Liefert die Zahlen, damit der Start sie protokollieren kann.
    """
    from app.models import Video, VideoStatus

    offen = list(
        db.scalars(
            select(Job)
            .where(Job.type == JobType.VIDEO_ARCHIVE, Job.status == JobStatus.PENDING)
            .order_by(Job.created_at, Job.id)
        )
    )
    if not offen:
        return {"erledigt": 0, "doppelt": 0, "geblieben": 0}

    # Alle betroffenen Videos in einem Rutsch holen. Einzeln nachzuschlagen
    # waeren bei tausenden Auftraegen tausende Abfragen.
    ziele = {j.target_id for j in offen if j.target_id}
    fertig = {
        v.id
        for v in db.scalars(select(Video).where(Video.id.in_(ziele)))
        if v.status == VideoStatus.ARCHIVED and v.bundle_file
    }

    gesehen: set[str] = set()
    weg_erledigt: list[int] = []
    weg_doppelt: list[int] = []
    for j in offen:
        if not j.target_id:
            continue
        if j.target_id in fertig:
            weg_erledigt.append(j.id)
        elif j.target_id in gesehen:
            weg_doppelt.append(j.id)
        else:
            gesehen.add(j.target_id)

    for gruppe in (weg_erledigt, weg_doppelt):
        # In Haeppchen: SQLite nimmt nicht beliebig viele Werte in ein IN().
        for anfang in range(0, len(gruppe), 500):
            db.execute(
                delete(Job).where(Job.id.in_(gruppe[anfang : anfang + 500]))
            )
    db.commit()

    ergebnis = {
        "erledigt": len(weg_erledigt),
        "doppelt": len(weg_doppelt),
        "geblieben": len(gesehen),
    }
    if weg_erledigt or weg_doppelt:
        log.info(
            "Warteschlange bereinigt: %d Auftraege fuer bereits archivierte Videos, "
            "%d doppelte - %d bleiben stehen",
            ergebnis["erledigt"], ergebnis["doppelt"], ergebnis["geblieben"],
        )
    return ergebnis


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
