"""Welcher Weg ins Netz gerade benutzt wird.

Bis hierher gab es nur einen: die Adresse des Servers. Damit war "YouTube
weist ab" gleichbedeutend mit "das Archiv steht" - eine Sperre gilt der
IP-Adresse, und es gab nur die eine.

Mit WireGuard-Tunneln gibt es mehrere. Ein Ausgang ist deshalb ab jetzt ein
eigenes Ding mit eigenem Namen, eigener Adresse und eigenem Sperrzustand: die
Direktverbindung, oder einer der eingerichteten Tunnel. Wird einer abgewiesen,
wechselt das Archiv auf den naechsten, statt anzuhalten.

Dieses Modul haelt davon nur das kleinste Stueck: **welcher Ausgang gerade
gilt**, je Arbeiterstrang. Mehr nicht - keine Prozesse, keine Sperrleiter,
keine Datenbank. Das ist Absicht, denn drei Module brauchen diese eine
Auskunft, und wuerde sie in einem davon wohnen, zoege sie die uebrigen
zwei hinter sich her:

* :mod:`app.services.vpn` waehlt den Ausgang aus und startet die Tunnel.
* :mod:`app.services.drosselung` fuehrt Buch, welcher Ausgang gesperrt ist.
* :mod:`app.services.ytdlp` braucht die Proxy-Adresse fuer jeden Aufruf.

Warum ein Thread-lokaler Wert und kein Parameter: Der Ausgang muesste sonst
durch jede Funktion dieser Kette gereicht werden - ``fetch_channel``,
``list_entries``, ``download_video``, ``fetch_video_info``, ``peek_recent`` -
und in jeder neuen wieder. Vergisst man ihn an einer Stelle, laedt genau die
an der Rotation vorbei ueber die eigene Leitung, ohne dass es auffaellt. Der
Arbeiterstrang setzt ihn stattdessen einmal fuer die Dauer eines Auftrags; ein
Strang bearbeitet immer genau einen, und weiter unten gibt es keine Threads
mehr. yt-dlp laedt Fragmente zwar nebenlaeufig, aber innerhalb seiner eigenen
Verbindung, die die Optionen bereits mitbekommen hat.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

#: Kennung der Verbindung ohne Tunnel. Sie ist kein Sonderfall, sondern ein
#: Ausgang wie jeder andere - sonst braeuchte jede Stelle, die mit Ausgaengen
#: rechnet, einen Zweig fuer "VPN ist aus".
DIREKT = "direkt"


@dataclass(frozen=True, slots=True)
class Ausgang:
    """Ein Weg ins Netz.

    ``proxy`` ist die Adresse, die yt-dlp bekommt - ``socks5h://127.0.0.1:...``
    fuer einen Tunnel, ``None`` fuer die Direktverbindung. Das ``h`` ist nicht
    schmueckend: Ohne es loest der Server die Namen selbst auf, und dann geht
    zwar der Verkehr durch den Tunnel, die DNS-Anfrage aber nicht.
    """

    id: str = DIREKT
    name: str = "Direktverbindung"
    proxy: str | None = None

    @property
    def ist_tunnel(self) -> bool:
        return self.proxy is not None


DIREKTER_AUSGANG = Ausgang()

_lokal = threading.local()


def aktiv() -> Ausgang:
    """Der Ausgang dieses Strangs. Ohne Festlegung die Direktverbindung."""
    return getattr(_lokal, "ausgang", DIREKTER_AUSGANG)


def setzen(ausgang: Ausgang) -> None:
    """Legt den Ausgang fuer diesen Strang fest, bis er ersetzt wird."""
    _lokal.ausgang = ausgang


@contextmanager
def benutzen(ausgang: Ausgang) -> Iterator[Ausgang]:
    """Setzt den Ausgang fuer die Dauer eines Auftrags.

    Der vorherige Wert wird wiederhergestellt statt einfach geloescht. Das
    zaehlt: Die Arbeiterstraenge sind langlebig und holen einen Auftrag nach
    dem anderen: Bliebe ein Ausgang haengen, liefe der naechste Auftrag ueber
    einen Tunnel, der laengst abgeschaltet sein kann.
    """
    vorher = aktiv()
    _lokal.ausgang = ausgang
    try:
        yield ausgang
    finally:
        _lokal.ausgang = vorher


def zuruecksetzen() -> None:
    """Vergisst die Festlegung dieses Strangs. Fuer Tests."""
    if hasattr(_lokal, "ausgang"):
        del _lokal.ausgang
