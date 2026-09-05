"""Feste H.264-Live-Encoder; Einstellungen werden je Zuschauersitzung eingefroren.

Die Groessenbegrenzung bleibt vor dem Hardware-Upload auf der CPU. So werden
auch unbekannte Archivmasse begrenzt und alle drei GPU-Wege erhalten NV12.
QSV-Geraet und Bitratenmodus folgen der FFmpeg-Dokumentation:
https://ffmpeg.org/ffmpeg.html#Advanced-Video-options
https://ffmpeg.org/ffmpeg-codecs.html#QSV-Encoders
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

from app.config import HardwareAccel, settings
from app.services.playback_quality import Profile


@dataclass(frozen=True, slots=True)
class Encoding:
    hardware_accel: HardwareAccel
    encoder: str
    device_options: tuple[str, ...] = ()
    filter_suffix: str = ""
    encoder_options: tuple[str, ...] = ()

    def video_options(self, profile: Profile) -> list[str]:
        options = ["-vf", profile.scale_filter + self.filter_suffix,
                   "-c:v", self.encoder, *self.encoder_options]
        if self.hardware_accel is not HardwareAccel.NONE:
            # QSV ohne global_quality: b < maxrate waehlt VBR statt ICQ.
            options += ["-b:v", f"{profile.max_rate_kbps * 4 // 5}k"]
        return [*options, "-maxrate", f"{profile.max_rate_kbps}k",
                "-bufsize", f"{profile.max_rate_kbps * 2}k"]


SOFTWARE = Encoding(
    HardwareAccel.NONE, "libx264", encoder_options=(
        "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
        "-keyint_min", "180", "-sc_threshold", "0",
    ),
)


def _intel_render_node(device: str) -> bool:
    """Nur lokale PCI-Metadaten; keine Treiberprozesse oder Geraeteversuche."""
    if not sys.platform.startswith("linux"):
        return False
    try:
        vendor = Path("/sys/class/drm", Path(device).name, "device/vendor")
        return vendor.read_text(encoding="ascii").strip().lower() == "0x8086"
    except (OSError, UnicodeError):
        return False


def configured() -> Encoding:
    hw = HardwareAccel(settings.hwaccel)
    if hw is HardwareAccel.NONE:
        return SOFTWARE
    if hw is HardwareAccel.NVENC:
        return Encoding(hw, "h264_nvenc", filter_suffix=",format=nv12", encoder_options=(
            "-preset", "p3", "-tune", "ll", "-rc", "vbr", "-cq", "23",
            "-rc-lookahead", "0", "-zerolatency", "1", "-forced-idr", "1", "-bf", "0",
        ))

    device = settings.hwaccel_device
    linux = sys.platform.startswith("linux")
    if (hw is HardwareAccel.VAAPI or linux) and not re.fullmatch(r"/dev/dri/renderD[0-9]{1,4}", device):
        # Keine zusaetzlichen FFmpeg-Geraeteoptionen aus einem konfigurierten
        # Pfad interpretieren. Der Manager behandelt dies wie einen GPU-Ausfall.
        raise ValueError("Ungueltiger Render-Knoten")
    if hw is HardwareAccel.QSV:
        init = "qsv=live:hw"
        if linux:
            init += f",child_device={device},child_device_type=vaapi"
        # QSV richtet den internen Upload selbst aus: hwupload auf beliebige
        # gerade Qualitaetsmasse (z.B. 426x240) scheitert an D3D11-Texturgroessen.
        # FFmpeg bindet den einzigen QSV-Kontext an den Encoder, auch bei NV12.
        return Encoding(hw, "h264_qsv", ("-init_hw_device", init, "-filter_hw_device", "live"),
                        ",format=nv12", (
                            "-preset", "veryfast", "-async_depth", "2", "-look_ahead", "0",
                            "-forced_idr", "1", "-bf", "0",
                            # Debians freier Intel-Treiber stellt den VDEnc-Pfad
                            # bereit. Fehlt HuC/Hardwareunterstuetzung, folgt CPU.
                            *(("-low_power", "1") if linux else ()),
                        ))
    return Encoding(hw, "h264_vaapi", (
        "-init_hw_device", f"vaapi=live:{device}", "-filter_hw_device", "live",
    ), ",format=nv12,hwupload", (
        "-rc_mode", "VBR", "-async_depth", "2", "-bf", "0",
        *(("-low_power", "1") if _intel_render_node(device) else ()),
    ))
