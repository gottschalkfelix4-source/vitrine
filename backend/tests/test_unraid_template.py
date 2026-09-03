"""Prueft das Unraid-Template gegen die Konfiguration.

Der Anlass ist konkret: Der erste Unraid-Start ist gescheitert, weil im
Template ``YTA_SUBTITLE_LANGUAGES=de,en`` stand. pydantic-settings versucht
bei Listen-Feldern zuerst ``json.loads`` und wirft einen JSONDecodeError, bevor
irgendein Validator laeuft. Der Dienst kam nicht einmal hoch.

Auffallen konnte das nirgends: Die Tests setzen diese Variablen nicht, die
docker-compose.yml auch nicht - nur das Template. Es wurde also gegen nichts
geprueft. Diese Datei schliesst die Luecke, indem sie die Voreinstellungen aus
dem Template genau so anwendet, wie Unraid es tut.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from app.config import Settings

TEMPLATE = Path(__file__).resolve().parent.parent.parent / "unraid" / "vitrine.xml"

pytestmark = pytest.mark.skipif(not TEMPLATE.is_file(), reason="Template nicht vorhanden")


def _eintraege() -> list[ET.Element]:
    return ET.parse(TEMPLATE).getroot().findall("Config")


def _umgebung_aus_template() -> dict[str, str]:
    """Die Variablen so, wie Unraid sie in den Container gibt.

    Wichtig: auch die leeren. Hier stand frueher das Gegenteil - leere
    Voreinstellungen wurden ausgelassen, mit der Begruendung, Unraid setze
    eine Variable ohne Wert nicht. Das stimmt nicht: Unraid uebergibt jede
    Variable seines Templates, ein nicht ausgefuelltes Feld eben als leeren
    String. Diese eine falsche Annahme hat den Test an genau der Stelle blind
    gemacht, an der er haette anschlagen muessen (siehe
    ``test_leere_variablen_bedeuten_nicht_gesetzt``).
    """
    werte = {}
    for c in _eintraege():
        if c.get("Type") != "Variable":
            continue
        ziel = c.get("Target") or ""
        if ziel.startswith("YTA_"):
            werte[ziel] = (c.text or c.get("Default") or "").strip()
    return werte


def test_template_werte_starten_den_dienst(monkeypatch: pytest.MonkeyPatch):
    """Der Kern: Mit den Voreinstellungen des Templates muss Settings() gehen.

    Frueher scheiterte hier YTA_SUBTITLE_LANGUAGES=de,en - und mit ihm der
    ganze Start.
    """
    umgebung = _umgebung_aus_template()
    assert umgebung, "das Template setzt keine einzige YTA_-Variable"
    for k, v in umgebung.items():
        monkeypatch.setenv(k, v)

    s = Settings()

    # Stichproben, die belegen, dass die Werte auch ankommen statt nur zu parsen
    assert s.subtitle_languages == ["de", "en"]
    assert s.archive_min_height == 1080
    assert s.archive_max_height == 0
    assert s.download_concurrency == 1


def test_jede_template_variable_existiert_wirklich():
    """Ein Tippfehler im Template faellt sonst nie auf: extra='ignore' laesst
    unbekannte Variablen stillschweigend fallen, und der Nutzer wundert sich,
    warum seine Einstellung nichts bewirkt."""
    bekannt = {f"YTA_{n.upper()}" for n in Settings.model_fields}
    genutzt = {
        c.get("Target")
        for c in _eintraege()
        if c.get("Type") == "Variable" and (c.get("Target") or "").startswith("YTA_")
    }
    unbekannt = genutzt - bekannt
    assert not unbekannt, f"Template setzt Variablen, die es nicht gibt: {sorted(unbekannt)}"


def test_pfade_und_port_passen_zum_container():
    ziele = {c.get("Target") for c in _eintraege() if c.get("Type") == "Path"}
    # /data ist das Datenverzeichnis, /data/bundles der Kaltspeicher - die
    # Trennung ist der Kern der Unraid-Einrichtung (Cache-Pool vs. Array).
    assert ziele == {"/data", "/data/bundles"}

    ports = [c for c in _eintraege() if c.get("Type") == "Port"]
    assert len(ports) == 1 and ports[0].get("Target") == "8000"

    # PUID/PGID muessen drin sein, sonst gehoeren die Dateien im Share niemandem.
    variablen = {c.get("Target") for c in _eintraege() if c.get("Type") == "Variable"}
    assert {"PUID", "PGID"} <= variablen


@pytest.mark.parametrize(
    "wert,erwartet",
    [
        ("de,en", ["de", "en"]),
        ("de, en , fr", ["de", "en", "fr"]),
        ("de", ["de"]),
        ('["de","en"]', ["de", "en"]),
        ("", ["de", "en"]),  # leer = Voreinstellung
    ],
)
def test_listenfelder_nehmen_beide_schreibweisen(monkeypatch, wert, erwartet):
    """Kommaliste ist die Form, die ein Mensch in ein Unraid-Feld tippt.
    JSON muss weiter gehen, damit bestehende .env-Dateien nicht brechen."""
    if wert:
        monkeypatch.setenv("YTA_SUBTITLE_LANGUAGES", wert)
        monkeypatch.setenv("YTA_CORS_ORIGINS", wert.replace("de", "http://a").replace("en", "http://b"))
    s = Settings()
    assert s.subtitle_languages == erwartet


def test_leere_variablen_bedeuten_nicht_gesetzt(monkeypatch: pytest.MonkeyPatch):
    """Ein leeres Feld im Template darf nicht anders wirken als ein fehlendes.

    Der Anlass: ``YTA_YTDLP_COOKIES_FILE`` hat im Template die Voreinstellung
    "". Daraus wurde ``Path(".")``, und weil ein Path immer wahr ist, hat
    yt-dlp anschliessend das Arbeitsverzeichnis als Cookie-Datei zu lesen
    versucht. Jeder Versuch, einen Kanal hinzuzufuegen, endete mit einem
    Serverfehler - aber nur im Container, denn lokal ist die Variable gar
    nicht gesetzt.
    """
    for name in (
        "YTA_YTDLP_COOKIES_FILE",
        "YTA_YTDLP_FORMAT",
        "YTA_YTDLP_RATELIMIT",
        # Die beiden Notausgaenge gegen die Bot-Pruefung stehen im Template
        # ebenfalls leer - und ein leerer Zahlenwert haette den Dienst gar
        # nicht erst starten lassen.
        "YTA_YTDLP_SLEEP_REQUESTS",
        "YTA_YTDLP_PLAYER_CLIENTS",
    ):
        monkeypatch.setenv(name, "")
    s = Settings()
    assert s.ytdlp_cookies_file is None
    assert s.ytdlp_format is None
    assert s.ytdlp_ratelimit is None
    assert s.ytdlp_sleep_requests == 0.0
    assert s.ytdlp_player_clients == []


def test_leere_zahlenvariable_verhindert_den_start_nicht(monkeypatch: pytest.MonkeyPatch):
    """Dieselbe Ursache, nur frueher sichtbar: Ein geleertes Zahlenfeld haette
    den Dienst gar nicht erst hochkommen lassen."""
    monkeypatch.setenv("YTA_AV1_CRF", "")
    monkeypatch.setenv("YTA_ARCHIVE_MIN_HEIGHT", "")
    monkeypatch.setenv("YTA_HOT_TTL_HOURS", "")
    s = Settings()
    assert s.av1_crf == 30
    assert s.archive_min_height == 1080
    assert s.hot_ttl_hours == 24.0
