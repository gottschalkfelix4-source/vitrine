"""Der Cookie-Assistent: hochladen, beurteilen, gegen YouTube ausprobieren.

Bewusst ein eigener Router und nicht ein paar Endpunkte mehr in
:mod:`app.api.library`: Hier geht es nicht um die Bibliothek, sondern um einen
Sitzungsschluessel. Das gehoert schon im Aufbau getrennt.

Was es hier ausdruecklich **nicht** gibt, ist ein Endpunkt, der die Datei
wieder herausgibt. Sie geht rein und wird beurteilt; gelesen wird sie nur von
yt-dlp. Ein Herunterladen waere bequem und einmal zu viel: Wer die Oberflaeche
erreicht, haette damit das Google-Konto.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models import Video, VideoStatus
from app.services import cookies, drosselung, ytdlp

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/cookies", tags=["cookies"])

#: Rueckfall fuer den Probelauf, falls die Datenbank noch leer ist. Sonst wird
#: ein Video aus der eigenen Warteschlange genommen - der Test sagt dann etwas
#: ueber genau die Videos, die gerade nicht durchkommen, statt ueber ein
#: fremdes.
_TESTVIDEO = "dQw4w9WgXcQ"


def _auskunft() -> dict[str, Any]:
    befund = cookies.pruefen()
    pfad = cookies.aktiver_pfad()
    return {
        **befund.als_dict(),
        "vorhanden": pfad is not None,
        # Nur der Dateiname, nicht der volle Pfad: Der Nutzer soll sehen, ob
        # seine eigene Datei gilt oder die hochgeladene - mehr braucht es nicht.
        "eigener_pfad": bool(settings.ytdlp_cookies_file),
    }


@router.get("")
def zustand() -> dict[str, Any]:
    """Wie es um die hinterlegte Datei steht."""
    return _auskunft()


@router.post("", status_code=status.HTTP_200_OK)
async def hochladen(datei: UploadFile = File(...)) -> dict[str, Any]:
    """Nimmt eine cookies.txt entgegen - aber nur, wenn sie etwas taugt.

    Der Ablauf ist absichtlich streng: geprueft wird vor dem Ersetzen. Eine
    kaputte Datei laesst jeden yt-dlp-Aufruf scheitern, auch das Auflisten
    eines Kanals. Eine funktionierende gegen eine kaputte zu tauschen, weil
    jemand die falsche Datei erwischt hat, waere der schlechteste Ausgang.
    """
    roh = await datei.read()
    befund = cookies.speichern(roh)
    if not befund.brauchbar:
        # 422 statt 400: Die Anfrage war formal richtig, der Inhalt taugt nur
        # nicht. Die Oberflaeche zeigt die Meldung unveraendert an.
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, befund.meldung)
    return _auskunft()


@router.delete("", status_code=status.HTTP_200_OK)
def entfernen() -> dict[str, Any]:
    """Loescht die hochgeladene Datei wieder."""
    if not cookies.entfernen():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Es ist keine Cookie-Datei hinterlegt.")
    return _auskunft()


@router.post("/test")
def probelauf(db: Session = Depends(get_db)) -> dict[str, Any]:
    """Fragt ein einzelnes Video bei YouTube ab und berichtet, was passiert.

    Der eigentliche Sinn des Assistenten. Ob eine Cookie-Datei taugt, sagt
    einem sonst erst der naechste Download - also womoeglich Stunden spaeter,
    versteckt zwischen tausend anderen Auftraegen. Ein Klick, eine Anfrage,
    eine klare Antwort.

    Gemessen wird nebenbei die angebotene Qualitaet. Das faengt den zweiten,
    stilleren Fehlerfall mit ab: Eine Sitzung kann zustande kommen und trotzdem
    nur eine verstuemmelte Formatauswahl liefern - dann sind 360p das Ergebnis,
    und man merkt es erst beim Zuschauen.
    """
    rest = drosselung.wartezeit()
    if rest > 0:
        # Jetzt zu testen waere doppelt falsch: Das Ergebnis waere unabhaengig
        # von den Cookies ein Fehlschlag, und die Anfrage verlaengerte die
        # Sperre. Lieber ehrlich vertroesten.
        return {
            "erfolg": False,
            "pausiert": True,
            "meldung": drosselung.hinweis(rest),
        }

    video = db.scalar(
        select(Video).where(Video.status.in_([VideoStatus.QUEUED, VideoStatus.FAILED]))
    )
    video_id = video.id if video else _TESTVIDEO

    try:
        info = ytdlp.fetch_video_info(video_id)
    except ytdlp.Gedrosselt as e:
        # Die aussagekraeftigste Antwort ueberhaupt: Mit diesen Cookies weist
        # YouTube uns weiterhin ab. Entweder sind sie rotiert, oder sie stammen
        # aus einer abgemeldeten Sitzung.
        drosselung.melden(str(e))
        return {
            "erfolg": False,
            "video_id": video_id,
            "meldung": (
                "YouTube weist auch mit diesen Cookies ab. Sie sind vermutlich rotiert - "
                "das passiert, sobald man sich im selben Browser weiterbewegt. Am "
                "zuverlaessigsten ist ein privates Fenster: anmelden, Cookies exportieren, "
                "Fenster schliessen, ohne sich abzumelden."
            ),
        }
    except ytdlp.VideoUnavailable as e:
        return {
            "erfolg": False,
            "video_id": video_id,
            "meldung": f"Das Testvideo ist bei der Quelle nicht verfuegbar: {e}",
        }
    except ytdlp.YtdlpError as e:
        return {"erfolg": False, "video_id": video_id, "meldung": str(e)}

    drosselung.entwarnung()
    guete = ytdlp.angebotene_guete(info)
    return {
        "erfolg": True,
        "video_id": video_id,
        "titel": info.get("title"),
        "angebotene_guete": guete,
        "meldung": (
            f"YouTube hat geantwortet, angeboten werden bis zu {guete}p."
            if guete
            else "YouTube hat geantwortet."
        ),
    }
