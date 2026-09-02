"""Ausliefern der Oberflaeche neben der API.

Diese Weiche gibt es nur im Container: Dort liefert derselbe Prozess das
gebaute Frontend und die API aus. Im Entwicklungsbetrieb laeuft Vite getrennt,
weshalb Fehler hier - so steht es auch im Kommentar der Klasse selbst - genau
einmal auffallen, naemlich nach dem Deploy. Getestet wurde sie bisher gar
nicht.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.main import _EinseitenDateien


@pytest.fixture
def client(tmp_path):
    """Eine App wie im Container: ein paar API-Pfade, darunter das Frontend."""
    (tmp_path / "index.html").write_text("<!doctype html><title>Vitrine</title>", encoding="utf-8")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "haupt.js").write_text("console.log(1)", encoding="utf-8")

    app = FastAPI()

    @app.get("/api/health")
    def health():
        return {"ok": True}

    app.mount("/", _EinseitenDateien(directory=tmp_path, html=True), name="frontend")
    return TestClient(app)


def test_echte_datei_wird_ausgeliefert(client):
    assert client.get("/assets/haupt.js").text == "console.log(1)"


def test_api_geht_vor(client):
    assert client.get("/api/health").json() == {"ok": True}


@pytest.mark.parametrize("pfad", ["/video/dQw4w9WgXcQ", "/playlist/PLabc", "/speicher"])
def test_tiefe_adressen_liefern_die_oberflaeche(client, pfad):
    """Ein geteilter Link oder ein F5 auf einer Unterseite muss funktionieren -
    diese Adressen sind Zustaende im Browser, keine Dateien."""
    antwort = client.get(pfad)
    assert antwort.status_code == 200
    assert "<!doctype html>" in antwort.text


def test_fehlende_datei_mit_endung_bleibt_ein_fehler(client):
    """Sonst bekaeme ein fehlendes Bild stillschweigend HTML zurueck, und die
    Fehlersuche faengt bei null an."""
    assert client.get("/assets/gibtsnicht.js").status_code == 404


@pytest.mark.parametrize("pfad", ["/api/gibtsnicht", "/api/videos/x/unbekannt", "/api"])
def test_unbekannte_api_pfade_liefern_keine_html_seite(client, pfad):
    """Ein Tippfehler in einer API-Adresse ergab bisher die Oberflaeche mit
    Status 200. Ein Aufrufer, der JSON erwartet, scheitert dann an HTML statt
    an einer klaren Meldung - und ein Test, der einen 404 prueft, ginge
    stillschweigend durch."""
    antwort = client.get(pfad)
    assert antwort.status_code == 404
    assert "<!doctype html>" not in antwort.text.lower()
