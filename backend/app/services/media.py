"""ffmpeg- und ffprobe-Anbindung.

Zwei Aufgaben:

*Archivieren* - Die heruntergeladene Datei wird nach AV1/Opus umkodiert. Das ist
der Schritt, der tatsaechlich Platz spart; das ZIP drumherum tut es nicht
(gemessen: 0,01 % bei H.264). Zielcontainer ist WebM oder MP4, niemals MKV -
Matroska spielt kein Browser ab und wuerde jeden Direktstream verhindern.

*Vorbereiten* - Kann ein Client den Archivcodec nicht, entsteht daraus eine
H.264/AAC-Fassung als Heisskopie.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from app.config import ArchiveCodec, HardwareAccel, settings

log = logging.getLogger(__name__)


class MediaError(RuntimeError):
    pass


@dataclass(slots=True)
class MediaInfo:
    duration_s: float | None
    width: int | None
    height: int | None
    fps: float | None
    video_codec: str | None
    audio_codec: str | None
    bitrate: int | None
    size_bytes: int

    @property
    def is_video(self) -> bool:
        return self.video_codec is not None


def _run(cmd: list[str], timeout: int = 120) -> subprocess.CompletedProcess[str]:
    log.debug("Ausfuehren: %s", " ".join(cmd))
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)


def tools_available() -> dict[str, str | None]:
    """Prueft beim Start, ob die externen Werkzeuge da sind.

    Fehlt ffmpeg, faellt das sonst erst beim ersten Archivierungsauftrag auf -
    also womoeglich Stunden nach dem Start.
    """
    return {
        "ffmpeg": shutil.which(settings.ffmpeg_path),
        "ffprobe": shutil.which(settings.ffprobe_path),
    }


def _parse_fps(raw: str | None) -> float | None:
    """ffprobe liefert die Bildrate als Bruch, z.B. '30000/1001'."""
    if not raw or raw in ("0/0", "N/A"):
        return None
    if "/" in raw:
        zaehler, _, nenner = raw.partition("/")
        try:
            n = float(nenner)
            return float(zaehler) / n if n else None
        except ValueError:
            return None
    try:
        return float(raw)
    except ValueError:
        return None


def probe(path: Path) -> MediaInfo:
    """Liest die technischen Eckdaten einer Mediendatei."""
    if not path.is_file():
        raise MediaError(f"Datei nicht gefunden: {path}")

    p = _run([
        settings.ffprobe_path, "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ])
    if p.returncode != 0:
        raise MediaError(f"ffprobe scheiterte an {path.name}: {p.stderr.strip()[:400]}")

    try:
        daten = json.loads(p.stdout)
    except json.JSONDecodeError as e:
        raise MediaError(f"ffprobe lieferte kein gueltiges JSON fuer {path.name}") from e

    streams = daten.get("streams", [])
    fmt = daten.get("format", {})
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)

    def _int(wert: object) -> int | None:
        try:
            return int(wert)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None

    def _float(wert: object) -> float | None:
        try:
            return float(wert)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None

    return MediaInfo(
        duration_s=_float(fmt.get("duration")),
        width=_int(v.get("width")) if v else None,
        height=_int(v.get("height")) if v else None,
        fps=_parse_fps(v.get("avg_frame_rate")) if v else None,
        video_codec=v.get("codec_name") if v else None,
        audio_codec=a.get("codec_name") if a else None,
        bitrate=_int(fmt.get("bit_rate")),
        size_bytes=path.stat().st_size,
    )


# ------------------------------------------------------------------ Kodieren

#: ffmpeg meldet mit -progress den Fortschritt in Mikrosekunden.
_PROGRESS_TIME = re.compile(r"out_time_us=(\d+)")


def _hwaccel_encoder(codec: ArchiveCodec) -> str:
    """Waehlt den Encoder passend zur eingestellten Beschleunigung.

    Hardware-Encoder sind um ein Vielfaches schneller, liefern bei gleicher
    Dateigroesse aber sichtbar weniger Qualitaet als die Software-Encoder. Fuer
    ein Archiv, das einmal geschrieben und lange behalten wird, ist Software
    deshalb die Voreinstellung.
    """
    hw = settings.hwaccel
    if codec is ArchiveCodec.AV1:
        return {
            HardwareAccel.QSV: "av1_qsv",
            HardwareAccel.NVENC: "av1_nvenc",
            HardwareAccel.VAAPI: "av1_vaapi",
        }.get(hw, "libsvtav1")
    if codec is ArchiveCodec.HEVC:
        return {
            HardwareAccel.QSV: "hevc_qsv",
            HardwareAccel.NVENC: "hevc_nvenc",
            HardwareAccel.VAAPI: "hevc_vaapi",
        }.get(hw, "libx265")
    raise MediaError(f"kein Encoder fuer {codec}")


def archive_container(codec: ArchiveCodec) -> str:
    """Zielcontainer fuer den Kaltspeicher.

    AV1 kommt nach WebM (spielt in Chrome, Firefox und Edge direkt), HEVC nach
    MP4. MKV kaeme technisch auch in Frage, scheidet aber aus: Browser spielen
    es nicht, und damit waere der Direktstream aus dem Buendel wertlos.
    """
    return ".webm" if codec is ArchiveCodec.AV1 else ".mp4"


def build_archive_cmd(src: Path, dst: Path, codec: ArchiveCodec) -> list[str]:
    encoder = _hwaccel_encoder(codec)
    cmd = [settings.ffmpeg_path, "-hide_banner", "-nostdin", "-y", "-i", str(src)]

    if codec is ArchiveCodec.AV1:
        cmd += ["-c:v", encoder]
        if encoder == "libsvtav1":
            cmd += [
                "-preset", str(settings.av1_preset),
                "-crf", str(settings.av1_crf),
                # Immer 10 Bit, auch bei 8-Bit-Quelle: kostet praktisch nichts an
                # Groesse und Tempo, beseitigt aber Streifenbildung in dunklen
                # Verlaeufen - genau dort, wo YouTube-Material ohnehin schwach ist.
                "-pix_fmt", "yuv420p10le",
                # Schluesselbild etwa alle 10 Sekunden. Weiter auseinander spart
                # Platz, laesst den Player beim Spulen aber traege wirken.
                "-g", "300",
                # tune=0 optimiert auf visuelle Guete statt auf Messwerte.
                # enable-overlays und scd verbessern Szenenwechsel.
                "-svtav1-params", "tune=0:enable-overlays=1:scd=1",
            ]
        else:
            cmd += ["-preset", "medium", "-cq", str(settings.av1_crf)]
    elif codec is ArchiveCodec.HEVC:
        cmd += ["-c:v", encoder, "-preset", settings.hevc_preset, "-crf", str(settings.hevc_crf)]
        cmd += ["-tag:v", "hvc1"]  # sonst spielt Apple es nicht ab
    else:
        cmd += ["-c:v", "copy"]

    if codec is ArchiveCodec.COPY:
        cmd += ["-c:a", "copy"]
    else:
        cmd += ["-c:a", settings.audio_codec, "-b:a", f"{settings.audio_bitrate_kbps}k"]

    # Untertitel und Kapitel bleiben erhalten, Cover-Bilder fliegen raus - die
    # wuerden manche Container als zweiten Videostream missverstehen.
    cmd += ["-map", "0:v:0", "-map", "0:a?", "-map_chapters", "0", "-sn", "-dn"]

    if dst.suffix.lower() == ".mp4":
        cmd += ["-movflags", "+faststart"]

    cmd += ["-progress", "pipe:1", "-nostats", str(dst)]
    return cmd


# ------------------------------------------------------- Behaelter waehlen

#: Welcher Videocodec in welchen browsertauglichen Behaelter gehoert, und
#: welcher Toncodec dort erwartet wird.
_CONTAINER_FUER_VIDEO = {
    "av1": (".webm", "opus"),
    "av01": (".webm", "opus"),
    "vp9": (".webm", "opus"),
    "vp09": (".webm", "opus"),
    "vp8": (".webm", "opus"),
    "h264": (".mp4", "aac"),
    "avc1": (".mp4", "aac"),
    "hevc": (".mp4", "aac"),
    "h265": (".mp4", "aac"),
}

#: Toncodecs, die im jeweiligen Behaelter unveraendert bleiben duerfen.
_TON_OK = {
    ".webm": {"opus", "vorbis"},
    # Opus in MP4 bewusst NICHT: Safari spielt das auch 2026 nicht ab, dort kam
    # nur Opus in Ogg dazu. Ein MP4 mit Opus waere auf Apple-Geraeten stumm.
    ".mp4": {"aac", "mp3"},
}


@dataclass(slots=True)
class ContainerPlan:
    suffix: str
    #: True, wenn die Tonspur umkodiert werden muss. Das ist billig - Video
    #: bleibt unangetastet.
    ton_umkodieren: bool
    ziel_ton: str | None
    grund: str


def plan_container(info: MediaInfo) -> ContainerPlan:
    """Waehlt den Behaelter, in dem das Video ins Buendel wandert.

    Der Grund fuer diesen Schritt: yt-dlp fuehrt Video und Ton nach MKV
    zusammen, weil MKV jede Codec-Kombination annimmt. Nur spielt kein Browser
    MKV ab. Ohne Umpacken muesste also jede einzelne Wiedergabe durch den
    Transkodierpfad - genau das, was die Kalt/Heiss-Architektur vermeiden will.

    Das Umpacken selbst ist fast umsonst, weil der Videostrom dabei unberuehrt
    kopiert wird.
    """
    vcodec = (info.video_codec or "").lower()
    acodec = (info.audio_codec or "").lower()

    eintrag = _CONTAINER_FUER_VIDEO.get(vcodec)
    if eintrag is None:
        # Unbekannter Videocodec: MP4 mit H.264 ist der sichere Hafen, aber das
        # heisst echtes Transkodieren - der Aufrufer muss das entscheiden.
        return ContainerPlan(".mp4", True, "aac", f"Videocodec {vcodec or 'unbekannt'} unklar")

    suffix, bevorzugter_ton = eintrag
    if not acodec or acodec == "none":
        return ContainerPlan(suffix, False, None, f"{vcodec} ohne Tonspur -> {suffix}")

    if acodec in _TON_OK[suffix]:
        return ContainerPlan(suffix, False, None, f"{vcodec}/{acodec} passt in {suffix}")

    return ContainerPlan(
        suffix, True, bevorzugter_ton,
        f"{acodec} passt nicht in {suffix}, Ton wird nach {bevorzugter_ton} umgesetzt",
    )


def build_remux_cmd(src: Path, dst: Path, plan: ContainerPlan) -> list[str]:
    """Packt in einen anderen Behaelter um, ohne das Video neu zu kodieren."""
    cmd = [settings.ffmpeg_path, "-hide_banner", "-nostdin", "-y", "-i", str(src), "-c:v", "copy"]
    if plan.ton_umkodieren and plan.ziel_ton:
        encoder = "libopus" if plan.ziel_ton == "opus" else "aac"
        bitrate = f"{settings.audio_bitrate_kbps}k" if encoder == "libopus" else "160k"
        cmd += ["-c:a", encoder, "-b:a", bitrate]
    else:
        cmd += ["-c:a", "copy"]
    cmd += ["-map", "0:v:0", "-map", "0:a?", "-map_chapters", "0", "-sn", "-dn"]
    if plan.suffix == ".mp4":
        cmd += ["-movflags", "+faststart"]
    cmd += ["-progress", "pipe:1", "-nostats", str(dst)]
    return cmd


def build_playback_cmd(src: Path, dst: Path) -> list[str]:
    """H.264/AAC in MP4 - der kleinste gemeinsame Nenner fuer alte Clients.

    ``+faststart`` schiebt den Index an den Dateianfang; ohne das muss der
    Player erst das Dateiende laden, bevor er ueberhaupt anfangen kann.
    """
    return [
        settings.ffmpeg_path, "-hide_banner", "-nostdin", "-y", "-i", str(src),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        # yuv420p, weil Browser mit 10-Bit-H.264 nichts anfangen koennen.
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k",
        "-map", "0:v:0", "-map", "0:a?", "-sn", "-dn",
        "-movflags", "+faststart",
        "-progress", "pipe:1", "-nostats", str(dst),
    ]


def run_ffmpeg(
    cmd: list[str],
    *,
    dauer_s: float | None = None,
    fortschritt: Callable[[float], None] | None = None,
    abbruch: Callable[[], bool] | None = None,
) -> None:
    """Fuehrt ffmpeg aus und meldet den Fortschritt.

    ``abbruch`` wird regelmaessig abgefragt; liefert es True, wird der Prozess
    beendet. Ohne das laeuft ein abgebrochener Encode-Auftrag im Hintergrund
    stundenlang weiter.
    """
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1
    )
    assert proc.stdout is not None

    try:
        for zeile in proc.stdout:
            if abbruch and abbruch():
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                raise MediaError("Kodierung abgebrochen")
            if fortschritt and dauer_s:
                treffer = _PROGRESS_TIME.search(zeile)
                if treffer:
                    fortschritt(min(1.0, int(treffer.group(1)) / 1e6 / dauer_s))
    finally:
        if proc.stdout:
            proc.stdout.close()

    rc = proc.wait()
    fehler = proc.stderr.read() if proc.stderr else ""
    if proc.stderr:
        proc.stderr.close()
    if rc != 0:
        raise MediaError(f"ffmpeg endete mit Code {rc}: {fehler.strip()[-800:]}")


#: Quellcodecs, bei denen sich ein AV1-Re-Encode nicht mehr rechnet.
#:
#: Der Grund ist wichtig und wird leicht falsch verstanden: Die bekannten
#: "AV1 spart 50 % gegenueber H.264"-Zahlen gelten fuer einen Encode aus einem
#: unkomprimierten Master. YouTube liefert aber bereits auf 2-4 Mbit/s
#: gequetschtes Material. Aus einer VP9-Quelle, die schon bei 1,5-2,5 Mbit/s
#: liegt, holt AV1 real kaum noch etwas heraus - man zahlt Stunden Rechenzeit
#: fuer Generationsverlust. Bei einer AV1-Quelle ist es reiner Verlust.
_AV1_SINNLOS = {"av1", "av01", "libaom-av1", "vp9", "vp09", "libvpx-vp9"}
_HEVC_SINNLOS = {"hevc", "h265", "av1", "av01", "vp9", "vp09"}


def should_recode(info: MediaInfo, codec: ArchiveCodec) -> tuple[bool, str]:
    """Entscheidet, ob sich eine Recodierung ueberhaupt lohnt.

    Diese Entscheidung spart mehr Rechenzeit als jede Preset-Optimierung: Ein
    Kanal, dessen Videos YouTube als VP9 ausliefert, laeuft damit ohne einen
    einzigen Encode durch.
    """
    if codec is ArchiveCodec.COPY:
        return False, "Recodierung ist abgeschaltet"
    if not info.is_video:
        return False, "kein Videostream"
    if info.height and info.height < settings.recode_min_height:
        return False, f"Quelle ist nur {info.height}p - Recodierung lohnt nicht"

    quelle = (info.video_codec or "").lower()
    if codec is ArchiveCodec.AV1 and quelle in _AV1_SINNLOS:
        return False, f"Quelle ist bereits {quelle} - Re-Encode braechte fast nur Generationsverlust"
    if codec is ArchiveCodec.HEVC and quelle in _HEVC_SINNLOS:
        return False, f"Quelle ist bereits {quelle} - Re-Encode lohnt nicht"
    return True, f"{quelle or 'unbekannt'} -> {codec.value}"
