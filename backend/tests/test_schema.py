"""Nachziehen des Schemas bei einem bestehenden Archiv.

``Base.metadata.create_all`` legt fehlende *Tabellen* an und ruehrt vorhandene
nicht an. Eine neue Spalte im Modell faellt deshalb erst im Betrieb auf, und
zwar nicht beim Start, sondern beim ersten Zugriff - mit "no such column"
mitten in einer Anfrage. Wer die Anwendung nur gegen eine frische Datenbank
testet, sieht das nie.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.pool import StaticPool

from app.db import _spalten_nachziehen, set_pragmas
from app.models import Base, Channel, Video, VideoStatus


@pytest.fixture
def altbestand():
    """Eine Datenbank, wie sie vor der neuen Spalte ausgesehen hat - mit Daten
    darin, denn genau die darf das Nachziehen nicht verlieren."""
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    event.listens_for(eng, "connect")(set_pragmas)
    Base.metadata.create_all(eng)

    # Bestand ueber das Modell anlegen, nicht ueber SQL von Hand: Nur so sind
    # alle Vorgabewerte gesetzt, und der Datensatz sieht aus wie einer aus dem
    # echten Betrieb.
    from tests.conftest import neue_sitzung

    db = neue_sitzung(eng, mit_suche=False)
    db.add(Channel(id="UCalt", name="Bestandskanal"))
    db.add(Video(id="altesvideo1", channel_id="UCalt", title="Schon da",
                 status=VideoStatus.ARCHIVED))
    db.commit()
    db.close()

    # Zurueck auf den alten Stand: erst der Index, dann die Spalte - andersherum
    # bleibt ein Index auf einer Spalte stehen, die es nicht mehr gibt, und
    # SQLite verweigert schon das Loeschen.
    with eng.begin() as c:
        c.execute(text("DROP INDEX IF EXISTS ix_videos_uploads_position"))
        c.execute(text("ALTER TABLE videos DROP COLUMN uploads_position"))
    return eng


def test_fehlende_spalte_wird_ergaenzt(altbestand):
    assert "uploads_position" not in {s["name"] for s in inspect(altbestand).get_columns("videos")}

    ergaenzt = _spalten_nachziehen(altbestand)

    assert "videos.uploads_position" in ergaenzt
    assert "uploads_position" in {s["name"] for s in inspect(altbestand).get_columns("videos")}


def test_vorhandene_daten_bleiben_erhalten(altbestand):
    """Eine Migration, die den Bestand kostet, waere schlimmer als der Fehler."""
    _spalten_nachziehen(altbestand)
    with altbestand.begin() as c:
        zeile = c.execute(text(
            "SELECT title, status, uploads_position FROM videos WHERE id='altesvideo1'"
        )).one()
    assert zeile.title == "Schon da"
    assert zeile.status == "archived"
    assert zeile.uploads_position is None


def test_zweiter_lauf_aendert_nichts(altbestand):
    """Der Aufruf steht im Start und laeuft bei jedem Neustart erneut."""
    assert _spalten_nachziehen(altbestand) == ["videos.uploads_position"]
    assert _spalten_nachziehen(altbestand) == []


def test_index_der_neuen_spalte_entsteht(altbestand):
    """Ein Index entsteht sonst nur beim Anlegen der Tabelle - ausgerechnet
    ueber diese Spalte wird aber sortiert."""
    _spalten_nachziehen(altbestand)
    indizes = {i["name"] for i in inspect(altbestand).get_indexes("videos")}
    assert any("uploads_position" in n for n in indizes), indizes


def test_frische_datenbank_braucht_nichts(altbestand):
    """Gegenprobe: Beim Normalfall darf die Nachfuehrung nicht zuschlagen."""
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    event.listens_for(eng, "connect")(set_pragmas)
    Base.metadata.create_all(eng)
    assert _spalten_nachziehen(eng) == []
