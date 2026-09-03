"""Prueft, ob der Hardware-Encoder wirklich arbeitet.

Der Anlass ist ein Verdacht, den man nicht selbst ausraeumen konnte: "Ich
glaube, die Arc wird nicht verwendet." Dass diese Frage ueberhaupt offen sein
konnte, war der eigentliche Mangel - der Hardware-Pfad scheiterte auf drei
Ebenen, und keine davon meldete sich:

* Die Einstellung stand auf ``none``. Dann fragt niemand die Karte.
* Dem Image fehlte der Laufzeittreiber. ffmpeg listet ``av1_qsv`` und
  ``av1_vaapi`` trotzdem unter seinen Encodern, denn es ist *gebaut* mit dieser
  Unterstuetzung - nur laden kann libva ohne ``iHD_drv_video.so`` nichts.
* Die erzeugten Befehle waren falsch, und ffmpeg **ignoriert** eine unbekannte
  Encoder-Option stillschweigend, statt sie anzumeckern.

Deshalb wird hier nicht nach Anzeichen gesucht, sondern tatsaechlich kodiert.
Ein paar Bilder aus einem Testbild, mit genau dem Befehl, den auch die
Recodierung benutzt. Was dabei herauskommt, ist keine Vermutung mehr.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from app.config import ArchiveCodec, HardwareAccel, settings
from app.services import media

log = logging.getLogger(__name__)

#: Verzeichnis der Render-Knoten. Ohne dieses Verzeichnis im Container ist
#: jede weitere Pruefung gegenstandslos - dann wurde die Karte gar nicht
#: durchgereicht.
DRI = Path("/dev/dri")

#: Laenge des Probe-Encodes. Kurz genug, dass der Knopf in der Oberflaeche
#: nicht haengt, lang genug fuer eine brauchbare Tempoangabe.
PROBE_S = 3
PROBE_TIMEOUT_S = 90

_TREIBER = re.compile(r"vainfo:\s*Driver version:\s*(.+)", re.I)


@dataclass(slots=True)
class Probe:
    """Ergebnis eines echten Probe-Encodes."""

    beschleunigung: str
    encoder: str
    erfolg: bool
    dauer_s: float | None = None
    #: Verhaeltnis Videolaenge zu Rechenzeit. 1.0 heisst Echtzeit; die
    #: aussagekraeftigste Zahl fuer "lohnt sich das".
    tempo: float | None = None
    meldung: str = ""


@dataclass(slots=True)
class Zustand:
    geraete: list[str] = field(default_factory=list)
    treiber: str | None = None
    #: True, wenn ueberhaupt ein VA-API-Treiber im Image liegt.
    treiber_vorhanden: bool = False
    eingestellt: str = "none"
    proben: list[Probe] = field(default_factory=list)
    meldung: str = ""

    def als_dict(self) -> dict[str, object]:
        d = asdict(self)
        d["proben"] = [asdict(p) for p in self.proben]
        return d


def geraete() -> list[str]:
    """Die durchgereichten Render-Knoten.

    Sind hier keine, wurde ``/dev/dri`` nicht in den Container gereicht - im
    Unraid-Template ist das eine eigene Zeile, und sie ist standardmaessig
    leer. Das ist die haeufigste Ursache und von aussen voellig unsichtbar.
    """
    if not DRI.is_dir():
        return []
    return sorted(p.name for p in DRI.iterdir() if p.name.startswith(("renderD", "card")))


def _treiberdateien() -> list[str]:
    """Die installierten VA-API-Treiber.

    Ohne mindestens einen davon ist der ganze Hardware-Pfad tot, egal was
    ffmpeg unter seinen Encodern auffuehrt.
    """
    gefunden: list[str] = []
    for ordner in ("/usr/lib/x86_64-linux-gnu/dri", "/usr/lib/dri"):
        p = Path(ordner)
        if p.is_dir():
            gefunden += sorted(d.name for d in p.glob("*_drv_video.so"))
    return gefunden


def _vainfo(geraet: str) -> str | None:
    """Fragt den Treiber nach seinem Namen. Liefert None, wenn es nicht geht."""
    werkzeug = shutil.which("vainfo")
    if werkzeug is None:
        return None
    try:
        fertig = subprocess.run(
            [werkzeug, "--display", "drm", "--device", geraet],
            capture_output=True, text=True, timeout=20, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    treffer = _TREIBER.search(fertig.stdout or "")
    return treffer.group(1).strip() if treffer else None


def probe_encode(hw: HardwareAccel, codec: ArchiveCodec | None = None) -> Probe:
    """Kodiert ein paar Sekunden Testbild - mit dem echten Befehl.

    Das ist der Kern des Moduls. Jede billigere Pruefung - Datei da, Encoder
    gelistet, Geraet vorhanden - kann gruen sein, waehrend die Kodierung
    trotzdem scheitert oder still auf die CPU zurueckfaellt.
    """
    codec = ArchiveCodec(codec or settings.archive_codec)
    hw = HardwareAccel(hw)
    if codec is ArchiveCodec.COPY:
        codec = ArchiveCodec.AV1  # "copy" kodiert nichts, taugt nicht als Probe
    encoder = media._hwaccel_encoder(codec, hw)

    with tempfile.TemporaryDirectory(prefix="hwprobe-") as ordner:
        ziel = Path(ordner) / f"probe{media.archive_container(codec)}"
        cmd = media.build_archive_cmd(Path("PLATZHALTER"), ziel, codec, hwaccel=hw)
        # Die Quelle gegen ein erzeugtes Testbild tauschen: Es soll nichts von
        # der Platte gelesen werden, und die Probe muss auch auf einem frisch
        # aufgesetzten Archiv ohne ein einziges Video laufen.
        i = cmd.index("-i")
        cmd[i : i + 2] = [
            "-f", "lavfi",
            "-i", f"testsrc2=size=1280x720:rate=30:duration={PROBE_S}",
        ]
        # Kein Ton im Testbild - die Tonoptionen wuerden sonst scheitern.
        for schalter in ("-c:a", "-b:a"):
            if schalter in cmd:
                j = cmd.index(schalter)
                del cmd[j : j + 2]
        if "-map" in cmd:
            cmd = [x for k, x in enumerate(cmd)
                   if not (x == "0:a?" or (x == "-map" and cmd[k + 1] == "0:a?"))]

        begonnen = time.monotonic()
        try:
            fertig = subprocess.run(
                cmd, capture_output=True, text=True, timeout=PROBE_TIMEOUT_S, check=False
            )
        except subprocess.TimeoutExpired:
            return Probe(hw.value, encoder, False, meldung=f"Zeitueberschreitung nach {PROBE_TIMEOUT_S} s")
        except OSError as e:
            return Probe(hw.value, encoder, False, meldung=f"ffmpeg liess sich nicht starten: {e}")

        dauer = time.monotonic() - begonnen
        if fertig.returncode != 0 or not ziel.is_file() or ziel.stat().st_size == 0:
            # Die letzte Zeile von ffmpeg ist fast immer die aussagekraeftige.
            zeilen = [z for z in (fertig.stderr or "").strip().splitlines() if z.strip()]
            return Probe(hw.value, encoder, False, dauer_s=round(dauer, 2),
                         meldung=" | ".join(zeilen[-2:])[:500] or "ohne Fehlermeldung gescheitert")

    return Probe(
        hw.value, encoder, True,
        dauer_s=round(dauer, 2),
        tempo=round(PROBE_S / dauer, 1) if dauer > 0 else None,
        meldung=f"{PROBE_S} s 720p in {dauer:.1f} s kodiert",
    )


def zustand(*, mit_probe: bool = False) -> Zustand:
    """Der vollstaendige Befund fuer die Oberflaeche.

    Ohne ``mit_probe`` nur die billigen Auskuenfte - die darf die
    Einstellungsseite bei jedem Aufruf holen. Der Probe-Encode kostet Sekunden
    und laeuft nur auf Knopfdruck.
    """
    z = Zustand(eingestellt=HardwareAccel(settings.hwaccel).value)
    z.geraete = geraete()
    treiber = _treiberdateien()
    z.treiber_vorhanden = bool(treiber)

    if not z.geraete:
        z.meldung = (
            "Keine Grafikkarte im Container: /dev/dri fehlt. Im Unraid-Template ist "
            "das die Zeile \"Intel/AMD GPU\" - sie ist standardmaessig leer und muss "
            "auf /dev/dri gesetzt werden."
        )
    elif not z.treiber_vorhanden:
        z.meldung = (
            "Die Karte ist durchgereicht, aber im Image liegt kein VA-API-Treiber. "
            "Ein aktuelleres Image behebt das."
        )
    else:
        knoten = next((g for g in z.geraete if g.startswith("renderD")), z.geraete[0])
        z.treiber = _vainfo(str(DRI / knoten))
        z.meldung = (
            f"Karte und Treiber vorhanden ({', '.join(treiber)})."
            if z.treiber is None
            else f"Treiber meldet sich: {z.treiber}"
        )

    if mit_probe:
        # Alle Wege der Reihe nach, nicht nur den eingestellten: Der Nutzer
        # will wissen, was seine Karte kann, bevor er sich entscheidet.
        for hw in (HardwareAccel.QSV, HardwareAccel.VAAPI, HardwareAccel.NONE):
            if hw is not HardwareAccel.NONE and not (z.geraete and z.treiber_vorhanden):
                continue
            z.proben.append(probe_encode(hw))

    return z
