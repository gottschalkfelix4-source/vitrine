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
