"""Ein einziges Signal fuer "der Dienst faehrt gleich herunter".

Ohne das wird ein laufender Download beim Container-Update hart abgeschossen:
Docker schickt SIGTERM, der Arbeiterstrang steckt aber mitten in yt-dlp und
merkt davon nichts, und nach zehn Sekunden Gnadenfrist kommt SIGKILL. Die
halbe Datei bleibt in einem unbestimmten Zustand liegen, der Auftrag steht
noch auf "laeuft", und beim naechsten Start beginnt alles von vorn.

Der Weg hier ist bewusst einfach gehalten. Es gibt genau ein prozessweites
Ereignis, das beim Herunterfahren gesetzt wird, und zwei Arten, es zu
beachten:

* :func:`pruefen` an Stellen, die ohnehin regelmaessig durchlaufen werden -
  der Fortschritts-Hook von yt-dlp etwa. Dort wird :class:`Abgebrochen`
  geworfen, was die laufende Arbeit sofort verlaesst.
* :func:`laeuft_herunter` fuer Aufrufer, die einen Rueckgabewert brauchen
  statt einer Ausnahme, wie der ffmpeg-Aufruf.

:class:`Abgebrochen` ist ausdruecklich **kein Fehler**. Ein Auftrag, der so
endet, wird nicht als gescheitert vermerkt, sondern wieder eingereiht - sonst
saehe der Nutzer nach jedem Update eine Liste roter Fehlschlaege, die keine
sind.
"""

from __future__ import annotations

import threading

_signal = threading.Event()


class Abgebrochen(Exception):
    """Die Arbeit wurde durch das Herunterfahren unterbrochen.

    Kein Fehlerfall: Der Auftrag wird wieder eingereiht und beim naechsten
    Start fortgesetzt.
    """


def anfordern() -> None:
    """Meldet allen laufenden Arbeiten, dass Schluss ist."""
    _signal.set()


def zuruecksetzen() -> None:
    """Hebt die Anforderung auf. Fuer Tests und einen Neustart im selben
    Prozess."""
    _signal.clear()


def laeuft_herunter() -> bool:
    return _signal.is_set()


def pruefen() -> None:
    """Bricht die laufende Arbeit ab, wenn heruntergefahren wird."""
    if _signal.is_set():
        raise Abgebrochen("Dienst faehrt herunter")
