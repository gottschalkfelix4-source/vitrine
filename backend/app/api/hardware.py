"""Auskunft ueber den Hardware-Encoder.

Getrennt vom Rest der Einstellungen, weil hier etwas anderes passiert: Die
uebrigen Felder speichern einen Wert, dieser Endpunkt kodiert tatsaechlich ein
paar Sekunden Video und berichtet, was dabei herauskam.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter

from app.services import hardware

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/hardware", tags=["hardware"])


@router.get("")
def zustand() -> dict[str, Any]:
    """Die billigen Auskuenfte: Karte durchgereicht? Treiber da?"""
    return hardware.zustand().als_dict()


@router.post("/test")
def probelauf() -> dict[str, Any]:
    """Kodiert wirklich - einmal je moeglichem Weg.

    Dauert einige Sekunden. Das ist die einzige Auskunft, der man trauen kann:
    Karte vorhanden, Treiber vorhanden und Encoder gelistet koennen alle drei
    zutreffen, waehrend die Kodierung trotzdem scheitert.
    """
    return hardware.zustand(mit_probe=True).als_dict()
