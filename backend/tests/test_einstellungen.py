"""Tests der Einstellungen zur Laufzeit.

Der heikle Teil ist die Rangfolge Datenbank > Umgebung > Standard: Sie
entscheidet, ob eine Aenderung in der Oberflaeche ueberhaupt wirkt - und ob
ein Eintrag im Unraid-Template noch etwas bewirkt, wenn dasselbe Feld schon
im UI gesetzt wurde.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.config import Settings, settings
from app.models import Setting
from app.services import einstellungen
from tests.conftest import neue_sitzung


@pytest.fixture
def db() -> Session:
    return neue_sitzung(mit_suche=False)


@pytest.fixture(autouse=True)
def urzustand(monkeypatch: pytest.MonkeyPatch):
    """Das Einstellungsobjekt ist ein Singleton - jeder Test bekommt die
    Standardwerte zurueck, sonst faerben Tests aufeinander ab."""
    frisch = Settings()
    for name in einstellungen.NACH_NAME:
        monkeypatch.setattr(settings, name, getattr(frisch, name))


# --------------------------------------------------------------- Registry


def test_jedes_feld_gibt_es_wirklich():
    """Ein Tippfehler im Registry-Namen faellt sonst erst beim Speichern auf."""
    fehlend = [f.name for f in einstellungen.FELDER if f.name not in Settings.model_fields]
    assert not fehlend, f"Felder ohne Entsprechung in Settings: {fehlend}"


def test_auswahlfelder_kennen_ihre_werte():
    for feld in einstellungen.FELDER:
        if feld.art == "auswahl":
            assert feld.auswahl, f"{feld.name} ist eine Auswahl ohne Optionen"
            standard = einstellungen._standardwert(feld)
            wert = getattr(standard, "value", standard)
            assert wert in feld.auswahl, f"{feld.name}: Standard {wert!r} steht nicht zur Auswahl"


# ---------------------------------------------------------------- Herkunft


def test_herkunft_standard(db: Session):
    felder = {f["name"]: f for f in einstellungen.lesen(db)}
    assert felder["av1_crf"]["herkunft"] == "standard"
    assert felder["av1_crf"]["wert"] == 30


def test_herkunft_umgebung(db: Session, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("YTA_AV1_CRF", "34")
    monkeypatch.setattr(settings, "av1_crf", 34)
    felder = {f["name"]: f for f in einstellungen.lesen(db)}
    assert felder["av1_crf"]["herkunft"] == "umgebung"


def test_datenbank_gewinnt_ueber_umgebung(db: Session, monkeypatch: pytest.MonkeyPatch):
    """Der Fall, der ohne Anzeige verwirrt: Im Unraid-Template steht 34, im UI
    wurde 26 eingestellt - es gilt 26, und die Oberflaeche sagt das auch."""
    monkeypatch.setenv("YTA_AV1_CRF", "34")
    einstellungen.schreiben(db, {"av1_crf": 26})

    assert settings.av1_crf == 26
    felder = {f["name"]: f for f in einstellungen.lesen(db)}
    assert felder["av1_crf"]["wert"] == 26
    assert felder["av1_crf"]["herkunft"] == "datenbank"


def test_anwenden_beim_start(db: Session):
    """Gespeicherte Werte muessen den Neustart ueberleben - dafuer sorgt
    anwenden(), das der Lebenszyklus beim Start aufruft."""
    db.add(Setting(key="av1_preset", value="10"))
    db.add(Setting(key="archive_min_height", value="720"))
    db.commit()

    assert einstellungen.anwenden(db) == 2
    assert settings.av1_preset == 10
    assert settings.archive_min_height == 720


def test_unbrauchbarer_eintrag_verhindert_den_start_nicht(db: Session, caplog):
    """Nach einem Schemawechsel koennen unsinnige Eintraege zurueckbleiben.
    Der Dienst muss trotzdem hochkommen."""
    db.add(Setting(key="av1_crf", value="voellig kaputt"))
    db.add(Setting(key="gibtsnichtmehr", value="1"))
    db.add(Setting(key="av1_preset", value="8"))
    db.commit()

    assert einstellungen.anwenden(db) == 1  # nur das gueltige
    assert settings.av1_preset == 8
    assert settings.av1_crf == 30, "der kaputte Wert darf nicht durchschlagen"


# ---------------------------------------------------------------- Schreiben


def test_schreiben_und_sofort_wirksam(db: Session):
    ergebnis = einstellungen.schreiben(db, {"av1_crf": 34, "archive_max_height": 1440})
    assert ergebnis["geaendert"] == ["archive_max_height", "av1_crf"]
    assert settings.av1_crf == 34
    assert settings.archive_max_height == 1440
    # Die Qualitaetsentscheidung muss die Aenderung sofort sehen.
    assert "[height<=1440]" in settings.format_selector()


def test_einheiten_werden_umgerechnet(db: Session):
    """Bytes tippt niemand von Hand ein - das Feld zeigt GB."""
    einstellungen.schreiben(db, {"hot_max_bytes": 8})
    assert settings.hot_max_bytes == 8 * 1024**3

    felder = {f["name"]: f for f in einstellungen.lesen(db)}
    assert felder["hot_max_bytes"]["wert"] == 8
    assert felder["hot_max_bytes"]["einheit"] == "GB"


def test_liste_nimmt_kommatext_und_liste(db: Session):
    einstellungen.schreiben(db, {"subtitle_languages": "de, en ,fr"})
    assert settings.subtitle_languages == ["de", "en", "fr"]
    einstellungen.schreiben(db, {"subtitle_languages": ["es"]})
    assert settings.subtitle_languages == ["es"]


def test_zurzeit_braucht_nichts_einen_neustart(db: Session):
    """Frueher stand bei "Parallele Downloads" ein Neustart-Hinweis: Die Zahl
    wurde nur beim Hochfahren gelesen. Inzwischen zieht das Arbeiterwerk die
    Straenge zur Laufzeit nach, und kein Feld ist mehr neustartpflichtig.

    Der Test haelt das fest, damit der Hinweis nicht unbemerkt wieder
    auftaucht - er waere dann eine Zusage, die niemand einloest."""
    assert [f.name for f in einstellungen.FELDER if f.neustart] == []
    assert einstellungen.schreiben(db, {"download_concurrency": 3})["neustart_noetig"] == []
    assert einstellungen.schreiben(db, {"av1_crf": 28})["neustart_noetig"] == []


def test_neustart_wuerde_gemeldet_wenn_es_noetig_waere(db: Session, monkeypatch):
    """Die Mechanik bleibt geprueft, auch ohne aktuellen Anwendungsfall - sonst
    faellt erst beim naechsten neustartpflichtigen Feld auf, dass sie kaputt
    ist."""
    feld = einstellungen.NACH_NAME["av1_crf"]
    monkeypatch.setattr(feld, "neustart", True)
    ergebnis = einstellungen.schreiben(db, {"av1_crf": 27})
    assert feld.titel in ergebnis["neustart_noetig"]


# ----------------------------------------------------------------- Pruefung


@pytest.mark.parametrize(
    "aenderung,teil",
    [
        ({"av1_crf": 999}, "höchstens"),
        ({"av1_crf": -5}, "mindestens"),
        ({"av1_crf": "keine Zahl"}, "Zahl erwartet"),
        ({"archive_codec": "mpeg2"}, "erlaubt sind"),
        ({"subtitle_languages": ""}, "mindestens ein Eintrag"),
        ({"gibtsnicht": 1}, "Unbekannte Einstellungen"),
    ],
)
def test_unsinn_wird_abgewiesen(db: Session, aenderung, teil):
    with pytest.raises(einstellungen.Ungueltig, match=teil):
        einstellungen.schreiben(db, aenderung)


def test_ein_fehler_laesst_nichts_halb_geschrieben(db: Session):
    """Erst pruefen, dann schreiben: Eine Eingabe mit einem Fehler darin darf
    nicht die Haelfte der Aenderungen hinterlassen."""
    with pytest.raises(einstellungen.Ungueltig):
        einstellungen.schreiben(db, {"av1_crf": 28, "av1_preset": 99})

    assert settings.av1_crf == 30, "der gueltige Teil wurde trotzdem geschrieben"
    assert einstellungen.gespeicherte(db) == {}


# ------------------------------------------------------------ Zuruecksetzen


def test_zuruecksetzen_auf_standard(db: Session):
    einstellungen.schreiben(db, {"av1_crf": 20})
    assert settings.av1_crf == 20

    assert einstellungen.zuruecksetzen(db, ["av1_crf"]) == ["av1_crf"]
    assert settings.av1_crf == 30
    assert einstellungen.gespeicherte(db) == {}


def test_zuruecksetzen_faellt_auf_die_umgebung_zurueck(db: Session, monkeypatch):
    """Nach dem Zuruecksetzen gilt wieder, was im Unraid-Template steht -
    nicht der Code-Standard."""
    monkeypatch.setenv("YTA_AV1_CRF", "34")
    einstellungen.schreiben(db, {"av1_crf": 20})
    einstellungen.zuruecksetzen(db, ["av1_crf"])

    assert settings.av1_crf == 34
    felder = {f["name"]: f for f in einstellungen.lesen(db)}
    assert felder["av1_crf"]["herkunft"] == "umgebung"


def test_alles_zuruecksetzen(db: Session):
    einstellungen.schreiben(db, {"av1_crf": 20, "av1_preset": 2, "hot_ttl_hours": 1.5})
    assert len(einstellungen.zuruecksetzen(db)) == 3
    assert einstellungen.gespeicherte(db) == {}
    assert settings.av1_crf == 30 and settings.av1_preset == 6
