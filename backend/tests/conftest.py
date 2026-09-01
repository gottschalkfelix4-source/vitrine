"""Gemeinsame Testhilfen.

Der wichtigste Punkt hier ist :func:`neue_sitzung`. Die Testsitzungen wurden
frueher mit ``sessionmaker(bind=eng, expire_on_commit=False)`` gebaut - und
damit mit ``autoflush=True``, waehrend der Betrieb ``autoflush=False``
verwendet. Der Unterschied klingt nebensaechlich, ist es aber nicht:

Bei eingeschaltetem Autoflush schreibt ``Session.get()`` vorher alles Offene
weg und findet deshalb auch ein Objekt, das gerade erst in derselben Schleife
angelegt wurde. Ohne Autoflush findet es das nicht - und der Code legt einen
zweiten Datensatz mit demselben Schluessel an.

Genau daran ist ein Kanalabgleich im Betrieb gescheitert, waehrend der eigens
dafuer geschriebene Test gruen war. Deshalb bauen alle Tests ihre Sitzung ab
jetzt hierueber, mit denselben Einstellungen wie ``app.db.SessionLocal``.
"""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.models import Base


def neue_engine():
    """In-Memory-Datenbank, die sich mehrere Threads teilen.

    ``StaticPool`` ist noetig, weil eine In-Memory-Datenbank der jeweiligen
    Verbindung gehoert - ohne geteilte Verbindung saehe der Thread des
    TestClient eine leere Datenbank ohne Tabellen.
    """
    eng = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(eng)
    return eng


def neue_sitzung(engine=None, *, mit_suche: bool = True) -> Session:
    """Sitzung mit denselben Einstellungen wie im Betrieb.

    ``autoflush=False`` ist hier kein Detail, sondern der Kern - siehe
    Modulkopf.
    """
    eng = engine if engine is not None else neue_engine()
    db = sessionmaker(bind=eng, autoflush=False, expire_on_commit=False, future=True)()
    if mit_suche:
        # Die FTS5-Tabellen stehen nicht im SQLAlchemy-Modell und muessen
        # getrennt angelegt werden, sonst testet man den Rueckfallweg.
        from app.services import suche

        suche.schema_anlegen(db)
    return db
