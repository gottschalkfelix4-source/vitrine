"""Gemeinsames, dauerhaftes Anfragebudget fuer das gesamte Archiv.

Die Grenzwerte sind eigene Vorsichtsmassnahmen, keine zugesicherten YouTube-
Kontingente. Webseiten, Player-API und RSS teilen ein gleitendes Stunden- und
24-Stunden-Budget. Videostarts werden zusaetzlich gleichmaessig verteilt.
Mediensegmente und Bilder werden getrennt gezaehlt: Ein langes Video darf nicht
allein wegen seiner Segmentierung mit Hunderten Videoabfragen verwechselt werden.

Die separate SQLite-Datei verhindert verlorene Reservierungen bei parallelen
Arbeitern und bewahrt Zaehler, Pausen und reduziertes Tempo ueber Neustarts.
Weder URLs noch Cookies oder IP-Adressen werden darin gespeichert. Mehrere
Tunnel erhoehen die gemeinsame Quote dieser Archivinstanz nicht.
"""

from __future__ import annotations

import logging
import math
import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from app.config import settings
from app.services import abbruch

log = logging.getLogger(__name__)
STUNDE = 3600.0
TAG = 24 * STUNDE
PAUSEN = (STUNDE, 6 * STUNDE, TAG, 2 * TAG)
_verbindungs_sperre = threading.Lock()


def _pfad() -> Path:
    return settings.data_dir / "youtube-anfragebudget.sqlite3"


class Pause(Exception):
    """Vorsorglich warten; kein Fehlversuch des Videos."""

    def __init__(self, rest_s: float, grund: str | None = None):
        self.rest_s = max(0.0, float(rest_s))
        self.grund = grund or "Das gemeinsame YouTube-Budget ist ausgeschöpft."
        minuten = math.ceil(self.rest_s / 60)
        super().__init__(f"{self.grund} Weiter frühestens in {minuten} {'Minute' if minuten == 1 else 'Minuten'}.")


def _art(url: str) -> str | None:
    try:
        host = (urlsplit(url).hostname or "").lower().rstrip(".")
    except ValueError:
        return None
    for ende in ("googlevideo.com", "ytimg.com", "ggpht.com"):
        if host == ende or host.endswith("." + ende):
            return "medien"
    for ende in ("youtube.com", "youtu.be", "youtube-nocookie.com", "youtubei.googleapis.com"):
        if host == ende or host.endswith("." + ende):
            return "anfragen"
    return None


@contextmanager
def _db() -> Iterator[sqlite3.Connection]:
    # Auch das erstmalige Umschalten auf WAL muss zwischen Threads geordnet
    # sein. BEGIN IMMEDIATE sichert danach die Reservierung auf Dateiebene.
    with _verbindungs_sperre, _datenbank() as db:
        yield db


@contextmanager
def _datenbank() -> Iterator[sqlite3.Connection]:
    db = None
    try:
        pfad = _pfad()
        pfad.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(pfad, timeout=5, isolation_level=None)
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        db.execute("CREATE TABLE IF NOT EXISTS ereignisse (zeit REAL NOT NULL, art TEXT NOT NULL)")
        db.execute("CREATE INDEX IF NOT EXISTS ereignisse_art_zeit ON ereignisse(art, zeit)")
        db.execute("CREATE INDEX IF NOT EXISTS ereignisse_zeit ON ereignisse(zeit)")
        db.execute("CREATE TABLE IF NOT EXISTS zustand (name TEXT PRIMARY KEY, wert REAL NOT NULL)")
        db.execute("BEGIN IMMEDIATE")
        yield db
        db.commit()
    except (OSError, sqlite3.Error) as e:
        # Ein defekter oder nicht beschreibbarer Speicher darf den Schutz nicht
        # unbemerkt ausschalten. Der Auftrag wartet, statt ohne Zaehler zu laden.
        log.error("YouTube-Anfragebudget nicht lesbar/speicherbar: %s", e)
        raise Pause(60, "Der YouTube-Schutz kann seinen Zustand gerade nicht speichern.") from e
    finally:
        if db is not None:
            db.close()


def _wert(db: sqlite3.Connection, name: str) -> float:
    zeile = db.execute("SELECT wert FROM zustand WHERE name = ?", (name,)).fetchone()
    return float(zeile[0]) if zeile else 0.0


def _setzen(db: sqlite3.Connection, name: str, wert: float) -> None:
    db.execute("INSERT INTO zustand VALUES (?, ?) ON CONFLICT(name) DO UPDATE SET wert = excluded.wert", (name, wert))


def _jetzt(db: sqlite3.Connection) -> float:
    # Eine rueckwaerts gestellte Systemuhr darf weder Reservierungen aus der
    # Vergangenheit duplizieren noch eine gespeicherte Pause aufheben.
    return max(time.time(), _wert(db, "uhr"))


def _aufraeumen(db: sqlite3.Connection, jetzt: float) -> None:
    db.execute("DELETE FROM ereignisse WHERE zeit <= ?", (jetzt - TAG,))
    _setzen(db, "uhr", jetzt)


def _grenzen(db: sqlite3.Connection, jetzt: float) -> tuple[dict[str, int], int]:
    stufe = int(_wert(db, "stufe")) if _wert(db, "reduziert_bis") > jetzt else 0
    faktor = 2 ** min(stufe, 3)
    grenzen = {
        "anfragen_stunde": max(1, int(settings.youtube_anfragen_stunde) // faktor),
        "anfragen_tag": max(1, int(settings.youtube_anfragen_tag) // faktor),
        "videos_stunde": max(1, int(settings.youtube_videos_stunde) // faktor),
        "videos_tag": max(1, int(settings.youtube_videos_tag) // faktor),
    }
    return grenzen, faktor


def _fenster(db: sqlite3.Connection, art: str, jetzt: float, grenzen: dict[str, int]) -> tuple[int, int, float, str | None]:
    tag = [float(z[0]) for z in db.execute(
        "SELECT zeit FROM ereignisse WHERE art = ? AND zeit > ? ORDER BY zeit", (art, jetzt - TAG),
    )]
    stunde = [t for t in tag if t > jetzt - STUNDE]
    rest = 0.0
    grund = None
    for zeiten, dauer, ende in ((stunde, STUNDE, "stunde"), (tag, TAG, "tag")):
        limit = grenzen[f"{art}_{ende}"]
        if len(zeiten) >= limit:
            frist = zeiten[-limit] + dauer - jetzt
            if frist > rest:
                rest = frist
                was = "Steueranfragen" if art == "anfragen" else "Downloadversuche"
                zeitraum = "Stundenbudget" if ende == "stunde" else "Budget der letzten 24 Stunden"
                grund = f"{zeitraum} für {was} erreicht."
    return len(stunde), len(tag), rest, grund


def _blick(db: sqlite3.Connection, jetzt: float) -> dict[str, object]:
    grenzen, faktor = _grenzen(db, jetzt)
    ah, at, ar, ag = _fenster(db, "anfragen", jetzt, grenzen)
    vh, vt, vr, vg = _fenster(db, "videos", jetzt, grenzen)
    sperre = max(0.0, _wert(db, "pause_bis") - jetzt)
    abstand = max(0.0, _wert(db, "naechstes_video") - jetzt)
    rest, grund = max([
        (sperre, "YouTube hat Anfragen abgewiesen; das gesamte Archiv legt eine Schutzpause ein."),
        (ar, ag), (vr, vg), (abstand, "Downloads werden gleichmäßig verteilt."),
    ], key=lambda eintrag: eintrag[0])
    medien = db.execute("SELECT count(*) FROM ereignisse WHERE art = 'medien' AND zeit > ?", (jetzt - TAG,)).fetchone()[0]
    return {
        "pausiert": rest > 0,
        "rest_s": math.ceil(rest),
        "bis": datetime.fromtimestamp(jetzt + rest, UTC).isoformat() if rest > 0 else None,
        "grund": grund if rest > 0 else None,
        "anfragen_stunde": ah, "anfragen_tag": at,
        "videos_stunde": vh, "videos_tag": vt,
        **{f"limit_{k}": v for k, v in grenzen.items()},
        "medienanfragen_tag": medien,
        "reduziert": faktor > 1,
    }


def zustand() -> dict[str, object]:
    """Admin-Auskunft; reduzierte Limits sind die tatsaechlich wirksamen Werte."""
    try:
        with _db() as db:
            return _blick(db, _jetzt(db))
    except Pause as e:
        # Die Anzeige muss erreichbar bleiben, wenn gerade die Budgetdatei
        # kaputt ist. Null signalisiert unbekannte Zaehler statt falscher Nullen.
        return {
            "pausiert": True, "rest_s": math.ceil(e.rest_s), "bis": None, "grund": e.grund,
            "anfragen_stunde": None, "anfragen_tag": None, "videos_stunde": None, "videos_tag": None,
            "limit_anfragen_stunde": settings.youtube_anfragen_stunde,
            "limit_anfragen_tag": settings.youtube_anfragen_tag,
            "limit_videos_stunde": settings.youtube_videos_stunde,
            "limit_videos_tag": settings.youtube_videos_tag,
            "medienanfragen_tag": None, "reduziert": False,
        }


def wartezeit(*, nur_anfragen: bool = False) -> float:
    """Vor dem Holen eines Netzauftrags pruefen, ohne zu reservieren.

    Kanal-/Playlistabgleiche brauchen keine Videostartquote. Der Runner kann
    sie mit nur_anfragen=True weiter zulassen, wenn allein Videostarts warten.
    """
    if not nur_anfragen:
        return float(zustand()["rest_s"])
    try:
        with _db() as db:
            jetzt = _jetzt(db)
            grenzen, _ = _grenzen(db, jetzt)
            _, _, rest, _ = _fenster(db, "anfragen", jetzt, grenzen)
            return max(0.0, rest, _wert(db, "pause_bis") - jetzt)
    except Pause as e:
        return e.rest_s


def vor_video() -> None:
    """Einen tatsaechlich geplanten Downloadversuch atomar reservieren."""
    abbruch.pruefen()
    with _db() as db:
        jetzt = _jetzt(db)
        blick = _blick(db, jetzt)
        if blick["pausiert"]:
            raise Pause(float(blick["rest_s"]), str(blick["grund"]))
        _aufraeumen(db, jetzt)
        db.execute("INSERT INTO ereignisse VALUES (?, 'videos')", (jetzt,))
        _setzen(db, "naechstes_video", jetzt + STUNDE / int(blick["limit_videos_stunde"]))


def vor_anfrage(url: str) -> None:
    """Vor jedem HTTP-Versuch aufrufen, einschliesslich Wiederholungen.

    Kurze Abstaende werden abbrechbar abgewartet. Stunden-/Tagesbudgets oder
    Sperrpausen geben den Auftrag an die Warteschlange zurueck. Es wird waehrend
    des Wartens weder eine SQLite-Transaktion noch ein anderer Worker blockiert.
    """
    art = _art(url)
    if art is None:
        return
    while True:
        abbruch.pruefen()
        with _db() as db:
            jetzt = _jetzt(db)
            sperre = _wert(db, "pause_bis") - jetzt
            if sperre > 0:
                raise Pause(sperre, "YouTube-Schutzpause für das gesamte Archiv.")
            grenzen, faktor = _grenzen(db, jetzt)
            abstand = 0.0
            if art == "anfragen":
                _, _, rest, grund = _fenster(db, art, jetzt, grenzen)
                if rest > 0:
                    raise Pause(rest, grund)
                abstand = _wert(db, "letzte_anfrage") + settings.youtube_anfrage_abstand * faktor - jetzt
            if abstand <= 0:
                _aufraeumen(db, jetzt)
                db.execute("INSERT INTO ereignisse VALUES (?, ?)", (jetzt, art))
                if art == "anfragen":
                    _setzen(db, "letzte_anfrage", jetzt)
                return
        time.sleep(min(abstand, 0.25))


def abweisung(retry_after_s: float | None = None) -> None:
    """Ein eindeutiges Sperrsignal stoppt alle Ausgaenge und senkt das Tempo.

    Gleichzeitige Meldungen waehrend derselben Pause eskalieren nicht erneut.
    Eine laengere gueltige Retry-After-Vorgabe darf deren Mindestfrist erhoehen.
    Nach der Pause bleiben die Limits fuer weitere 24 Stunden reduziert.
    """
    with _db() as db:
        jetzt = _jetzt(db)
        server_pause = float(retry_after_s or 0)
        server_pause = min(server_pause, 365 * TAG) if math.isfinite(server_pause) and server_pause > 0 else 0
        if _wert(db, "pause_bis") > jetzt:
            if jetzt + server_pause > _wert(db, "pause_bis"):
                _setzen(db, "pause_bis", jetzt + server_pause)
                _setzen(db, "reduziert_bis", jetzt + server_pause + TAG)
            return
        vorher = int(_wert(db, "stufe")) if jetzt - _wert(db, "letzte_abweisung") < 7 * TAG else 0
        stufe = min(vorher + 1, len(PAUSEN))
        dauer = max(PAUSEN[stufe - 1], server_pause)
        bis = jetzt + dauer
        _setzen(db, "stufe", stufe)
        _setzen(db, "pause_bis", bis)
        _setzen(db, "reduziert_bis", bis + TAG)
        _setzen(db, "letzte_abweisung", jetzt)
        _setzen(db, "uhr", jetzt)
    log.warning("YouTube-Schutz: alle Netzauftraege pausieren %s Stunden; danach reduzierte Quote", dauer / STUNDE)
