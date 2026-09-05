"""GPU-Wege, wirkliche H.264-Segmente und begrenzter CPU-Rueckfall.

Optionale echte GPU-Proben: VITRINE_TEST_LIVE_GPUS=nvenc,qsv (nur Testprozess).
Ohne passende Hardware prueft die Suite den echten Fehlgeraet->CPU-Pfad.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from app.config import HardwareAccel, settings
from app.services import live_encoding, live_streams, playback_quality
from app.services.bundle import BundleManifest, BundleReader, write_bundle
from tests.test_live_streams import create


@pytest.fixture
def manager(monkeypatch):
    monkeypatch.setattr(settings, "hwaccel", HardwareAccel.NONE)
    instance = live_streams.StreamManager()
    yield instance
    instance.close()


def option(command, name):
    return command[command.index(name) + 1]


@pytest.mark.parametrize("hardware", list(HardwareAccel))
def test_encoder_and_bounded_profile_command(manager, tmp_path, monkeypatch, hardware):
    monkeypatch.setattr(settings, "hwaccel", hardware)
    monkeypatch.setattr(live_encoding.sys, "platform", "linux")
    monkeypatch.setattr(live_encoding, "_intel_render_node", lambda _: False)
    monkeypatch.setattr(settings, "hwaccel_device", "/dev/dri/renderD129")
    viewer = create(manager, tmp_path, profile=playback_quality.PROFILES["480p"])
    cmd = manager._command(viewer, 2)
    assert option(cmd, "-c:v") == {"none": "libx264", "nvenc": "h264_nvenc",
                                    "qsv": "h264_qsv", "vaapi": "h264_vaapi"}[hardware]
    assert option(cmd, "-vf").startswith(viewer.profile.scale_filter)
    assert option(cmd, "-ss") == "12" and option(cmd, "-t") == "3.0"
    assert option(cmd, "-maxrate") == "1200k" and option(cmd, "-bufsize") == "2400k"
    assert option(cmd, "-b:a") == "96k" and option(cmd, "-r") == "30"
    assert option(cmd, "-protocol_whitelist") == "file,subfile,pipe"
    assert option(cmd, "-format_whitelist") == "mov,matroska,webm,ogg,mp3"
    assert option(cmd, "-fs") == str(live_streams.MAX_SEGMENT_BYTES)
    assert cmd.count("-threads") == 2 and option(cmd, "-filter_threads") == "1"
    assert option(cmd, "-i").startswith("subfile,,start,0,end,10,,:")
    if hardware is HardwareAccel.NONE:
        assert "-init_hw_device" not in cmd and "hwupload" not in option(cmd, "-vf")
        assert option(cmd, "-crf") == "23" and option(cmd, "-pix_fmt") == "yuv420p"
    else:
        assert "format=nv12" in option(cmd, "-vf") and "-pix_fmt" not in cmd
        assert option(cmd, "-b:v") == "960k"
        assert "-global_quality" not in cmd and "-crf" not in cmd
    if hardware in (HardwareAccel.QSV, HardwareAccel.VAAPI):
        assert cmd.index("-init_hw_device") < cmd.index("-i")
        assert "/dev/dri/renderD129" in option(cmd, "-init_hw_device")
        assert option(cmd, "-filter_hw_device") == "live"
    if hardware is HardwareAccel.QSV:
        assert option(cmd, "-init_hw_device") == "qsv=live:hw,child_device=/dev/dri/renderD129,child_device_type=vaapi"
        assert option(cmd, "-low_power") == "1"
        assert "hwupload" not in option(cmd, "-vf"), "QSV richtet den internen Upload selbst aus"
    if hardware is HardwareAccel.VAAPI:
        assert "-low_power" not in cmd and option(cmd, "-rc_mode") == "VBR"
        assert "hwupload" in option(cmd, "-vf")


def test_windows_qsv_uses_hardware_context_without_linux_render_node(manager, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "hwaccel", HardwareAccel.QSV)
    monkeypatch.setattr(live_encoding.sys, "platform", "win32")
    cmd = manager._command(create(manager, tmp_path), 0)
    assert option(cmd, "-init_hw_device") == "qsv=live:hw"
    assert "-low_power" not in cmd
    assert "/dev/dri/" not in " ".join(cmd)


@pytest.mark.parametrize("intel", [False, True])
def test_vaapi_uses_low_power_only_for_locally_identified_intel(manager, tmp_path, monkeypatch, intel):
    monkeypatch.setattr(settings, "hwaccel", HardwareAccel.VAAPI)
    monkeypatch.setattr(live_encoding, "_intel_render_node", lambda _: intel)
    cmd = manager._command(create(manager, tmp_path), 0)
    assert ("-low_power" in cmd) == intel


@pytest.mark.parametrize("vendor", ["0x8086\n", "0x1002\n", "invalid", None])
def test_vendor_detection_reads_only_selected_local_node(monkeypatch, vendor):
    monkeypatch.setattr(live_encoding.sys, "platform", "linux")

    def read(path, **kwargs):
        assert path.as_posix() == "/sys/class/drm/renderD129/device/vendor"
        if vendor is None:
            raise OSError("not mounted")
        return vendor

    monkeypatch.setattr(Path, "read_text", read)
    assert live_encoding._intel_render_node("/dev/dri/renderD129") is (vendor == "0x8086\n")


def test_configuration_is_frozen_per_viewer_and_direct_has_no_encoder(manager, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "hwaccel", HardwareAccel.NVENC)
    first = create(manager, tmp_path)
    direct = create(manager, tmp_path, mode="direct")
    monkeypatch.setattr(settings, "hwaccel", HardwareAccel.NONE)
    second = create(manager, tmp_path)
    assert first.encoding.encoder == "h264_nvenc" and second.encoding.encoder == "libx264"
    with pytest.raises(FrozenInstanceError):
        first.encoding.encoder = "arbitrary"
    rows = {r["id"]: r for r in manager.snapshot()["streams"]}
    assert rows[first.id]["encoder_state"] == rows[second.id]["encoder_state"] == "pending"
    assert rows[direct.id]["encoder"] is None and rows[direct.id]["encoder_state"] == "direct"
    assert rows[direct.id]["hardware_accel"] == "none"


@pytest.mark.parametrize("device", ["/dev/dri/renderD128,child_device=secret", "/tmp/secret", "-i", ""])
def test_invalid_render_configuration_falls_back_without_exposing_value(manager, tmp_path, monkeypatch, device):
    monkeypatch.setattr(settings, "hwaccel", HardwareAccel.VAAPI)
    monkeypatch.setattr(settings, "hwaccel_device", device)
    viewer = create(manager, tmp_path)
    assert viewer.encoding == live_encoding.SOFTWARE
    assert viewer.fallback_reason == live_streams.HARDWARE_FALLBACK_REASON
    assert device not in json.dumps(manager.snapshot()) if device else True


class Process:
    def __init__(self, *, data=b"segment", code=0, callback=None):
        self.data = data
        self.code = code
        self.callback = callback
        self.returncode = None
        self.killed = False

    def communicate(self, timeout):
        if self.callback:
            self.callback(self, timeout)
        self.returncode = self.code
        return self.data, None

    def poll(self):
        return self.returncode

    def kill(self):
        self.killed = True
        self.returncode = -9

    def wait(self, timeout):
        return self.returncode


def mock_processes(monkeypatch, processes):
    calls = []

    def spawn(command, **kwargs):
        assert kwargs["stderr"] == subprocess.DEVNULL and kwargs["stdin"] == subprocess.DEVNULL
        assert not kwargs.get("shell")
        calls.append(command)
        return processes[len(calls) - 1]

    monkeypatch.setattr(live_streams.subprocess, "Popen", spawn)
    return calls


@pytest.mark.parametrize("hardware", [HardwareAccel.QSV, HardwareAccel.NVENC, HardwareAccel.VAAPI])
def test_success_is_reported_only_after_a_real_process_completes(manager, tmp_path, monkeypatch, hardware):
    monkeypatch.setattr(settings, "hwaccel", hardware)
    viewer = create(manager, tmp_path)

    def during(process, timeout):
        row = manager.snapshot()["streams"][0]
        assert row["encoder_state"] == "running" and row["transcoding"]
        assert row["segments_ready"] == 0 and row["fallback_reason"] is None

    calls = mock_processes(monkeypatch, [Process(callback=during)])
    assert manager.segment(viewer.token, 0) == b"segment"
    row = manager.snapshot()["streams"][0]
    assert row["encoder_state"] == "ready" and not row["transcoding"]
    assert row["encoder"] == f"h264_{hardware}" and row["hardware_accel"] == hardware
    assert row["segments_ready"] == 1 and row["fallback_reason"] is None
    assert len(calls) == 1 and viewer.process is None


@pytest.mark.parametrize("failure", ["exit", "empty", "oversize", "spawn"])
def test_gpu_failure_retries_cpu_once_and_stays_cpu_next_segment(manager, tmp_path, monkeypatch, failure):
    monkeypatch.setattr(settings, "hwaccel", HardwareAccel.NVENC)
    viewer = create(manager, tmp_path)
    monkeypatch.setattr(live_streams, "MAX_SEGMENT_BYTES", 16)
    failed = Process(code=1 if failure == "exit" else 0,
                     data=b"" if failure == "empty" else b"x" * 16 if failure == "oversize" else b"bad")
    processes = [failed, Process(), Process(data=b"second")]
    calls = []

    def spawn(command, **kwargs):
        calls.append(option(command, "-c:v"))
        if failure == "spawn" and len(calls) == 1:
            raise OSError("SECRET device=/private/gpu")
        return processes[len(calls) - 1]

    monkeypatch.setattr(live_streams.subprocess, "Popen", spawn)
    assert manager.segment(viewer.token, 0) == b"segment"
    assert manager.segment(viewer.token, 1) == b"second"
    assert manager.segment(viewer.token, 0) == b"segment"
    assert calls == ["h264_nvenc", "libx264", "libx264"]
    row = manager.snapshot()["streams"][0]
    assert row["encoder"] == "libx264" and row["hardware_accel"] == "none"
    assert row["fallback_reason"] == live_streams.HARDWARE_FALLBACK_REASON
    assert row["encoder_state"] == "ready" and row["segments_ready"] == 2
    assert "SECRET" not in json.dumps(row) and viewer.token not in json.dumps(row)


def test_both_encoders_fail_without_third_attempt_or_cached_error(manager, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "hwaccel", HardwareAccel.NVENC)
    viewer = create(manager, tmp_path)
    calls = mock_processes(monkeypatch, [Process(code=1), Process(code=1)])
    with pytest.raises(live_streams.PlaybackError) as error:
        manager.segment(viewer.token, 0)
    assert error.value.status_code == 503 and len(calls) == 2
    assert not manager._segments and manager.snapshot()["streams"][0]["encoder_state"] == "failed"
    assert viewer.encoding == live_encoding.SOFTWARE
    assert manager._slots.acquire(blocking=False) and manager._slots.acquire(blocking=False)
    manager._slots.release()
    manager._slots.release()


@pytest.mark.parametrize("interrupt", ["cancel", "end", "expire"])
def test_gpu_failure_during_interruption_never_starts_cpu(manager, tmp_path, monkeypatch, interrupt):
    monkeypatch.setattr(settings, "hwaccel", HardwareAccel.NVENC)
    viewer = create(manager, tmp_path)
    cancelled = threading.Event()

    def stop(process, timeout):
        if interrupt == "cancel":
            cancelled.set()
        elif interrupt == "end":
            manager.end(viewer.token)
        else:
            viewer.last_seen -= live_streams.IDLE_SECONDS + 1

    calls = mock_processes(monkeypatch, [Process(code=1, callback=stop)])
    with pytest.raises(live_streams.PlaybackError) as error:
        manager.segment(viewer.token, 0, cancelled)
    assert error.value.status_code == (499 if interrupt == "cancel" else 404)
    assert len(calls) == 1 and not manager._segments and viewer.fallback_reason is None


def test_hardware_timeout_and_cpu_share_one_45_second_budget(manager, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "hwaccel", HardwareAccel.NVENC)
    clock = [100.0]
    monkeypatch.setattr(live_streams.time, "monotonic", lambda: clock[0])
    viewer = create(manager, tmp_path)

    def stalled(process, timeout):
        clock[0] += timeout
        raise subprocess.TimeoutExpired("ffmpeg", timeout)

    gpu, cpu = Process(callback=stalled), Process(callback=stalled)
    calls = mock_processes(monkeypatch, [gpu, cpu])
    with pytest.raises(live_streams.PlaybackError) as error:
        manager.segment(viewer.token, 0)
    assert error.value.status_code == 503 and len(calls) == 2
    assert clock[0] == 145 and gpu.killed and cpu.killed
    assert viewer.fallback_reason == live_streams.HARDWARE_TIMEOUT_REASON
    assert viewer.encoding == live_encoding.SOFTWARE and viewer.process is None


def test_stuck_gpu_keeps_slot_until_exit_and_cannot_be_retried(manager, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "hwaccel", HardwareAccel.NVENC)
    monkeypatch.setattr(live_streams, "HARDWARE_TIMEOUT_SECONDS", 0)
    viewer = create(manager, tmp_path)
    exited = threading.Event()

    class Stuck(Process):
        def kill(self):
            self.killed = True

        def poll(self):
            return -9 if exited.is_set() else None

        def wait(self, timeout):
            if not exited.wait(0.01):
                raise subprocess.TimeoutExpired("ffmpeg", timeout)
            return -9

    process = Stuck()
    calls = mock_processes(monkeypatch, [process, Process()])
    try:
        with pytest.raises(live_streams.PlaybackError):
            manager.segment(viewer.token, 0)
        assert len(calls) == 1 and process.killed and viewer.process is process
        assert viewer.encoding == live_encoding.SOFTWARE
        assert manager._slots.acquire(blocking=False)
        assert not manager._slots.acquire(blocking=False)
        manager._slots.release()
        with pytest.raises(live_streams.PlaybackError):
            manager.segment(viewer.token, 1)
        assert len(calls) == 1
    finally:
        exited.set()
    # Einen freien zweiten Slot belegen; der festgehaltene erste wird nach
    # bestaetigtem Prozessende wieder frei, ohne ein Polling im Test.
    assert manager._slots.acquire(timeout=1)
    assert manager._slots.acquire(timeout=1)
    manager._slots.release()
    manager._slots.release()
    assert manager.segment(viewer.token, 1) == b"segment"
    assert option(calls[1], "-c:v") == "libx264"


@pytest.fixture
def real_source(tmp_path, monkeypatch):
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        pytest.skip("FFmpeg/ffprobe fehlt")
    monkeypatch.setattr(settings, "ffmpeg_path", ffmpeg)
    source = tmp_path / "source.mp4"
    subprocess.run([ffmpeg, "-v", "error", "-nostdin", "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30",
                    "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000", "-t", "7", "-c:v", "libx264",
                    "-preset", "ultrafast", "-threads", "2", "-c:a", "aac", "-y", str(source)],
                   capture_output=True, check=True, timeout=30)
    manifest = BundleManifest(schema_version=1, video_id="film", channel_id="kanal", title="GPU-Test",
                              media_name="", media_bytes=0, mime_type="", video_codec="h264", audio_codec="aac",
                              width=1280, height=720, duration_s=7)
    bundle = tmp_path / "archive.zip"
    write_bundle(bundle, manifest=manifest, media_file=source)
    with BundleReader(bundle) as reader:
        offset, size = reader.media_data_offset(), reader.media_size
    return dict(source=bundle, offset=offset, size=size, duration_s=7,
                profile=playback_quality.PROFILES["240p"]), ffprobe


def assert_real_segment(manager, viewer, index, ffprobe, target):
    target.write_bytes(manager.segment(viewer.token, index))
    result = subprocess.run([ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(target)],
                            capture_output=True, text=True, check=True, timeout=15)
    info = json.loads(result.stdout)
    assert {s["codec_name"] for s in info["streams"]} == {"h264", "aac"}
    video = next(s for s in info["streams"] if s["codec_type"] == "video")
    assert (video["width"], video["height"]) == (426, 240)
    assert video["pix_fmt"] == "yuv420p"
    assert float(info["format"]["duration"]) < (1.4 if index == 1 else 6.4)
    assert 0 < target.stat().st_size < live_streams.MAX_SEGMENT_BYTES


def test_real_missing_gpu_falls_back_and_decodes_seekable_segments(manager, real_source, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "hwaccel", HardwareAccel.VAAPI)
    monkeypatch.setattr(settings, "hwaccel_device", "/dev/dri/renderD9999")
    source, ffprobe = real_source
    viewer = create(manager, tmp_path, **source)
    assert viewer.encoding.encoder == "h264_vaapi"
    assert_real_segment(manager, viewer, 1, ffprobe, tmp_path / "last.ts")
    assert viewer.encoding.encoder == "libx264" and viewer.fallback_reason == live_streams.HARDWARE_FALLBACK_REASON
    assert_real_segment(manager, viewer, 0, ffprobe, tmp_path / "first.ts")
    assert viewer.encoder_state == "ready" and len(manager._segments) == 2


@pytest.mark.parametrize("hardware", [HardwareAccel.NVENC, HardwareAccel.QSV, HardwareAccel.VAAPI])
def test_real_opt_in_gpu_succeeds_without_cpu_fallback(manager, tmp_path, monkeypatch, hardware, request):
    if hardware not in os.getenv("VITRINE_TEST_LIVE_GPUS", "").split(","):
        pytest.skip("Explizit mit VITRINE_TEST_LIVE_GPUS auf einem GPU-Testhost aktivieren")
    source, ffprobe = request.getfixturevalue("real_source")
    monkeypatch.setattr(settings, "hwaccel", hardware)
    viewer = create(manager, tmp_path, **source)
    assert_real_segment(manager, viewer, 1, ffprobe, tmp_path / f"{hardware}-last.ts")
    assert_real_segment(manager, viewer, 0, ffprobe, tmp_path / f"{hardware}-first.ts")
    row = manager.snapshot()["streams"][0]
    assert row["encoder"] == f"h264_{hardware}" and row["hardware_accel"] == hardware
    assert row["fallback_reason"] is None and row["encoder_state"] == "ready"
    print(f"\nEchter {hardware}-Test: 426x240 H.264/AAC, zwei seekbare Abschnitte, kein CPU-Fallback.")
