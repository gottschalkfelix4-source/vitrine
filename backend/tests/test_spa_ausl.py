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


# ------------------------------------------------------------------- PWA
#
# Die drei Dateien, ohne die sich das Archiv nicht als App ablegen laesst.
# Sie liegen im Wurzelverzeichnis und muessen den SPA-Rueckfall ueberstehen:
# Wuerde statt sw.js die Oberflaeche mit Status 200 ausgeliefert, scheiterte
# die Anmeldung des Workers mit einer Meldung ueber einen falschen Inhaltstyp -
# und niemand kaeme darauf, dass das Frontend selbst geantwortet hat.


@pytest.fixture
def pwa_client(tmp_path):
    (tmp_path / "index.html").write_text("<!doctype html><title>Vitrine</title>", encoding="utf-8")
    (tmp_path / "sw.js").write_text("self.addEventListener('fetch', () => {});", encoding="utf-8")
    (tmp_path / "manifest.webmanifest").write_text('{"name":"Vitrine"}', encoding="utf-8")
    (tmp_path / "icons").mkdir()
    (tmp_path / "icons" / "icon-192.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    app = FastAPI()
    app.mount("/", _EinseitenDateien(directory=tmp_path, html=True), name="frontend")
    return TestClient(app)


def test_service_worker_kommt_als_javascript(pwa_client):
    antwort = pwa_client.get("/sw.js")
    assert antwort.status_code == 200
    assert "javascript" in antwort.headers["content-type"]
    assert "<!doctype" not in antwort.text.lower(), "die Oberflaeche statt des Workers"


def test_manifest_wird_ausgeliefert(pwa_client):
    antwort = pwa_client.get("/manifest.webmanifest")
    assert antwort.status_code == 200
    assert antwort.json()["name"] == "Vitrine"


def test_symbole_sind_erreichbar(pwa_client):
    antwort = pwa_client.get("/icons/icon-192.png")
    assert antwort.status_code == 200
    assert antwort.content.startswith(b"\x89PNG")


def test_service_worker_gilt_fuer_die_ganze_seite(pwa_client):
    """Der Worker liegt im Wurzelverzeichnis, nicht unter /assets/. Nur von
    dort aus darf er alle Adressen der Anwendung bedienen - ein Worker unter
    /assets/sw.js koennte nur /assets/* abfangen und waere nutzlos."""
    assert pwa_client.get("/sw.js").status_code == 200
    assert pwa_client.get("/assets/sw.js").status_code == 404
