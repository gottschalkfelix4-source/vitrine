"""Prozessweite Zwangspause, wenn YouTube die IP-Adresse abweist.

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
aufzuhoeren. Eine Abweisung dieser Art gilt fuer die IP-Adresse, nicht fuer das
Video - das naechste Video traefe garantiert auf dieselbe Wand. Deshalb ist die
Pause hier prozessweit und nicht je Auftrag.

Aufbau bewusst wie :mod:`app.services.abbruch`: ein Stueck gemeinsamer Zustand
im Speicher, keine Datenbank. Er muss einen Neustart nicht ueberleben - beim
Hochfahren ist eine abgelaufene Sperre der Normalfall, und eine noch laufende
meldet sich beim ersten Versuch von selbst wieder.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import timedelta

from app.models import utcnow

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

_sperre = threading.Lock()
#: Ende der laufenden Pause auf der monotonen Uhr. 0 = keine Pause.
_bis = 0.0
#: Welche Stufe zuletzt gegriffen hat. 0 = noch keine.
_stufe = 0
_grund = ""


def melden(grund: str) -> float:
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
    global _bis, _stufe, _grund
    with _sperre:
        jetzt = time.monotonic()
        if _bis > jetzt:
            # Wir sitzen die Pause ohnehin schon ab - nur noch mitzaehlen.
            return _bis - jetzt
        _stufe = min(_stufe + 1, len(STUFEN_S))
        dauer = STUFEN_S[_stufe - 1]
        _bis = jetzt + dauer
        _grund = grund
    log.warning(
        "YouTube weist ab (Stufe %d): %s - Netzauftraege pausieren %.0f Minuten",
        _stufe, grund, dauer / 60,
    )
    return dauer


def hinweis(rest_s: float) -> str:
    """Die Meldung, die der Nutzer bei einer Abweisung sieht.

    Bewusst ohne den englischen Originaltext von yt-dlp: "Sign in to confirm
    you're not a bot. Use --cookies-from-browser ..." liest sich wie ein
    Fehler, den man selbst verbockt hat, und nennt einen Schalter, den es in
    dieser Oberflaeche gar nicht gibt. Der volle Wortlaut steht im Log.
    """
    return f"YouTube weist gerade ab - neuer Versuch in {rest_s / 60:.0f} Minuten"


def entwarnung() -> None:
    """Hebt Pause und Stufe auf - nach einem geglueckten Netzauftrag.

    Das Zuruecksetzen der Stufe ist wichtig, damit eine einzelne Abweisung
    Wochen spaeter nicht mit einer Stunde Pause beginnt, nur weil vor einem
    halben Jahr schon einmal eine war.
    """
    global _bis, _stufe, _grund
    with _sperre:
        if _bis == 0.0 and _stufe == 0:
            return
        _bis, _stufe, _grund = 0.0, 0, ""
    log.info("YouTube antwortet wieder - Pause aufgehoben")


def wartezeit() -> float:
    """Verbleibende Pause in Sekunden, 0 wenn frei."""
    with _sperre:
        return max(0.0, _bis - time.monotonic())


def zuruecksetzen() -> None:
    """Setzt den Zustand hart zurueck. Fuer Tests und einen Neustart."""
    global _bis, _stufe, _grund
    with _sperre:
        _bis, _stufe, _grund = 0.0, 0, ""


def zustand() -> dict[str, object]:
    """Auskunft fuer die Oberflaeche.

    Der Endzeitpunkt wird aus der verbleibenden Dauer errechnet statt
    gespeichert: Gerechnet wird mit der monotonen Uhr, damit eine Zeitumstellung
    oder ein NTP-Sprung die Pause nicht verkuerzt oder verewigt. Fuer die
    Anzeige braucht es aber eine echte Uhrzeit.
    """
    rest = wartezeit()
    with _sperre:
        stufe, grund = _stufe, _grund
    return {
        "pausiert": rest > 0,
        "rest_s": round(rest),
        "bis": (utcnow() + timedelta(seconds=rest)).isoformat() if rest > 0 else None,
        "stufe": stufe,
        "grund": grund or None,
    }
