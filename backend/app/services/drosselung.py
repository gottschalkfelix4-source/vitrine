"""Reaktive YouTube-Sperrpausen je Ausgang, auch über Containerneustarts hinweg.

Die Wartezeiten sind vorsichtige lokale Schutzregeln, keine zugesicherten
YouTube-Grenzen. Eine Abweisung gehört zum verwendeten Ausgang; der Auftrag
bleibt unverändert in der Warteschlange. Ein später eintreffender Erfolg
desselben Ausgangs darf eine laufende Pause nicht aufheben.

Im Prozess zählt die monotone Uhr. Zusätzlich werden UTC-Endzeit und
Eskalationsstufe atomar unter dem Datenverzeichnis gespeichert, damit ein
Neustart weder einen neuen Versuch auslöst noch die Eskalation vergisst.
"""

from __future__ import annotations

import json
import logging
import math
import os
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from app.config import settings
from app.models import utcnow
from app.services.ausgang import DIREKT

log = logging.getLogger(__name__)

# Vorsichtige Wiederholungsabstände nach erneuter Abweisung. Keine Aussage
# darüber, wann YouTube einen bestimmten Ausgang tatsächlich wieder zulässt.
STUFEN_S: tuple[float, ...] = (3600.0, 21600.0, 86400.0, 172800.0)
# Ein einzelner Erfolg direkt nach einer Pause setzt die Leiter nicht zurück.
# Erst nach einem weiteren Tag ohne Abweisung darf erfolgreiche Arbeit entwarnen.
ERHOLUNG_S = 86400.0
DATEINAME = "youtube-cooldowns.json"


@dataclass(slots=True)
class _Sperre:
    bis: float
    bis_utc: float
    erholung_bis: float
    stufe: int
    grund: str


_sperre = threading.Lock()
_zustaende: dict[str, _Sperre] = {}
_geladen_von: Path | None = None


def _pfad() -> Path:
    return settings.data_dir.resolve() / DATEINAME


def _laden() -> None:
    """Lädt einmal je Datenverzeichnis. Aufruf nur unter _sperre."""
    global _geladen_von
    pfad = _pfad()
    if _geladen_von == pfad:
        return
    _zustaende.clear()
    _geladen_von = pfad
    try:
        daten = json.loads(pfad.read_text(encoding="utf-8"))
        if not isinstance(daten, dict) or daten.get("version") != 1 or not isinstance(daten.get("ausgaenge"), dict):
            raise ValueError("unbekanntes Format")
    except FileNotFoundError:
        return
    except (OSError, ValueError) as e:
        log.error("Gespeicherte YouTube-Sperrpausen konnten nicht gelesen werden (%s): %s", pfad, e)
        return

    jetzt, utc = time.monotonic(), time.time()
    for name, roh in daten["ausgaenge"].items():
        try:
            if not isinstance(name, str) or not name or not isinstance(roh, dict):
                raise ValueError("ungültiger Ausgang")
            stufe = roh["stufe"]
            bis_utc = float(roh["bis_utc"])
            if isinstance(stufe, bool) or not isinstance(stufe, int) or not 1 <= stufe <= len(STUFEN_S):
                raise ValueError("ungültige Stufe")
            if not math.isfinite(bis_utc):
                raise ValueError("ungültiger Zeitpunkt")
            # Bei einer rückwärts korrigierten Systemuhr keine längere Pause
            # als die betreffende Stufe rekonstruieren.
            rest = min(STUFEN_S[stufe - 1], max(0.0, bis_utc - utc))
            erholung = min(STUFEN_S[stufe - 1] + ERHOLUNG_S, max(0.0, bis_utc + ERHOLUNG_S - utc))
            _zustaende[name] = _Sperre(
                bis=jetzt + rest, bis_utc=bis_utc, erholung_bis=jetzt + erholung,
                stufe=stufe, grund=str(roh.get("grund", ""))[:1000],
            )
        except (KeyError, TypeError, ValueError):
            log.warning("Ungültiger Eintrag einer YouTube-Sperrpause für %s wurde ignoriert", name)


def _speichern() -> None:
    """Schreibt ein vollständiges Abbild atomar. Aufruf nur unter _sperre."""
    pfad = _pfad()
    temporaer: Path | None = None
    try:
        pfad.parent.mkdir(parents=True, exist_ok=True)
        daten = {
            "version": 1,
            "ausgaenge": {
                name: {"bis_utc": z.bis_utc, "stufe": z.stufe, "grund": z.grund}
                for name, z in _zustaende.items()
            },
        }
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=pfad.parent,
            prefix=f".{DATEINAME}.", suffix=".tmp", delete=False,
        ) as datei:
            temporaer = Path(datei.name)
            json.dump(daten, datei, ensure_ascii=False, allow_nan=False)
            datei.flush()
            os.fsync(datei.fileno())
        os.replace(temporaer, pfad)
    except OSError:
        # Die laufende Pause bleibt im RAM wirksam, auch wenn der Datenträger
        # gerade nicht beschreibbar ist. Die fehlende Dauerhaftigkeit ist sichtbar.
        log.exception("YouTube-Sperrpausen konnten nicht dauerhaft gespeichert werden: %s", pfad)
    finally:
        if temporaer is not None:
            try:
                temporaer.unlink(missing_ok=True)
            except OSError:
                log.warning("Temporäre Sperrdatei konnte nicht entfernt werden: %s", temporaer)


def _ausgang(ausgang: str | None) -> str:
    if ausgang is not None:
        return ausgang
    from app.services import ausgang as ausgang_modul

    return ausgang_modul.aktiv().id


def melden(grund: str, ausgang: str | None = None) -> float:
    """Meldet eine Abweisung; parallele Meldungen verlängern eine Pause nicht."""
    name = _ausgang(ausgang)
    with _sperre:
        _laden()
        jetzt = time.monotonic()
        vorher = _zustaende.get(name)
        if vorher and vorher.bis > jetzt:
            return vorher.bis - jetzt
        stufe = min((vorher.stufe if vorher else 0) + 1, len(STUFEN_S))
        dauer = STUFEN_S[stufe - 1]
        _zustaende[name] = _Sperre(
            bis=jetzt + dauer, bis_utc=time.time() + dauer,
            erholung_bis=jetzt + dauer + ERHOLUNG_S, stufe=stufe, grund=str(grund)[:1000],
        )
        _speichern()
    log.warning(
        "YouTube weist %s ab (Stufe %d): %s - dieser Ausgang pausiert %.0f Stunden",
        name, stufe, grund, dauer / 3600,
    )
    return dauer


def hinweis(rest_s: float, ausgang: str | None = None) -> str:
    """Lesbare Wartezeit; der vollständige Ablehnungsgrund steht im Log."""
    name = _ausgang(ausgang)
    wo = "" if name == DIREKT else f" ueber {name}"
    if rest_s >= 3600:
        zahl = math.ceil(rest_s / 3600)
        dauer = f"{zahl} {'Stunde' if zahl == 1 else 'Stunden'}"
    else:
        zahl = max(1, math.ceil(rest_s / 60))
        dauer = f"{zahl} {'Minute' if zahl == 1 else 'Minuten'}"
    return f"YouTube weist{wo} gerade ab - neuer Versuch in {dauer}"


def entwarnung(ausgang: str | None = None) -> None:
    """Setzt die Leiter nur nach abgelaufener Pause und Erholungsfenster zurück.

    Ein vor der Abweisung gestarteter paralleler Auftrag kann noch erfolgreich
    enden. Ein solcher Erfolg ist keine Erlaubnis für neue YouTube-Anfragen.
    """
    name = _ausgang(ausgang)
    with _sperre:
        _laden()
        zustand = _zustaende.get(name)
        if zustand is None or time.monotonic() < zustand.erholung_bis:
            return
        _zustaende.pop(name)
        _speichern()
    log.info("%s nach dem Erholungsfenster erfolgreich - Eskalationsstufe zurückgesetzt", name)


def wartezeit(ausgang: str | None = None) -> float:
    name = _ausgang(ausgang)
    with _sperre:
        _laden()
        zustand = _zustaende.get(name)
        return max(0.0, zustand.bis - time.monotonic()) if zustand else 0.0


def frei(ausgaenge: list[str]) -> list[str]:
    """Die nicht gesperrten Ausgänge in der übergebenen Reihenfolge."""
    with _sperre:
        _laden()
        jetzt = time.monotonic()
        return [a for a in ausgaenge if (z := _zustaende.get(a)) is None or z.bis <= jetzt]


def kuerzeste_wartezeit(ausgaenge: list[str]) -> float:
    """0, sobald ein Ausgang frei ist; ohne Ausgänge gibt es keinen Wartezeitwert."""
    if not ausgaenge:
        return 0.0
    with _sperre:
        _laden()
        jetzt = time.monotonic()
        return min(max(0.0, z.bis - jetzt) if (z := _zustaende.get(a)) else 0.0 for a in ausgaenge)


def zuruecksetzen() -> None:
    """Vergisst nur den RAM-Cache; der nächste Zugriff lädt die gespeicherten Pausen."""
    global _geladen_von
    with _sperre:
        _zustaende.clear()
        _geladen_von = None


def zustand(ausgaenge: list[str] | None = None) -> dict[str, object]:
    """Gesamtzustand: pausiert erst, wenn keiner der genannten Ausgänge frei ist."""
    namen = ausgaenge if ausgaenge is not None else [DIREKT]
    with _sperre:
        _laden()
        jetzt = time.monotonic()
        rest_je = [
            (a, max(0.0, z.bis - jetzt) if (z := _zustaende.get(a)) else 0.0, z)
            for a in namen
        ]
    if not rest_je:
        return {"pausiert": False, "rest_s": 0, "bis": None, "stufe": 0, "grund": None}

    name, rest, sperre = min(rest_je, key=lambda e: e[1])
    return {
        "pausiert": rest > 0,
        "rest_s": round(rest),
        "bis": (utcnow() + timedelta(seconds=rest)).isoformat() if rest > 0 else None,
        "stufe": sperre.stufe if sperre and rest > 0 else 0,
        "grund": (sperre.grund or None) if sperre and rest > 0 else None,
        "ausgang": name if rest > 0 else None,
    }


def zustand_je_ausgang(ausgaenge: list[str]) -> dict[str, dict[str, object]]:
    """Sperrzustand je Ausgang; die Eskalationsstufe bleibt nach Ablauf sichtbar."""
    with _sperre:
        _laden()
        jetzt = time.monotonic()
        roh = {a: _zustaende.get(a) for a in ausgaenge}
    aus: dict[str, dict[str, object]] = {}
    for name, z in roh.items():
        rest = max(0.0, z.bis - jetzt) if z else 0.0
        aus[name] = {
            "gesperrt": rest > 0,
            "rest_s": round(rest),
            "bis": (utcnow() + timedelta(seconds=rest)).isoformat() if rest > 0 else None,
            "stufe": z.stufe if z else 0,
            "grund": (z.grund or None) if z and rest > 0 else None,
        }
    return aus
