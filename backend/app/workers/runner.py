"""Die Arbeiterstraenge.

Drei getrennte Gruppen statt eines gemeinsamen Pools, weil die drei Arten von
Arbeit voellig unterschiedliche Eigenschaften haben:

*Netz* - Downloads und Kanalabgleiche. Muss schmal bleiben, weil YouTube pro
IP-Adresse drosselt und nicht pro Prozess; als Gast liegt die Grenze bei rund
300 Videos je Stunde. Mehr Straenge machen nicht schneller fertig, sondern
voruebergehend gesperrt.

*Vorbereitung* - Jemand sitzt davor und wartet auf sein Video. Braucht einen
eigenen Strang, sonst steht die Wiedergabe hinter einer stundenlangen
Recodierung in der Schlange.

*Recodierung* - Reine Rechenlast, laeuft tagelang. Darf beliebig warten, darf
aber nie die beiden anderen blockieren.

Wuerde man das in einen Pool werfen, waere der haeufigste Effekt genau der
aergerlichste: Man klickt auf ein Video und wartet, weil gerade drei
Recodierungen alle Plaetze belegen.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass

from app.config import settings
from app.db import session_scope
from app.models import JobType
from app.services import jobs

log = logging.getLogger(__name__)

#: Pause, wenn nichts zu tun ist. Kurz genug, dass sich eine Vorbereitung
#: nicht spuerbar verzoegert, lang genug, um nicht dauernd die Datenbank zu
#: befragen.
LEERLAUF_S = 2.0


@dataclass(slots=True)
class Gruppe:
    name: str
    typen: list[str]
    straenge: int


def _gruppen() -> list[Gruppe]:
    return [
        Gruppe(
            "netz",
            [JobType.CHANNEL_SYNC, JobType.PLAYLIST_SYNC, JobType.VIDEO_ARCHIVE],
            settings.download_concurrency,
        ),
        # Immer genau einer: Mehr bringt nichts, weil ffmpeg ohnehin alle Kerne
        # nutzt, und zwei gleichzeitige Vorbereitungen machen beide langsam.
        Gruppe("vorbereitung", [JobType.VIDEO_PREPARE], 1),
        Gruppe("recodierung", [JobType.VIDEO_RECODE], settings.encode_concurrency),
    ]


class Arbeiterwerk:
    """Startet und beendet alle Arbeiterstraenge."""

    def __init__(self) -> None:
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    def start(self) -> None:
        # Import mit Nebenwirkung: Dabei tragen sich die Bearbeiter ueber den
        # @jobs.register-Dekorator in die Registrierung ein.
        from app.workers import archive, prepare, sync  # noqa: F401

        fehlend = [t for t in JobType if t not in jobs.HANDLERS]
        if fehlend:
            log.warning("Kein Bearbeiter fuer: %s", ", ".join(fehlend))

        for gruppe in _gruppen():
            for nr in range(max(1, gruppe.straenge)):
                t = threading.Thread(
                    target=self._schleife,
                    args=(gruppe,),
                    name=f"{gruppe.name}-{nr + 1}",
                    daemon=True,
                )
                t.start()
                self._threads.append(t)
        log.info(
            "Arbeiter gestartet: %s",
            ", ".join(f"{g.name}x{max(1, g.straenge)}" for g in _gruppen()),
        )

    def stop(self, timeout: float = 10.0) -> None:
        self._stop.set()
        for t in self._threads:
            t.join(timeout=timeout / max(1, len(self._threads)))

    def _schleife(self, gruppe: Gruppe) -> None:
        while not self._stop.is_set():
            try:
                with session_scope() as db:
                    job = jobs.claim_next(db, gruppe.typen)
                    if job is None:
                        self._stop.wait(LEERLAUF_S)
                        continue

                    bearbeiter = jobs.HANDLERS.get(job.type)
                    if bearbeiter is None:
                        jobs.gescheitert(db, job, f"kein Bearbeiter fuer {job.type}")
                        continue

                    log.info("[%s] %s %s beginnt", gruppe.name, job.type, job.target_id or "")
                    try:
                        bearbeiter(db, job)
                    except Exception:
                        # Der Bearbeiter hat den Auftrag bereits als gescheitert
                        # vermerkt; hier geht es nur noch darum, den Strang am
                        # Leben zu halten. Ein einzelnes kaputtes Video darf die
                        # Warteschlange nicht anhalten.
                        log.exception("[%s] %s %s abgebrochen", gruppe.name, job.type, job.target_id or "")
            except Exception:
                log.exception("[%s] Arbeiterschleife gestoert", gruppe.name)
                self._stop.wait(LEERLAUF_S)


werk = Arbeiterwerk()
