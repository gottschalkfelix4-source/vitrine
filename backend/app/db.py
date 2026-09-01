"""Datenbankanbindung.

SQLite wird hier von mehreren Seiten gleichzeitig benutzt: die Web-Requests und
die Hintergrund-Worker (Download, Encode, Reaper). Ohne WAL-Modus und ein
gesetztes ``busy_timeout`` fuehrt das zuverlaessig zu "database is locked".
Beides wird deshalb bei jeder neuen Verbindung erzwungen.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.models import Base


def set_pragmas(dbapi_conn, _record=None) -> None:
    """Verbindungs-Einstellungen - auch fuer die Test-Engines gedacht.

    Die Tests MUESSEN dieselben Pragmas benutzen wie der Betrieb. Vor allem
    ``foreign_keys=ON``: Ohne das kaskadiert SQLite nicht, und ein Test, der
    das Loeschen eines Kanals prueft, prueft dann etwas anderes als das, was
    im Betrieb passiert.
    """
    cur = dbapi_conn.cursor()
    # WAL: Leser blockieren den Schreiber nicht. Entscheidend, weil ein
    # laufender Encode-Job minutenlang schreiben kann, waehrend das UI liest.
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute("PRAGMA busy_timeout=30000")
    cur.execute("PRAGMA temp_store=MEMORY")
    cur.close()


def _create_engine() -> Engine:
    engine = create_engine(
        settings.database_url,
        # check_same_thread=False, weil die Worker in eigenen Threads laufen.
        connect_args={"check_same_thread": False, "timeout": 30},
        pool_pre_ping=True,
        future=True,
    )
    event.listens_for(engine, "connect")(set_pragmas)
    return engine


engine = _create_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def init_db() -> None:
    """Legt Verzeichnisse und Schema an. Idempotent."""
    settings.ensure_dirs()
    Base.metadata.create_all(engine)

    # Die Volltext-Tabellen sind virtuelle FTS5-Tabellen und stehen deshalb
    # nicht im SQLAlchemy-Modell - sie werden hier von Hand angelegt.
    from app.services import suche

    with SessionLocal() as s:
        try:
            suche.schema_anlegen(s)
        except Exception:
            # Ohne FTS5 laeuft das Archiv weiter, nur die Suche faellt auf
            # einfaches Vergleichen zurueck. Kein Grund, den Start abzubrechen.
            logging.getLogger(__name__).warning(
                "Volltextsuche konnte nicht eingerichtet werden - SQLite ohne FTS5? "
                "Die Suche benutzt dann den langsameren Rueckfallweg.",
                exc_info=True,
            )


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transaktionsklammer fuer Worker und Skripte."""
    s = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def get_db() -> Iterator[Session]:
    """FastAPI-Abhaengigkeit."""
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()
