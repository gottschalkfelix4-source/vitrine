"""HTTP-Bereichsanforderungen nach RFC 9110, Abschnitt 14.

Ohne korrekte Range-Behandlung kann der Player nicht springen und laedt bei
jedem Spulen das Video von vorn. Die Regeln sind kleinteilig genug, dass sich
das Parsen als eigene, reine Funktion lohnt - so laesst es sich ohne HTTP-Stack
durchtesten.

Bewusst nicht unterstuetzt: Mehrfachbereiche (``bytes=0-99,200-299``). Kein
Browser fordert die fuer Videowiedergabe an, und die Antwort waere ein
mehrteiliger Body. Solche Anforderungen werden wie eine Anfrage ohne Bereich
behandelt - laut Norm zulaessig.
"""

from __future__ import annotations

from dataclasses import dataclass

_PREFIX = "bytes="


@dataclass(frozen=True, slots=True)
class ByteRange:
    """Ein aufgeloester, halboffener Bereich: ``start`` bis einschliesslich ``end``."""

    start: int
    end: int

    @property
    def length(self) -> int:
        return self.end - self.start + 1

    def content_range(self, total: int) -> str:
        return f"bytes {self.start}-{self.end}/{total}"


class UnsatisfiableRange(ValueError):
    """Der Bereich liegt vollstaendig ausserhalb der Datei -> HTTP 416."""


def parse_range(header: str | None, size: int) -> ByteRange | None:
    """Loest einen ``Range``-Kopf gegen eine bekannte Dateigroesse auf.

    Liefert ``None``, wenn die gesamte Datei auszuliefern ist - also wenn kein
    Kopf gesetzt ist oder er nicht verwertbar ist. Wirft
    :class:`UnsatisfiableRange`, wenn ein syntaktisch gueltiger Bereich
    komplett hinter dem Dateiende liegt.
    """
    if not header:
        return None

    header = header.strip()
    if not header.lower().startswith(_PREFIX):
        return None  # andere Einheit als bytes -> ignorieren

    spec = header[len(_PREFIX) :].strip()
    if "," in spec:
        return None  # Mehrfachbereiche: siehe Modulkopf

    if "-" not in spec:
        return None

    roh_start, _, roh_end = spec.partition("-")
    roh_start, roh_end = roh_start.strip(), roh_end.strip()

    # Sonderfall Leerdatei: Jeder Bereich ist unerfuellbar.
    if size == 0:
        raise UnsatisfiableRange("Datei ist leer")

    if not roh_start:
        # Suffixform "bytes=-500": die letzten 500 Bytes.
        if not roh_end.isdigit():
            return None
        anzahl = int(roh_end)
        if anzahl == 0:
            raise UnsatisfiableRange("Suffixlaenge 0")
        start = max(0, size - anzahl)
        return ByteRange(start, size - 1)

    if not roh_start.isdigit():
        return None
    start = int(roh_start)
    if start >= size:
        raise UnsatisfiableRange(f"Start {start} liegt hinter dem Ende {size}")

    if not roh_end:
        # Offene Form "bytes=500-": bis zum Dateiende.
        return ByteRange(start, size - 1)

    if not roh_end.isdigit():
        return None
    end = int(roh_end)
    if end < start:
        return None  # unsinnig -> als "ganze Datei" behandeln
    # Ein zu grosses Ende wird nach Norm auf das Dateiende gekuerzt.
    return ByteRange(start, min(end, size - 1))
