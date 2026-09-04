"""Die Zwangspause, wenn YouTube die IP-Adresse abweist.

Anlass war ein echter Schaden: Bei rund 1800 offenen Videos hat YouTube nach
etwa fuenfzig Downloads mit "Sign in to confirm you're not a bot" geantwortet.
Ohne Pause lief danach jeder folgende Auftrag binnen Sekunden in dieselbe Wand
und wurde als gescheitert vermerkt - die Warteschlange haette sich in einer
halben Stunde selbst abgeraeumt.
"""

from __future__ import annotations

import pytest
from yt_dlp.utils import YoutubeDLError

from app.services import drosselung, ytdlp


@pytest.fixture(autouse=True)
def _sauber():
    drosselung.zuruecksetzen()
    yield
    drosselung.zuruecksetzen()


# --------------------------------------------------------------- Einordnung


def test_bot_pruefung_gilt_als_drosselung():
    """Der Wortlaut aus dem Betrieb, unveraendert - samt typografischem
    Apostroph, den yt-dlp tatsaechlich ausgibt."""
    meldung = (
        "ERROR: [youtube] CJPGm0HDjO0: Sign in to confirm you\u2019re not a bot. "
        "Use --cookies-from-browser or --cookies for the authentication."
    )
    assert isinstance(ytdlp._einordnen(YoutubeDLError(meldung)), ytdlp.Gedrosselt)


@pytest.mark.parametrize(
    "meldung",
    [
        "HTTP Error 429: Too Many Requests",
        "Sign in to confirm you're not a bot",
        "ERROR: unable to download video data: HTTP Error 429",
    ],
)
def test_weitere_abweisungen(meldung):
    assert isinstance(ytdlp._einordnen(YoutubeDLError(meldung)), ytdlp.Gedrosselt)


def test_altersschranke_ist_keine_drosselung():
    """Der gefaehrlichste Fehltreffer.

    "Sign in to confirm your age" sieht der Bot-Pruefung zum Verwechseln
    aehnlich, ist aber eine Sache des Kontos. Als Drosselung eingeordnet legte
    ein einziges altersbeschraenktes Video die ganze Warteschlange fuer
    Minuten still - und zwar wieder und wieder, denn eine Pause aendert daran
    nichts.
    """
    fehler = ytdlp._einordnen(YoutubeDLError("Sign in to confirm your age"))
    assert not isinstance(fehler, ytdlp.Gedrosselt)


def test_drosselung_gewinnt_gegen_verschwunden():
    """Reihenfolge der Einordnung.

    Eine Abweisung, die zufaellig das Wort "unavailable" enthaelt, darf nicht
    als geloeschtes Video gelten - das Video wuerde nie wieder angefasst.
    """
    fehler = ytdlp._einordnen(
        YoutubeDLError("This content is unavailable. Sign in to confirm you're not a bot")
    )
    assert isinstance(fehler, ytdlp.Gedrosselt)
    assert not isinstance(fehler, ytdlp.VideoUnavailable)


def test_geloeschtes_video_bleibt_geloescht():
    assert isinstance(
        ytdlp._einordnen(YoutubeDLError("Video unavailable")), ytdlp.VideoUnavailable
    )


# ------------------------------------------------------------------- Pause


def test_erste_abweisung_pausiert_die_erste_stufe():
    dauer = drosselung.melden("nicht als Bot bestaetigt")
    assert dauer == drosselung.STUFEN_S[0]
    assert drosselung.wartezeit() > 0
    assert drosselung.zustand()["pausiert"] is True


def test_mehrere_straenge_stufen_nicht_gemeinsam_hoch(monkeypatch):
    """Der Fallstrick der naiven Umsetzung.

    Bei zwei parallelen Downloads laufen beide in dieselbe Wand und melden
    beide. Wuerde jede Meldung hochstufen, waere nach einer einzigen Sperre
    sofort die Hoechststufe erreicht - eine Stunde Stillstand wegen eines
    Ausrutschers.
    """
    drosselung.melden("erster Strang")
    drosselung.melden("zweiter Strang")
    drosselung.melden("dritter Strang")
    assert drosselung.zustand()["stufe"] == 1


def test_erst_nach_abgelaufener_pause_wird_hochgestuft(monkeypatch):
    uhr = [1000.0]
    monkeypatch.setattr(drosselung.time, "monotonic", lambda: uhr[0])

    assert drosselung.melden("erste") == drosselung.STUFEN_S[0]
    uhr[0] += drosselung.STUFEN_S[0] + 1  # Pause abgesessen
    assert drosselung.wartezeit() == 0

    assert drosselung.melden("zweite") == drosselung.STUFEN_S[1]
    uhr[0] += drosselung.STUFEN_S[1] + 1
    assert drosselung.melden("dritte") == drosselung.STUFEN_S[2]


def test_hoechste_stufe_gilt_dauerhaft(monkeypatch):
    uhr = [1000.0]
    monkeypatch.setattr(drosselung.time, "monotonic", lambda: uhr[0])
    for stufe in drosselung.STUFEN_S:
        assert drosselung.melden("wieder") == stufe
        uhr[0] += stufe + 1
    # Darueber hinaus bleibt es bei der letzten Stufe, statt ins Unendliche zu
    # wachsen - irgendwann muss auch mal wieder angeklopft werden.
    assert drosselung.melden("und wieder") == drosselung.STUFEN_S[-1]


def test_erfolg_hebt_pause_und_stufe_auf(monkeypatch):
    uhr = [1000.0]
    monkeypatch.setattr(drosselung.time, "monotonic", lambda: uhr[0])
    drosselung.melden("erste")
    uhr[0] += drosselung.STUFEN_S[0] + 1
    drosselung.melden("zweite")

    drosselung.entwarnung()
    assert drosselung.wartezeit() == 0
    assert drosselung.zustand()["stufe"] == 0
    # Und die naechste Abweisung beginnt wieder unten. Ohne das schleppte eine
    # einmalige Sperre ihre Stufe wochenlang mit.
    assert drosselung.melden("spaeter mal wieder") == drosselung.STUFEN_S[0]


def test_zustand_nennt_einen_zeitpunkt():
    drosselung.melden("nicht als Bot bestaetigt")
    z = drosselung.zustand()
    assert z["pausiert"] is True
    assert z["rest_s"] > 0
    assert isinstance(z["bis"], str)
    assert z["grund"] == "nicht als Bot bestaetigt"


def test_ohne_abweisung_ist_nichts_pausiert():
    z = drosselung.zustand()
    assert z == {
        "pausiert": False, "rest_s": 0, "bis": None, "stufe": 0, "grund": None,
        # Welcher Ausgang betroffen ist - ohne Sperre keiner. Mit mehreren
        # Tunneln nennt das Feld den, der als naechster wieder darf.
        "ausgang": None,
    }


# ------------------------------------------------- Wirkung auf die Auftraege


@pytest.fixture
def umgebung(tmp_path, monkeypatch):
    from app.models import Channel, Video, VideoStatus
    from tests.conftest import neue_sitzung

    monkeypatch.setattr(ytdlp.settings, "data_dir", tmp_path)
    ytdlp.settings.ensure_dirs()

    db = neue_sitzung()
    db.add(Channel(id="UCtest", name="Testkanal", auto_archive=True))
    db.add(Video(id="vid1", channel_id="UCtest", title="Platzhalter", status=VideoStatus.QUEUED))
    db.commit()
    return db


def test_abgewiesener_download_wird_nicht_als_fehlschlag_verbucht(umgebung, monkeypatch):
    """Der Kern der Sache.

    Vor dieser Aenderung galt die Bot-Pruefung als Fehler des Videos: Auftrag
    rot, Versuchszaehler hoch, Video auf "fehlgeschlagen". Bei 1800 offenen
    Videos hat das binnen Minuten die gesamte Warteschlange verbrannt, obwohl
    kein einziges Video etwas dafuer konnte.
    """
    from app.models import Job, JobStatus, JobType, Video, VideoStatus
    from app.services import jobs
    from app.workers.archive import archivieren

    db = umgebung

    def abgewiesen(*a, **kw):
        raise ytdlp.Gedrosselt("Sign in to confirm you're not a bot")

    monkeypatch.setattr(ytdlp, "download_video", abgewiesen)

    jobs.enqueue_archive(db, "vid1")
    job = jobs.claim_next(db, [JobType.VIDEO_ARCHIVE])
    assert job is not None
    archivieren(db, job)

    frisch = db.get(Job, job.id)
    assert frisch.status == JobStatus.PENDING, "muss wieder warten, nicht scheitern"
    assert frisch.error is None

    video = db.get(Video, "vid1")
    assert video.status == VideoStatus.QUEUED
    assert video.retry_count == 0, "die Abweisung galt der IP-Adresse, nicht dem Video"
    assert drosselung.wartezeit() > 0, "und alle Netzauftraege pausieren jetzt"


def _straenge_laufen_lassen(werk, gruppe, dauer=0.3):
    """Laesst einen Arbeiterstrang kurz laufen und beendet ihn wieder."""
    import threading
    import time

    t = threading.Thread(target=werk._arbeiten, args=(gruppe, 0), daemon=True)
    t.start()
    time.sleep(dauer)
    werk._stop.set()
    t.join(timeout=2.0)
    assert not t.is_alive(), "Strang haengt - die Pause muss unterbrechbar bleiben"


def test_netzgruppe_holt_waehrend_der_pause_keinen_auftrag(monkeypatch):
    """Sonst waere die Pause wirkungslos: Der Strang griffe sofort das naechste
    Video und liefe in dieselbe Wand - jeder Versuch verlaengert die Sperre."""
    from app.workers import runner

    geholt: list[str] = []

    def merken(*a, **kw):
        geholt.append("x")
        return None

    monkeypatch.setattr(runner.jobs, "claim_next", merken)
    monkeypatch.setattr(runner, "DROSSEL_TAKT_S", 0.02)
    monkeypatch.setattr(runner, "LEERLAUF_S", 0.02)

    netz = next(g for g in runner._gruppen() if g.name == "netz")
    werk = runner.Arbeiterwerk()
    werk._soll["netz"] = 1

    # Zur Gegenprobe erst ohne Pause: Der Strang muss ganz normal zugreifen,
    # sonst sagt der eigentliche Test nichts aus.
    _straenge_laufen_lassen(werk, netz)
    assert geholt, "ohne Pause muss der Strang Auftraege holen"

    geholt.clear()
    werk._stop.clear()
    drosselung.melden("nicht als Bot bestaetigt")
    _straenge_laufen_lassen(werk, netz)
    assert geholt == [], "waehrend der Pause darf kein Auftrag geholt werden"


def test_nur_die_netzgruppe_pausiert():
    """Recodierung und Vorbereitung arbeiten auf bereits geladenen Dateien.
    Sie mitpausieren zu lassen waere reiner Stillstand ohne Nutzen."""
    from app.workers import runner

    nach_name = {g.name: g for g in runner._gruppen()}
    assert nach_name["netz"].netz is True
    assert nach_name["recodierung"].netz is False
    assert nach_name["vorbereitung"].netz is False


# ----------------------------------------------- Mehrere Ausgaenge (WireGuard)
#
# Der Punkt der ganzen Umstellung: Eine Sperre gilt einer Adresse. Solange es
# nur eine gab, war "Adresse gesperrt" dasselbe wie "Archiv steht". Mit
# mehreren Tunneln ist es das nicht mehr - und das darf die Buchfuehrung nicht
# verwechseln, sonst wirft sie genau die Bandbreite weg, fuer die die Tunnel
# eingerichtet wurden.


def test_sperre_gilt_nur_dem_gemeldeten_ausgang():
    drosselung.melden("abgewiesen", ausgang="tunnel-1")
    assert drosselung.wartezeit("tunnel-1") > 0
    assert drosselung.wartezeit("tunnel-2") == 0
    assert drosselung.wartezeit(drosselung.DIREKT) == 0


def test_jeder_ausgang_hat_seine_eigene_leiter(monkeypatch):
    """Ein oft gesperrter Tunnel darf einen frischen nicht mitbelasten."""
    uhr = [1000.0]
    monkeypatch.setattr(drosselung.time, "monotonic", lambda: uhr[0])

    assert drosselung.melden("erste", ausgang="tunnel-1") == drosselung.STUFEN_S[0]
    uhr[0] += drosselung.STUFEN_S[0] + 1
    assert drosselung.melden("zweite", ausgang="tunnel-1") == drosselung.STUFEN_S[1]

    # Der zweite Tunnel war nie auffaellig - er beginnt unten.
    assert drosselung.melden("erste dort", ausgang="tunnel-2") == drosselung.STUFEN_S[0]


def test_freie_ausgaenge_werden_genannt():
    drosselung.melden("abgewiesen", ausgang="tunnel-2")
    assert drosselung.frei(["tunnel-1", "tunnel-2", "tunnel-3"]) == ["tunnel-1", "tunnel-3"]


def test_solange_ein_ausgang_frei_ist_pausiert_nichts():
    """Der Kern. Frueher hiess eine einzige Abweisung: alles steht."""
    drosselung.melden("abgewiesen", ausgang="tunnel-1")
    z = drosselung.zustand(["tunnel-1", "tunnel-2"])
    assert z["pausiert"] is False
    assert z["rest_s"] == 0
    assert drosselung.kuerzeste_wartezeit(["tunnel-1", "tunnel-2"]) == 0


def test_erst_wenn_alle_gesperrt_sind_wird_pausiert(monkeypatch):
    uhr = [1000.0]
    monkeypatch.setattr(drosselung.time, "monotonic", lambda: uhr[0])

    drosselung.melden("abgewiesen", ausgang="tunnel-1")
    uhr[0] += 100.0  # tunnel-1 hat schon ein Stueck abgesessen
    drosselung.melden("abgewiesen", ausgang="tunnel-2")

    z = drosselung.zustand(["tunnel-1", "tunnel-2"])
    assert z["pausiert"] is True
    # Genannt wird der, der als naechster wieder darf - nicht der zuletzt
    # gesperrte. Wer wartet, will die kuerzeste Frist wissen.
    assert z["ausgang"] == "tunnel-1"
    assert z["rest_s"] == round(drosselung.STUFEN_S[0] - 100.0)


def test_entwarnung_gilt_nur_dem_eigenen_ausgang():
    drosselung.melden("abgewiesen", ausgang="tunnel-1")
    drosselung.melden("abgewiesen", ausgang="tunnel-2")
    drosselung.entwarnung("tunnel-1")
    assert drosselung.wartezeit("tunnel-1") == 0
    assert drosselung.wartezeit("tunnel-2") > 0


def test_ohne_angabe_gilt_der_ausgang_des_strangs():
    """So melden die Bearbeiter: Sie wissen nicht, ueber welchen Tunnel sie
    gerade arbeiten - der Arbeiterstrang hat das vorher festgelegt."""
    from app.services import ausgang

    tunnel = ausgang.Ausgang(id="tunnel-7", name="Berlin", proxy="socks5h://127.0.0.1:51807")
    with ausgang.benutzen(tunnel):
        drosselung.melden("abgewiesen")
        assert drosselung.wartezeit() > 0
    # Ausserhalb des Blocks gilt wieder die Direktverbindung - und die ist frei.
    assert drosselung.wartezeit() == 0
    assert drosselung.wartezeit("tunnel-7") > 0
