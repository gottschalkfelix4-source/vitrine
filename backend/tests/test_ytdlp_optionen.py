"""Prueft, dass eine schlechte Konfiguration keinen Serverfehler ausloest.

Anlass war der erste Unraid-Betrieb: Das Hinzufuegen eines Kanals endete mit
"Internal Server Error". Die Ursache lag nicht bei YouTube, sondern in der
leeren Cookie-Variable des Templates - yt-dlp brach beim Aufbau ab, mit einer
Ausnahme, die :func:`app.services.ytdlp._extract` nicht gefangen hat. Der
Nutzer sah einen 500er ohne jeden Hinweis, was falsch war.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from yt_dlp.utils import YoutubeDLError

from app.services import ytdlp


def test_fehlende_cookie_datei_wird_uebergangen(monkeypatch, tmp_path, caplog):
    monkeypatch.setattr(ytdlp.settings, "ytdlp_cookies_file", tmp_path / "fehlt.txt")
    with caplog.at_level("WARNING"):
        opts = ytdlp._base_opts()
    assert "cookiefile" not in opts
    assert "fehlt.txt" in caplog.text


def test_vorhandene_cookie_datei_wird_benutzt(monkeypatch, tmp_path):
    datei = tmp_path / "cookies.txt"
    datei.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    monkeypatch.setattr(ytdlp.settings, "ytdlp_cookies_file", datei)
    assert ytdlp._base_opts()["cookiefile"] == str(datei)


def test_verzeichnis_als_cookie_pfad_bricht_nicht_durch(monkeypatch, tmp_path):
    """Der Originalfall: Path(".") ist ein Verzeichnis, keine Datei."""
    monkeypatch.setattr(ytdlp.settings, "ytdlp_cookies_file", Path("."))
    assert "cookiefile" not in ytdlp._base_opts()


def test_jeder_ytdlp_fehler_wird_zu_ytdlperror(monkeypatch):
    """_extract muss die Basisklasse fangen, nicht nur DownloadError.

    CookieLoadError etwa erbt direkt von YoutubeDLError und waere sonst als
    unbehandelte Ausnahme bis in die HTTP-Antwort durchgeschlagen.
    """

    class Kaputt:
        def __enter__(self):
            raise YoutubeDLError("irgendwas in yt-dlp")

        def __exit__(self, *_):
            return False

    monkeypatch.setattr(ytdlp.yt_dlp, "YoutubeDL", lambda _opts: Kaputt())
    with pytest.raises(ytdlp.YtdlpError):
        ytdlp._extract("https://example.invalid", {})


# ------------------------------------------------- Notausgaenge gegen Sperren


def test_ohne_einstellung_keine_extraktorargumente():
    """Der Normalfall: yt-dlp entscheidet selbst, welche Clients es anfragt.

    Das ist kein Versehen, sondern Absicht. yt-dlp zieht die Wahl bei jeder
    Version nach; eine feste Vorgabe hier wuerde diese Pflege aushebeln und
    im schlechtesten Fall dauerhaft 360p archivieren.
    """
    opts = ytdlp._base_opts()
    assert "extractor_args" not in opts
    assert "sleep_interval_requests" not in opts


def test_player_clients_landen_bei_yt_dlp(monkeypatch):
    monkeypatch.setattr(ytdlp.settings, "ytdlp_player_clients", ["tv", "web_safari"])
    assert ytdlp._base_opts()["extractor_args"] == {
        "youtube": {"player_client": ["tv", "web_safari"]}
    }


def test_pause_zwischen_anfragen_wird_gesetzt(monkeypatch):
    monkeypatch.setattr(ytdlp.settings, "ytdlp_sleep_requests", 1.5)
    assert ytdlp._base_opts()["sleep_interval_requests"] == 1.5


def test_verschluckter_fehler_wird_wieder_sichtbar(monkeypatch):
    """Der blinde Fleck von ``ignoreerrors``.

    Beim Auflisten eines Kanals ist die Option unverzichtbar - ein gesperrtes
    Video darf einen Abgleich ueber tausend Videos nicht abbrechen. yt-dlp
    wirft dann aber nicht mehr, sondern schreibt den Grund ins Log und liefert
    None. Eine Abweisung durch YouTube war so nicht von einem kaputten Kanal zu
    unterscheiden: beides endete als "keine Metadaten".
    """

    class LeererLauf:
        def __init__(self, opts):
            self._logger = opts["logger"]

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def extract_info(self, _url, download=False):
            self._logger.error("ERROR: [youtube] abc: Sign in to confirm you're not a bot.")
            return None

    monkeypatch.setattr(ytdlp.yt_dlp, "YoutubeDL", LeererLauf)
    with pytest.raises(ytdlp.Gedrosselt) as fehler:
        ytdlp._extract("https://example.invalid", {})
    assert "not a bot" in str(fehler.value)


def test_ohne_meldung_bleibt_es_bei_der_knappen_auskunft(monkeypatch):
    class LeererLauf:
        def __init__(self, _opts):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def extract_info(self, _url, download=False):
            return None

    monkeypatch.setattr(ytdlp.yt_dlp, "YoutubeDL", LeererLauf)
    with pytest.raises(ytdlp.YtdlpError, match="keine Metadaten"):
        ytdlp._extract("https://example.invalid", {})
