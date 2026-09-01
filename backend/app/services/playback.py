"""Entscheidet, wie ein Video an den Client kommt.

Es gibt zwei Wege, und der Unterschied ist gross:

*Direktstream* - Die Mediendatei wird byteweise aus dem Buendel geliefert. Es
entsteht keine zweite Datei, nichts muss aufgeraeumt werden, und die Wiedergabe
startet sofort. Moeglich, weil die Medien im Buendel unkomprimiert liegen (siehe
``bundle.py``).

*Heisskopie* - Das Video wird entpackt und ggf. umkodiert, liegt als eigene
Datei im Cache und wird spaeter vom Reaper wieder entfernt. Noetig, wenn der
Client den Archivcodec nicht abspielen kann.

Die Codec-Frage entscheidet nicht der Server per User-Agent-Raterei, sondern der
Browser selbst: Das Frontend prueft mit ``MediaSource.isTypeSupported`` bzw.
``video.canPlayType``, was es kann, und schickt das Ergebnis mit. Das ist
verlaesslich, waehrend UA-Erkennung bei jedem Browser-Update neu bricht.

Wichtige Container-Falle: Matroska (.mkv) spielt kein Browser ab, egal welcher
Codec darin steckt. Ein AV1-Archiv muss deshalb in WebM oder MP4 liegen, sonst
landet jede Wiedergabe unnoetig im Transkodierpfad.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from app.services.bundle import BundleManifest

#: Container, die Browser ueberhaupt anfassen. MKV fehlt hier mit Absicht.
BROWSER_CONTAINERS = {".mp4", ".webm", ".m4a", ".ogg", ".opus", ".mp3"}

#: Codec-Kuerzel, wie sie in ffprobe und in unseren Manifesten auftauchen,
#: abgebildet auf das Merkmal, das der Client melden muss.
_VIDEO_ALIASES = {
    "av1": "av01", "av01": "av01", "libsvtav1": "av01", "libaom-av1": "av01",
    "vp9": "vp09", "vp09": "vp09", "libvpx-vp9": "vp09",
    "vp8": "vp8", "h264": "h264", "avc1": "h264", "libx264": "h264",
    "hevc": "hevc", "h265": "hevc", "libx265": "hevc", "hvc1": "hevc",
}
_AUDIO_ALIASES = {
    "opus": "opus", "libopus": "opus",
    "aac": "aac", "mp4a": "aac",
    "vorbis": "vorbis", "mp3": "mp3", "flac": "flac",
}

#: Was ein Client mindestens koennen muss, damit wir ihm etwas schicken. Wird
#: als Rueckfallebene benutzt, wenn das Frontend nichts gemeldet hat - H.264 in
#: MP4 mit AAC spielt praktisch jedes Geraet der letzten fuenfzehn Jahre.
FALLBACK_SUPPORT = frozenset({"mp4", "h264", "aac"})

#: Zielformat des Transkodierpfads.
TRANSCODE_VARIANT = "h264"
TRANSCODE_SUFFIX = ".mp4"
TRANSCODE_MIME = "video/mp4"


class Mode(StrEnum):
    DIRECT = "direct"
    TRANSCODE = "transcode"


@dataclass(frozen=True, slots=True)
class Decision:
    mode: Mode
    #: Name der Heisskopie-Variante, nur bei ``TRANSCODE`` gesetzt.
    variant: str | None
    #: Menschenlesbare Begruendung - taucht im UI unter "Warum wird vorbereitet?"
    #: auf und spart im Fehlerfall viel Ratenaufwand.
    reason: str


def normalize_video_codec(codec: str | None) -> str | None:
    if not codec:
        return None
    key = codec.lower().split(".")[0].strip()
    return _VIDEO_ALIASES.get(key)


def normalize_audio_codec(codec: str | None) -> str | None:
    if not codec:
        return None
    key = codec.lower().split(".")[0].strip()
    return _AUDIO_ALIASES.get(key)


def parse_client_support(raw: str | None) -> frozenset[str]:
    """Liest die Faehigkeitsmeldung des Clients.

    Erwartet eine Kommaliste wie ``mp4,webm,av01,vp09,h264,opus,aac``. Fehlt
    sie oder ist sie leer, wird konservativ die Rueckfallebene angenommen -
    lieber einmal unnoetig transkodieren als ein schwarzes Bild ausliefern.
    """
    if not raw or not raw.strip():
        return FALLBACK_SUPPORT
    teile = {t.strip().lower() for t in raw.split(",") if t.strip()}
    return frozenset(teile) if teile else FALLBACK_SUPPORT


def decide(manifest: BundleManifest, support: frozenset[str]) -> Decision:
    """Waehlt den Auslieferungsweg fuer ein Buendel."""
    container = Path(manifest.media_name).suffix.lower()

    if container not in BROWSER_CONTAINERS:
        return Decision(
            Mode.TRANSCODE,
            TRANSCODE_VARIANT,
            f"Container {container or '?'} wird von Browsern nicht abgespielt",
        )

    container_tag = container.lstrip(".")
    if container_tag not in support:
        return Decision(
            Mode.TRANSCODE, TRANSCODE_VARIANT, f"Client unterstuetzt {container_tag} nicht"
        )

    vcodec = normalize_video_codec(manifest.video_codec)
    if vcodec is None:
        # Unbekannter oder nicht erfasster Codec: nicht raten, sondern den
        # sicheren Weg gehen.
        return Decision(
            Mode.TRANSCODE,
            TRANSCODE_VARIANT,
            f"Videocodec {manifest.video_codec or 'unbekannt'} nicht einschaetzbar",
        )
    if vcodec not in support:
        return Decision(Mode.TRANSCODE, TRANSCODE_VARIANT, f"Client kann {vcodec} nicht dekodieren")

    acodec = normalize_audio_codec(manifest.audio_codec)
    if acodec is not None and acodec not in support:
        return Decision(Mode.TRANSCODE, TRANSCODE_VARIANT, f"Client kann Ton {acodec} nicht dekodieren")

    return Decision(Mode.DIRECT, None, f"{vcodec} in {container_tag} laeuft direkt aus dem Buendel")


# --------------------------------------------------------------- Quellenobjekt


@dataclass(frozen=True, slots=True)
class StreamSource:
    """Alles, was der Auslieferungs-Endpunkt braucht, um Bytes zu schicken."""

    #: Datei, aus der gelesen wird - das Buendel selbst oder die Heisskopie.
    file: Path
    #: Byte-Offset, ab dem die Mediendaten beginnen. Bei einer Heisskopie 0,
    #: beim Direktstream der Offset im ZIP.
    base_offset: int
    size: int
    mime_type: str
    mode: Mode
