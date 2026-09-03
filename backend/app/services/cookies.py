"""Die Cookie-Datei fuer yt-dlp: pruefen, ablegen, beurteilen.

Warum das ein eigenes Stueck Software ist und nicht bloss ein Pfad in einer
Umgebungsvariablen:

YouTube weist Gastzugriffe ab einer gewissen Rate ab ("Sign in to confirm
you're not a bot"). Ein angemeldeter Zugriff hat ein deutlich groesseres
Budget, und angemeldet heisst bei yt-dlp ausschliesslich: Cookies. Eine
Anmeldung mit Konto und Passwort gibt es nicht mehr, und OAuth auch nicht -
yt-dlp lehnt beides ausdruecklich ab (siehe ``_perform_login`` im
YouTube-Extractor).

Damit haengt alles an einer Textdatei, die der Nutzer aus seinem Browser
exportiert. Und genau daran scheitert es reihenweise, auf drei Arten, die alle
gleich aussehen - naemlich nach gar nichts, bis der naechste Download kommt:

1. **Falsches Format.** Viele Erweiterungen exportieren JSON. Ohne die
   Kopfzeile ``# Netscape HTTP Cookie File`` wirft Pythons MozillaCookieJar
   beim Laden, yt-dlp verpackt das als ``CookieLoadError`` - und dann scheitert
   *jeder* Aufruf, auch das blosse Auflisten eines Kanals.
2. **Abgemeldet exportiert.** Die Datei ist formal in Ordnung, enthaelt aber
   keine Anmeldung. Sie wirkt dann exakt wie keine Datei, ohne jeden Hinweis.
3. **Rotiert.** YouTube tauscht die Sitzungsschluessel aus, sobald man sich im
   selben Browser weiterbewegt. Die Datei von gestern ist tot, sieht aber
   unveraendert aus.

Der Assistent macht diese drei Faelle sichtbar, bevor sie Downloads kosten.

Zur Beurteilung wird bewusst yt-dlps eigener Lader benutzt statt einer
nachgebauten Pruefung: Was er nicht laden kann, ist kaputt - das ist die
einzige Wahrheit, auf die es ankommt. Nachgebaute Pruefungen laufen mit der
Zeit auseinander und nehmen dann Dateien an, an denen der Download scheitert.
"""

from __future__ import annotations

import contextlib
import logging
import os
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from http.cookiejar import LoadError
from pathlib import Path

from yt_dlp.cookies import YoutubeDLCookieJar

from app.config import settings

log = logging.getLogger(__name__)

#: Die Kopfzeile, die Pythons MozillaCookieJar in der ERSTEN Zeile sehen will
#: (``NETSCAPE_MAGIC_RGX``). Fehlt sie, ist die Datei unbrauchbar - unabhaengig
#: vom Inhalt.
KOPFZEILE = "# Netscape HTTP Cookie File"

#: yt-dlps Kriterium fuer "angemeldet", woertlich uebernommen aus
#: ``YoutubeBaseInfoExtractor._has_auth_cookies``: ``LOGIN_INFO`` muss da sein,
#: dazu mindestens einer der drei SAPISID-Schluessel.
#:
#: Das ist keine Auslegung, sondern genau die Bedingung, die im Betrieb
#: darueber entscheidet, ob YouTube uns als angemeldet behandelt. Deshalb wird
#: hier dasselbe geprueft und nicht etwa "irgendein Cookie ist vorhanden".
PFLICHT_COOKIE = "LOGIN_INFO"
SAPISID_COOKIES = ("SAPISID", "__Secure-1PAPISID", "__Secure-3PAPISID")

#: Ab wann gewarnt wird. Cookies laufen aus, und ein Archiv, das naechtelang
#: durchlaeuft, soll nicht am Montag stillstehen, weil es am Sonntag soweit war.
WARNFRIST_TAGE = 7

#: Obergrenze fuer den Upload. Eine Cookie-Datei ist ein paar Kilobyte gross;
#: alles darueber ist ein Versehen und muss gar nicht erst geparst werden.
MAX_BYTES = 1024 * 1024


def standardpfad() -> Path:
    """Wohin der Assistent die hochgeladene Datei legt."""
    return settings.data_dir / "cookies.txt"


def aktiver_pfad() -> Path | None:
    """Die Datei, mit der tatsaechlich gearbeitet wird - oder None.

    Rangfolge: Eine ausdruecklich gesetzte Umgebungsvariable gewinnt, damit wer
    seine Datei selbst ins Volume legt, das weiterhin tun kann. Sonst gilt die
    ueber die Oberflaeche hochgeladene.
    """
    eigen = settings.ytdlp_cookies_file
    if eigen:
        return eigen if eigen.is_file() else None
    standard = standardpfad()
    return standard if standard.is_file() else None


@dataclass(slots=True)
class Befund:
    """Das Urteil ueber eine Cookie-Datei."""

    brauchbar: bool
    meldung: str
    angemeldet: bool = False
    #: Wann der erste der Anmelde-Cookies ablaeuft.
    laeuft_ab: datetime | None = None
    anzahl: int = 0
    gefunden: list[str] = field(default_factory=list)

    def als_dict(self) -> dict[str, object]:
        rest = None
        if self.laeuft_ab is not None:
            rest = max(0, int((self.laeuft_ab - datetime.now(UTC)).total_seconds()))
        return {
            "brauchbar": self.brauchbar,
            "meldung": self.meldung,
            "angemeldet": self.angemeldet,
            "laeuft_ab": self.laeuft_ab.isoformat() if self.laeuft_ab else None,
            "rest_s": rest,
            "bald_abgelaufen": rest is not None and rest < WARNFRIST_TAGE * 86400,
            "anzahl": self.anzahl,
            "gefunden": self.gefunden,
        }


def _vorpruefung(roh: bytes) -> str | None:
    """Faengt die zwei haeufigsten Missgriffe mit einer lesbaren Meldung ab.

    yt-dlp bemerkt beide auch, aber nur mit "failed to load cookies" - einer
    Meldung, aus der niemand ableiten kann, was zu tun ist.
    """
    if not roh.strip():
        return "Die Datei ist leer."
    try:
        text = roh.decode("utf-8-sig")
    except UnicodeDecodeError:
        return "Die Datei ist keine Textdatei. Erwartet wird eine cookies.txt im Netscape-Format."

    erste = next((z for z in text.splitlines() if z.strip()), "")
    if erste.lstrip()[:1] in ("[", "{", '"'):
        return (
            "Das ist eine JSON-Datei. Gebraucht wird das Netscape-Format - in den "
            "gaengigen Erweiterungen heisst die Auswahl 'Netscape' oder 'cookies.txt'."
        )
    if not erste.startswith("#") or "HTTP Cookie File" not in erste:
        return (
            f"Die erste Zeile muss '{KOPFZEILE}' lauten. Ohne sie lehnt yt-dlp die "
            "Datei ab, und dann scheitert jeder Download - nicht nur dieser eine."
        )
    return None


def _pruefen_datei(pfad: Path) -> Befund:
    """Beurteilt eine abgelegte Datei mit yt-dlps eigenem Lader."""
    jar = YoutubeDLCookieJar(str(pfad))
    try:
        jar.load()
    except (LoadError, OSError, ValueError) as e:
        return Befund(False, f"yt-dlp kann die Datei nicht lesen: {e}")

    # Nur die YouTube-Domaenen zaehlen. Ein Export "alle Cookies" bringt
    # hunderte fremde mit; die stoeren nicht, sagen aber auch nichts.
    youtube = {c.name: c for c in jar if c.domain and "youtube.com" in c.domain}
    if not youtube:
        return Befund(
            False,
            "Die Datei enthaelt keine Cookies von youtube.com. Wurde sie auf der "
            "richtigen Seite exportiert?",
            anzahl=len(list(jar)),
        )

    hat_login = PFLICHT_COOKIE in youtube
    sapisid = [n for n in SAPISID_COOKIES if n in youtube]
    gefunden = ([PFLICHT_COOKIE] if hat_login else []) + sapisid

    if not (hat_login and sapisid):
        fehlt = []
        if not hat_login:
            fehlt.append(PFLICHT_COOKIE)
        if not sapisid:
            fehlt.append("eines von " + ", ".join(SAPISID_COOKIES))
        return Befund(
            False,
            "Die Datei ist formal in Ordnung, enthaelt aber keine Anmeldung - es fehlt "
            + " und ".join(fehlt)
            + ". So wirkt sie genau wie gar keine Datei. Beim Export muss man bei "
            "YouTube angemeldet sein.",
            anzahl=len(youtube),
            gefunden=gefunden,
        )

    # Der frueheste Ablauf unter den Anmelde-Cookies bestimmt die Haltbarkeit:
    # Faellt einer davon weg, gilt die Sitzung als abgemeldet.
    fristen = [
        datetime.fromtimestamp(youtube[n].expires, tz=UTC)
        for n in gefunden
        if youtube[n].expires
    ]
    ablauf = min(fristen) if fristen else None
    if ablauf is not None and ablauf <= datetime.now(UTC):
        return Befund(
            False,
            "Die Anmeldung in dieser Datei ist bereits abgelaufen. Sie muss neu "
            "exportiert werden.",
            laeuft_ab=ablauf,
            anzahl=len(youtube),
            gefunden=gefunden,
        )

    return Befund(
        True,
        "Angemeldete Sitzung erkannt.",
        angemeldet=True,
        laeuft_ab=ablauf,
        anzahl=len(youtube),
        gefunden=gefunden,
    )


def pruefen() -> Befund:
    """Beurteilt die gerade aktive Datei."""
    pfad = aktiver_pfad()
    if pfad is None:
        return Befund(False, "Keine Cookie-Datei hinterlegt.")
    return _pruefen_datei(pfad)


def speichern(roh: bytes) -> Befund:
    """Prueft den Upload und legt ihn nur ab, wenn er etwas taugt.

    Erst pruefen, dann ersetzen, ist hier keine Umstaendlichkeit: Eine kaputte
    Datei laesst nicht nur diesen einen Download scheitern, sondern jeden
    Aufruf an yt-dlp - auch das Auflisten eines Kanals. Eine funktionierende
    gegen eine kaputte zu tauschen, weil jemand die falsche Datei erwischt hat,
    waere der schlechteste denkbare Ausgang.
    """
    if len(roh) > MAX_BYTES:
        return Befund(
            False,
            f"Die Datei ist groesser als {MAX_BYTES // 1024} KB - das ist keine cookies.txt.",
        )
    if (meldung := _vorpruefung(roh)) is not None:
        return Befund(False, meldung)

    ziel = standardpfad()
    ziel.parent.mkdir(parents=True, exist_ok=True)
    # Im selben Verzeichnis schreiben, damit das Ersetzen ein Umbenennen
    # innerhalb eines Dateisystems bleibt und damit unteilbar.
    fd, name = tempfile.mkstemp(dir=str(ziel.parent), prefix=".cookies-", suffix=".txt")
    vorlaeufig: Path | None = Path(name)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(roh)
        befund = _pruefen_datei(vorlaeufig)
        if not befund.brauchbar:
            return befund
        # 0600: Die Datei ist ein Sitzungsschluessel. Auf einem NAS-Share liegt
        # sie in einem Verzeichnis, in das oft mehr Leute sehen als gedacht.
        with contextlib.suppress(OSError):
            vorlaeufig.chmod(0o600)
        vorlaeufig.replace(ziel)
        vorlaeufig = None  # gehoert jetzt dem Ziel, nicht mehr aufraeumen
        log.info("Cookie-Datei ersetzt (%d YouTube-Cookies)", befund.anzahl)
        return befund
    finally:
        if vorlaeufig is not None:
            vorlaeufig.unlink(missing_ok=True)


def entfernen() -> bool:
    """Loescht die ueber die Oberflaeche hochgeladene Datei."""
    ziel = standardpfad()
    if not ziel.is_file():
        return False
    ziel.unlink()
    log.info("Cookie-Datei entfernt")
    return True
