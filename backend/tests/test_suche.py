"""Tests der Volltextsuche.

Schwerpunkt liegt auf den beiden Dingen, die im Deutschen schiefgehen:
zusammengesetzte Woerter und die vielen Schreibweisen von Umlauten.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.models import Base, Channel, Video, VideoStatus
from app.services import suche

VTT = """WEBVTT
Kind: captions
Language: de

00:00:02.500 --> 00:00:06.000
Heute geht es um Netzwerkkonfiguration

00:00:06.000 --> 00:00:09.240
und wie man die Dateigrößen im Griff behält

00:00:09.240 --> 00:00:12.000
und wie man die Dateigrößen im Griff behält

00:01:15.000 --> 00:01:18.500
Zum Schluss noch ein Wort zur <c>Straßenmusik</c>

01:02:03.000 --> 01:02:07.000
Das war der letzte Abschnitt
"""


@pytest.fixture
def db() -> Session:
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng, expire_on_commit=False)()
    suche.schema_anlegen(s)
    s.add(Channel(id="UCtest", name="Werkstattfunk"))
    s.add(Video(id="v1", channel_id="UCtest", title="Netzwerk im Selbstbau",
                description="Über Router, Switches und Dateigrößen", status=VideoStatus.ARCHIVED))
    s.add(Video(id="v2", channel_id="UCtest", title="Heimserver aufräumen",
                description="Platten sortieren", status=VideoStatus.ARCHIVED))
    s.commit()
    suche.video_indizieren(s, video_id="v1", titel="Netzwerk im Selbstbau",
                           beschreibung="Über Router, Switches und Dateigrößen", kanal="Werkstattfunk")
    suche.video_indizieren(s, video_id="v2", titel="Heimserver aufräumen",
                           beschreibung="Platten sortieren", kanal="Werkstattfunk")
    suche.untertitel_indizieren(s, "v1", "de", VTT)
    s.commit()
    return s


# ----------------------------------------------------------------- Umschrift


@pytest.mark.parametrize(
    "eingabe,erwartet",
    [
        ("Größe", "groesse"),
        ("STRASSE", "strasse"),
        ("Straße", "strasse"),
        ("Köln", "koeln"),
        ("Über", "ueber"),
        ("Café", "cafe"),
        (None, ""),
        ("", ""),
    ],
)
def test_umschrift(eingabe, erwartet):
    assert suche.normalisieren(eingabe) == erwartet


def test_umlaut_und_umschrift_fallen_zusammen(db: Session):
    """Der Kern: Wie jemand tippt, darf keine Rolle spielen."""
    for schreibweise in ["dateigrößen", "dateigroessen", "Dateigrößen", "DATEIGROESSEN"]:
        assert suche.video_treffer(db, schreibweise) == ["v1"], schreibweise


def test_komposita_werden_gefunden(db: Session):
    """Der Grund fuer den Trigram-Tokenizer: Mit Wortzerlegung faende
    'server' das Wort 'Heimserver' nicht."""
    assert suche.video_treffer(db, "server") == ["v2"]
    assert suche.video_treffer(db, "netzwerk") == ["v1"]
    assert suche.video_treffer(db, "räumen") == ["v2"]


def test_titel_wiegt_schwerer_als_beschreibung(db: Session):
    """'Netzwerk' steht in v1 im Titel. Ein Video, das den Begriff nur in der
    Beschreibung fuehrt, darf nicht davor landen."""
    suche.video_indizieren(db, video_id="v2", titel="Heimserver aufräumen",
                           beschreibung="Platten sortieren und Netzwerk pruefen", kanal="Werkstattfunk")
    db.commit()
    assert suche.video_treffer(db, "netzwerk")[0] == "v1"


def test_zu_kurze_anfrage_liefert_nichts(db: Session):
    assert suche.video_treffer(db, "ab") == []
    assert suche.untertitel_treffer(db, "ab") == []


def test_sonderzeichen_stuerzen_nicht_ab(db: Session):
    """Ohne Anfuehrungszeichen deutet FTS5 solche Eingaben als Suchsyntax und
    wirft einen Syntaxfehler statt eines leeren Ergebnisses."""
    for eingabe in ['C++ vs. Rust', 'was ist "das"', "foo AND bar", "a-b-c", "(klammer", "stern*"]:
        assert suche.video_treffer(db, eingabe) == []


def test_index_laesst_sich_aktualisieren(db: Session):
    suche.video_indizieren(db, video_id="v1", titel="Ganz anderer Titel",
                           beschreibung=None, kanal=None)
    db.commit()
    assert suche.video_treffer(db, "selbstbau") == []
    assert suche.video_treffer(db, "anderer") == ["v1"]


def test_entfernen(db: Session):
    suche.entfernen(db, "v1")
    db.commit()
    assert suche.video_treffer(db, "netzwerk") == []
    assert suche.untertitel_treffer(db, "netzwerk") == []


# ----------------------------------------------------------------- Untertitel


def test_vtt_zerlegung():
    zeilen = suche.vtt_zerlegen(VTT)
    texte = [z.text for z in zeilen]
    assert "Heute geht es um Netzwerkkonfiguration" in texte
    # Auszeichnungen muessen raus, sonst steht "<c>" im Index.
    assert "Zum Schluss noch ein Wort zur Straßenmusik" in texte
    # Automatische Untertitel wiederholen Zeilen - die doppelte darf nur
    # einmal auftauchen.
    assert texte.count("und wie man die Dateigrößen im Griff behält") == 1


def test_vtt_zeiten():
    zeilen = {z.text[:20]: z.start_s for z in suche.vtt_zerlegen(VTT)}
    assert zeilen["Heute geht es um Net"] == pytest.approx(2.5)
    assert zeilen["Zum Schluss noch ein"] == pytest.approx(75.0)
    # Stundenangabe muss mitgerechnet werden.
    assert zeilen["Das war der letzte A"] == pytest.approx(3723.0)


def test_vtt_vertraegt_muell():
    assert suche.vtt_zerlegen("") == []
    assert suche.vtt_zerlegen("WEBVTT\n\nkeine Zeitmarken hier\n") == []
    # Komma statt Punkt (SRT-Schreibweise) soll trotzdem gehen.
    z = suche.vtt_zerlegen("WEBVTT\n\n00:00:05,000 --> 00:00:07,000\nHallo\n")
    assert len(z) == 1 and z[0].start_s == pytest.approx(5.0)


def test_untertitelfund_liefert_zeitstempel(db: Session):
    """Der eigentliche Mehrwert: nicht nur welches Video, sondern wo darin."""
    (fund,) = suche.untertitel_treffer(db, "straßenmusik")
    assert fund.video_id == "v1"
    assert fund.start_s == pytest.approx(75.0)
    assert fund.sprache == "de"
    assert "strassenmusik" in fund.zeile


def test_untertitelsuche_findet_komposita_mit_umschrift(db: Session):
    (fund,) = suche.untertitel_treffer(db, "konfiguration")
    assert fund.start_s == pytest.approx(2.5)
    (fund,) = suche.untertitel_treffer(db, "groessen")
    assert fund.start_s == pytest.approx(6.0)


def test_je_video_nur_eine_fundstelle(db: Session):
    """Ein Wort, das in einem Vortrag dreissigmal faellt, soll nicht dreissig
    Zeilen desselben Videos liefern."""
    suche.untertitel_indizieren(
        db, "v2", "de",
        "WEBVTT\n\n" + "\n\n".join(
            f"00:00:{i:02d}.000 --> 00:00:{i + 1:02d}.000\nimmer wieder Router Nummer {i}"
            for i in range(20)
        ),
    )
    db.commit()
    funde = suche.untertitel_treffer(db, "router")
    assert len(funde) == len({f.video_id for f in funde}), "mehrere Fundstellen je Video"


def test_statistik(db: Session):
    s = suche.statistik(db)
    assert s["videos_im_index"] == 2
    assert s["untertitelzeilen"] >= 4
