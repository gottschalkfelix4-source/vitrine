"""Mehrere WireGuard-Tunnel als Ausgaenge - und der Wechsel zwischen ihnen.

Das Problem, das dieses Modul loest, ist nicht "Anonymitaet", sondern
Bandbreite. YouTube zaehlt pro IP-Adresse: Als Gast liegt die Grenze bei rund
300 Videos je Stunde, danach kommt "Sign in to confirm you're not a bot" und
alles steht. Bei einem Erstbestand von tausenden Videos ist das der
bestimmende Engpass - das Archiv wartet dann laenger, als es laedt.

Vier Tunnel sind vier Adressen und damit grob das vierfache Budget. Faellt
einer in die Sperre, wird nicht mehr angehalten, sondern gewechselt.

Wie der Verkehr in den Tunnel kommt - und warum nicht mit wg-quick
--------------------------------------------------------------------

Der naheliegende Weg waere ein echtes WireGuard-Geraet im Container. Er ist
hier falsch, und zwar aus drei Gruenden:

1. Er braucht ``NET_ADMIN`` und ``/dev/net/tun``. Ein Archivdienst, der dafuer
   erweiterte Rechte am Wirtssystem verlangt, ist ein schlechter Tausch - und
   auf Unraid ein zusaetzlicher Handgriff, den niemand vergessen darf.
2. Ein Tunnel als Geraet gilt fuer den **ganzen Prozess**. Genau das ist hier
   nicht gewollt: Vier Downloads sollen gleichzeitig ueber vier verschiedene
   Adressen laufen. Mit Netzwerk-Namensraeumen ginge das theoretisch, aber je
   Thread - und das ist mit Python-Threads und blockierenden Bibliotheken ein
   Minenfeld.
3. Faellt so ein Tunnel aus, ist die Route weg und der Dienst blind, statt
   einfach auf einen anderen Ausgang zu wechseln.

Deshalb laeuft WireGuard hier im Benutzerraum: ``wireproxy`` spricht das
Protokoll selbst und bietet das Ergebnis als SOCKS5-Proxy auf einem lokalen
Port an. Ein Prozess je Tunnel, ein Port je Tunnel, keine erweiterten Rechte,
kein Kernelmodul. yt-dlp bekommt je Auftrag die passende Proxy-Adresse - und
damit ist "welcher Download nimmt welchen Ausgang" eine schlichte Option statt
einer Frage der Netzwerkkonfiguration.

Was das Modul ausdruecklich nicht ist
--------------------------------------

Es ist kein Schutzversprechen. Es verschleiert nicht die Herkunft und umgeht
keine Bezahlschranke; es verteilt die Anfragen eines Archivs auf die Adressen,
die der Nutzer selbst mitbringt. Die Tunnel gehoeren ihm, die Zugangsdaten
legt er ab, und ohne eigene Konfiguration passiert hier gar nichts.
"""

from __future__ import annotations

import logging
import re
import shutil
import socket
import subprocess
import threading
import time
from collections import deque
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import VpnTunnel, utcnow
from app.services import drosselung
from app.services.ausgang import DIREKT, DIREKTER_AUSGANG, Ausgang

log = logging.getLogger(__name__)


class VpnFehler(RuntimeError):
    """Eine Tunnelkonfiguration taugt nicht oder laesst sich nicht starten."""


# --------------------------------------------------------------- Konfiguration

#: Ein WireGuard-Schluessel ist ein 32-Byte-Wert in Base64 - immer 44 Zeichen
#: mit abschliessendem "=". Die Pruefung faengt den haeufigsten Kopierfehler ab:
#: eine halb markierte Zeile.
_SCHLUESSEL = re.compile(r"^[A-Za-z0-9+/]{42}[A-Za-z0-9+/=]{2}$")

#: Die Schluessel, die wir aus einer .conf uebernehmen. Alles andere wird
#: bewusst verworfen statt durchgereicht: wg-quick kennt PostUp, PreDown,
#: Table, SaveConfig und einiges mehr - Anweisungen fuer ein Kommandozeilen-
#: werkzeug, das hier gar nicht laeuft. wireproxy bricht daran ab, und der
#: Nutzer saehe einen Fehler ueber eine Zeile, die sein Anbieter ihm so
#: geliefert hat.
_INTERFACE_SCHLUESSEL = {"privatekey", "address", "dns", "mtu"}
_PEER_SCHLUESSEL = {"publickey", "presharedkey", "endpoint", "allowedips", "persistentkeepalive"}


@dataclass(slots=True)
class Tunnelkonfig:
    """Das Wesentliche aus einer WireGuard-Konfigurationsdatei."""

    private_key: str
    address: list[str]
    public_key: str
    endpoint: str
    allowed_ips: list[str] = field(default_factory=lambda: ["0.0.0.0/0", "::/0"])
    dns: list[str] = field(default_factory=list)
    mtu: int | None = None
    preshared_key: str | None = None
    keepalive: int | None = 25

    @property
    def endpunkt_host(self) -> str:
        """Nur der Rechnername des Gegenuebers - fuer die Anzeige."""
        return self.endpoint.rsplit(":", 1)[0].strip("[]")


def konfig_lesen(text: str) -> Tunnelkonfig:
    """Liest eine WireGuard-.conf und beanstandet, was fehlt.

    Warum von Hand statt mit configparser: Eine WireGuard-Datei ist kein INI -
    sie darf denselben Abschnitt mehrfach enthalten (mehrere ``[Peer]``), und
    configparser wirft dabei ``DuplicateSectionError``. Ausserdem sollen die
    Meldungen deutsch und konkret sein: "Es fehlt der PrivateKey" hilft, ein
    ``KeyError: 'privatekey'`` nicht.

    Mehrere Peers sind zulaessig, aber nur der erste wird genommen - fuer einen
    Ausgang ins Internet gibt es genau ein Gegenueber. Ein zweiter waere ein
    Sonderfall aus dem Netzwerkbau, den dieses Archiv nicht bedient.
    """
    abschnitt = ""
    interface: dict[str, str] = {}
    peers: list[dict[str, str]] = []

    for rohzeile in text.splitlines():
        zeile = rohzeile.split("#")[0].strip()
        if not zeile:
            continue
        if zeile.startswith("[") and zeile.endswith("]"):
            abschnitt = zeile[1:-1].strip().lower()
            if abschnitt == "peer":
                peers.append({})
            continue
        if "=" not in zeile:
            continue
        name, _, wert = zeile.partition("=")
        name, wert = name.strip().lower(), wert.strip()
        if abschnitt == "interface" and name in _INTERFACE_SCHLUESSEL:
            interface[name] = wert
        elif abschnitt == "peer" and peers and name in _PEER_SCHLUESSEL:
            peers[-1][name] = wert

    fehlt: list[str] = []
    if not interface.get("privatekey"):
        fehlt.append("PrivateKey im Abschnitt [Interface]")
    if not interface.get("address"):
        fehlt.append("Address im Abschnitt [Interface]")
    if not peers:
        fehlt.append("ein Abschnitt [Peer]")
    else:
        if not peers[0].get("publickey"):
            fehlt.append("PublicKey im Abschnitt [Peer]")
        if not peers[0].get("endpoint"):
            fehlt.append("Endpoint im Abschnitt [Peer]")
    if fehlt:
        raise VpnFehler(
            "Das ist keine vollstaendige WireGuard-Konfiguration. Es fehlt: "
            + ", ".join(fehlt)
            + "."
        )

    peer = peers[0]
    privat = interface["privatekey"]
    if not _SCHLUESSEL.match(privat):
        raise VpnFehler(
            "Der PrivateKey sieht nicht aus wie ein WireGuard-Schluessel "
            "(44 Zeichen Base64). Meist ist beim Kopieren ein Stueck der Zeile "
            "verlorengegangen."
        )
    if not _SCHLUESSEL.match(peer["publickey"]):
        raise VpnFehler("Der PublicKey des Peers sieht nicht aus wie ein WireGuard-Schluessel.")
    if ":" not in peer["endpoint"]:
        raise VpnFehler(
            f"Der Endpoint {peer['endpoint']!r} hat keinen Port - erwartet wird host:port."
        )

    def liste(roh: str | None) -> list[str]:
        return [t.strip() for t in (roh or "").split(",") if t.strip()]

    mtu = None
    if interface.get("mtu"):
        with suppress(ValueError):
            mtu = int(interface["mtu"])
    keepalive = 25
    if peer.get("persistentkeepalive"):
        with suppress(ValueError):
            keepalive = int(peer["persistentkeepalive"])

    return Tunnelkonfig(
        private_key=privat,
        address=liste(interface["address"]),
        public_key=peer["publickey"],
        endpoint=peer["endpoint"],
        allowed_ips=liste(peer.get("allowedips")) or ["0.0.0.0/0", "::/0"],
        dns=liste(interface.get("dns")),
        mtu=mtu,
        preshared_key=peer.get("presharedkey") or None,
        keepalive=keepalive,
    )


def wireproxy_konfig(konfig: Tunnelkonfig, port: int) -> str:
    """Baut die Konfiguration, die wireproxy tatsaechlich bekommt.

    Sie sieht einer WireGuard-Datei absichtlich aehnlich - wireproxy liest
    dasselbe Format - hat aber einen Abschnitt mehr: ``[Socks5]`` bestimmt, wo
    der Tunnel als Proxy erreichbar wird.

    Gebunden wird ausschliesslich an 127.0.0.1. Ein Tunnel, der auf allen
    Schnittstellen lauscht, waere im Heimnetz ein offener Weiterleitungsdienst
    fuer jeden, der die Portnummer kennt.

    Mehrere Adressen kommen als **eine Zeile mit Kommas**, nicht als mehrere
    Zeilen. Das ist gegen wireproxy 1.1.3 nachgemessen und kein Geschmack:
    Bei zwei ``Address``-Zeilen liest es nur die erste. Eine ausdruecklich
    unsinnige zweite Zeile nimmt es anstandslos an - ein Beweis, dass sie gar
    nicht erst angesehen wird. Da Anbieter IPv4 und IPv6 liefern, waere die
    Folge ein Tunnel, der laeuft und dabei still die halbe Erreichbarkeit
    verliert. Mit Kommas werden beide geprueft und beide benutzt. Fuer ``DNS``
    gilt dasselbe.
    """
    zeilen = [
        "# Erzeugt von Vitrine - Aenderungen werden beim naechsten Start ueberschrieben.",
        "[Interface]",
        f"PrivateKey = {konfig.private_key}",
        "Address = " + ", ".join(konfig.address),
    ]
    if konfig.dns:
        zeilen.append("DNS = " + ", ".join(konfig.dns))
    if konfig.mtu:
        zeilen.append(f"MTU = {konfig.mtu}")

    zeilen += ["", "[Peer]", f"PublicKey = {konfig.public_key}"]
    if konfig.preshared_key:
        zeilen.append(f"PresharedKey = {konfig.preshared_key}")
    zeilen.append(f"Endpoint = {konfig.endpoint}")
    zeilen.append("AllowedIPs = " + ", ".join(konfig.allowed_ips))
    if konfig.keepalive:
        # Ohne Keepalive schliesst eine Fritzbox die UDP-Zuordnung nach kurzer
        # Zeit, und der Tunnel ist erst beim naechsten Paket wieder da - was
        # sich als scheinbar haengender Download zeigt.
        zeilen.append(f"PersistentKeepalive = {konfig.keepalive}")

    zeilen += ["", "[Socks5]", f"BindAddress = 127.0.0.1:{port}", ""]
    return "\n".join(zeilen)


# ------------------------------------------------------------------ Prozesse

#: Erster lokaler Port. 51800 liegt oberhalb des ueblichen WireGuard-Ports
#: (51820 ist der Gegenpart draussen, nicht hier) und weit weg von allem, was
#: ein NAS sonst belegt.
PORT_BASIS = 51800

#: So lange wird nach dem Start auf den Proxy-Port gewartet.
STARTFRIST_S = 15.0

#: Dienste, die die eigene oeffentliche Adresse zurueckmelden - im Klartext,
#: eine Zeile. Mehrere, weil ein einzelner ausfaellt oder gedrosselt wird und
#: ein fehlgeschlagener Test sonst wie ein kaputter Tunnel aussaehe.
PRUEF_URLS = (
    "https://api.ipify.org",
    "https://icanhazip.com",
    "https://ifconfig.me/ip",
)

#: Nur so viele Ausgabezeilen je Tunnel werden aufgehoben. Die letzte ist die,
#: die den Grund nennt; alles davor ist Startgeplauder.
LOGZEILEN = 20


@dataclass(slots=True)
class Tunnel:
    """Ein eingerichteter Tunnel samt laufendem Prozess."""

    id: int
    name: str
    port: int
    konfig: Tunnelkonfig
    prozess: subprocess.Popen[str] | None = None
    ausgabe: deque[str] = field(default_factory=lambda: deque(maxlen=LOGZEILEN))
    #: Gemessene oeffentliche Adresse. None = noch nicht geprueft.
    exit_ip: str | None = None
    #: Prozess laeuft und der SOCKS-Port nimmt Verbindungen an.
    gestartet: bool = False
    #: Es ist nachweislich etwas durchgekommen. NUR das zaehlt fuer die
    #: Auswahl - siehe :func:`pruefen`.
    bereit: bool = False
    fehler: str | None = None
    #: Laufende Nummer der letzten Wahl - KEINE Uhrzeit.
    #:
    #: Das ist kein Geschmack, sondern eine Notwendigkeit: Die monotone Uhr
    #: hat unter Windows eine Aufloesung von rund 16 Millisekunden. Zwei
    #: Wahlen kurz hintereinander bekaemen denselben Zeitstempel, die
    #: Sortierung waere ein Gleichstand - und weil sie stabil ist, gewaenne
    #: immer wieder derselbe Tunnel. Der Reihum-Wechsel faende dann gar nicht
    #: statt, und genau dafuer gibt es die Tunnel.
    zuletzt: int = 0
    #: Wie viele Auftraege ihn gerade benutzen.
    belegt: int = 0

    @property
    def ausgang_id(self) -> str:
        return f"tunnel-{self.id}"

    @property
    def proxy(self) -> str:
        # socks5h: Auch die Namensaufloesung geht durch den Tunnel. Mit
        # schlichtem socks5 fragt der Server selbst beim DNS des Providers -
        # der Verkehr liefe getunnelt, die Anfrage darueber nicht.
        return f"socks5h://127.0.0.1:{self.port}"

    def als_ausgang(self) -> Ausgang:
        return Ausgang(id=self.ausgang_id, name=self.name, proxy=self.proxy)


_werk = threading.RLock()
_tunnel: dict[int, Tunnel] = {}
#: Zaehlt die Wahlen durch und gibt damit die Reihenfolge vor.
_wahlen = 0


def binaer() -> str | None:
    """Pfad zum wireproxy-Programm, oder None."""
    pfad = settings.wireproxy_path
    if pfad and Path(pfad).is_file():
        return pfad
    return shutil.which(pfad or "wireproxy")


def verzeichnis() -> Path:
    """Wo die Tunnelkonfigurationen liegen."""
    return settings.data_dir / "vpn"


def _konfigpfad(tunnel_id: int) -> Path:
    return verzeichnis() / f"{tunnel_id}.conf"


def _laufkonfigpfad(tunnel_id: int) -> Path:
    return verzeichnis() / f"{tunnel_id}.wireproxy.conf"


def _mitlesen(tunnel: Tunnel) -> None:
    """Sammelt die Ausgabe des Prozesses ein.

    Nicht Beiwerk, sondern die einzige Quelle fuer "warum startet der Tunnel
    nicht". wireproxy schreibt den Grund - falscher Schluessel, Endpunkt nicht
    aufloesbar, Port belegt - auf die Standardausgabe und beendet sich. Ohne
    Mitleser stuende in der Oberflaeche nur "nicht bereit".
    """
    strom = tunnel.prozess.stdout if tunnel.prozess else None
    if strom is None:
        return
    for zeile in strom:
        text = zeile.rstrip()
        if text:
            tunnel.ausgabe.append(text)
            log.debug("wireproxy[%s]: %s", tunnel.name, text)


def _port_offen(tunnel: Tunnel, frist_s: float) -> bool:
    """Wartet, bis der SOCKS-Port Verbindungen annimmt.

    Beendet sich der Prozess vorher, wird nicht weiter gewartet. Das ist der
    haeufigste Fall bei einer falschen Konfiguration: wireproxy schreibt den
    Grund und steigt mit ``log.Fatal`` sofort aus. Die volle Frist abzusitzen
    hiesse, den Nutzer fuenfzehn Sekunden auf eine Antwort warten zu lassen,
    die schon nach einer feststeht.
    """
    ende = time.monotonic() + frist_s
    while time.monotonic() < ende:
        if tunnel.prozess is not None and tunnel.prozess.poll() is not None:
            return False
        with suppress(OSError), socket.create_connection(("127.0.0.1", tunnel.port), timeout=1.0):
            return True
        time.sleep(0.25)
    return False


def _starten(tunnel: Tunnel) -> None:
    """Startet den wireproxy-Prozess eines Tunnels."""
    programm = binaer()
    if programm is None:
        tunnel.fehler = (
            "wireproxy ist nicht installiert. Im mitgelieferten Container ist es dabei - "
            "ausserhalb muss es im PATH liegen oder YTA_WIREPROXY_PATH gesetzt sein."
        )
        return

    pfad = _laufkonfigpfad(tunnel.id)
    pfad.parent.mkdir(parents=True, exist_ok=True)
    pfad.write_text(wireproxy_konfig(tunnel.konfig, tunnel.port), encoding="utf-8")
    with suppress(OSError):
        # Der private Schluessel steht darin. Auf einem NAS-Share sehen mehr
        # Leute in dieses Verzeichnis als gedacht.
        pfad.chmod(0o600)

    tunnel.ausgabe.clear()
    tunnel.fehler = None
    tunnel.gestartet = False
    tunnel.bereit = False
    try:
        tunnel.prozess = subprocess.Popen(  # fester Pfad, keine Shell
            [programm, "-c", str(pfad)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
    except OSError as e:
        tunnel.fehler = f"wireproxy liess sich nicht starten: {e}"
        log.error("Tunnel %s: %s", tunnel.name, tunnel.fehler)
        return

    threading.Thread(
        target=_mitlesen, args=(tunnel,), name=f"wireproxy-{tunnel.id}", daemon=True
    ).start()

    if not _port_offen(tunnel, STARTFRIST_S):
        # Kurz Luft lassen: Der Mitleser laeuft in einem eigenen Strang und
        # haengt der Wirklichkeit um Sekundenbruchteile hinterher. Ohne diese
        # Pause stuende bei einem sofort abgestuerzten Prozess "keine Meldung",
        # obwohl er den Grund gerade geschrieben hat.
        time.sleep(0.3)
        letzte = tunnel.ausgabe[-1] if tunnel.ausgabe else ""
        gestorben = tunnel.prozess is not None and tunnel.prozess.poll() is not None
        tunnel.fehler = (
            "wireproxy hat sich sofort wieder beendet."
            if gestorben
            else f"Der Tunnel war nach {STARTFRIST_S:.0f} Sekunden nicht erreichbar."
        ) + (f" Meldung: {letzte}" if letzte else "")
        log.warning("Tunnel %s: %s", tunnel.name, tunnel.fehler)
        _beenden(tunnel)
        return

    tunnel.gestartet = True
    log.info(
        "Tunnel %s gestartet auf 127.0.0.1:%d - wird jetzt gemessen",
        tunnel.name, tunnel.port,
    )


def _beenden(tunnel: Tunnel) -> None:
    """Beendet den Prozess eines Tunnels, notfalls hart."""
    prozess, tunnel.prozess = tunnel.prozess, None
    tunnel.gestartet = False
    tunnel.bereit = False
    if prozess is None or prozess.poll() is not None:
        return
    prozess.terminate()
    try:
        prozess.wait(timeout=5)
    except subprocess.TimeoutExpired:
        prozess.kill()
        with suppress(subprocess.TimeoutExpired):
            prozess.wait(timeout=5)
    log.info("Tunnel %s beendet", tunnel.name)


# -------------------------------------------------------------------- Pruefen


def _durch_proxy(url: str, proxy: str | None, timeout: float = 12.0) -> str:
    """Holt eine kurze Textseite - wahlweise durch einen Tunnel.

    Benutzt bewusst yt-dlps eigenen Netzwerkunterbau statt urllib oder httpx.
    Zwei Gruende, und der zweite ist der wichtigere: yt-dlp bringt die
    SOCKS-Unterstuetzung mit, ohne die urllib gar nicht durch einen Tunnel
    spricht - und es ist derselbe Weg, den ein Download nimmt. Ein Test, der
    einen anderen Weg prueft als den spaeter benutzten, ist keiner.
    """
    import yt_dlp

    opts: dict[str, Any] = {"quiet": True, "no_warnings": True, "socket_timeout": timeout}
    if proxy:
        opts["proxy"] = proxy
    with yt_dlp.YoutubeDL(opts) as ydl:
        antwort = ydl.urlopen(url)
        return antwort.read(256).decode("utf-8", "replace").strip()


def exit_ip_ermitteln(proxy: str | None) -> str:
    """Die oeffentliche Adresse, unter der ein Ausgang auftritt.

    Die einzige Auskunft, die wirklich beantwortet, ob ein Tunnel etwas tut.
    Ein Prozess kann laufen, der Port kann offen sein und der Verkehr trotzdem
    ueber die Hausleitung gehen - dann steht hier dieselbe Adresse wie bei der
    Direktverbindung, und die Oberflaeche sagt es.
    """
    letzter = ""
    for url in PRUEF_URLS:
        try:
            text = _durch_proxy(url, proxy)
        # Jede Netzstoerung ist hier gleichbedeutend: dieser Dienst
            # antwortet nicht, also den naechsten fragen.
        except Exception as e:
            letzter = str(e)
            continue
        # Die Dienste antworten mit der blanken Adresse; alles andere ist eine
        # Fehlerseite, die zufaellig mit 200 kam.
        kandidat = text.split()[0] if text.split() else ""
        if kandidat.count(".") == 3 or ":" in kandidat:
            return kandidat
        letzter = f"unerwartete Antwort von {url}: {text[:60]!r}"
    raise VpnFehler(letzter or "keine Antwort")


def pruefen(tunnel_id: int) -> dict[str, Any]:
    """Misst die Adresse eines Tunnels und entscheidet ueber seine Eignung.

    Der zweite Teil ist der wichtigere. Ein offener SOCKS-Port heisst **nicht**,
    dass der Tunnel etwas kann: wireproxy bindet ihn, sobald es die Datei
    gelesen hat, ganz gleich ob das Gegenueber je antwortet. Ein Tunnel mit
    totem Endpunkt meldete sich also als "bereit", bekaeme reihum Auftraege und
    liesse jeden davon scheitern - und weil das kein "not a bot" ist, waeren es
    echte Fehlschlaege mit hochgezaehltem Versuchszaehler. Bei 1800 wartenden
    Videos ist das genau der Schaden, gegen den es die Drosselpause gibt.

    Deshalb gilt: Kommt hier nichts durch, faellt der Tunnel aus der Rotation,
    bis es wieder klappt.
    """
    with _werk:
        tunnel = _tunnel.get(tunnel_id)
    if tunnel is None:
        raise VpnFehler("Diesen Tunnel gibt es nicht (mehr).")
    if not tunnel.gestartet:
        return {"erfolg": False, "meldung": tunnel.fehler or "Der Tunnel laeuft nicht."}

    beginn = time.monotonic()
    try:
        ip = exit_ip_ermitteln(tunnel.proxy)
    except VpnFehler as e:
        tunnel.bereit = False
        tunnel.fehler = f"Der Tunnel steht, aber es kommt nichts durch: {e}"
        log.warning("Tunnel %s faellt aus der Rotation: %s", tunnel.name, e)
        return {"erfolg": False, "meldung": tunnel.fehler}
    tunnel.exit_ip = ip
    tunnel.fehler = None
    tunnel.bereit = True
    return {
        "erfolg": True,
        "ip": ip,
        "dauer_s": round(time.monotonic() - beginn, 2),
        "meldung": f"Der Tunnel antwortet und tritt als {ip} auf.",
    }


#: Wie oft die Wache nachsieht. Fuenf Minuten sind der Kompromiss: Ein
#: ausgefallener Tunnel steht damit hoechstens so lange nutzlos in der
#: Rotation, und die Pruefung selbst ist eine einzige kleine HTTPS-Anfrage.
WACHE_TAKT_S = 300


def nachsehen() -> None:
    """Prueft alle Tunnel und holt zurueck, was wieder kann.

    Drei Dinge koennen einem laufenden Tunnel zustossen, und keines meldet sich
    von selbst: Der Prozess stirbt, der Anbieter schaltet den Standort ab, oder
    die Leitung dahinter faellt aus. Ohne diesen Lauf bliebe ein toter Tunnel
    fuer immer "bereit" - oder, schlimmer, ein wieder gesunder fuer immer
    draussen.
    """
    with _werk:
        tunnel = list(_tunnel.values())
    for t in tunnel:
        if t.prozess is None or t.prozess.poll() is not None:
            log.info("Tunnel %s laeuft nicht mehr - Neustart", t.name)
            _beenden(t)
            _starten(t)
            if not t.prozess:
                continue
        # Belegte Tunnel nicht anfassen: Die Messung wuerde zwar nur eine
        # Anfrage kosten, aber ein laufender Download ist der bessere Beweis,
        # dass der Tunnel arbeitet, als jede zusaetzliche Anfrage.
        if t.belegt > 0:
            continue
        with suppress(Exception):
            pruefen(t.id)


# ------------------------------------------------------------------- Verwaltung


def _freier_port() -> int:
    """Die kleinste noch nicht vergebene Portnummer.

    Klingt nach Kleinkram, ist aber ein echter Fallstrick. Naheliegend waere,
    die Nummer aus der Position in der Liste abzuleiten - und genau das geht
    schief, sobald jemand einen Tunnel loescht: Bei drei Tunneln auf 51800,
    51801 und 51802 rutschen nach dem Loeschen des ersten die uebrigen in der
    Liste hoch, behalten aber ihre laufenden Prozesse und damit ihre Ports.
    Der naechste neue Tunnel bekaeme dann eine Nummer, auf der schon jemand
    lauscht - wireproxy startet nicht, und die Meldung ("address already in
    use") deutet auf alles Moegliche hin, nur nicht auf den wahren Grund.

    Muss unter ``_werk`` aufgerufen werden.
    """
    vergeben = {t.port for t in _tunnel.values()}
    port = PORT_BASIS
    while port in vergeben:
        port += 1
    return port


def laden(db: Session, *, pruefen_nach_start: bool = True) -> None:
    """Bringt die laufenden Prozesse mit der Datenbank in Deckung.

    Wird beim Start aufgerufen und nach jeder Aenderung an den Tunneln. Der
    Weg ist bewusst grob: Was nicht mehr gebraucht wird, wird beendet, was neu
    ist, gestartet. Ein Tunnel, der unveraendert weiterlaeuft, wird nicht
    angefasst - sonst risse jedes Speichern in den Einstellungen alle laufenden
    Downloads mit.
    """
    aktiv = {
        t.id: t
        for t in db.scalars(select(VpnTunnel).order_by(VpnTunnel.reihenfolge, VpnTunnel.id))
        if t.aktiv
    }
    if not settings.vpn_aktiv:
        aktiv = {}

    with _werk:
        for tid in list(_tunnel):
            if tid not in aktiv:
                _beenden(_tunnel.pop(tid))
                drosselung.entwarnung(f"tunnel-{tid}")

        neu: list[Tunnel] = []
        for tid, zeile in aktiv.items():
            vorhanden = _tunnel.get(tid)
            # Laeuft er noch wirklich, wird er nicht angefasst - sonst risse
            # jedes Speichern in den Einstellungen alle laufenden Downloads
            # mit. Ein gestorbener Prozess dagegen soll neu starten; ``prozess
            # is not None`` allein genuegt dafuer nicht, denn das Popen-Objekt
            # bleibt auch nach dem Ende bestehen.
            if vorhanden is not None and vorhanden.prozess is not None:
                if vorhanden.prozess.poll() is None:
                    vorhanden.name = zeile.name
                    continue
                _beenden(vorhanden)
            pfad = _konfigpfad(tid)
            if not pfad.is_file():
                log.error("Tunnel %s: Konfigurationsdatei %s fehlt", zeile.name, pfad)
                continue
            try:
                konfig = konfig_lesen(pfad.read_text(encoding="utf-8"))
            except (VpnFehler, OSError) as e:
                log.error("Tunnel %s ist unbrauchbar: %s", zeile.name, e)
                continue
            tunnel = Tunnel(
                id=tid,
                name=zeile.name,
                port=vorhanden.port if vorhanden is not None else _freier_port(),
                konfig=konfig,
            )
            _tunnel[tid] = tunnel
            neu.append(tunnel)

    for tunnel in neu:
        _starten(tunnel)

    if pruefen_nach_start and neu:
        # Im Hintergrund: Die Messung dauert je Tunnel ein bis zwei Sekunden,
        # und der Aufrufer ist entweder der Start des Dienstes oder eine
        # HTTP-Anfrage - beide sollen darauf nicht warten.
        threading.Thread(
            target=lambda: [_still_pruefen(t) for t in neu],
            name="vpn-pruefung",
            daemon=True,
        ).start()


def _still_pruefen(tunnel: Tunnel) -> None:
    with suppress(Exception):
        pruefen(tunnel.id)


def alles_beenden() -> None:
    """Beendet alle Tunnel - beim Herunterfahren des Dienstes."""
    with _werk:
        for tunnel in list(_tunnel.values()):
            _beenden(tunnel)
        _tunnel.clear()


def gegenpruefen(konfig: Tunnelkonfig) -> None:
    """Laesst wireproxy selbst ueber die Konfiguration urteilen.

    ``-n`` prueft die Datei und beendet sich, ohne etwas zu verbinden. Das ist
    die zweite Meinung zu unserer eigenen Pruefung, und sie ist es wert: Der
    Leser hier nimmt an, was er versteht, aber wireproxy hat das letzte Wort -
    ein Schluessel mit richtiger Laenge und falschem Inhalt etwa faellt erst
    dort auf. Ohne diesen Schritt merkte man es beim ersten Download.

    Fehlt das Programm, wird stillschweigend uebersprungen: Der Entwicklungs-
    rechner hat es meist nicht, und ein Upload soll dort trotzdem gehen.
    """
    programm = binaer()
    if programm is None:
        return
    import tempfile

    with tempfile.NamedTemporaryFile(
        "w", suffix=".conf", delete=False, encoding="utf-8"
    ) as datei:
        datei.write(wireproxy_konfig(konfig, PORT_BASIS + 99))
        pfad = datei.name
    try:
        lauf = subprocess.run(  # fester Pfad, keine Shell
            [programm, "-n", "-c", pfad],
            capture_output=True, text=True, timeout=20, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        log.warning("wireproxy-Pruefung nicht moeglich: %s", e)
        return
    finally:
        with suppress(OSError):
            Path(pfad).unlink()

    if lauf.returncode != 0:
        meldung = (lauf.stderr or lauf.stdout or "").strip().splitlines()
        raise VpnFehler(
            "wireproxy lehnt diese Konfiguration ab: "
            + (meldung[-1] if meldung else f"Rueckgabewert {lauf.returncode}")
        )


def anlegen(db: Session, name: str, roh: str) -> VpnTunnel:
    """Prueft eine hochgeladene Konfiguration und legt sie ab.

    Erst pruefen, dann speichern - wie beim Cookie-Assistenten und aus
    demselben Grund: Eine unbrauchbare Datei soll gar nicht erst in den Bestand
    geraten, wo sie spaeter als stiller Ausfall auffaellt.
    """
    konfig = konfig_lesen(roh)  # wirft VpnFehler mit lesbarer Begruendung
    gegenpruefen(konfig)

    zeile = VpnTunnel(
        name=name.strip() or konfig.endpunkt_host,
        endpunkt=konfig.endpoint,
        aktiv=True,
        reihenfolge=(db.scalar(select(VpnTunnel.reihenfolge).order_by(
            VpnTunnel.reihenfolge.desc())) or 0) + 1,
        angelegt_am=utcnow(),
    )
    db.add(zeile)
    db.commit()

    pfad = _konfigpfad(zeile.id)
    pfad.parent.mkdir(parents=True, exist_ok=True)
    pfad.write_text(roh, encoding="utf-8")
    with suppress(OSError):
        pfad.chmod(0o600)
    log.info("Tunnel %s angelegt (%s)", zeile.name, konfig.endpoint)
    return zeile


def entfernen(db: Session, tunnel_id: int) -> bool:
    """Loescht einen Tunnel samt Konfigurationsdateien."""
    zeile = db.get(VpnTunnel, tunnel_id)
    if zeile is None:
        return False
    with _werk:
        vorhanden = _tunnel.pop(tunnel_id, None)
        if vorhanden is not None:
            _beenden(vorhanden)
    db.delete(zeile)
    db.commit()
    for pfad in (_konfigpfad(tunnel_id), _laufkonfigpfad(tunnel_id)):
        with suppress(OSError):
            pfad.unlink(missing_ok=True)
    drosselung.entwarnung(f"tunnel-{tunnel_id}")
    log.info("Tunnel %s entfernt", zeile.name)
    return True


# --------------------------------------------------------------------- Auswahl


def ausgaenge() -> list[Ausgang]:
    """Alle Ausgaenge, die gerade grundsaetzlich zur Verfuegung stehen.

    Ohne eingeschaltetes VPN ist das genau einer: die Direktverbindung. Damit
    verhaelt sich alles Uebrige unveraendert, solange niemand einen Tunnel
    einrichtet - die Auswahl ist dann eine Liste mit einem Element.

    Ist VPN eingeschaltet, entscheidet ``vpn_nur_tunnel``, ob die
    Direktverbindung mitspielt. Standard ist nein, und das ist die sichere
    Vorgabe: Wer Tunnel einrichtet, will nicht, dass bei deren Ausfall
    stillschweigend wieder die eigene Leitung benutzt wird.
    """
    if not settings.vpn_aktiv:
        return [DIREKTER_AUSGANG]
    with _werk:
        offen = [t.als_ausgang() for t in _tunnel.values() if t.bereit]
    if not settings.vpn_nur_tunnel:
        offen.append(DIREKTER_AUSGANG)
    return offen


def ausgang_ids() -> list[str]:
    return [a.id for a in ausgaenge()]


def waehlen() -> Ausgang | None:
    """Der Ausgang fuer den naechsten Auftrag - oder None, wenn keiner frei ist.

    Zwei Regeln, in dieser Reihenfolge:

    1. **Gesperrte fallen weg.** Ein Tunnel, den YouTube gerade abweist, wird
       nicht gewaehlt, bis seine Pause abgelaufen ist.
    2. **Reihum, unbelegte zuerst.** Bei vier Tunneln und vier parallelen
       Downloads bekommt jeder Download einen eigenen - denn zwei Downloads
       ueber dieselbe Adresse sind genau das, was das Budget frisst. Sind mehr
       Downloads eingestellt als Tunnel da sind, teilen sich zwei einen; das
       ist die Entscheidung des Nutzers und kein Grund, den Betrieb anzuhalten.

    Kein Zufall, sondern der aelteste zuerst: Zufall trifft bei vier Tunneln
    erstaunlich oft zweimal denselben.

    Die laufende Nummer wird schon **hier** gesetzt und nicht erst beim
    Belegen. Das schliesst eine sonst offene Luecke: Zwischen der Wahl und dem
    Beginn der Arbeit holt der Strang noch seinen Auftrag aus der Datenbank,
    und in dieser Zeit gilt der Tunnel noch als unbelegt. Ein zweiter Strang
    waehlte ohne diese Buchung denselben - beide Downloads liefen dann ueber
    eine Adresse, obwohl daneben einer frei ist.
    """
    moeglich = ausgaenge()
    if not moeglich:
        return None
    freie = set(drosselung.frei([a.id for a in moeglich]))
    kandidaten = [a for a in moeglich if a.id in freie]
    if not kandidaten:
        return None

    global _wahlen
    with _werk:
        def rang(a: Ausgang) -> tuple[int, int]:
            tunnel = _nach_ausgang(a.id)
            if tunnel is None:
                # Die Direktverbindung fuehrt keine Buchhaltung. Sie ist immer
                # verfuegbar und kommt zuletzt - sind Tunnel frei, sollen die
                # arbeiten.
                return (2, 0)
            return (1 if tunnel.belegt else 0, tunnel.zuletzt)

        kandidaten.sort(key=rang)
        gewaehlt = kandidaten[0]
        tunnel = _nach_ausgang(gewaehlt.id)
        if tunnel is not None:
            _wahlen += 1
            tunnel.zuletzt = _wahlen
    return gewaehlt


def _nach_ausgang(ausgang_id: str) -> Tunnel | None:
    for tunnel in _tunnel.values():
        if tunnel.ausgang_id == ausgang_id:
            return tunnel
    return None


def wartezeit() -> float:
    """Wie lange, bis wieder irgendein Ausgang frei ist."""
    return drosselung.kuerzeste_wartezeit(ausgang_ids())


@contextmanager
def benutzen(gewaehlt: Ausgang) -> Iterator[Ausgang]:
    """Belegt einen Ausgang fuer die Dauer eines Auftrags."""
    from app.services import ausgang as ausgang_modul

    tunnel = None
    with _werk:
        tunnel = _nach_ausgang(gewaehlt.id)
        if tunnel is not None:
            tunnel.belegt += 1
    try:
        with ausgang_modul.benutzen(gewaehlt):
            yield gewaehlt
    finally:
        with _werk:
            if tunnel is not None:
                tunnel.belegt = max(0, tunnel.belegt - 1)


# -------------------------------------------------------------------- Auskunft


def zustand(db: Session) -> dict[str, Any]:
    """Alles, was die Oberflaeche ueber die Ausgaenge wissen muss."""
    zeilen = list(
        db.scalars(select(VpnTunnel).order_by(VpnTunnel.reihenfolge, VpnTunnel.id))
    )
    sperren = drosselung.zustand_je_ausgang(
        [DIREKT] + [f"tunnel-{z.id}" for z in zeilen]
    )

    liste: list[dict[str, Any]] = []
    with _werk:
        for zeile in zeilen:
            tunnel = _tunnel.get(zeile.id)
            liste.append({
                "id": zeile.id,
                "name": zeile.name,
                "endpunkt": zeile.endpunkt,
                "aktiv": zeile.aktiv,
                "laeuft": bool(tunnel and tunnel.gestartet),
                # Nur das entscheidet ueber die Rotation: ein offener Port ist
                # noch kein Beweis, dass etwas durchkommt.
                "bereit": bool(tunnel and tunnel.bereit),
                "port": tunnel.port if tunnel else None,
                "exit_ip": tunnel.exit_ip if tunnel else None,
                "belegt": tunnel.belegt if tunnel else 0,
                "fehler": tunnel.fehler if tunnel else None,
                "sperre": sperren.get(f"tunnel-{zeile.id}"),
            })

    # Zwei Tunnel mit derselben oeffentlichen Adresse sind kein Gewinn - sie
    # teilen sich das Budget, das sie eigentlich verdoppeln sollten. Das faellt
    # sonst nie auf: Beide laufen, beide melden "bereit".
    gesehen: dict[str, int] = {}
    for eintrag in liste:
        ip = eintrag.get("exit_ip")
        if isinstance(ip, str):
            gesehen[ip] = gesehen.get(ip, 0) + 1
    doppelt = sorted(ip for ip, anzahl in gesehen.items() if anzahl > 1)

    return {
        "aktiv": settings.vpn_aktiv,
        "nur_tunnel": settings.vpn_nur_tunnel,
        "wireproxy": binaer(),
        "tunnel": liste,
        "bereit": sum(1 for e in liste if e["bereit"]),
        "doppelte_adressen": doppelt,
        "direkt": {
            "benutzt": (not settings.vpn_aktiv) or (not settings.vpn_nur_tunnel),
            "sperre": sperren.get(DIREKT),
        },
    }
