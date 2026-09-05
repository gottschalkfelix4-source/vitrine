"""Regressionen fuer externe URLs, gespeicherte Pfade und Uploadgrenzen."""

from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path
from urllib.parse import quote

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.api import cookies as cookies_api
from app.api import library
from app.api import vpn as vpn_api
from app.config import settings
from app.db import get_db
from app.models import Channel, HotCopy, Job, JobStatus, JobType, Video, VideoStatus, utcnow
from app.services import bundle, cache, cookies, paths, ytdlp
from tests.conftest import neue_sitzung

CHANNEL_ID = "UC" + "a" * 22


@pytest.fixture
def archive(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    settings.ensure_dirs()
    db = neue_sitzung()
    app = FastAPI()
    app.include_router(library.router)
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as client:
        yield client, db
    db.close()


@pytest.mark.parametrize(("value", "canonical"), [
    (CHANNEL_ID, f"https://www.youtube.com/channel/{CHANNEL_ID}"),
    (f"  {CHANNEL_ID}  ", f"https://www.youtube.com/channel/{CHANNEL_ID}"),
    ("@Example-name_1", "https://www.youtube.com/@Example-name_1"),
    ("@München", "https://www.youtube.com/@M%C3%BCnchen"),
    ("@日本語", "https://www.youtube.com/@%E6%97%A5%E6%9C%AC%E8%AA%9E"),
    ("@Mu\u0308nchen", "https://www.youtube.com/@Mu%CC%88nchen"),
    ("@M%C3%BCnchen", "https://www.youtube.com/@M%C3%BCnchen"),
    ("youtube.com/@Example", "https://www.youtube.com/@Example"),
    ("www.youtube.com/@M%C3%BCnchen/videos?si=shared", "https://www.youtube.com/@M%C3%BCnchen"),
    ("M.YOUTUBE.COM/user/Example", "https://www.youtube.com/user/Example"),
    ("music.youtube.com/@Example/releases", "https://www.youtube.com/@Example"),
    ("https://youtube.com/@Example/videos?si=tracking", "https://www.youtube.com/@Example"),
    ("https://m.youtube.com/%40M%C3%BCnchen/shorts/", "https://www.youtube.com/@M%C3%BCnchen"),
    ("https://music.youtube.com/@Example/releases", "https://www.youtube.com/@Example"),
    ("https://www.youtube.com:443/c/Example/playlists", "https://www.youtube.com/c/Example"),
    ("https://www.youtube.com/user/Example/about?feature=shared", "https://www.youtube.com/user/Example"),
    (f"https://www.youtube.com/channel/{CHANNEL_ID}/streams", f"https://www.youtube.com/channel/{CHANNEL_ID}"),
])
def test_channel_addresses_are_rebuilt(value, canonical):
    assert ytdlp.canonical_channel_url(value) == canonical


UNSAFE_URLS = [
    "", "UCtest", "http://www.youtube.com/@Example", "file:///etc/passwd",
    "https://127.0.0.1/@Example", "https://169.254.169.254/latest/meta-data/",
    "https://example.invalid/@Example", "https://youtube.com.example.invalid/@Example",
    "youtube.com.example.invalid/@Example", "youtube.com@evil.invalid/@Example",
    "evil@youtube.com/@Example", "//youtube.com/@Example", "youtube.com:8443/@Example",
    "https://youtube.com@evil.invalid/@Example", "https://evil@youtube.com/@Example",
    "https://www.youtube.com:8443/@Example", "https://www.youtube.com/@Example#",
    "https://www.youtube.com/@Example#__youtubedl_smuggle=%7B%22force_videoid%22%3A%22..%22%7D",
    "https://www.youtube.com/redirect?q=https://127.0.0.1",
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "https://www.youtube.com/playlist?list=PLtest", "https://www.youtube.com/channel/UCtest",
    "https://www.youtube.com/@Example/unknown", "https://www.youtube.com/@Example/videos/extra",
    "https://www.youtube.com//@Example", "https://www.youtube.com/@Example%2Fvideos",
    "https://www.youtube.com/@Example%5C..", "https://www.youtube.com/@Example%255C..",
    "https://www.youtube.com/@Example%0A", "https://www.youtube.com/c/%2e%2e",
    "https://www.youtube.com/@bad%GG", "https://www.youtube.com/@Example\\..",
    "https://www.youtube.com/@Example\n", "https://www.youtube.com/@Example\x00",
    "@Example/../other", "@Example name", "@", "@a" * 1100,
]


@pytest.mark.parametrize("url", UNSAFE_URLS)
def test_unsafe_channel_addresses_never_reach_extraction(url, monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("Eine abgelehnte Adresse hat den Extraktor erreicht")

    monkeypatch.setattr(ytdlp, "_extract", forbidden)
    with pytest.raises(ytdlp.YtdlpError):
        ytdlp.fetch_channel(url)


def test_channel_api_validates_before_calling_extractor(archive, monkeypatch):
    client, db = archive

    def forbidden(*_args):
        pytest.fail("Die API hat eine fremde Adresse weitergegeben")

    monkeypatch.setattr(ytdlp, "fetch_channel", forbidden)
    response = client.post("/api/channels", json={"url": "http://127.0.0.1/private"})
    assert response.status_code == 400
    assert db.scalar(select(func.count(Channel.id))) == 0
    assert db.scalar(select(func.count(Job.id))) == 0


@pytest.mark.parametrize("bad_id", [None, "", ".", "..", "UCtest", "@example", "../other", "C:\\data"])
def test_extractor_identity_is_validated(bad_id, monkeypatch):
    monkeypatch.setattr(ytdlp, "_extract", lambda *_: {"id": bad_id, "title": "Untrusted"})
    with pytest.raises(ytdlp.YtdlpError, match="Kanal-ID"):
        ytdlp.fetch_channel("@Example")


def test_channel_api_rejects_invalid_identity_before_database_write(archive, monkeypatch):
    client, db = archive
    monkeypatch.setattr(ytdlp, "fetch_channel", lambda _: ytdlp.ChannelInfo(
        id="..", name="Unsafe", handle=None, description=None,
        avatar_url=None, banner_url=None, subscriber_count=None,
    ))
    assert client.post("/api/channels", json={"url": "@Example"}).status_code == 400
    assert db.scalar(select(func.count(Channel.id))) == 0
    assert db.scalar(select(func.count(Job.id))) == 0


def test_channel_extraction_uses_the_channel_extractor_only(monkeypatch):
    def extract(url, options):
        assert url == "https://www.youtube.com/@Example"
        # Prueft die wirkliche Auswahl von yt-dlp, ohne einen Abruf zu starten.
        with ytdlp.yt_dlp.YoutubeDL(options) as downloader:
            assert set(downloader._ies) == {"YoutubeTab"}
        return {"channel_id": CHANNEL_ID, "title": "Example"}

    monkeypatch.setattr(ytdlp, "_extract", extract)
    assert ytdlp.fetch_channel("https://m.youtube.com/@Example/videos?si=drop").id == CHANNEL_ID


def _video_with_files(db, channel_id="UClegacy"):
    db.add(Channel(id=channel_id, name="Legacy"))
    directory = settings.bundle_dir / "UClegacy"
    directory.mkdir(exist_ok=True)
    bundle = directory / "video.zip"
    bundle.write_bytes(b"archive")
    thumb = settings.thumb_dir / "video.jpg"
    thumb.write_bytes(b"image")
    hot = settings.cache_dir / "video.h264.mp4"
    hot.write_bytes(b"playback")
    video = Video(id="video", channel_id=channel_id, title="Video", status=VideoStatus.ARCHIVED,
                  bundle_file=str(bundle), thumb_file=thumb.name)
    copy = HotCopy(video_id="video", variant="h264", path=str(hot))
    job = Job(type=JobType.VIDEO_RECODE, target_id="video", status=JobStatus.PENDING)
    db.add_all([video, copy, job])
    db.commit()
    return video, copy, job, [bundle, thumb, hot]


def _assert_intact(db, video, job, files):
    db.expire_all()
    assert db.get(Channel, video.channel_id) is not None
    assert db.get(Video, video.id).status == VideoStatus.ARCHIVED
    assert db.get(Job, job.id).status == JobStatus.PENDING
    assert [file.read_bytes() for file in files] == [b"archive", b"image", b"playback"]


@pytest.mark.parametrize("channel_id", [".", "..", "UClegacy/..", "..\\..", "C:\\data", "UClegacy."])
def test_unsafe_channel_ids_cannot_delete_files_or_database(archive, channel_id):
    _, db = archive
    video, _, job, files = _video_with_files(db, channel_id)
    with pytest.raises(HTTPException) as error:
        library.kanal_entfernen(channel_id, dateien=True, db=db)
    assert error.value.status_code == 409
    _assert_intact(db, video, job, files)


def test_encoded_dot_route_cannot_delete_parent(archive):
    client, db = archive
    video, _, job, files = _video_with_files(db, "..")
    assert client.delete("/api/channels/%2e%2e").status_code == 409
    _assert_intact(db, video, job, files)


@pytest.mark.parametrize("operation", ["channel", "video"])
@pytest.mark.parametrize("field", ["hot", "thumb", "cache_root"])
def test_all_file_references_are_checked_before_any_deletion(archive, tmp_path, operation, field):
    client, db = archive
    video, hot, job, files = _video_with_files(db)
    outside = tmp_path / "outside.jpg"
    outside.write_bytes(b"keep outside")
    if field == "hot":
        hot.path = str(outside)
    elif field == "cache_root":
        hot.path = str(settings.cache_dir)
    else:
        video.thumb_file = "..\\outside.jpg"
    db.commit()

    endpoint = "/api/channels/UClegacy" if operation == "channel" else "/api/videos/video"
    assert client.delete(endpoint).status_code == 409
    assert outside.read_bytes() == b"keep outside"
    _assert_intact(db, video, job, files)


@pytest.mark.parametrize("target", ["sibling", "parent", "root"])
def test_video_bundle_must_be_within_its_own_channel(archive, tmp_path, target):
    client, db = archive
    video, _, job, files = _video_with_files(db)
    outside = settings.bundle_dir / "OtherChannel" / "other.zip"
    outside.parent.mkdir()
    outside.write_bytes(b"other archive")
    video.bundle_file = str({
        "sibling": outside,
        "parent": settings.db_path,
        "root": settings.bundle_dir / "UClegacy",
    }[target])
    db.commit()

    assert client.delete("/api/videos/video").status_code == 409
    assert outside.read_bytes() == b"other archive"
    _assert_intact(db, video, job, files)


@pytest.mark.parametrize("field", ["avatar_file", "banner_file"])
def test_channel_image_references_cannot_escape(archive, field):
    client, db = archive
    video, _, job, files = _video_with_files(db)
    setattr(db.get(Channel, video.channel_id), field, "../cookies.txt")
    db.commit()
    assert client.delete("/api/channels/UClegacy").status_code == 409
    _assert_intact(db, video, job, files)


def _symlink(link: Path, target: Path):
    try:
        link.symlink_to(target, target_is_directory=target.is_dir())
    except OSError as error:
        if sys.platform == "win32" and target.is_dir():
            # Verzeichnis-Junctions brauchen unter Windows keine erhoehten
            # Rechte und muessen dieselbe Schutzgrenze einhalten.
            from _winapi import CreateJunction

            CreateJunction(str(target), str(link))
        else:
            pytest.skip(f"Symlinks stehen hier nicht zur Verfuegung: {error}")


def test_channel_symlink_is_rejected_before_database_changes(archive, tmp_path):
    client, db = archive
    video, _, job, files = _video_with_files(db, "UClink")
    outside = tmp_path / "outside"
    outside.mkdir()
    protected = outside / "private.txt"
    protected.write_bytes(b"private")
    _symlink(settings.bundle_dir / "UClink", outside)

    assert client.delete("/api/channels/UClink").status_code == 409
    assert protected.read_bytes() == b"private"
    _assert_intact(db, video, job, files)


@pytest.mark.parametrize("operation", ["channel", "video"])
def test_symlink_inside_channel_is_rejected_before_database_changes(archive, tmp_path, operation):
    client, db = archive
    video, _, job, files = _video_with_files(db)
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    outside = outside_dir / "private.zip"
    outside.write_bytes(b"private")
    link = settings.bundle_dir / "UClegacy" / "linked"
    _symlink(link, outside_dir)
    video.bundle_file = str(link / "private.zip")
    db.commit()

    endpoint = "/api/channels/UClegacy" if operation == "channel" else "/api/videos/video"
    assert client.delete(endpoint).status_code == 409
    assert outside.read_bytes() == b"private"
    _assert_intact(db, video, job, files)


@pytest.mark.parametrize("name", ["..\\cookies.txt", "C:\\private.jpg", "\\\\server\\share\\private.jpg",
                                 "private.txt", "active.svg", "file.jpg:stream", "CON.jpg"])
def test_thumbnail_rejects_windows_paths_and_nonimages(archive, name):
    client, _ = archive
    settings.db_path.write_bytes(b"private")
    (settings.thumb_dir / "private.txt").write_bytes(b"private")
    response = client.get("/api/thumbs/" + quote(name, safe=""))
    assert response.status_code == 404
    assert b"private" not in response.content


def test_thumbnail_cannot_serve_a_symlink(archive, tmp_path):
    client, _ = archive
    outside = tmp_path / "secret.txt"
    outside.write_bytes(b"secret")
    _symlink(settings.thumb_dir / "preview.jpg", outside)
    response = client.get("/api/thumbs/preview.jpg")
    assert response.status_code == 404
    assert b"secret" not in response.content


def test_safe_legacy_channel_deletion_still_works(archive):
    client, db = archive
    _, _, _, files = _video_with_files(db)
    assert client.delete("/api/channels/UClegacy").status_code == 200
    assert db.get(Channel, "UClegacy") is None
    assert not any(file.exists() for file in files)


@pytest.mark.parametrize("name", [".", "..", "../escape", "..\\escape", "C:relative", "C:\\absolute"])
def test_unsafe_components_fail_on_every_platform(tmp_path, name):
    with pytest.raises(paths.UnsafePath):
        paths.child(tmp_path, name)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["cookie", "vpn"])
async def test_oversized_upload_is_read_with_a_bound(archive, kind):
    _, db = archive
    maximum = cookies.MAX_BYTES if kind == "cookie" else vpn_api.MAX_BYTES
    old_cookies = settings.data_dir / "cookies.txt"
    old_cookies.write_bytes(b"existing session")
    read_sizes = []

    class Upload:
        async def read(self, size=-1):
            read_sizes.append(size)
            assert size == maximum + 1, "Der Upload wurde unbegrenzt gelesen"
            return b"x" * size

    with pytest.raises(HTTPException) as error:
        if kind == "cookie":
            await cookies_api.hochladen(Upload())
        else:
            await vpn_api.hochladen(Upload(), name="test", db=db)
    assert error.value.status_code == 422
    assert read_sizes == [maximum + 1]
    assert old_cookies.read_bytes() == b"existing session"


@pytest.mark.parametrize("operation", ["drop", "reap"])
@pytest.mark.parametrize("target", ["outside", "root", "windows", "junction"])
def test_cache_cleanup_rejects_unsafe_stored_paths(archive, tmp_path, operation, target):
    _, db = archive
    video, hot, job, files = _video_with_files(db)
    outside_dir = tmp_path / "outside-cache"
    outside_dir.mkdir()
    outside = outside_dir / "private.mp4"
    outside.write_bytes(b"keep")
    partial = outside.with_suffix(".mp4.part")
    partial.write_bytes(b"keep partial")
    if target == "junction":
        link = settings.cache_dir / "linked"
        _symlink(link, outside_dir)
        hot.path = str(link / outside.name)
    else:
        hot.path = {"outside": str(outside), "root": str(settings.cache_dir),
                    "windows": "C:\\untrusted\\private.mp4"}[target]
    hot.expires_at = utcnow() - timedelta(hours=2)
    hot.last_access_at = utcnow() - timedelta(hours=3)
    db.commit()
    if operation == "drop":
        assert cache.drop(db, hot) == 0
    else:
        stats = cache.reap(db)
        assert stats["abgelaufen"] == 0
        assert stats["bytes_frei"] == 0
    assert db.get(HotCopy, hot.id) is not None
    assert outside.read_bytes() == b"keep"
    assert partial.read_bytes() == b"keep partial"
    _assert_intact(db, video, job, files)


@pytest.mark.parametrize("operation", ["drop", "reap"])
def test_cache_cleanup_validates_partial_before_deleting_primary(archive, tmp_path, operation):
    _, db = archive
    video, hot, job, files = _video_with_files(db)
    outside = tmp_path / "keep-part"
    outside.mkdir()
    marker = outside / "marker"
    marker.write_bytes(b"keep")
    _symlink(Path(hot.path + ".part"), outside)
    hot.expires_at = utcnow() - timedelta(hours=2)
    hot.last_access_at = utcnow() - timedelta(hours=3)
    db.commit()
    if operation == "drop":
        assert cache.drop(db, hot) == 0
    else:
        assert cache.reap(db)["bytes_frei"] == 0
    assert db.get(HotCopy, hot.id) is not None
    assert marker.read_bytes() == b"keep"
    _assert_intact(db, video, job, files)


def test_reaper_checks_orphans_before_touching_them(archive, monkeypatch):
    import os

    _, db = archive
    orphan = settings.cache_dir / "orphan.mp4"
    orphan.write_bytes(b"keep")
    old = (utcnow() - timedelta(days=1)).timestamp()
    os.utime(orphan, (old, old))
    checked = []
    real_contained = paths.contained

    def reject_orphan(root, target):
        if target == orphan:
            checked.append(target)
            raise paths.UnsafePath("Verknuepfung")
        return real_contained(root, target)

    monkeypatch.setattr(paths, "contained", reject_orphan)
    assert cache.reap(db)["verwaist"] == 0
    assert checked == [orphan]
    assert orphan.read_bytes() == b"keep"


@pytest.mark.parametrize("bad_id", [".", "..", "../other", "..\\other", "C:\\other", "CON"])
@pytest.mark.parametrize("field", ["channel", "video", "variant"])
def test_derived_archive_and_cache_paths_reject_unsafe_ids(archive, bad_id, field):
    if field == "channel":
        with pytest.raises(paths.UnsafePath):
            bundle.bundle_path_for(settings.bundle_dir, bad_id, "safe")
    elif field == "video":
        with pytest.raises(paths.UnsafePath):
            bundle.bundle_path_for(settings.bundle_dir, "UClegacy", bad_id)
        with pytest.raises(paths.UnsafePath):
            cache.hot_path_for(bad_id, "source", ".mp4")
    else:
        with pytest.raises(paths.UnsafePath):
            cache.hot_path_for("safe", bad_id, ".mp4")


@pytest.mark.parametrize("target", ["outside", "root", "channel-junction", "partial-junction"])
def test_bundle_writer_validates_all_targets_before_writing(archive, tmp_path, target):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"media")
    outside = tmp_path / "outside-bundles"
    outside.mkdir()
    marker = outside / "keep.txt"
    marker.write_bytes(b"keep")
    channel_dir = settings.bundle_dir / "UClegacy"
    if target == "channel-junction":
        _symlink(channel_dir, outside)
    else:
        channel_dir.mkdir()
    dest = {"outside": outside / "video.zip", "root": settings.bundle_dir}.get(
        target, channel_dir / "video.zip"
    )
    if target == "partial-junction":
        _symlink(dest.with_suffix(".zip.part"), outside)
    manifest = bundle.BundleManifest(schema_version=1, video_id="video", channel_id="UClegacy",
                                     title="Video", media_name="", media_bytes=0, mime_type="")
    with pytest.raises(paths.UnsafePath):
        bundle.write_bundle(dest, root=settings.bundle_dir, manifest=manifest, media_file=source)
    assert marker.read_bytes() == b"keep"
    assert not (outside / "video.zip").exists()
    assert not (outside / "video.zip.part").exists()
    assert not (channel_dir / "video.zip").exists()


@pytest.mark.parametrize("target", ["outside", "sibling", "root", "junction", "partial-junction"])
def test_recoding_checks_stored_bundle_before_clearing_work_or_writing(archive, tmp_path, target):
    from app.workers.archive import recodieren

    _, db = archive
    video, _, job, files = _video_with_files(db)
    outside = tmp_path / "outside-recode"
    outside.mkdir()
    marker = outside / "keep.zip"
    marker.write_bytes(b"keep")
    channel = settings.bundle_dir / "UClegacy"
    if target == "junction":
        link = channel / "linked"
        _symlink(link, outside)
        video.bundle_file = str(link / marker.name)
    elif target == "partial-junction":
        _symlink(Path(video.bundle_file + ".part"), outside)
    else:
        video.bundle_file = str({"outside": marker, "sibling": settings.bundle_dir / "other.zip",
                                 "root": channel}[target])
    db.commit()
    work = settings.tmp_dir / "video.recode"
    work.mkdir()
    work_marker = work / "keep"
    work_marker.write_bytes(b"keep work")
    with pytest.raises(paths.UnsafePath):
        recodieren(db, job)
    assert work_marker.read_bytes() == b"keep work"
    assert marker.read_bytes() == b"keep"
    _assert_intact(db, video, job, files)


@pytest.mark.parametrize("worker", ["archivieren", "recodieren", "hochstufen"])
def test_background_workers_reject_unsafe_video_ids_before_work(archive, tmp_path, worker):
    from app.workers import archive as archive_worker

    _, db = archive
    db.add(Channel(id="UClegacy", name="Legacy"))
    video = Video(id="../outside", channel_id="UClegacy", title="Unsafe",
                  status=VideoStatus.ARCHIVED, bundle_file=str(tmp_path / "old.zip"))
    job = Job(type=JobType.VIDEO_RECODE, target_id=video.id, status=JobStatus.PENDING)
    db.add_all([video, job])
    db.commit()
    with pytest.raises(paths.UnsafePath):
        getattr(archive_worker, worker)(db, job)
    assert video.status == VideoStatus.ARCHIVED
    assert job.status == JobStatus.PENDING


def test_safe_legacy_ids_still_generate_and_write_bundles(archive, tmp_path):
    dest = bundle.bundle_path_for(settings.bundle_dir, "UCtest", "vid1")
    assert dest == settings.bundle_dir / "UCtest" / "vid1.zip"
    source = tmp_path / "source.mp4"
    source.write_bytes(b"media")
    manifest = bundle.BundleManifest(schema_version=1, video_id="vid1", channel_id="UCtest",
                                     title="Video", media_name="", media_bytes=0, mime_type="")
    assert bundle.write_bundle(dest, root=settings.bundle_dir, manifest=manifest, media_file=source) == dest
    with bundle.BundleReader(dest) as reader:
        assert b"".join(reader.media_range()) == b"media"
    assert cache.hot_path_for("vid1", "source", ".mp4") == settings.cache_dir / "vid1.source.mp4"
