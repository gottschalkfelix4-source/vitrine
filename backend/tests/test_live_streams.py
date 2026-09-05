"""Echte seekbare Transkodierung und Grenzen oeffentlicher Zuschauersitzungen."""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import stream
from app.config import settings
from app.db import get_db
from app.models import Channel, Video, VideoStatus
from app.services import live_streams
from app.services.bundle import BundleManifest, write_bundle
from tests.conftest import neue_sitzung


@pytest.fixture
def manager(monkeypatch):
    instance = live_streams.StreamManager()
    monkeypatch.setattr(live_streams, "manager", instance)
    yield instance
    instance.close()


def create(manager, tmp_path, **overrides):
    values = dict(video_id="video", video_title="Film", channel_title="Kanal",
                  client_address="127.0.0.1", client_name="Browser", mode="transcode",
                  duration_s=15.0, source=tmp_path / "archive.zip", offset=0, size=10)
    values.update(overrides)
    return manager.create(**values)


def test_playlist_supports_seeking_and_last_short_segment(manager, tmp_path):
    viewer = create(manager, tmp_path)
    playlist = manager.playlist(viewer.token)
    assert "#EXT-X-PLAYLIST-TYPE:VOD" in playlist
    assert playlist.count("#EXT-X-DISCONTINUITY") == 2
    assert "#EXTINF:3.000000,\nsegments/2.ts" in playlist
    assert playlist.endswith("#EXT-X-ENDLIST\n")
    assert manager.snapshot()["streams"][0]["segments_ready"] == 0
    assert not list(tmp_path.iterdir()), "Manifestabruf darf keine gesamte Datei vorbereiten"


def test_sessions_are_separate_and_dashboard_never_exposes_capabilities(manager, tmp_path):
    first, second = create(manager, tmp_path), create(manager, tmp_path)
    manager.heartbeat(first.token, 7.5, "playing")
    manager.heartbeat(second.token, 2, "paused")
    snapshot = manager.snapshot()
    assert len(snapshot["streams"]) == 2
    assert first.token not in json.dumps(snapshot)
    assert first.id != first.token
    manager.end(first.token)
    assert manager.snapshot()["streams"][0]["state"] == "paused"
    assert manager.snapshot()["streams"][0]["position_s"] == 2
    with pytest.raises(live_streams.PlaybackError):
        manager.playlist(first.token)
    assert manager.playlist(second.token)


def test_idle_reaper_removes_crashed_tabs_and_their_cache(manager, tmp_path):
    viewer = create(manager, tmp_path)
    manager._segments[(viewer.token, 0)] = b"123"
    manager._cache_bytes = 3
    viewer.last_seen -= live_streams.IDLE_SECONDS + 1
    manager.reap()
    assert manager.snapshot()["streams"] == []
    assert manager._cache_bytes == 0


def test_admission_limits_apply_per_peer_and_globally(manager, tmp_path, monkeypatch):
    monkeypatch.setattr(live_streams, "MAX_CLIENT_SESSIONS", 2)
    monkeypatch.setattr(live_streams, "MAX_SESSIONS", 3)
    create(manager, tmp_path)
    create(manager, tmp_path)
    with pytest.raises(live_streams.PlaybackError) as error:
        create(manager, tmp_path)
    assert error.value.status_code == 429
    create(manager, tmp_path, client_address="192.0.2.2")
    with pytest.raises(live_streams.PlaybackError) as error:
        create(manager, tmp_path, client_address="192.0.2.3")
    assert error.value.status_code == 503


@pytest.mark.parametrize("duration", [None, 0, -1, float("nan"), float("inf"), 48 * 3600 + 1])
def test_unknown_or_unbounded_duration_rejected_before_process(manager, tmp_path, duration):
    with pytest.raises(live_streams.PlaybackError):
        create(manager, tmp_path, duration_s=duration)
    assert manager.snapshot()["streams"] == []


@pytest.mark.parametrize("index", [-1, 3, 999999])
def test_out_of_bounds_segments_rejected(manager, tmp_path, index):
    viewer = create(manager, tmp_path)
    with pytest.raises(live_streams.PlaybackError) as error:
        manager.segment(viewer.token, index)
    assert error.value.status_code == 404


def test_failed_and_timed_out_processes_release_slots(manager, tmp_path, monkeypatch):
    viewer = create(manager, tmp_path)
    monkeypatch.setattr(live_streams, "ENCODE_TIMEOUT_SECONDS", 0)

    class Process:
        returncode = None
        killed = False

        def communicate(self, timeout=None):
            if not self.killed:
                raise subprocess.TimeoutExpired("ffmpeg", timeout)
            return b"", None

        def kill(self):
            self.killed = True
            self.returncode = -9

        def poll(self):
            return self.returncode

    process = Process()
    monkeypatch.setattr(live_streams.subprocess, "Popen", lambda *a, **kw: process)
    with pytest.raises(live_streams.PlaybackError) as error:
        manager.segment(viewer.token, 0)
    assert error.value.status_code == 503
    assert process.killed and viewer.process is None
    assert manager._slots.acquire(blocking=False)
    assert manager._slots.acquire(blocking=False)
    manager._slots.release()
    manager._slots.release()


def test_disconnected_segment_kills_encoder_and_keeps_other_viewers(manager, tmp_path, monkeypatch):
    cancelled = threading.Event()
    viewer, other = create(manager, tmp_path), create(manager, tmp_path)

    class Process:
        returncode = None
        killed = False

        def communicate(self, timeout=None):
            cancelled.set()
            raise subprocess.TimeoutExpired("ffmpeg", timeout)

        def kill(self):
            self.killed = True
            self.returncode = -9

        def wait(self, timeout):
            return self.returncode

        def poll(self):
            return self.returncode

    process = Process()
    monkeypatch.setattr(live_streams.subprocess, "Popen", lambda *a, **kw: process)
    with pytest.raises(live_streams.PlaybackError) as error:
        manager.segment(viewer.token, 0, cancelled)
    assert error.value.status_code == 499 and process.killed
    assert manager.playlist(other.token)
    assert manager.playlist(viewer.token), "Spulen darf die gesamte Zuschauersitzung nicht beenden"


def test_end_kills_only_own_process(manager, tmp_path):
    class Process:
        killed = False

        def poll(self):
            return None

        def kill(self):
            self.killed = True

    a, b = create(manager, tmp_path), create(manager, tmp_path)
    a.process, b.process = Process(), Process()
    manager.end(a.token)
    assert a.process.killed
    assert not b.process.killed


def test_lru_cache_stays_bounded_and_reuses_finished_segments(manager, tmp_path, monkeypatch):
    viewer = create(manager, tmp_path)
    calls = []

    class Process:
        returncode = 0

        def communicate(self, timeout):
            return b"1234", None

        def poll(self):
            return 0

    def spawn(*args, **kwargs):
        calls.append(args)
        return Process()

    monkeypatch.setattr(live_streams.subprocess, "Popen", spawn)
    monkeypatch.setattr(live_streams, "MAX_CACHE_BYTES", 6)
    assert manager.segment(viewer.token, 0) == b"1234"
    assert manager.segment(viewer.token, 0) == b"1234"
    assert len(calls) == 1
    manager.segment(viewer.token, 2)
    assert manager._cache_bytes == 4
    assert list(manager._segments) == [(viewer.token, 2)]


def test_only_two_encoders_can_run_at_once(manager, tmp_path, monkeypatch):
    release = threading.Event()
    both_running = threading.Event()
    spawned = []

    class Process:
        returncode = 0

        def communicate(self, timeout):
            assert release.wait(10)
            return b"segment", None

        def poll(self):
            return 0

    def spawn(*args, **kwargs):
        process = Process()
        spawned.append(process)
        if len(spawned) == 2:
            both_running.set()
        return process

    monkeypatch.setattr(live_streams.subprocess, "Popen", spawn)
    viewers = [create(manager, tmp_path) for _ in range(3)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(manager.segment, viewer.token, 0) for viewer in viewers[:2]]
        try:
            assert both_running.wait(3)
            with pytest.raises(live_streams.PlaybackError) as error:
                manager.segment(viewers[2].token, 0)
            assert error.value.status_code == 503
            assert len(spawned) == 2
        finally:
            release.set()
        assert [future.result(timeout=3) for future in futures] == [b"segment", b"segment"]


@pytest.fixture
def media_api(tmp_path, monkeypatch, manager):
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        pytest.skip("ffmpeg/ffprobe fehlen")
    monkeypatch.setattr(settings, "ffmpeg_path", ffmpeg)
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    settings.ensure_dirs()
    source = tmp_path / "source.mkv"
    subprocess.run([
        ffmpeg, "-v", "error", "-nostdin", "-f", "lavfi", "-i",
        "testsrc2=size=320x180:rate=30", "-f", "lavfi", "-i", "sine=frequency=660:sample_rate=48000",
        "-t", "15", "-c:v", "libx264", "-preset", "ultrafast", "-threads", "2",
        "-c:a", "aac", "-y", str(source),
    ], check=True, capture_output=True, timeout=30)
    bundle = settings.bundle_dir / "UCtest" / "film.zip"
    manifest = BundleManifest(schema_version=1, video_id="film", channel_id="UCtest", title="Film",
                              media_name="", media_bytes=0, mime_type="", video_codec="h264",
                              audio_codec="aac", duration_s=15)
    write_bundle(bundle, manifest=manifest, media_file=source)
    db = neue_sitzung()
    db.add(Channel(id="UCtest", name="Kanal"))
    db.commit()
    db.add(Video(id="film", channel_id="UCtest", title="Film", status=VideoStatus.ARCHIVED,
                 duration_s=15, bundle_file=str(bundle)))
    db.commit()
    app = FastAPI()
    app.include_router(stream.router)
    app.include_router(stream.playback_router)
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as client:
        yield client, db, tmp_path, ffprobe
    db.close()


def test_real_ffmpeg_starts_with_requested_segment_and_seeks_to_last(media_api, manager):
    client, _, tmp, ffprobe = media_api
    response = client.post("/api/videos/film/playback", json={"support": "mp4,h264,aac"})
    assert response.status_code == 200, response.text
    playback = response.json()
    assert playback["mode"] == "transcode"
    assert client.get(playback["url"]).status_code == 200
    token = playback["token"]
    # Den letzten Abschnitt zuerst anfordern: davor wird nichts kodiert.
    last = client.get(f"/api/playback/{token}/segments/2.ts")
    assert last.status_code == 200, last.text[:200]
    assert list(manager._segments) == [(token, 2)]
    assert manager.snapshot()["streams"][0]["segments_ready"] == 1
    target = tmp / "last.ts"
    target.write_bytes(last.content)
    probe = subprocess.run([ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(target)],
                           capture_output=True, text=True, check=True, timeout=15)
    info = json.loads(probe.stdout)
    assert {s["codec_name"] for s in info["streams"]} == {"h264", "aac"}
    assert 2.8 < float(info["format"]["duration"]) < 3.3
    first = client.get(f"/api/playback/{token}/segments/0.ts")
    assert first.status_code == 200
    assert len(manager._segments) == 2
    assert client.post(f"/api/playback/{token}/heartbeat", json={"position_s": 12, "state": "paused"}).status_code == 204
    dashboard = client.get("/api/streams").json()["streams"][0]
    assert dashboard["state"] == "paused" and dashboard["position_s"] == 12
    assert client.post(f"/api/playback/{token}/ended").status_code == 204
    assert client.get(playback["url"]).status_code == 404
    assert manager._cache_bytes == 0


def test_direct_playback_and_validation(media_api, manager):
    client, db, tmp_path, _ = media_api
    video = db.get(Video, "film")
    # Ein Mkv muss transkodiert werden, auch wenn der Client den Codec versteht.
    assert client.post("/api/videos/film/playback", json={"support": "mkv,h264,aac"}).json()["mode"] == "transcode"
    video.status = VideoStatus.QUEUED
    db.commit()
    assert client.post("/api/videos/film/playback", json={}).status_code == 404
    assert client.get("/api/videos/film/playback-state").status_code == 404
    token = create(manager, Path("."), mode="direct", duration_s=None).token
    assert client.get(f"/api/playback/{token}/index.m3u8").status_code == 404
    assert client.post(f"/api/playback/{token}/heartbeat", json={"position_s": -1}).status_code == 422
    assert client.post(f"/api/playback/{token}/heartbeat", json={"state": "arbitrary"}).status_code == 422
    assert client.get("/api/playback/unknown/segments/0.ts").status_code == 404

    # Ein browsergeeignetes Archiv muss auch ueber die neue Sitzungs-API
    # unveraendert und bytegenau ausgeliefert werden.
    source = tmp_path / "direct.mp4"
    subprocess.run([settings.ffmpeg_path, "-v", "error", "-nostdin", "-i", str(tmp_path / "source.mkv"),
                    "-c", "copy", "-y", str(source)], capture_output=True, check=True, timeout=15)
    bundle = settings.bundle_dir / "UCtest" / "direct.zip"
    manifest = BundleManifest(schema_version=1, video_id="direct", channel_id="UCtest", title="Direct",
                              media_name="", media_bytes=0, mime_type="", video_codec="h264", audio_codec="aac")
    write_bundle(bundle, manifest=manifest, media_file=source)
    db.add(Video(id="direct", title="Direct", status=VideoStatus.ARCHIVED, duration_s=15,
                 channel_id="UCtest", bundle_file=str(bundle)))
    db.commit()
    direct = client.post("/api/videos/direct/playback", json={}).json()
    assert direct["mode"] == "direct" and direct["duration_s"] == 15
    delivered = client.get(direct["url"], headers={"Range": "bytes=100-999"})
    assert delivered.status_code == 206 and delivered.content == source.read_bytes()[100:1000]
    fallback = client.post("/api/videos/direct/playback", json={"force_transcode": True}).json()
    assert fallback["mode"] == "transcode"
    assert client.get(f"/api/playback/{fallback['token']}/segments/0.ts").status_code == 200
