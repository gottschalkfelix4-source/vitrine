"""Kein Kontakt zu YouTube: Grenzen, Parallelitaet und Neustarts lokal pruefen."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from app.services import abbruch
from app.services import anfragelimit as limit


@pytest.fixture
def uhr(monkeypatch):
    jetzt = [1_000_000.0]
    abbruch.zuruecksetzen()
    monkeypatch.setattr(limit.time, "time", lambda: jetzt[0])
    monkeypatch.setattr(limit.time, "sleep", lambda sekunden: jetzt.__setitem__(0, jetzt[0] + sekunden))
    for name, wert in {
        "youtube_anfragen_stunde": 100, "youtube_anfragen_tag": 1000,
        "youtube_videos_stunde": 10, "youtube_videos_tag": 100,
        "youtube_anfrage_abstand": 5.0,
    }.items():
        monkeypatch.setattr(limit.settings, name, wert)
    yield jetzt
    abbruch.zuruecksetzen()


def test_stundenfenster_ist_gleitend_und_verbraucht_keinen_abgewiesenen_versuch(uhr, monkeypatch):
    monkeypatch.setattr(limit.settings, "youtube_anfragen_stunde", 2)
    limit.vor_anfrage("https://www.youtube.com/watch?v=1")
    limit.vor_anfrage("https://www.youtube.com/youtubei/v1/player")
    assert uhr[0] == 1_000_005.0  # gemeinsame Pause ueber zwei Aufrufer
    with pytest.raises(limit.Pause) as e:
        limit.vor_anfrage("https://www.youtube.com/watch?v=3")
    assert e.value.rest_s == 3595
    assert limit.zustand()["anfragen_tag"] == 2
    uhr[0] = 1_003_600.0
    limit.vor_anfrage("https://www.youtube.com/watch?v=3")
    assert limit.zustand()["anfragen_stunde"] == 2
    assert limit.zustand()["anfragen_tag"] == 3


def test_tagesfenster_ist_24_stunden_statt_reset_um_mitternacht(uhr, monkeypatch):
    monkeypatch.setattr(limit.settings, "youtube_anfragen_tag", 2)
    limit.vor_anfrage("https://youtube.com/watch?v=1")
    uhr[0] += 3700
    limit.vor_anfrage("https://youtube.com/watch?v=2")
    with pytest.raises(limit.Pause) as e:
        limit.vor_anfrage("https://youtube.com/watch?v=3")
    assert e.value.rest_s == 86400 - 3700
    assert "24 Stunden" in e.value.grund
    uhr[0] = 1_000_000 + 86400
    limit.vor_anfrage("https://youtube.com/watch?v=3")
    assert limit.zustand()["anfragen_tag"] == 2


def test_medien_und_fremde_hosts_verbrauchen_keine_playerquote(uhr, monkeypatch):
    monkeypatch.setattr(limit.settings, "youtube_anfragen_stunde", 1)
    limit.vor_anfrage("https://youtube.com/watch?v=1")
    for url in (
        "https://rr1.googlevideo.com/videoplayback", "https://i.ytimg.com/vi/1/default.jpg",
        "https://yt3.ggpht.com/avatar",
    ):
        limit.vor_anfrage(url)
    for url in ("https://youtube.com.example.org/watch", "https://example.org", "http://[kaputt"):
        limit.vor_anfrage(url)
    status = limit.zustand()
    assert status["anfragen_tag"] == 1
    assert status["medienanfragen_tag"] == 3
    assert uhr[0] == 1_000_000.0


def test_videostarts_sind_verteilt_und_gelten_auch_fuer_andere_tunnel(uhr):
    from app.services.ausgang import Ausgang, benutzen

    with benutzen(Ausgang(id="tunnel-1")):
        limit.vor_video()
    with benutzen(Ausgang(id="tunnel-2")), pytest.raises(limit.Pause) as e:
        limit.vor_video()
    assert e.value.rest_s == 360
    assert limit.zustand()["videos_tag"] == 1
    uhr[0] += 360
    limit.vor_video()
    assert limit.zustand()["videos_tag"] == 2


def test_video_tagesbudget_blockiert_weitere_starts(uhr, monkeypatch):
    monkeypatch.setattr(limit.settings, "youtube_videos_tag", 1)
    limit.vor_video()
    uhr[0] += 360
    with pytest.raises(limit.Pause) as e:
        limit.vor_video()
    assert e.value.rest_s == 86400 - 360
    assert limit.wartezeit(nur_anfragen=True) == 0
    # Die Steueranfragen eines bereits gestarteten Downloads bleiben moeglich.
    limit.vor_anfrage("https://youtube.com/youtubei/v1/player")
    assert limit.zustand()["videos_tag"] == 1


def test_parallelstart_reserviert_genau_einen_platz(uhr):
    barriere = threading.Barrier(6)

    def starten(_):
        barriere.wait(timeout=5)
        try:
            limit.vor_video()
            return "gestartet"
        except limit.Pause as e:
            assert "gleichmäßig" in e.grund
            return "wartet"

    with ThreadPoolExecutor(max_workers=6) as pool:
        resultate = list(pool.map(starten, range(6)))
    assert resultate.count("gestartet") == 1
    assert resultate.count("wartet") == 5
    assert limit.zustand()["videos_tag"] == 1


def test_parallelrequests_ueberziehen_budget_nicht(uhr, monkeypatch):
    monkeypatch.setattr(limit.settings, "youtube_anfragen_stunde", 3)
    # Die Uhr springt im Test, statt echte Wartezeit zu verbrauchen. Alle
    # Threads konkurrieren weiterhin um dieselbe atomare Reservierung.
    monkeypatch.setattr(limit.settings, "youtube_anfrage_abstand", 0.1)
    barriere = threading.Barrier(8)

    def anfragen(_):
        barriere.wait(timeout=5)
        try:
            limit.vor_anfrage("https://youtube.com/youtubei/v1/player")
            return "gesendet"
        except limit.Pause as e:
            assert "Stundenbudget" in e.grund
            return "wartet"

    with ThreadPoolExecutor(max_workers=8) as pool:
        resultate = list(pool.map(anfragen, range(8)))
    assert resultate.count("gesendet") == 3
    assert limit.zustand()["anfragen_stunde"] == 3


def test_abweisung_stoppt_auch_medien_und_reduziert_spaeter_alle_quoten(uhr):
    limit.abweisung()
    status = limit.zustand()
    assert status["pausiert"]
    assert status["rest_s"] == 3600
    assert status["limit_anfragen_stunde"] == 50
    assert status["limit_videos_tag"] == 50
    with pytest.raises(limit.Pause):
        limit.vor_anfrage("https://rr1.googlevideo.com/videoplayback")
    uhr[0] += 60
    limit.abweisung()  # Parallelmeldung verlaengert die Pause nicht.
    assert limit.wartezeit() == 3540
    uhr[0] += 3540
    assert limit.wartezeit() == 0
    assert limit.zustand()["reduziert"]
    limit.vor_anfrage("https://youtube.com/watch?v=1")
    limit.vor_anfrage("https://youtube.com/watch?v=2")
    assert uhr[0] == 1_003_610  # doppelt so langer Requestabstand
    uhr[0] += 86400
    assert not limit.zustand()["reduziert"]
    assert limit.zustand()["limit_anfragen_stunde"] == 100


def test_wiederholte_abweisung_eskaliert_nach_ablauf_der_pause(uhr):
    limit.abweisung()
    uhr[0] += 3600
    limit.abweisung()
    assert limit.wartezeit() == 21600
    assert limit.zustand()["limit_anfragen_tag"] == 250
    uhr[0] += 21600
    limit.abweisung()
    assert limit.wartezeit() == 86400
    uhr[0] += 86400
    limit.abweisung()
    assert limit.wartezeit() == 172800


def test_retry_after_verlaengert_mindestfrist_ohne_erneute_eskalation(uhr):
    limit.abweisung(retry_after_s=7200)
    assert limit.wartezeit() == 7200
    assert limit.zustand()["limit_anfragen_stunde"] == 50
    uhr[0] += 60
    limit.abweisung(retry_after_s=100)
    assert limit.wartezeit() == 7140
    limit.abweisung(retry_after_s=10000)
    assert limit.wartezeit() == 10000
    assert limit.zustand()["limit_anfragen_stunde"] == 50


@pytest.mark.parametrize("retry", [float("nan"), float("inf"), -1])
def test_ungueltiges_retry_after_umgeht_eigene_schutzpause_nicht(uhr, retry):
    limit.abweisung(retry_after_s=retry)
    assert limit.wartezeit() == 3600


def test_verkleinerte_quote_beruecksichtigt_schon_verbrauchte_anfragen(uhr, monkeypatch):
    monkeypatch.setattr(limit.settings, "youtube_anfragen_stunde", 100)
    monkeypatch.setattr(limit.settings, "youtube_anfragen_tag", 4)
    for _ in range(3):
        limit.vor_anfrage("https://youtube.com/watch?v=1")
    limit.abweisung()
    uhr[0] += 3600
    with pytest.raises(limit.Pause) as e:
        limit.vor_anfrage("https://youtube.com/watch?v=4")
    assert "24 Stunden" in e.value.grund
    assert limit.zustand()["anfragen_tag"] == 3


def test_neuer_prozess_liest_pausen_und_zaehler_weiter():
    abbruch.zuruecksetzen()
    limit.vor_video()
    limit.vor_anfrage("https://youtube.com/watch?v=1")
    limit.abweisung()
    code = """import json, sys
from pathlib import Path
from app.services import anfragelimit
anfragelimit._pfad = lambda: Path(sys.argv[1])
print(json.dumps(anfragelimit.zustand()))
"""
    ergebnis = subprocess.run(
        [sys.executable, "-c", code, str(limit._pfad())], cwd=Path(__file__).resolve().parents[1],
        check=True, capture_output=True, text=True, timeout=15,
    )
    status = json.loads(ergebnis.stdout)
    assert status["videos_tag"] == 1
    assert status["anfragen_tag"] == 1
    assert status["pausiert"]
    assert 3500 < status["rest_s"] <= 3600
    assert status["reduziert"]


def test_kaputte_datei_schaltet_schutz_nicht_aus(uhr):
    limit._pfad().write_bytes(b"kein sqlite")
    with pytest.raises(limit.Pause, match="speichern"):
        limit.vor_anfrage("https://youtube.com/watch?v=1")
    status = limit.zustand()
    assert status["pausiert"]
    assert status["anfragen_tag"] is None
    assert "speichern" in status["grund"]


def test_speicherfehler_sendet_nichts(uhr, monkeypatch):
    def kaputt(*_args, **_kwargs):
        raise OSError("kein Platz")

    monkeypatch.setattr(limit.sqlite3, "connect", kaputt)
    with pytest.raises(limit.Pause, match="speichern"):
        limit.vor_anfrage("https://youtube.com/watch?v=1")


def test_rueckwaertsuhr_hebt_keine_pause_auf(uhr):
    limit.abweisung()
    uhr[0] -= 36000
    assert limit.wartezeit() == 3600
    with pytest.raises(limit.Pause):
        limit.vor_video()


def test_kurze_requestpause_bleibt_beim_herunterfahren_abbrechbar(uhr, monkeypatch):
    limit.vor_anfrage("https://youtube.com/watch?v=1")
    monkeypatch.setattr(time, "sleep", lambda _: abbruch.anfordern())
    with pytest.raises(abbruch.Abgebrochen):
        limit.vor_anfrage("https://youtube.com/watch?v=2")
    assert limit.zustand()["anfragen_tag"] == 1


@pytest.mark.parametrize("fenster,dauer", [("stunde", 3600), ("tag", 86400)])
def test_hintergrundabgleich_wartet_budgetfenster_ohne_abbruch_ab(uhr, monkeypatch, fenster, dauer):
    monkeypatch.setattr(limit.settings, f"youtube_anfragen_{fenster}", 1)
    limit.vor_anfrage("https://youtube.com/youtubei/v1/browse")
    uhr[0] += dauer - 2
    beginn = uhr[0]
    limit.vor_anfrage("https://youtube.com/youtubei/v1/browse", budget_abwarten=True)
    assert uhr[0] == beginn + 2
    assert limit.zustand()[f"anfragen_{fenster}"] == 1


def test_lange_budgetpause_haelt_keine_datenbanksperre_und_bleibt_abbrechbar(uhr, monkeypatch):
    monkeypatch.setattr(limit.settings, "youtube_anfragen_stunde", 1)
    limit.vor_anfrage("https://youtube.com/youtubei/v1/browse")

    def schlafen(_):
        # Wuerde blockieren, wenn die Transaktion/Sperre waehrend sleep offen bliebe.
        assert limit.zustand()["anfragen_stunde"] == 1
        abbruch.anfordern()

    monkeypatch.setattr(limit.time, "sleep", schlafen)
    with pytest.raises(abbruch.Abgebrochen):
        limit.vor_anfrage("https://youtube.com/youtubei/v1/browse", budget_abwarten=True)


def test_echte_sperre_unterbricht_auch_bereits_wartende_pagination(uhr, monkeypatch):
    monkeypatch.setattr(limit.settings, "youtube_anfragen_stunde", 1)
    limit.vor_anfrage("https://youtube.com/youtubei/v1/browse")

    def schlafen(sekunden):
        uhr[0] += sekunden
        limit.abweisung()

    monkeypatch.setattr(limit.time, "sleep", schlafen)
    with pytest.raises(limit.Pause, match="YouTube-Schutzpause"):
        limit.vor_anfrage("https://youtube.com/youtubei/v1/browse", budget_abwarten=True)
    assert limit.zustand()["anfragen_tag"] == 1
