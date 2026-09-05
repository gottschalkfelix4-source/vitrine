"""Kurzlebige Zuschauersitzungen und bedarfsgesteuerte HLS-Segmente.

FFmpeg liest mit dem seekbaren subfile-Protokoll direkt den gespeicherten
Medienbereich des ZIPs. Es entsteht weder eine entpackte Quelldatei noch eine
vollstaendige Transkodierung: ein Abruf kodiert genau sechs Sekunden. Der
Speicher, die Zahl der Prozesse und deren Laufzeit haben feste Obergrenzen.
"""

from __future__ import annotations

import math
import secrets
import subprocess
import threading
import time
from collections import OrderedDict, deque
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from app.config import HardwareAccel, settings
from app.services import live_encoding, playback_quality

SEGMENT_SECONDS = 6
MAX_SESSIONS = 64
MAX_CLIENT_SESSIONS = 16
MAX_TRANSCODES = 2
MAX_CACHE_BYTES = 64 * 1024**2
MAX_SEGMENT_BYTES = 8 * 1024**2
MAX_DURATION_SECONDS = 48 * 3600
IDLE_SECONDS = 90
ENCODE_TIMEOUT_SECONDS = 45
# Eine blockierte GPU-Initialisierung darf nicht das gesamte CPU-Fallbackbudget
# verbrauchen. Beide Versuche teilen weiterhin dieselbe 45-Sekunden-Frist.
HARDWARE_TIMEOUT_SECONDS = 15
HARDWARE_FALLBACK_REASON = "GPU-Transkodierung nicht verfuegbar; diese Wiedergabe verwendet die CPU."
HARDWARE_TIMEOUT_REASON = "GPU-Transkodierung hat zu lange gedauert; diese Wiedergabe verwendet die CPU."


class _EncodingFailure(Exception):
    def __init__(self, *, timed_out: bool = False) -> None:
        self.timed_out = timed_out


class PlaybackError(Exception):
    def __init__(self, message: str, status_code: int = 409) -> None:
        super().__init__(message)
        self.status_code = status_code


def _iso() -> str:
    return datetime.now(UTC).isoformat()


def client_name(user_agent: str) -> str:
    """Nur eine kurze Geraeteklasse; keine frei behaupteten Namen im Dashboard."""
    browser = next((name for marker, name in (
        ("Edg/", "Edge"), ("Firefox/", "Firefox"), ("Chrome/", "Chrome"),
        ("Safari/", "Safari"),
    ) if marker in user_agent), "Browser")
    device = "Mobilgeraet" if any(x in user_agent for x in ("Mobile", "Android", "iPhone")) else "Computer"
    return f"{browser} · {device}"


@dataclass(slots=True)
class Viewer:
    token: str
    id: str
    video_id: str
    video_title: str
    channel_title: str | None
    client_address: str
    client_name: str
    mode: Literal["direct", "transcode"]
    duration_s: float | None
    source: Path
    offset: int
    size: int
    quality: playback_quality.Quality = "auto"
    quality_label: str = "Automatisch"
    profile: playback_quality.Profile = playback_quality.PROFILES["1080p"]
    encoding: live_encoding.Encoding = live_encoding.SOFTWARE
    encoder_state: str = "pending"
    fallback_reason: str | None = None
    started_at: str = field(default_factory=_iso)
    last_seen_at: str = field(default_factory=_iso)
    last_seen: float = field(default_factory=time.monotonic)
    state: str = "buffering"
    position_s: float = 0
    process: subprocess.Popen[bytes] | None = None
    segment_lock: threading.Lock = field(default_factory=threading.Lock)
    segment_requests: deque[float] = field(default_factory=deque)


class StreamManager:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._viewers: dict[str, Viewer] = {}
        self._segments: OrderedDict[tuple[str, int], bytes] = OrderedDict()
        self._cache_bytes = 0
        self._slots = threading.BoundedSemaphore(MAX_TRANSCODES)
        self._creates: deque[float] = deque()
        self._requests: deque[float] = deque()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._watch, name="wiedergabe-aufraeumen", daemon=True)
            self._thread.start()

    def _watch(self) -> None:
        while not self._stop.wait(15):
            self.reap()

    @staticmethod
    def _budget(history: deque[float], limit: int, now: float) -> None:
        while history and history[0] <= now - 60:
            history.popleft()
        if len(history) >= limit:
            raise PlaybackError("Zu viele Wiedergabeanfragen. Bitte kurz warten.", 429)
        history.append(now)

    def create(self, **kwargs: object) -> Viewer:
        self.reap()
        with self._lock:
            if len(self._viewers) >= MAX_SESSIONS:
                raise PlaybackError("Alle Wiedergabeplaetze sind belegt. Bitte spaeter erneut versuchen.", 503)
            address = kwargs["client_address"]
            if sum(v.client_address == address for v in self._viewers.values()) >= MAX_CLIENT_SESSIONS:
                raise PlaybackError("Zu viele offene Player fuer diese Verbindung. Bitte andere Player schliessen.", 429)
            self._budget(self._creates, 60, time.monotonic())
            viewer = Viewer(token=secrets.token_urlsafe(32), id=secrets.token_hex(8), **kwargs)  # type: ignore[arg-type]
            if viewer.mode == "transcode" and (
                viewer.duration_s is None or not math.isfinite(viewer.duration_s)
                or not 0 < viewer.duration_s <= MAX_DURATION_SECONDS
            ):
                raise PlaybackError("Die Videolaenge fehlt oder ist fuer Live-Transkodierung nicht geeignet.")
            if viewer.mode == "direct" and viewer.duration_s is not None and (
                not math.isfinite(viewer.duration_s) or viewer.duration_s <= 0
            ):
                viewer.duration_s = None
            if viewer.mode == "transcode":
                try:
                    viewer.encoding = live_encoding.configured()
                except ValueError:
                    viewer.fallback_reason = HARDWARE_FALLBACK_REASON
            else:
                viewer.encoder_state = "direct"
            self._viewers[viewer.token] = viewer
            return viewer

    def _get(self, token: str) -> Viewer:
        viewer = self._viewers.get(token)
        if viewer is None or time.monotonic() - viewer.last_seen >= IDLE_SECONDS:
            raise PlaybackError("Die Wiedergabesitzung ist abgelaufen. Bitte den Player neu laden.", 404)
        return viewer

    @staticmethod
    def _touch(viewer: Viewer) -> None:
        viewer.last_seen = time.monotonic()
        viewer.last_seen_at = _iso()

    def heartbeat(self, token: str, position_s: float, state: str) -> None:
        with self._lock:
            viewer = self._get(token)
            viewer.position_s = min(position_s, viewer.duration_s) if viewer.duration_s else position_s
            viewer.state = state
            self._touch(viewer)

    def playlist(self, token: str) -> str:
        with self._lock:
            viewer = self._get(token)
            if viewer.mode != "transcode" or viewer.duration_s is None:
                raise PlaybackError("Diese Wiedergabe verwendet keinen HLS-Stream.", 404)
            self._touch(viewer)
            duration = viewer.duration_s
        lines = ["#EXTM3U", "#EXT-X-VERSION:6", f"#EXT-X-TARGETDURATION:{SEGMENT_SECONDS}",
                 "#EXT-X-MEDIA-SEQUENCE:0", "#EXT-X-PLAYLIST-TYPE:VOD", "#EXT-X-INDEPENDENT-SEGMENTS"]
        for index in range(math.ceil(duration / SEGMENT_SECONDS)):
            # Jeder Abschnitt beginnt mit einem IDR-Bild und eigener Zeitbasis.
            # Das erlaubt Spruenge ohne vorherige Abschnitte zu kodieren.
            if index:
                lines.append("#EXT-X-DISCONTINUITY")
            length = min(SEGMENT_SECONDS, duration - index * SEGMENT_SECONDS)
            lines.extend([f"#EXTINF:{length:.6f},", f"segments/{index}.ts"])
        return "\n".join([*lines, "#EXT-X-ENDLIST", ""])

    def _command(self, viewer: Viewer, index: int) -> list[str]:
        source = f"subfile,,start,{viewer.offset},end,{viewer.offset + viewer.size},,:{viewer.source.resolve()}"
        start = index * SEGMENT_SECONDS
        length = min(SEGMENT_SECONDS, (viewer.duration_s or 0) - start)
        return [
            settings.ffmpeg_path, "-hide_banner", "-loglevel", "error", "-nostdin",
            *viewer.encoding.device_options,
            "-protocol_whitelist", "file,subfile,pipe", "-threads", "2",
            "-format_whitelist", "mov,matroska,webm,ogg,mp3",
            "-ss", str(start), "-i", source, "-t", str(length),
            "-map", "0:v:0", "-map", "0:a:0?", "-sn", "-dn",
            *viewer.encoding.video_options(viewer.profile),
            "-threads", "2", "-filter_threads", "1", "-r", "30", "-g", "180",
            "-c:a", "aac",
            "-b:a", f"{viewer.profile.audio_bitrate_kbps}k", "-ac", "2", "-ar", "48000",
            "-fs", str(MAX_SEGMENT_BYTES), "-f", "mpegts", "pipe:1",
        ]

    @staticmethod
    def _check_cancelled(cancelled: threading.Event | None) -> None:
        if cancelled is not None and cancelled.is_set():
            raise PlaybackError("Der Videoabruf wurde beendet.", 499)

    def _finish_process(self, viewer: Viewer, process: subprocess.Popen[bytes]) -> None:
        if process.poll() is None:
            with suppress(OSError):
                process.kill()
        if process.poll() is None:
            try:
                process.wait(timeout=1)
            except (subprocess.TimeoutExpired, OSError):
                # Kein zweiter Encoder, solange der erste noch lebt. Der Slot
                # bleibt bis zum tatsaechlichen Prozessende reserviert.
                if process.poll() is None:
                    with self._lock:
                        if viewer.encoding.hardware_accel is not HardwareAccel.NONE:
                            viewer.encoding = live_encoding.SOFTWARE
                            viewer.fallback_reason = HARDWARE_FALLBACK_REASON
                    raise PlaybackError("Die Live-Transkodierung wird noch beendet. Bitte kurz warten.", 503) from None
        if getattr(process, "stdout", None) is not None:
            with suppress(OSError):
                process.stdout.close()
        with self._lock:
            if viewer.process is process:
                viewer.process = None

    def _release_slot(self, viewer: Viewer) -> None:
        process = viewer.process
        if process is None or process.poll() is not None:
            self._slots.release()
            return

        def after_exit() -> None:
            # Hoechstens MAX_TRANSCODES solcher Aufraeumer sind moeglich:
            # Solange der Prozess lebt, bleibt sein Encoderplatz belegt.
            while process.poll() is None:
                with suppress(subprocess.TimeoutExpired, OSError):
                    process.wait(timeout=1)
                if process.poll() is None:
                    time.sleep(0.1)
            with self._lock:
                if viewer.process is process:
                    viewer.process = None
            if getattr(process, "stdout", None) is not None:
                with suppress(OSError):
                    process.stdout.close()
            self._slots.release()

        threading.Thread(target=after_exit, name="wiedergabe-prozessende", daemon=True).start()

    def _encode_attempt(self, viewer: Viewer, index: int, cancelled: threading.Event | None,
                        deadline: float) -> bytes:
        process = None
        try:
            self._check_cancelled(cancelled)
            with self._lock:
                self._get(viewer.token)
                process = subprocess.Popen(
                    self._command(viewer, index), stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
                )
                viewer.process = process
                viewer.encoder_state = "running"
            while True:
                self._check_cancelled(cancelled)
                with self._lock:
                    self._get(viewer.token)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise _EncodingFailure(timed_out=True)
                try:
                    data, _ = process.communicate(timeout=min(0.25, remaining))
                    break
                except subprocess.TimeoutExpired:
                    continue
            self._check_cancelled(cancelled)
            with self._lock:
                self._get(viewer.token)
            if process.returncode != 0 or not data or len(data) >= MAX_SEGMENT_BYTES:
                raise _EncodingFailure()
            return data
        except OSError:
            raise _EncodingFailure() from None
        finally:
            if process is not None:
                self._finish_process(viewer, process)

    def segment(self, token: str, index: int, cancelled: threading.Event | None = None) -> bytes:
        self._check_cancelled(cancelled)
        with self._lock:
            viewer = self._get(token)
            if (viewer.mode != "transcode" or viewer.duration_s is None or index < 0
                    or index >= math.ceil(viewer.duration_s / SEGMENT_SECONDS)):
                raise PlaybackError("Videoabschnitt unbekannt.", 404)
            now = time.monotonic()
            self._budget(self._requests, 240, now)
            self._budget(viewer.segment_requests, 60, now)
            self._touch(viewer)
        if not viewer.segment_lock.acquire(timeout=3):
            raise PlaybackError("Ein Videoabschnitt wird bereits vorbereitet. Bitte kurz warten.", 503)
        try:
            self._check_cancelled(cancelled)
            key = (token, index)
            with self._lock:
                self._get(token)
                if viewer.process is not None and viewer.process.poll() is None:
                    raise PlaybackError("Die Live-Transkodierung wird noch beendet. Bitte kurz warten.", 503)
                if key in self._segments:
                    self._segments.move_to_end(key)
                    return self._segments[key]
            if not self._slots.acquire(timeout=3):
                raise PlaybackError("Die Live-Transkodierung ist ausgelastet. Bitte kurz warten.", 503)
            try:
                self._check_cancelled(cancelled)
                deadline = time.monotonic() + ENCODE_TIMEOUT_SECONDS
                while True:
                    hardware = viewer.encoding.hardware_accel is not HardwareAccel.NONE
                    attempt_deadline = min(deadline, time.monotonic() + HARDWARE_TIMEOUT_SECONDS) if hardware else deadline
                    try:
                        data = self._encode_attempt(viewer, index, cancelled, attempt_deadline)
                        break
                    except _EncodingFailure as failure:
                        # Abbruch, Sitzungsende und Ablauf sind keine GPU-Fehler
                        # und duerfen keinen neuen CPU-Prozess ausloesen.
                        self._check_cancelled(cancelled)
                        with self._lock:
                            self._get(token)
                            if hardware:
                                viewer.encoding = live_encoding.SOFTWARE
                                viewer.encoder_state = "pending"
                                viewer.fallback_reason = HARDWARE_TIMEOUT_REASON if failure.timed_out else HARDWARE_FALLBACK_REASON
                        if hardware and time.monotonic() < deadline:
                            continue
                        message = ("Die Live-Transkodierung hat zu lange gedauert." if failure.timed_out
                                   else "Dieser Videoabschnitt konnte nicht transkodiert werden.")
                        raise PlaybackError(message, 503) from None
                with self._lock:
                    self._get(token)
                    self._touch(viewer)
                    viewer.encoder_state = "ready"
                    while self._segments and self._cache_bytes + len(data) > MAX_CACHE_BYTES:
                        _, discarded = self._segments.popitem(last=False)
                        self._cache_bytes -= len(discarded)
                    self._segments[key] = data
                    self._cache_bytes += len(data)
                return data
            except PlaybackError as error:
                with self._lock:
                    if error.status_code in (404, 499):
                        viewer.encoder_state = "ready" if any(k[0] == token for k in self._segments) else "pending"
                    else:
                        viewer.encoder_state = "failed"
                raise
            finally:
                self._release_slot(viewer)
        finally:
            viewer.segment_lock.release()

    def end(self, token: str) -> None:
        with self._lock:
            viewer = self._viewers.pop(token, None)
            for key in [k for k in self._segments if k[0] == token]:
                self._cache_bytes -= len(self._segments.pop(key))
            # Nur der Prozess dieser Zuschauersitzung wird beendet.
            if viewer and viewer.process and viewer.process.poll() is None:
                with suppress(OSError):
                    viewer.process.kill()

    def reap(self) -> None:
        with self._lock:
            now = time.monotonic()
            for token in [t for t, v in self._viewers.items() if now - v.last_seen >= IDLE_SECONDS]:
                self.end(token)

    def snapshot(self) -> dict[str, object]:
        self.reap()
        with self._lock:
            streams = [{
                "id": v.id, "video_id": v.video_id, "video_title": v.video_title,
                "channel_title": v.channel_title, "client_address": v.client_address,
                "client_name": v.client_name, "mode": v.mode, "state": v.state,
                "quality": v.quality, "quality_label": v.quality_label,
                "encoder": v.encoding.encoder if v.mode == "transcode" else None,
                "hardware_accel": v.encoding.hardware_accel.value if v.mode == "transcode" else "none",
                "encoder_state": v.encoder_state, "fallback_reason": v.fallback_reason,
                "position_s": v.position_s, "duration_s": v.duration_s,
                "started_at": v.started_at, "last_seen_at": v.last_seen_at,
                "transcoding": v.process is not None and v.process.poll() is None,
                "segments_ready": sum(k[0] == v.token for k in self._segments),
            } for v in self._viewers.values()]
            return {"streams": streams, "limits": {"sessions": MAX_SESSIONS, "transcodes": MAX_TRANSCODES}}

    def close(self) -> None:
        self._stop.set()
        with self._lock:
            for token in list(self._viewers):
                self.end(token)
        if self._thread:
            self._thread.join(timeout=2)


manager = StreamManager()
