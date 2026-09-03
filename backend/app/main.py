"""Einstiegspunkt der Anwendung."""

from __future__ import annotations

import logging
import os
import shutil
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response
from starlette.types import Scope

from app.api import cookies, hardware, library, stream
from app.config import settings
from app.db import init_db, session_scope
from app.services import abbruch, cache, einstellungen, jobs
from app.workers.runner import werk

logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)
log = logging.getLogger(__name__)

_stop = threading.Event()


def _reaper_loop() -> None:
    """Raeumt den Heissspeicher regelmaessig auf.

    Laeuft als Daemon-Thread und nicht als asyncio-Task, weil das Aufraeumen
    blockierende Dateioperationen macht - die wuerden sonst den Event-Loop und
    damit alle laufenden Streams anhalten.
    """
    while not _stop.wait(settings.reaper_interval_seconds):
        try:
            with session_scope() as db:
                cache.reap(db)
        except Exception:
            log.exception("Aufraeumlauf fehlgeschlagen")


def _scheduler_loop() -> None:
    """Reiht faellige Kanalabgleiche ein."""
    from app.workers.sync import faellige_kanaele_einreihen

    while not _stop.wait(settings.sync_scheduler_interval_seconds):
        try:
            anzahl = faellige_kanaele_einreihen()
            if anzahl:
                log.info("%d faellige Kanalabgleiche eingereiht", anzahl)
        except Exception:
            log.exception("Zeitplaner fehlgeschlagen")


def _werkzeuge_pruefen() -> None:
    """Meldet fehlende externe Werkzeuge beim Start statt beim ersten Auftrag.

    Ohne diese Pruefung faellt ein fehlendes ffmpeg erst auf, wenn Stunden
    spaeter der erste Download fertig ist - und dann als unverstaendlicher
    Fehler mitten in der Warteschlange.
    """
    from app.services.media import tools_available

    for name, pfad in tools_available().items():
        if pfad is None:
            log.error("%s wurde nicht gefunden - Archivierung wird scheitern", name)
        else:
            log.info("%s gefunden: %s", name, pfad)

    if shutil.which("deno") is None and shutil.which("node") is None:
        # Der stille 360p-Fehler: yt-dlp bricht ohne JavaScript-Laufzeit nicht
        # ab, sondern liefert klammheimlich eine reduzierte Formatauswahl.
        log.warning(
            "Keine JavaScript-Laufzeit gefunden (Deno oder Node). yt-dlp liefert dann "
            "moeglicherweise nur stark reduzierte Formate - im schlimmsten Fall 360p, "
            "ohne dass ein Fehler gemeldet wird. Im Container bringt das Dockerfile Deno mit."
        )


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    # Ein frischer Prozess hat ohnehin ein leeres Signal. Wichtig ist das fuer
    # Tests und fuer jeden Fall, in dem die Anwendung im selben Prozess erneut
    # hochfaehrt - ein stehengebliebenes Signal wuerde jeden Auftrag sofort
    # wieder abbrechen.
    abbruch.zuruecksetzen()
    init_db()
    with session_scope() as db:
        # Muss VOR allem anderen laufen: Was in der Oberflaeche eingestellt
        # wurde, gewinnt ueber Umgebung und Standard - und die Arbeiter lesen
        # gleich darauf ihre Straenge-Anzahl daraus.
        einstellungen.anwenden(db)
    _werkzeuge_pruefen()
    with session_scope() as db:
        jobs.reset_stale(db)
        cache.reap(db)

    for ziel, name in ((_reaper_loop, "reaper"), (_scheduler_loop, "zeitplaner")):
        threading.Thread(target=ziel, name=name, daemon=True).start()
    werk.start()

    log.info("%s bereit - Daten unter %s", settings.app_name, settings.data_dir)
    yield

    # Reihenfolge zaehlt: Erst das Abbruchsignal, damit ein laufender
    # Download oder Encode ueberhaupt mitbekommt, dass Schluss ist. werk.stop()
    # setzt es zwar auch, wartet aber gleich darauf - ohne das vorherige Signal
    # waere die Wartezeit sicher vergeblich.
    abbruch.anfordern()
    _stop.set()
    werk.stop()


app = FastAPI(
    title=settings.app_name,
    description="Eigenes YouTube-Archiv mit Kalt- und Heissspeicher",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    # Ohne das kommt der Player nicht an die Bereichsangaben im Entwicklungsmodus,
    # wenn Vite auf einem anderen Port laeuft.
    expose_headers=["Content-Range", "Accept-Ranges", "Content-Length", "X-Wiedergabe-Modus"],
)

app.include_router(stream.router)
app.include_router(cookies.router)
app.include_router(hardware.router)
app.include_router(library.router)


@app.get("/api/health", tags=["system"])
def health() -> dict[str, object]:
    with session_scope() as db:
        return {
            "status": "ok",
            "name": settings.app_name,
            "heissspeicher": cache.usage(db),
            "freier_platz": cache.free_space(),
        }


def _frontend_verzeichnis() -> Path | None:
    """Sucht das gebaute Frontend.

    Reihenfolge: ausdruecklich gesetzter Pfad, dann neben dem Anwendungspaket
    (so legt es das Dockerfile ab), dann das Vite-Ausgabeverzeichnis fuer den
    Fall, dass jemand ausserhalb des Containers arbeitet.

    Bewusst NICHT relativ zum Datenverzeichnis: Das ist ein eingehaengtes
    Volume und hat mit dem Programmcode nichts zu tun.
    """
    aus_umgebung = os.environ.get("YTA_STATIC_DIR")
    kandidaten = [
        Path(aus_umgebung) if aus_umgebung else None,
        Path(__file__).resolve().parent.parent / "static",
        Path(__file__).resolve().parent.parent.parent / "frontend" / "dist",
    ]
    for k in kandidaten:
        if k and k.is_dir() and (k / "index.html").is_file():
            return k
    return None


class _EinseitenDateien(StaticFiles):
    """Statische Dateien mit Rueckfall auf index.html.

    Die Oberflaeche verwaltet ihre Adressen selbst: ``/video/abc123`` und
    ``/playlist/PL...`` sind keine Dateien auf der Platte, sondern Zustaende im
    Browser. Ruft jemand so eine Adresse direkt auf - durch Neuladen, ein
    Lesezeichen oder einen geteilten Link -, fragt der Browser den Server danach.

    ``StaticFiles(html=True)`` hilft hier NICHT: Das liefert index.html nur fuer
    Verzeichnisse, fuer alles andere kommt ein 404. Ohne diesen Rueckfall waere
    also jeder geteilte Link und jedes F5 kaputt - und zwar nur im Container,
    wo das Frontend vom Backend ausgeliefert wird, nicht im Entwicklungsbetrieb
    mit Vite. Ein Fehler, der genau einmal auffaellt: nach dem Deploy.
    """

    async def get_response(self, path: str, scope: Scope) -> Response:
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as e:
            if e.status_code != 404:
                raise
            # Starlette reicht den Pfad in der Schreibweise des Betriebssystems
            # herein - unter Windows also mit Backslash. Ein Vergleich gegen
            # "api/" ginge dort ins Leere und der Unterschied faellt erst im
            # Container auf, wo genau umgekehrt getrennt wird.
            teile = path.replace("\\", "/").strip("/").split("/")

            # Fehlende Dateien mit Endung bleiben ein ehrlicher 404 - sonst
            # bekaeme ein fehlendes Bild oder Skript stillschweigend HTML
            # zurueck, was die Fehlersuche unnoetig schwer macht.
            if "." in teile[-1]:
                raise
            # Dasselbe gilt fuer die API: Wer sich in einer Adresse vertippt,
            # bekam bisher die Oberflaeche mit Status 200 zurueck. Ein Aufrufer,
            # der JSON erwartet, scheitert dann an einer HTML-Seite statt an
            # einer klaren Fehlermeldung - und ein Test, der einen 404 prueft,
            # ginge stillschweigend durch.
            if teile[0] == "api":
                raise
            return await super().get_response("index.html", scope)


def _mount_frontend() -> None:
    """Bindet das gebaute Frontend ein, sofern vorhanden.

    Im Entwicklungsbetrieb laeuft Vite separat; im Container liegt das fertige
    Bundle beim Programm und wird vom selben Prozess ausgeliefert - ein
    Container, ein Port, kein vorgelagerter Webserver noetig.
    """
    static = _frontend_verzeichnis()
    if static is None:
        log.info("Kein gebautes Frontend gefunden - es werden nur die API-Pfade bedient")
        return

    app.mount("/", _EinseitenDateien(directory=static, html=True), name="frontend")
    log.info("Frontend eingebunden aus %s", static)


_mount_frontend()
