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

log = logging.getLogger(__name__)


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


def _spalten_nachziehen(engine_: Engine) -> list[str]:
    """Ergaenzt Spalten, die im Modell stehen, aber nicht in der Datenbank.

    ``create_all`` legt fehlende *Tabellen* an, ruehrt vorhandene aber nicht
    an. Eine neue Spalte im Modell fuehrt deshalb bei einem bestehenden Archiv
    zu "no such column" - und zwar erst beim ersten Zugriff, nicht beim Start.

    Ein vollwertiges Migrationswerkzeug waere hier zu viel: Das Schema ist
    SQLite-only, und alles, was bisher dazugekommen ist, waren zusaetzliche
    Spalten mit NULL-Vorgabe. Genau die - und nur die - zieht diese Funktion
    nach. Umbenennungen, Typwechsel oder Loeschungen kann sie nicht und soll
    sie nicht; wer so etwas braucht, holt sich Alembic dazu.

    Liefert die Namen der ergaenzten Spalten, damit der Start es protokolliert.
    """
    from sqlalchemy import inspect, text

    ergaenzt: list[str] = []
    pruefer = inspect(engine_)
    with engine_.begin() as conn:
        for tabelle in Base.metadata.sorted_tables:
            if not pruefer.has_table(tabelle.name):
                continue  # legt gleich create_all an
            vorhanden = {s["name"] for s in pruefer.get_columns(tabelle.name)}
            for spalte in tabelle.columns:
                if spalte.name in vorhanden:
                    continue
                # Nur nachruestbare Spalten: SQLite verlangt bei ADD COLUMN
                # entweder NULL-Zulassung oder eine konstante Vorgabe. Alles
                # andere waere ein echter Schemawechsel und wird bewusst
                # uebersprungen statt halb ausgefuehrt.
                if not spalte.nullable and spalte.server_default is None:
                    log.warning(
                        "Spalte %s.%s fehlt, laesst sich aber nicht nachtragen "
                        "(NOT NULL ohne Vorgabe) - bitte von Hand migrieren.",
                        tabelle.name, spalte.name,
                    )
                    continue
                typ = spalte.type.compile(engine_.dialect)
                conn.execute(text(f'ALTER TABLE "{tabelle.name}" ADD COLUMN "{spalte.name}" {typ}'))
                ergaenzt.append(f"{tabelle.name}.{spalte.name}")

    # Indizes entstehen sonst nur beim Anlegen der Tabelle. Eine nachgetragene
    # Spalte mit index=True bekaeme ihren Index also nie - und ausgerechnet
    # ueber die wird sortiert.
    for tabelle in Base.metadata.sorted_tables:
        if pruefer.has_table(tabelle.name):
            for index in tabelle.indexes:
                index.create(bind=engine_, checkfirst=True)
    return ergaenzt


def init_db() -> None:
    """Legt Verzeichnisse und Schema an. Idempotent."""
    settings.ensure_dirs()
    Base.metadata.create_all(engine)
    nachgezogen = _spalten_nachziehen(engine)
    if nachgezogen:
        log.info("Schema ergaenzt: %s", ", ".join(nachgezogen))

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
