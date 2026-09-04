"""Zwangspause, wenn YouTube eine Adresse abweist - je Ausgang gefuehrt.

Der Anlass ist ein konkreter Schaden. Bei einem Kanal mit rund 1800 offenen
Videos hat YouTube nach etwa fuenfzig Downloads die Notbremse gezogen und auf
jede weitere Anfrage geantwortet: "Sign in to confirm you're not a bot." Ohne
diese Bremse hier passierte danach Folgendes: Der naechste Auftrag lief sofort
los, bekam dieselbe Antwort, wurde als gescheitert vermerkt - und so weiter,
zwei Straenge parallel, ein Video je Sekunde. Binnen einer halben Stunde waere
die gesamte Warteschlange abgebrannt gewesen, jedes Video rot, jedes mit
hochgezaehltem Versuchszaehler, und die Sperre haette sich durch das
Dauerfeuer immer weiter verlaengert.

Die Antwort darauf ist nicht, es schneller nochmal zu versuchen, sondern
aufzuhoeren. Eine Abweisung dieser Art gilt der IP-Adresse, nicht dem Video -
das naechste Video traefe ueber dieselbe Adresse garantiert auf dieselbe Wand.

Genau daran haengt die Buchfuehrung: Gezaehlt wird **je Ausgang**, nicht je
Prozess. Solange es nur die Direktverbindung gab, war das dasselbe. Mit
mehreren WireGuard-Tunneln ist es das nicht mehr: Wird Tunnel 2 abgewiesen,
sagt das ueber Tunnel 3 nichts aus. Ein prozessweiter Halt waere dort der
teuerste denkbare Fehler - er wuerfe genau die Bandbreite weg, fuer die die
Tunnel eingerichtet wurden. Pausiert wird deshalb erst, wenn **kein** Ausgang
mehr frei ist.

Aufbau bewusst wie :mod:`app.services.abbruch`: ein Stueck gemeinsamer Zustand
im Speicher, keine Datenbank. Er muss einen Neustart nicht ueberleben - beim
Hochfahren ist eine abgelaufene Sperre der Normalfall, und eine noch laufende
meldet sich beim ersten Versuch von selbst wieder.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import timedelta

from app.models import utcnow
from app.services.ausgang import DIREKT

log = logging.getLogger(__name__)

#: Die Leiter der Wartezeiten in Sekunden. Jede weitere Abweisung, nachdem eine
#: Pause abgelaufen war, ruckt eine Stufe hoch; die letzte gilt dann dauerhaft.
#:
#: Die Werte sind nicht geraten. Eine Gastsperre bei YouTube haelt
#: erfahrungsgemaess zwischen einigen Minuten und rund einer Stunde an. Fuenf
#: Minuten sind kurz genug, dass ein einmaliger Ausrutscher kaum auffaellt;
#: eine Stunde ist lang genug, dass auch eine harte Sperre abgelaufen ist,
#: bevor wieder angeklopft wird.
STUFEN_S: tuple[float, ...] = (300.0, 900.0, 1800.0, 3600.0)


@dataclass(slots=True)
class _Sperre:
    """Der Zustand eines einzelnen Ausgangs."""

    #: Ende der laufenden Pause auf der monotonen Uhr. 0 = keine Pause.
    bis: float = 0.0
    #: Welche Stufe zuletzt gegriffen hat. 0 = noch keine.
    stufe: int = 0
    grund: str = ""


_sperre = threading.Lock()
#: Nur die auffaellig gewordenen Ausgaenge stehen hier. Ein Ausgang ohne
#: Eintrag ist frei - so muss die Buchfuehrung die Liste der Tunnel nicht
#: kennen und nicht nachgefuehrt werden, wenn einer dazukommt oder wegfaellt.
_zustaende: dict[str, _Sperre] = {}


def _ausgang(ausgang: str | None) -> str:
    """Der gemeinte Ausgang - ohne Angabe der des aufrufenden Strangs.

    Die Vorgabe ist wichtiger, als sie aussieht: Die Bearbeiter melden eine
    Abweisung dort, wo sie sie bekommen haben, und muessen sich dafuer nicht
    merken, ueber welchen Tunnel sie gerade arbeiten. Der Arbeiterstrang hat
    das beim Holen des Auftrags festgelegt.
    """
    if ausgang is not None:
        return ausgang
    from app.services import ausgang as ausgang_modul

    return ausgang_modul.aktiv().id


def melden(grund: str, ausgang: str | None = None) -> float:
    """Meldet eine Abweisung und liefert die Dauer der Pause in Sekunden.

    Mehrfachmeldungen waehrend einer laufenden Pause ruecken die Stufe
    ausdruecklich **nicht** weiter. Das ist der Punkt, an dem eine naive
    Umsetzung kippt: Bei mehreren parallelen Downloadstraengen laufen zwei bis
    vier Auftraege gleichzeitig in dieselbe Wand, und jeder meldet sie. Wuerde
    jede Meldung hochstufen, waere nach einer einzigen Sperre sofort die
    Hoechststufe erreicht - eine Stunde Stillstand wegen eines Ausrutschers.

    Erst eine Abweisung, die eine bereits **abgelaufene** Pause folgen laesst,
    zaehlt als neuer Versuch und stuft hoch.
    """
    name = _ausgang(ausgang)
    with _sperre:
        zustand = _zustaende.setdefault(name, _Sperre())
        jetzt = time.monotonic()
        if zustand.bis > jetzt:
            # Wir sitzen die Pause ohnehin schon ab - nur noch mitzaehlen.
            return zustand.bis - jetzt
        zustand.stufe = min(zustand.stufe + 1, len(STUFEN_S))
        dauer = STUFEN_S[zustand.stufe - 1]
        zustand.bis = jetzt + dauer
        zustand.grund = grund
        stufe = zustand.stufe
    log.warning(
        "YouTube weist %s ab (Stufe %d): %s - dieser Ausgang pausiert %.0f Minuten",
        name, stufe, grund, dauer / 60,
    )
    return dauer


def hinweis(rest_s: float, ausgang: str | None = None) -> str:
    """Die Meldung, die der Nutzer bei einer Abweisung sieht.

    Bewusst ohne den englischen Originaltext von yt-dlp: "Sign in to confirm
    you're not a bot. Use --cookies-from-browser ..." liest sich wie ein
    Fehler, den man selbst verbockt hat, und nennt einen Schalter, den es in
    dieser Oberflaeche gar nicht gibt. Der volle Wortlaut steht im Log.

    Genannt wird der betroffene Ausgang, sobald es mehr als einen gibt.
    Andernfalls stuende in der Warteschlange "neuer Versuch in 5 Minuten",
    waehrend nebenan munter weitergeladen wird - was wie ein Widerspruch
    aussaehe, aber genau der Sinn der Sache ist.
    """
    name = _ausgang(ausgang)
    wo = "" if name == DIREKT else f" ueber {name}"
    return f"YouTube weist{wo} gerade ab - neuer Versuch in {rest_s / 60:.0f} Minuten"


def entwarnung(ausgang: str | None = None) -> None:
    """Hebt Pause und Stufe eines Ausgangs auf - nach geglueckter Arbeit.

    Das Zuruecksetzen der Stufe ist wichtig, damit eine einzelne Abweisung
    Wochen spaeter nicht mit einer Stunde Pause beginnt, nur weil vor einem
    halben Jahr schon einmal eine war.
    """
    name = _ausgang(ausgang)
    with _sperre:
        zustand = _zustaende.get(name)
        if zustand is None or (zustand.bis == 0.0 and zustand.stufe == 0):
            return
        _zustaende.pop(name, None)
    log.info("%s antwortet wieder - Pause aufgehoben", name)


def wartezeit(ausgang: str | None = None) -> float:
    """Verbleibende Pause eines Ausgangs in Sekunden, 0 wenn frei."""
    name = _ausgang(ausgang)
    with _sperre:
        zustand = _zustaende.get(name)
        return max(0.0, zustand.bis - time.monotonic()) if zustand else 0.0


def frei(ausgaenge: list[str]) -> list[str]:
    """Die nicht gesperrten unter den genannten Ausgaengen, in der Reihenfolge."""
    jetzt = time.monotonic()
    with _sperre:
        return [
            a for a in ausgaenge
            if (z := _zustaende.get(a)) is None or z.bis <= jetzt
        ]


def kuerzeste_wartezeit(ausgaenge: list[str]) -> float:
    """Wie lange es dauert, bis der erste der genannten Ausgaenge frei wird.

    0, wenn schon einer frei ist. Ohne Ausgaenge ebenfalls 0 - dann gibt es
    nichts zu warten, sondern nichts zu tun, und das ist ein anderer Fall.
    """
    if not ausgaenge:
        return 0.0
    jetzt = time.monotonic()
    with _sperre:
        reste = [
            max(0.0, z.bis - jetzt) if (z := _zustaende.get(a)) else 0.0
            for a in ausgaenge
        ]
    return min(reste)


def zuruecksetzen() -> None:
    """Setzt den Zustand aller Ausgaenge hart zurueck. Fuer Tests und Neustart."""
    with _sperre:
        _zustaende.clear()


def zustand(ausgaenge: list[str] | None = None) -> dict[str, object]:
    """Auskunft fuer die Oberflaeche: Steht das Archiv gerade still?

    Gemeint ist der Gesamtzustand, nicht der eines einzelnen Ausgangs.
    "Pausiert" heisst deshalb: **kein** Ausgang ist frei. Solange auch nur
    einer laedt, laeuft das Archiv, und die Leiste soll keine Pause behaupten.

    Genannt werden dann die Zahlen des Ausgangs, der als naechster frei wird -
    das ist die Auskunft, auf die es wartend ankommt.

    Der Endzeitpunkt wird aus der verbleibenden Dauer errechnet statt
    gespeichert: Gerechnet wird mit der monotonen Uhr, damit eine Zeitumstellung
    oder ein NTP-Sprung die Pause nicht verkuerzt oder verewigt. Fuer die
    Anzeige braucht es aber eine echte Uhrzeit.
    """
    namen = ausgaenge if ausgaenge is not None else [DIREKT]
    jetzt = time.monotonic()
    with _sperre:
        offen = [(a, _zustaende.get(a)) for a in namen]
        rest_je = [
            (a, max(0.0, z.bis - jetzt) if z else 0.0, z) for a, z in offen
        ]
    if not rest_je:
        return {"pausiert": False, "rest_s": 0, "bis": None, "stufe": 0, "grund": None}

    # Der Ausgang, der als naechstes wieder darf. Ist er frei, ist gar nichts
    # pausiert.
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
    """Sperrzustand jedes einzelnen Ausgangs - fuer die VPN-Seite."""
    jetzt = time.monotonic()
    with _sperre:
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
