"""Neuaufbau des Suchindex aus Datenbank und Buendeln.

Der Index ist bewusst ableitbar: Er enthaelt nichts, was nicht auch in der
Datenbank oder in den Buendeln steht. Geht er verloren oder aendert sich sein
Aufbau, laesst er sich jederzeit neu erzeugen - er muss deshalb weder gesichert
noch migriert werden.
"""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models import Video, VideoStatus
from app.services import suche
from app.services.bundle import BundleError, BundleReader

log = logging.getLogger(__name__)


def index_neu_aufbauen(db: Session, *, mit_untertiteln: bool = True) -> dict[str, int]:
    """Baut Video- und Untertitelindex komplett neu auf.

    Die Untertitel liegen in den Buendeln, nicht in der Datenbank - dafuer wird
    jedes Buendel einmal geoeffnet. Das dauert bei einem grossen Archiv, ist
    aber ein seltener Vorgang.
    """
    suche.schema_anlegen(db)
    db.execute(text("DELETE FROM video_suche"))
    db.execute(text("DELETE FROM untertitel_suche"))
    db.commit()

    zahlen = {"videos": 0, "untertitelspuren": 0, "untertitelzeilen": 0, "buendel_fehler": 0}

    for video in db.scalars(select(Video)):
        suche.video_indizieren(
            db,
            video_id=video.id,
            titel=video.title,
            beschreibung=video.description,
            kanal=video.channel.name if video.channel else None,
        )
        zahlen["videos"] += 1

        if not mit_untertiteln or video.status != VideoStatus.ARCHIVED or not video.bundle_file:
            continue
        if not video.subtitles:
            continue

        try:
            with BundleReader(Path(video.bundle_file)) as leser:
                for eintrag in video.subtitles:
                    try:
                        inhalt = leser.read(eintrag.name_in_bundle).decode("utf-8", errors="replace")
                    except KeyError:
                        continue
                    zeilen = suche.untertitel_indizieren(db, video.id, eintrag.language, inhalt)
                    if zeilen:
                        zahlen["untertitelspuren"] += 1
                        zahlen["untertitelzeilen"] += zeilen
        except (BundleError, OSError) as e:
            zahlen["buendel_fehler"] += 1
            log.warning("Buendel von %s nicht lesbar: %s", video.id, e)

        # Zwischendurch schreiben, damit ein Abbruch nicht alles verwirft.
        if zahlen["videos"] % 50 == 0:
            db.commit()

    db.commit()
    log.info(
        "Suchindex neu aufgebaut: %d Videos, %d Untertitelspuren, %d Zeilen, %d Buendelfehler",
        zahlen["videos"], zahlen["untertitelspuren"], zahlen["untertitelzeilen"],
        zahlen["buendel_fehler"],
    )
    return zahlen
