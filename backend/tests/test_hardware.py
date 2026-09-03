"""Der Hardware-Encoder-Pfad.

Diese Tests halten einen Fehler fest, der lange unbemerkt blieb, weil er sich
nicht meldet: ffmpeg **ignoriert** eine unbekannte Encoder-Option
stillschweigend. Die frueheren Befehle uebergaben durchweg ``-cq``, was es nur
bei NVENC gibt. Bei QSV und VAAPI fiel die eingestellte Qualitaet damit wortlos
unter den Tisch - kein Fehler, keine Warnung, nur unerklaerliche Dateigroessen.

Und dem VAAPI-Befehl fehlten Geraet und ``hwupload`` komplett. Er konnte nie
funktioniert haben.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config import ArchiveCodec, HardwareAccel, settings
from app.services import hardware, media


def cmd(hw: HardwareAccel, codec: ArchiveCodec = ArchiveCodec.AV1) -> list[str]:
    return media.build_archive_cmd(Path("in.mkv"), Path("out.webm"), codec, hwaccel=hw)


# ------------------------------------------------------- Qualitaet kommt an


@pytest.mark.parametrize(
    ("hw", "encoder", "schalter"),
    [
        (HardwareAccel.QSV, "av1_qsv", "-global_quality"),
        (HardwareAccel.VAAPI, "av1_vaapi", "-qp"),
        (HardwareAccel.NVENC, "av1_nvenc", "-cq"),
        (HardwareAccel.NONE, "libsvtav1", "-crf"),
    ],
)
def test_jeder_encoder_bekommt_seine_eigene_qualitaetsschraube(hw, encoder, schalter, monkeypatch):
    monkeypatch.setattr(settings, "av1_crf", 27)
    c = cmd(hw)
    assert encoder in c
    assert schalter in c, f"{encoder} bekommt {schalter} nicht"
    assert c[c.index(schalter) + 1] == "27"


@pytest.mark.parametrize("hw", [HardwareAccel.QSV, HardwareAccel.VAAPI])
def test_kein_cq_mehr_bei_intel(hw):
    """Der eigentliche Fehler. ``-cq`` gibt es nur bei NVENC; bei QSV und VAAPI
    wurde es kommentarlos verschluckt und die Qualitaetseinstellung war ohne
    jede Wirkung."""
    assert "-cq" not in cmd(hw)


def test_vaapi_bekommt_geraet_und_hwupload():
    """Ohne beides kann VAAPI grundsaetzlich nicht arbeiten: Der Encoder nimmt
    nur Hardware-Bilder, ffmpeg liefert aus einer Datei Software-Bilder."""
    c = cmd(HardwareAccel.VAAPI)
    assert "-init_hw_device" in c
    assert c[c.index("-init_hw_device") + 1].startswith("vaapi=hw:")
    assert "-filter_hw_device" in c
    assert "hwupload" in c[c.index("-vf") + 1]


def test_geraet_steht_vor_dem_eingang():
    """Reihenfolge ist bei ffmpeg bedeutungstragend: Nach ``-i`` ist
    ``-init_hw_device`` wirkungslos."""
    c = cmd(HardwareAccel.VAAPI)
    assert c.index("-init_hw_device") < c.index("-i")


def test_geraetepfad_ist_einstellbar(monkeypatch):
    """Steckt neben der Arc noch eine iGPU, ist die Arc womoeglich renderD129."""
    monkeypatch.setattr(settings, "hwaccel_device", "/dev/dri/renderD129")
    c = cmd(HardwareAccel.VAAPI)
    assert c[c.index("-init_hw_device") + 1] == "vaapi=hw:/dev/dri/renderD129"


def test_software_bleibt_unveraendert():
    """Der Weg, den alle bisher benutzt haben, darf sich nicht verschieben."""
    c = cmd(HardwareAccel.NONE)
    assert "libsvtav1" in c
    assert "-pix_fmt" in c and c[c.index("-pix_fmt") + 1] == "yuv420p10le"
    assert "-svtav1-params" in c
    assert "-init_hw_device" not in c
    assert "-vf" not in c


def test_hardware_erzwingt_kein_10_bit():
    """Ein erzwungenes 10-Bit laesst je nach Generation die Sitzung gar nicht
    erst zustande kommen - das ueberlaesst man dem Treiber."""
    assert "-pix_fmt" not in cmd(HardwareAccel.QSV)


def test_svt_preset_wandert_nicht_auf_die_hardware(monkeypatch):
    """SVT-AV1 zaehlt 0-13, QSV zaehlt 1-7 in die andere Richtung. Eine stille
    Umrechnung waere genau die Art Fehler, die niemand nachprueft."""
    monkeypatch.setattr(settings, "av1_preset", 12)
    c = cmd(HardwareAccel.QSV)
    assert c[c.index("-preset") + 1] == media._QSV_PRESET
    assert "12" not in c


def test_hevc_hardware_bekommt_kein_x265_preset(monkeypatch):
    """``-preset medium`` ist bei x265 richtig und bei VAAPI unbekannt - dort
    waere es wieder eine stillschweigend verschluckte Option."""
    monkeypatch.setattr(settings, "hevc_crf", 24)
    c = media.build_archive_cmd(
        Path("in.mkv"), Path("out.mp4"), ArchiveCodec.HEVC, hwaccel=HardwareAccel.VAAPI
    )
    assert "hevc_vaapi" in c
    assert "-preset" not in c
    assert c[c.index("-qp") + 1] == "24"


def test_eingestellte_beschleunigung_gilt_ohne_argument(monkeypatch):
    monkeypatch.setattr(settings, "hwaccel", HardwareAccel.QSV)
    c = media.build_archive_cmd(Path("in.mkv"), Path("out.webm"), ArchiveCodec.AV1)
    assert "av1_qsv" in c


# ------------------------------------------------------------------ Befund


def test_ohne_dev_dri_wird_die_ursache_benannt(monkeypatch, tmp_path):
    """Die haeufigste Ursache und die von aussen unsichtbarste: Die Karte ist
    gar nicht in den Container gereicht."""
    monkeypatch.setattr(hardware, "DRI", tmp_path / "gibtsnicht")
    z = hardware.zustand()
    assert z.geraete == []
    assert "/dev/dri" in z.meldung
    assert "Unraid" in z.meldung


def test_geraete_ohne_treiber_werden_erkannt(monkeypatch, tmp_path):
    """Karte da, Treiber fehlt - genau der Zustand des alten Images. ffmpeg
    listet die Encoder trotzdem auf, deshalb muss es hier stehen."""
    dri = tmp_path / "dri"
    dri.mkdir()
    (dri / "renderD128").touch()
    monkeypatch.setattr(hardware, "DRI", dri)
    monkeypatch.setattr(hardware, "_treiberdateien", list)
    z = hardware.zustand()
    assert z.geraete == ["renderD128"]
    assert z.treiber_vorhanden is False
    assert "kein VA-API-Treiber" in z.meldung


def test_probe_ohne_hardware_wird_uebersprungen(monkeypatch, tmp_path):
    """Einen Probe-Encode auf eine nicht vorhandene Karte zu werfen, kostet nur
    Zeit und liefert eine Fehlermeldung, die niemanden weiterbringt."""
    monkeypatch.setattr(hardware, "DRI", tmp_path / "gibtsnicht")
    gerufen: list[str] = []
    monkeypatch.setattr(
        hardware, "probe_encode",
        lambda hw, codec=None: gerufen.append(hw.value)
        or hardware.Probe(hw.value, "x", True),
    )
    hardware.zustand(mit_probe=True)
    assert gerufen == ["none"], "nur die CPU-Probe darf laufen"


@pytest.mark.skipif(
    not __import__("shutil").which("ffmpeg"), reason="ffmpeg nicht verfuegbar"
)
def test_software_probe_laeuft_wirklich_durch():
    """Kein Attrappentest: Hier wird tatsaechlich kodiert. Wenn dieser Test
    gruen ist, taugt die Messung auch fuer die Hardware-Wege."""
    p = hardware.probe_encode(HardwareAccel.NONE, ArchiveCodec.AV1)
    assert p.erfolg, p.meldung
    assert p.tempo is not None and p.tempo > 0
    assert p.encoder == "libsvtav1"


# ----------------------------------------------------- Rueckfall auf die CPU


@pytest.fixture
def auftrag(tmp_path, monkeypatch):
    from app.models import Channel, JobType, Video, VideoStatus
    from app.services import jobs
    from tests.conftest import neue_sitzung

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    settings.ensure_dirs()
    db = neue_sitzung()
    db.add(Channel(id="UCtest", name="Testkanal"))
    db.add(Video(id="vid1", channel_id="UCtest", title="X", status=VideoStatus.ARCHIVED))
    db.commit()
    jobs.enqueue(db, JobType.VIDEO_RECODE, "vid1")
    job = jobs.claim_next(db, [JobType.VIDEO_RECODE])
    assert job is not None
    return db, job


def test_versagende_grafikkarte_kodiert_auf_der_cpu_weiter(auftrag, monkeypatch, tmp_path):
    """Der Sicherheitsgurt.

    Ohne ihn waere ein eingestellter, aber nicht funktionierender
    Hardware-Encoder genauso verheerend wie eine Sperre durch YouTube: Jede
    einzelne der tausenden wartenden Recodierungen liefe in denselben Fehler
    und waere rot - und die Ursache laege ausserhalb des Programms, etwa bei
    einem Treiberwechsel auf dem Wirt.
    """
    from app.workers import archive

    db, job = auftrag
    monkeypatch.setattr(settings, "hwaccel", HardwareAccel.QSV)

    laeufe: list[list[str]] = []

    def ffmpeg(cmd, **kw):
        laeufe.append(cmd)
        if "av1_qsv" in cmd:
            raise media.MediaError("Device creation failed: -22")
        Path(cmd[-1]).write_bytes(b"x")  # der Software-Lauf gelingt

    monkeypatch.setattr(media, "run_ffmpeg", ffmpeg)
    ziel = tmp_path / "fertig.webm"
    archive._kodieren_mit_rueckfall(
        db, job, tmp_path / "quelle.mkv", ziel, ArchiveCodec.AV1, dauer_s=10
    )

    assert len(laeufe) == 2, "erst Hardware, dann CPU"
    assert "av1_qsv" in laeufe[0]
    assert "libsvtav1" in laeufe[1]
    assert ziel.is_file()


def test_ohne_hardware_wird_nicht_zweimal_versucht(auftrag, monkeypatch, tmp_path):
    """Steht die Einstellung ohnehin auf CPU, ist ein zweiter Anlauf sinnlos -
    er scheiterte genauso und verdoppelte nur die Wartezeit."""
    from app.workers import archive

    db, job = auftrag
    monkeypatch.setattr(settings, "hwaccel", HardwareAccel.NONE)

    laeufe: list[list[str]] = []

    def ffmpeg(cmd, **kw):
        laeufe.append(cmd)
        raise media.MediaError("kaputtes Video")

    monkeypatch.setattr(media, "run_ffmpeg", ffmpeg)
    with pytest.raises(media.MediaError):
        archive._kodieren_mit_rueckfall(
            db, job, tmp_path / "q.mkv", tmp_path / "z.webm", ArchiveCodec.AV1, dauer_s=10
        )
    assert len(laeufe) == 1


def test_herunterfahren_loest_keinen_rueckfall_aus(auftrag, monkeypatch, tmp_path):
    """Ein Abbruch durch das Herunterfahren ist kein Versagen der Grafikkarte.
    Daraufhin die CPU anzuwerfen hiesse, den Container am Beenden zu hindern."""
    from app.services import abbruch
    from app.workers import archive

    db, job = auftrag
    monkeypatch.setattr(settings, "hwaccel", HardwareAccel.QSV)

    laeufe: list[list[str]] = []

    def ffmpeg(cmd, **kw):
        laeufe.append(cmd)
        raise abbruch.Abgebrochen("Dienst faehrt herunter")

    monkeypatch.setattr(media, "run_ffmpeg", ffmpeg)
    with pytest.raises(abbruch.Abgebrochen):
        archive._kodieren_mit_rueckfall(
            db, job, tmp_path / "q.mkv", tmp_path / "z.webm", ArchiveCodec.AV1, dauer_s=10
        )
    assert len(laeufe) == 1


def test_bruchstueck_des_gescheiterten_laufs_wird_weggeraeumt(auftrag, monkeypatch, tmp_path):
    """Sonst haelt die Groessenpruefung spaeter das halbe Hardware-Bruchstueck
    fuer das Ergebnis."""
    from app.workers import archive

    db, job = auftrag
    monkeypatch.setattr(settings, "hwaccel", HardwareAccel.QSV)
    ziel = tmp_path / "fertig.webm"

    def ffmpeg(cmd, **kw):
        if "av1_qsv" in cmd:
            ziel.write_bytes(b"halbes Bruchstueck")
            raise media.MediaError("mittendrin abgebrochen")
        assert not ziel.exists(), "das Bruchstueck haette weg sein muessen"
        ziel.write_bytes(b"ganze Datei")

    monkeypatch.setattr(media, "run_ffmpeg", ffmpeg)
    archive._kodieren_mit_rueckfall(
        db, job, tmp_path / "q.mkv", ziel, ArchiveCodec.AV1, dauer_s=10
    )
    assert ziel.read_bytes() == b"ganze Datei"
