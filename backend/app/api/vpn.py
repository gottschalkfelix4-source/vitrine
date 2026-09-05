"""Die WireGuard-Tunnel: hochladen, ein- und ausschalten, ausprobieren.

Eigener Router, aus demselben Grund wie beim Cookie-Assistenten: Es geht nicht
um die Bibliothek, sondern um Zugangsdaten und laufende Prozesse. Das gehoert
schon im Aufbau getrennt.

Was es hier ausdruecklich **nicht** gibt, ist ein Endpunkt, der eine abgelegte
Konfiguration wieder herausgibt. Sie geht rein und wird geprueft; gelesen wird
sie nur beim Starten des Tunnels. In der Datei steht ein privater Schluessel -
wer die Oberflaeche erreicht, haette damit den Zugang zum VPN-Konto.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import VpnTunnel
from app.services import vpn

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/vpn", tags=["vpn"])

#: Obergrenze fuer den Upload. Eine WireGuard-Konfiguration ist unter einem
#: Kilobyte gross; alles darueber ist ein Versehen und muss nicht geparst
#: werden.
MAX_BYTES = 64 * 1024


@router.get("")
def zustand(db: Session = Depends(get_db)) -> dict[str, Any]:
    """Alle Tunnel samt Zustand, Sperre und gemessener Adresse."""
    return vpn.zustand(db)


@router.post("", status_code=status.HTTP_201_CREATED)
async def hochladen(
    datei: UploadFile = File(...),
    name: str = Form(""),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Nimmt eine .conf entgegen - aber nur, wenn sie etwas taugt.

    Der Dateiname dient als Vorschlag fuer die Bezeichnung. Das ist keine
    Bequemlichkeit: Die Dateien der Anbieter heissen ``de-ber-wg-001.conf``
    oder ``ProtonVPN-NL-42.conf``, und genau diese Namen will man in der Liste
    wiedererkennen - sie sagen, welcher Standort das ist.
    """
    roh = await datei.read(MAX_BYTES + 1)
    if len(roh) > MAX_BYTES:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"Die Datei ist groesser als {MAX_BYTES // 1024} KB - das ist keine "
            "WireGuard-Konfiguration.",
        )
    try:
        text = roh.decode("utf-8")
    except UnicodeDecodeError as e:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Die Datei ist keine Textdatei. Erwartet wird eine .conf, wie sie der "
            "VPN-Anbieter zum Herunterladen anbietet.",
        ) from e

    vorschlag = name.strip() or (datei.filename or "").rsplit(".", 1)[0]
    try:
        zeile = vpn.anlegen(db, vorschlag, text)
    except vpn.VpnFehler as e:
        # 422 statt 400: Die Anfrage war formal richtig, der Inhalt taugt nur
        # nicht. Die Oberflaeche zeigt die Meldung unveraendert an.
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from e

    vpn.laden(db)
    return {"id": zeile.id, **vpn.zustand(db)}


@router.put("/{tunnel_id}")
def aendern(
    tunnel_id: int,
    aenderungen: dict[str, Any] = Body(...),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Umbenennen oder ein- und ausschalten.

    Ausschalten beendet den Prozess, loescht aber nichts. Das ist der Schalter
    fuer den haeufigsten Fall: Ein Standort ist gerade schlecht erreichbar und
    soll eine Weile aus der Rotation - nicht fuer immer aus dem Bestand.
    """
    zeile = db.get(VpnTunnel, tunnel_id)
    if zeile is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Diesen Tunnel gibt es nicht.")
    if "name" in aenderungen:
        neu = str(aenderungen["name"]).strip()
        if not neu:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Der Name darf nicht leer sein.")
        zeile.name = neu
    if "aktiv" in aenderungen:
        zeile.aktiv = bool(aenderungen["aktiv"])
    db.commit()
    vpn.laden(db)
    return vpn.zustand(db)


@router.delete("/{tunnel_id}")
def entfernen(tunnel_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Loescht einen Tunnel samt seiner Konfigurationsdatei."""
    if not vpn.entfernen(db, tunnel_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Diesen Tunnel gibt es nicht.")
    vpn.laden(db)
    return vpn.zustand(db)


@router.post("/{tunnel_id}/test")
def probelauf(tunnel_id: int) -> dict[str, Any]:
    """Fragt durch den Tunnel nach der eigenen oeffentlichen Adresse.

    Die einzige Auskunft, die wirklich etwas beweist. Ein Prozess kann laufen,
    der Port kann offen sein und der Verkehr trotzdem nicht ankommen - dann
    steht hier ein Fehler statt einer Adresse. Und steht dort dieselbe Adresse
    wie ohne Tunnel, ist er zwar da, tut aber nichts.
    """
    try:
        return vpn.pruefen(tunnel_id)
    except vpn.VpnFehler as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e


@router.post("/test-direkt")
def probelauf_direkt() -> dict[str, Any]:
    """Die eigene Adresse ohne Tunnel - zum Vergleich.

    Ohne diesen Vergleichswert ist die Anzeige der Tunneladressen nur halb so
    viel wert: Erst wenn danebensteht, wie die Hausleitung heisst, sieht man
    auf einen Blick, ob ein Tunnel wirklich woanders herauskommt.
    """
    try:
        ip = vpn.exit_ip_ermitteln(None)
    except vpn.VpnFehler as e:
        return {"erfolg": False, "meldung": f"Die eigene Adresse war nicht zu ermitteln: {e}"}
    return {"erfolg": True, "ip": ip, "meldung": f"Ohne Tunnel tritt das Archiv als {ip} auf."}
