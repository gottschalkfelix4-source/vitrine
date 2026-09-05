"""Qualitaetsentscheidungen sowie echte getrennte HLS-Aufloesungen."""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import stream
from app.config import settings
from app.db import get_db
from app.models import Channel, Video, VideoStatus
from app.services import live_streams, playback
from app.services import playback_quality as quality
from app.services.bundle import BundleManifest, write_bundle
from tests.conftest import neue_sitzung


def manifest(width=1920, height=1080, *, extension="mp4"):
    return BundleManifest(schema_version=1, video_id="film", channel_id="channel", title="Film",
                          media_name=f"media/film.{extension}", media_bytes=1, mime_type="video/mp4",
                          video_codec="h264", audio_codec="aac", width=width, height=height)


def choose(source=None, **kwargs):
    return quality.choose(source or manifest(), playback.FALLBACK_SUPPORT, **kwargs)


def test_auto_prefers_compatible_original_and_offers_real_stages():
    plan = choose()
    assert plan.mode == "direct" and plan.quality == "auto" and plan.label == "Original (1080p)"
    assert [item["value"] for item in plan.available] == ["auto", "original", "1080p", "720p", "480p", "360p", "240p"]
    assert choose(quality="1080p").mode == "direct"
    assert choose(quality="720p").mode == "transcode"
    high = choose(manifest(3840, 2160))
    assert high.label == "Original (2160p)"
    assert "2160p" not in {item["value"] for item in high.available}


@pytest.mark.parametrize("source,forced", [(manifest(extension="mkv"), False), (manifest(), True)])
def test_incompatible_or_forced_transcode_cannot_claim_original(source, forced):
    with pytest.raises(quality.QualityError):
        choose(source, quality="original", force_transcode=forced)
    plan = choose(source, force_transcode=forced)
    assert plan.mode == "transcode" and plan.label == "1080p"
    assert "original" not in {item["value"] for item in plan.available}


@pytest.mark.parametrize(("width", "height", "requested", "expected"), [
    (1920, 1080, "720p", (1280, 720)),
    (1080, 1920, "720p", (720, 1280)),
    (1080, 1920, "360p", (360, 640)),
    (1920, 800, "720p", (1728, 720)),
    (1280, 720, "480p", (852, 480)),
    (1080, 1080, "720p", (720, 720)),
    (853, 481, "480p", (850, 480)),
    (721, 1281, "720p", (720, 1278)),
])
def test_numeric_quality_is_actual_short_edge_without_upscaling(width, height, requested, expected):
    plan = choose(manifest(width, height), quality=requested, force_transcode=True)
    assert (plan.width, plan.height) == expected
    assert plan.label == requested and min(expected) == int(requested[:-1])
    assert plan.width <= width and plan.height <= height


def test_ultrawide_stages_exceeding_encode_budget_are_not_advertised():
    source = manifest(3840, 1600, extension="mkv")
    automatic = choose(source)
    assert (automatic.width, automatic.height) == (1920, 800) and automatic.label == "800p"
    assert "1080p" not in {item["value"] for item in automatic.available}
    with pytest.raises(quality.QualityError):
        choose(source, quality="1080p")
    assert choose(source, quality="720p").label == "720p"
    # Ein bereits kompatibles Original benoetigt keinen Encoder und bleibt
    # unveraendert auch dann direkt, wenn seine lange Kante groesser ist.
    direct = choose(manifest(3840, 1080), quality="1080p")
    assert direct.mode == "direct" and direct.label == "Original (1080p)"
    with pytest.raises(quality.QualityError):
        choose(manifest(3840, 1080), quality="1080p", force_transcode=True)


@pytest.mark.parametrize(("width", "height", "label"), [
    (160, 90, "90p"), (640, 360, "360p"), (900, 506, "506p"), (900, 507, "506p"),
    (3840, 2160, "1080p"), (2160, 3840, "1080p"),
])
def test_auto_names_actual_bounded_resolution(width, height, label):
    plan = choose(manifest(width, height), force_transcode=True)
    assert plan.label == label
    assert max(plan.width, plan.height) <= quality.MAX_LONG_EDGE
    assert plan.width * plan.height <= quality.MAX_PIXELS


@pytest.mark.parametrize(("width", "height"), [
    (None, None), (True, 1080), (1920, False), (0, 720), (1920, -1),
    ("1920", 1080), (1920.0, 1080), (float("nan"), 720), (1920, float("inf")),
    (1, 720), (20_000, 1080), (10**1000, 1080),
])
def test_invalid_source_dimensions_offer_no_invented_resolution(width, height):
    source = manifest(width, height)
    direct = choose(source)
    assert direct.label == "Original" and [item["value"] for item in direct.available] == ["auto", "original"]
    live = choose(source, force_transcode=True)
    assert live.label == "Live-Transkodierung" and live.width is None and live.height is None
    assert live.available == [{"value": "auto", "label": "Automatisch"}]
    with pytest.raises(quality.QualityError):
        choose(source, quality="240p")


def test_dimensions_use_valid_pairs_and_prefer_concrete_bundle():
    assert choose(manifest(640, 360), source_width=1920, source_height=1080).label == "Original (360p)"
    assert choose(manifest(None, None), source_width=1280, source_height=720).label == "Original (720p)"
    assert choose(manifest(640, None), source_width=None, source_height=360).label == "Original"


def test_profiles_are_closed_immutable_and_have_lower_bitrates_for_lower_resolution():
    profiles = list(quality.PROFILES.values())
    assert [p.max_rate_kbps for p in profiles] == sorted((p.max_rate_kbps for p in profiles), reverse=True)
    with pytest.raises(FrozenInstanceError):
        profiles[0].short_edge = 10_000
    with pytest.raises(TypeError):
        quality.PROFILES["invalid"] = profiles[0]


@pytest.fixture
def quality_api(tmp_path, monkeypatch):
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        pytest.skip("ffmpeg/ffprobe fehlen")
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "ffmpeg_path", ffmpeg)
    settings.ensure_dirs()
    manager = live_streams.StreamManager()
    monkeypatch.setattr(live_streams, "manager", manager)
    db = neue_sitzung()
    db.add(Channel(id="channel", name="Kanal"))
    db.commit()

    def add_video(video_id="film", width=1280, height=720, *, known=True):
        source = tmp_path / f"{video_id}.mp4"
        odd = width % 2 or height % 2
        generator = "testsrc" if odd else "testsrc2"
        subprocess.run([ffmpeg, "-v", "error", "-nostdin", "-f", "lavfi", "-i",
                        f"{generator}=size={width}x{height}:rate=30", "-f", "lavfi", "-i", "sine=sample_rate=48000",
                        "-t", "3", "-c:v", "libx264", "-preset", "ultrafast", "-threads", "2",
                        "-pix_fmt", "yuv444p" if odd else "yuv420p", "-c:a", "aac", "-y", str(source)],
                       capture_output=True, check=True, timeout=30)
        bundle = settings.bundle_dir / "channel" / f"{video_id}.zip"
        info = manifest(width if known else None, height if known else None)
        info.video_id = video_id
        info.duration_s = 3
        write_bundle(bundle, manifest=info, media_file=source)
        db.add(Video(id=video_id, channel_id="channel", title=video_id, status=VideoStatus.ARCHIVED,
                     bundle_file=str(bundle), duration_s=3, width=width if known else None, height=height if known else None))
        db.commit()
        return source

    app = FastAPI()
    app.include_router(stream.router)
    app.include_router(stream.playback_router)
    app.dependency_overrides[get_db] = lambda: db
    client = TestClient(app)
    yield client, manager, add_video, ffprobe, tmp_path
    client.close()
    manager.close()
    db.close()


def probe_segment(client, session, ffprobe, target: Path):
    response = client.get(session["url"].replace("index.m3u8", "segments/0.ts"))
    assert response.status_code == 200, response.text[:200]
    target.write_bytes(response.content)
    result = subprocess.run([ffprobe, "-v", "error", "-show_streams", "-of", "json", str(target)],
                            capture_output=True, text=True, check=True, timeout=15)
    info = json.loads(result.stdout)
    video = next(s for s in info["streams"] if s["codec_type"] == "video")
    assert {s["codec_name"] for s in info["streams"]} == {"h264", "aac"}
    return video["width"], video["height"]


def test_two_real_qualities_keep_sessions_and_segments_separate(quality_api):
    client, manager, add_video, ffprobe, tmp = quality_api
    source = add_video()
    outputs = []
    for requested, expected in [("480p", (852, 480)), ("240p", (426, 240))]:
        response = client.post("/api/videos/film/playback", json={"quality": requested})
        assert response.status_code == 200
        session = response.json()
        outputs.append(session)
        assert session["quality"] == session["quality_label"] == requested and session["mode"] == "transcode"
        assert probe_segment(client, session, ffprobe, tmp / f"{requested}.ts") == expected
    assert outputs[0]["token"] != outputs[1]["token"]
    assert len(manager._segments) == 2 and len(manager.snapshot()["streams"]) == 2
    client.post(f"/api/playback/{outputs[0]['token']}/ended")
    assert len(manager._segments) == 1 and client.get(outputs[1]["url"]).status_code == 200
    original = client.post("/api/videos/film/playback", json={"quality": "original"}).json()
    assert original["mode"] == "direct" and original["quality_label"] == "Original (720p)"
    assert client.get(original["url"], headers={"Range": "bytes=0-999"}).content == source.read_bytes()[:1000]


def test_real_portrait_quality_is_short_edge_and_not_landscape_box(quality_api):
    client, _, add_video, ffprobe, tmp = quality_api
    add_video("portrait", 720, 1280)
    session = client.post("/api/videos/portrait/playback", json={"quality": "360p"}).json()
    assert session["quality_label"] == "360p"
    assert probe_segment(client, session, ffprobe, tmp / "portrait.ts") == (360, 640)


@pytest.mark.parametrize(("width", "height", "requested", "expected"), [
    (1920, 800, "720p", (1728, 720)), (853, 481, "480p", (850, 480)),
])
def test_real_wide_and_odd_sources_match_reported_quality(quality_api, width, height, requested, expected):
    client, _, add_video, ffprobe, tmp = quality_api
    add_video("unusual", width, height)
    session = client.post("/api/videos/unusual/playback", json={"quality": requested}).json()
    assert session["quality_label"] == requested
    assert probe_segment(client, session, ffprobe, tmp / "unusual.ts") == expected


def test_api_rejects_unavailable_or_arbitrary_quality_without_sessions(quality_api):
    client, manager, add_video, _, _ = quality_api
    add_video()
    for payload in [{"quality": "1080p"}, {"quality": "2160p"}, {"quality": "-vf movie=secret"},
                    {"quality": 480}, {"quality": None}, {"quality": "original", "force_transcode": True},
                    {"quality": "original", "support": "webm,av01,opus"}]:
        result = client.post("/api/videos/film/playback", json=payload)
        assert result.status_code == 422, (payload, result.text)
    assert manager.snapshot()["streams"] == [] and len(manager._creates) == 0


def test_unknown_dimensions_keep_auto_compatible_and_never_offer_fake_resolutions(quality_api):
    client, _, add_video, ffprobe, tmp = quality_api
    add_video("unknown", 640, 360, known=False)
    direct = client.post("/api/videos/unknown/playback", json={}).json()
    assert direct["quality"] == "auto" and direct["quality_label"] == "Original"
    assert [item["value"] for item in direct["available_qualities"]] == ["auto", "original"]
    live = client.post("/api/videos/unknown/playback", json={"force_transcode": True}).json()
    assert live["quality_label"] == "Live-Transkodierung"
    assert probe_segment(client, live, ffprobe, tmp / "unknown.ts") == (640, 360)
