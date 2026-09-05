"""Feste, begrenzte Qualitaeten fuer jede einzelne Wiedergabesitzung.

Die Zahl bezeichnet die kurze Bildkante, auch bei Hochkantvideos. Eine Stufe
wird nur angeboten, wenn sie ohne Hochskalieren und innerhalb des Encode-
Budgets erreichbar ist. Automatisch waehlt das Original bei passenden Codecs;
es ist keine netzwerkabhaengige adaptive Bitratensteuerung.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal, TypedDict

from app.services import playback
from app.services.bundle import BundleManifest

Quality = Literal["auto", "original", "1080p", "720p", "480p", "360p", "240p"]
MAX_SOURCE_DIMENSION = 16_384
MAX_LONG_EDGE = 1920
MAX_PIXELS = 1920 * 1080


class QualityError(ValueError):
    pass


class QualityChoice(TypedDict):
    value: Quality
    label: str


@dataclass(frozen=True, slots=True)
class Profile:
    short_edge: int
    max_rate_kbps: int
    audio_bitrate_kbps: int

    @property
    def scale_filter(self) -> str:
        # Nur Konstanten eines serverseitigen Profils stehen in der Expression.
        # FFmpeg verwendet die tatsaechlichen Quellmasse; veraltete Metadaten
        # duerfen keine Hochskalierung oder Ueberschreitung des Budgets bewirken.
        factor = f"min(1,min({self.short_edge}/min(iw,ih),{MAX_LONG_EDGE}/max(iw,ih)))"
        width = f"min(iw,max(2,trunc(iw*{factor}/2+0.0000001)*2))"
        height = f"min(ih,max(2,trunc(ih*{factor}/2+0.0000001)*2))"
        return f"scale=w='{width}':h='{height}',setsar=1"


PROFILES = MappingProxyType({
    "1080p": Profile(1080, 4000, 128),
    "720p": Profile(720, 2500, 128),
    "480p": Profile(480, 1200, 96),
    "360p": Profile(360, 800, 96),
    "240p": Profile(240, 450, 64),
})


@dataclass(frozen=True, slots=True)
class Plan:
    quality: Quality
    label: str
    mode: Literal["direct", "transcode"]
    profile: Profile
    available: list[QualityChoice]
    reason: str
    width: int | None
    height: int | None


def _dimensions(width: object, height: object) -> tuple[int, int] | None:
    if (type(width) is int and type(height) is int
            and 2 <= width <= MAX_SOURCE_DIMENSION and 2 <= height <= MAX_SOURCE_DIMENSION):
        return width, height
    return None


def source_dimensions(manifest: BundleManifest, width: object, height: object) -> tuple[int, int] | None:
    # Paarweise entscheiden: keine alte DB-Hoehe mit einer neuen Manifest-
    # Breite kombinieren. Das Manifest beschreibt das konkrete Archivbuendel.
    return _dimensions(manifest.width, manifest.height) or _dimensions(width, height)


def output_dimensions(source: tuple[int, int], profile: Profile) -> tuple[int, int]:
    width, height = source
    factor = min(1, profile.short_edge / min(source), MAX_LONG_EDGE / max(source))
    # Dieselbe Rundung wie im FFmpeg-Filter; YUV420 braucht gerade Bildkanten.
    return (min(width, max(2, math.floor(width * factor / 2 + 1e-7) * 2)),
            min(height, max(2, math.floor(height * factor / 2 + 1e-7) * 2)))


def _original_label(source: tuple[int, int] | None) -> str:
    return f"Original ({min(source)}p)" if source else "Original"


def choose(manifest: BundleManifest, support: frozenset[str], *, quality: Quality = "auto",
           force_transcode: bool = False, source_width: object = None, source_height: object = None) -> Plan:
    decision = playback.decide(manifest, support)
    native = decision.mode == playback.Mode.DIRECT and not force_transcode
    source = source_dimensions(manifest, source_width, source_height)
    available: list[QualityChoice] = [{"value": "auto", "label": "Automatisch"}]
    if native:
        available.append({"value": "original", "label": _original_label(source)})
    if source:
        for name, profile in PROFILES.items():
            dimensions = output_dimensions(source, profile)
            unchanged = native and min(source) == profile.short_edge
            if min(source) >= profile.short_edge and (unchanged or (
                min(dimensions) == profile.short_edge and max(dimensions) <= MAX_LONG_EDGE
                and dimensions[0] * dimensions[1] <= MAX_PIXELS
            )):
                available.append({"value": name, "label": name})  # type: ignore[typeddict-item]
    if quality not in {item["value"] for item in available}:
        raise QualityError("Diese Wiedergabequalitaet ist fuer das Video und diesen Browser nicht verfuegbar.")

    profile = PROFILES.get(quality, PROFILES["1080p"])
    if native and (quality in {"auto", "original"} or (source and min(source) == profile.short_edge)):
        return Plan(quality, _original_label(source), "direct", profile, available,
                    decision.reason, *(source or (None, None)))

    output = output_dimensions(source, profile) if source else None
    if quality == "auto" and output:
        # Native Zwischengroessen wie 800p behalten; nur die Bitratenobergrenze
        # aus der naechstgroesseren festen Stufe uebernehmen.
        edge = min(output)
        profile = next((p for p in reversed(list(PROFILES.values())) if p.short_edge >= edge), PROFILES["1080p"])
        output = output_dimensions(source, profile)
    label = f"{min(output)}p" if output else "Live-Transkodierung"
    reason = f"Live-Transkodierung in {label}" if output else "Live-Transkodierung fuer diesen Browser"
    return Plan(quality, label, "transcode", profile, available, reason, *(output or (None, None)))
