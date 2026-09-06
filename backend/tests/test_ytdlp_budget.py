"""Offline regression tests for the network boundary and friendly job pauses."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from io import BytesIO
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.error import HTTPError
from urllib.request import Request

import pytest
from yt_dlp.extractor.common import InfoExtractor

from app.services import abbruch, anfragelimit, ausgang, drosselung, jobs, ytdlp


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    abbruch.zuruecksetzen()
    monkeypatch.setattr(anfragelimit, "vor_anfrage", Mock())
    monkeypatch.setattr(anfragelimit, "vor_video", Mock())
    monkeypatch.setattr(anfragelimit, "wartezeit", Mock(return_value=0))
    monkeypatch.setattr(anfragelimit, "abweisung", Mock())
    monkeypatch.setattr(drosselung, "wartezeit", Mock(return_value=0))
    monkeypatch.setattr(drosselung, "melden", Mock())


def fake_factory(monkeypatch, network, extract=None):
    class FakeYDL:
        def __init__(self, opts):
            self.opts = opts
            self.urlopen = network

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def extract_info(self, url, download=False):
            return extract(self, url, download) if extract else {"id": "fixture"}

        def sanitize_info(self, info):
            return info

    monkeypatch.setattr(ytdlp.yt_dlp, "YoutubeDL", FakeYDL)
    return FakeYDL


def test_fragment_threads_keep_captured_egress_and_each_request_is_counted(monkeypatch):
    network = Mock(return_value=BytesIO(b"fragment"))
    fake_factory(monkeypatch, network)
    tunnel = ausgang.Ausgang(id="tunnel-1", proxy="socks5h://127.0.0.1:9000")
    urls = ["https://www.youtube.com/youtubei/v1/player", "https://r1.googlevideo.com/videoplayback"]
    with ausgang.benutzen(tunnel), ytdlp._youtube_dl({}) as instance, ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(instance.urlopen, Request(url)) for url in urls]
        for future in futures:
            future.result()
    assert network.call_count == 2
    assert {call.args[0] for call in anfragelimit.vor_anfrage.call_args_list} == set(urls)
    assert {call.args[0] for call in drosselung.wartezeit.call_args_list} == {"tunnel-1"}


def test_paused_egress_blocks_fragment_before_any_request(monkeypatch):
    network = Mock()
    fake_factory(monkeypatch, network)
    drosselung.wartezeit.return_value = 3600
    with (
        pytest.raises(anfragelimit.Pause) as caught,
        ytdlp._youtube_dl({}, ausgang_id="tunnel-7") as instance,
        ThreadPoolExecutor(max_workers=1) as pool,
    ):
        pool.submit(instance.urlopen, "https://r1.googlevideo.com/videoplayback").result()
    assert caught.value.rest_s == 3600
    network.assert_not_called()
    anfragelimit.vor_anfrage.assert_not_called()
    drosselung.melden.assert_not_called()
    anfragelimit.abweisung.assert_not_called()


def test_vpn_connectivity_probe_works_during_youtube_pause(monkeypatch):
    network = Mock(return_value=BytesIO(b"192.0.2.1"))
    fake_factory(monkeypatch, network)
    drosselung.wartezeit.return_value = 3600
    with ytdlp._youtube_dl({}, ausgang_id="tunnel-7") as instance:
        instance.urlopen("https://api.ipify.org")
    network.assert_called_once()
    drosselung.wartezeit.assert_not_called()
    anfragelimit.vor_anfrage.assert_not_called()


def test_429_aborts_before_extractor_can_retry_and_reports_once(monkeypatch):
    url = "https://www.youtube.com/youtubei/v1/player"
    network = Mock(side_effect=HTTPError(url, 429, "Too Many Requests", {}, None))

    def extract(instance, *_):
        for _ in range(4):
            try:
                instance.urlopen(url)
            except HTTPError:
                continue

    fake_factory(monkeypatch, network, extract)
    with pytest.raises(anfragelimit.Pause):
        ytdlp._extract(url, {"ignoreerrors": True})
    assert network.call_count == 1
    drosselung.melden.assert_called_once()
    anfragelimit.abweisung.assert_called_once()


def test_429_honors_server_retry_after(monkeypatch):
    url = "https://www.youtube.com/youtubei/v1/player"
    network = Mock(side_effect=HTTPError(url, 429, "Too Many Requests", {"Retry-After": "7200"}, None))
    fake_factory(monkeypatch, network)
    with pytest.raises(anfragelimit.Pause), ytdlp._youtube_dl({}) as instance:
        instance.urlopen(url)
    anfragelimit.abweisung.assert_called_once_with(retry_after_s=7200)


def test_retry_after_http_date_and_bad_headers():
    date = datetime.now(UTC) + timedelta(hours=2)
    response = SimpleNamespace(response=SimpleNamespace(headers={"Retry-After": format_datetime(date, usegmt=True)}))
    assert 7198 < ytdlp._retry_after(response) <= 7200
    for value in ("broken", "-3", "nan", "infinity"):
        assert ytdlp._retry_after(SimpleNamespace(headers={"Retry-After": value})) is None


def test_generic_403_is_not_a_global_rejection(monkeypatch):
    url = "https://r1.googlevideo.com/videoplayback"
    error = HTTPError(url, 403, "Forbidden", {}, None)
    network = Mock(side_effect=error)
    fake_factory(monkeypatch, network)
    with pytest.raises(HTTPError) as caught, ytdlp._youtube_dl({}) as instance:
        instance.urlopen(url)
    assert caught.value is error
    drosselung.melden.assert_not_called()
    anfragelimit.abweisung.assert_not_called()


def test_real_ytdlp_ignoreerrors_cannot_swallow_original_pause(monkeypatch):
    original_class = ytdlp.yt_dlp.YoutubeDL
    original_pause = anfragelimit.Pause(37, "Testbudget")
    anfragelimit.vor_anfrage.side_effect = original_pause
    network = Mock()

    class OfflineIE(InfoExtractor):
        _VALID_URL = r"offline:(?P<id>[^/]+)"
        IE_NAME = "offline"

        def _real_extract(self, _url):
            self._downloader.urlopen("https://www.youtube.com/youtubei/v1/player")
            return {"id": "fixture", "title": "fixture", "url": "https://r1.googlevideo.com/videoplayback"}

    def factory(opts):
        instance = original_class(opts, auto_init=False)
        instance.add_info_extractor(OfflineIE())
        instance.urlopen = network
        return instance

    monkeypatch.setattr(ytdlp.yt_dlp, "YoutubeDL", factory)
    with pytest.raises(anfragelimit.Pause) as caught:
        ytdlp._extract("offline:fixture", {"ignoreerrors": True})
    assert caught.value is original_pause
    network.assert_not_called()


@pytest.mark.parametrize("message", [
    "This content isn't available, try again later",
    "This content isn’t available, try again later",
    "Sign in to confirm you're not a bot",
])
def test_logger_stops_playlist_at_first_true_rejection(monkeypatch, message):
    subsequent = Mock()

    def extract(instance, *_):
        instance.opts["logger"].error(message)
        subsequent()
        return {"entries": []}

    fake_factory(monkeypatch, Mock(), extract)
    with pytest.raises(ytdlp.Gedrosselt):
        ytdlp.list_channel_playlists("https://www.youtube.com/@fixture")
    subsequent.assert_not_called()
    anfragelimit.abweisung.assert_called_once()
    drosselung.melden.assert_called_once()


def test_rss_respects_budget_and_preserves_original_pause(monkeypatch):
    import urllib.request
    pause = anfragelimit.Pause(123)
    anfragelimit.vor_anfrage.side_effect = pause
    network = Mock()
    monkeypatch.setattr(urllib.request, "urlopen", network)
    with pytest.raises(anfragelimit.Pause) as caught:
        ytdlp.peek_recent("UCfixture")
    assert caught.value is pause
    network.assert_not_called()


def test_download_reserves_one_start_before_extracting(monkeypatch, tmp_path):
    pause = anfragelimit.Pause(123)
    anfragelimit.vor_video.side_effect = pause
    extract = Mock()
    fake_factory(monkeypatch, Mock(), extract)
    with pytest.raises(anfragelimit.Pause) as caught:
        ytdlp.download_video("fixture", tmp_path)
    assert caught.value is pause
    anfragelimit.vor_video.assert_called_once()
    extract.assert_not_called()


@pytest.mark.parametrize("channel_playlists", [False, True])
def test_long_pagination_waits_in_same_extractor_without_repeating_pages(monkeypatch, channel_playlists):
    waiting = Event()
    released = Event()
    network = Mock(return_value=BytesIO(b"page"))
    extract_instances = []
    requests = []

    def gate(url, *, budget_abwarten=False):
        requests.append(url)
        assert budget_abwarten is True
        if len(requests) == 2:
            waiting.set()
            assert released.wait(5), "Test budget was not released"

    def extract(instance, *_):
        extract_instances.append(id(instance))
        entries = []
        for page in range(3):
            instance.urlopen(f"https://www.youtube.com/youtubei/v1/browse?page={page}")
            entries.append({"id": f"PLfixture{page}" if channel_playlists else f"fixture{page}", "title": f"Page {page}"})
        return {"entries": entries}

    anfragelimit.vor_anfrage.side_effect = gate
    fake_factory(monkeypatch, network, extract)
    listing = ytdlp.list_channel_playlists if channel_playlists else ytdlp.list_entries
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(listing, "https://www.youtube.com/@fixture")
        try:
            assert waiting.wait(5)
            assert not future.done()
            assert network.call_count == 1
        finally:
            released.set()
        result = future.result(timeout=5)
    assert len(extract_instances) == 1
    assert len(result) == 3
    assert requests == [f"https://www.youtube.com/youtubei/v1/browse?page={page}" for page in range(3)]
    assert [call.args[0] for call in network.call_args_list] == requests


@pytest.mark.parametrize("channel", [False, True])
def test_interactive_metadata_still_fails_fast_on_own_quota(monkeypatch, channel):
    pause = anfragelimit.Pause(123)
    network = Mock()

    def gate(_url, *, budget_abwarten=False):
        assert budget_abwarten is False
        raise pause

    def extract(instance, *_):
        instance.urlopen("https://www.youtube.com/youtubei/v1/player")

    anfragelimit.vor_anfrage.side_effect = gate
    fake_factory(monkeypatch, network, extract)
    with pytest.raises(anfragelimit.Pause) as caught:
        (ytdlp.fetch_channel if channel else ytdlp.fetch_video_info)("@fixture" if channel else "fixture")
    assert caught.value is pause
    network.assert_not_called()


@pytest.mark.parametrize("pause", [anfragelimit.Pause(3600, "YouTube-Schutzpause"), abbruch.Abgebrochen("shutdown")])
def test_paginated_budget_wait_still_propagates_shutdown_and_real_block(monkeypatch, pause):
    network = Mock()

    def gate(_url, *, budget_abwarten=False):
        assert budget_abwarten is True
        raise pause

    def extract(instance, *_):
        instance.urlopen("https://www.youtube.com/youtubei/v1/browse")

    anfragelimit.vor_anfrage.side_effect = gate
    fake_factory(monkeypatch, network, extract)
    with pytest.raises(type(pause)) as caught:
        ytdlp.list_entries("https://www.youtube.com/playlist?list=PLfixture")
    assert caught.value is pause
    network.assert_not_called()
    anfragelimit.abweisung.assert_not_called()


def test_paginated_extractor_stops_immediately_on_429_even_when_budget_waiting(monkeypatch):
    url = "https://www.youtube.com/youtubei/v1/browse"
    network = Mock(side_effect=HTTPError(url, 429, "Too Many Requests", {}, None))

    def extract(instance, *_):
        for _ in range(3):
            instance.urlopen(url)

    fake_factory(monkeypatch, network, extract)
    with pytest.raises(anfragelimit.Pause):
        ytdlp.list_entries("https://www.youtube.com/playlist?list=PLfixture")
    anfragelimit.vor_anfrage.assert_called_once_with(url, budget_abwarten=True)
    network.assert_called_once()
    anfragelimit.abweisung.assert_called_once()


def test_archiving_budget_pause_keeps_partial_download_without_failed_attempt(monkeypatch, tmp_path):
    from app.models import Channel, JobStatus, JobType, Video, VideoStatus
    from app.workers import archive
    from tests.conftest import neue_sitzung

    monkeypatch.setattr(ytdlp.settings, "data_dir", tmp_path)
    ytdlp.settings.ensure_dirs()
    db = neue_sitzung()
    db.add(Channel(id="UCfixture", name="Fixture"))
    db.flush()
    db.add(Video(id="fixture", channel_id="UCfixture", title="Fixture", status=VideoStatus.QUEUED, retry_count=2))
    db.commit()

    def download(_video_id, folder, **_kwargs):
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "fixture.mp4.part").write_bytes(b"partial")
        raise anfragelimit.Pause(123)

    monkeypatch.setattr(ytdlp, "download_video", download)
    jobs.enqueue_archive(db, "fixture")
    job = jobs.claim_next(db, [JobType.VIDEO_ARCHIVE])
    archive.archivieren(db, job)
    assert job.status == JobStatus.PENDING
    assert job.error is None
    assert db.get(Video, "fixture").retry_count == 2
    assert db.get(Video, "fixture").status == VideoStatus.QUEUED
    assert (tmp_path / "tmp" / "fixture" / "fixture.mp4.part").read_bytes() == b"partial"
    drosselung.melden.assert_not_called()
    anfragelimit.abweisung.assert_not_called()
    db.close()


@pytest.mark.parametrize("group_name,request_pause,video_pause,expected", [
    ("netz", 300, 300, None),
    ("netz", 0, 300, {"channel_sync", "playlist_sync"}),
    ("recodierung", 300, 300, {"video_recode"}),
    ("vorbereitung", 300, 300, {"video_prepare"}),
])
def test_runner_gates_only_the_affected_job_types(monkeypatch, group_name, request_pause, video_pause, expected):
    from app.workers import runner

    @contextmanager
    def scope():
        yield SimpleNamespace(rollback=Mock())

    group = next(item for item in runner._gruppen() if item.name == group_name)
    werk = runner.Arbeiterwerk()
    werk._soll[group_name] = 1
    monkeypatch.setattr(werk._stop, "wait", lambda _timeout: werk._stop.set())
    monkeypatch.setattr(runner, "session_scope", scope)
    claim = Mock(return_value=None)
    monkeypatch.setattr(runner.jobs, "claim_next", claim)
    monkeypatch.setattr(runner.vpn, "waehlen", lambda: ausgang.DIREKTER_AUSGANG)
    anfragelimit.wartezeit.side_effect = lambda *, nur_anfragen=False: request_pause if nur_anfragen else video_pause
    werk._arbeiten(group, 0)
    if expected is None:
        claim.assert_not_called()
    else:
        assert set(claim.call_args.args[1]) == expected
    if group_name != "netz":
        anfragelimit.wartezeit.assert_not_called()


@pytest.mark.parametrize("playlist", [False, True])
def test_sync_pause_requeues_without_stamping_success_or_failure(monkeypatch, playlist):
    from app.models import Channel, Job, JobStatus, JobType, Playlist
    from app.workers import sync
    from tests.conftest import neue_sitzung

    db = neue_sitzung()
    channel = Channel(id="UCfixture", name="Fixture")
    db.add(channel)
    db.flush()
    db.add(Playlist(id="PLfixture", channel_id=channel.id, title="Fixture"))
    job = Job(type=JobType.PLAYLIST_SYNC if playlist else JobType.CHANNEL_SYNC,
              target_id="PLfixture" if playlist else channel.id, status=JobStatus.RUNNING)
    db.add(job)
    db.commit()
    pause = anfragelimit.Pause(123)
    monkeypatch.setattr(ytdlp, "peek_recent", Mock(side_effect=pause))
    monkeypatch.setattr(ytdlp, "list_entries", Mock(side_effect=pause))
    (sync.playlist_abgleichen if playlist else sync.kanal_abgleichen)(db, job)
    assert job.status == JobStatus.PENDING
    assert job.error is None
    assert channel.last_synced_at is None
    drosselung.melden.assert_not_called()
    anfragelimit.abweisung.assert_not_called()
    db.close()


def test_upgrade_preflight_pause_keeps_existing_archive(monkeypatch, tmp_path):
    from app.models import Channel, Job, JobStatus, JobType, Video, VideoStatus
    from app.workers import archive
    from tests.conftest import neue_sitzung

    monkeypatch.setattr(ytdlp.settings, "data_dir", tmp_path)
    db = neue_sitzung()
    db.add(Channel(id="UCfixture", name="Fixture"))
    db.flush()
    video = Video(id="fixture", channel_id="UCfixture", title="Fixture", status=VideoStatus.ARCHIVED,
                  bundle_file=str(tmp_path / "bundles" / "UCfixture" / "fixture.zip"), retry_count=1)
    job = Job(type=JobType.VIDEO_UPGRADE, target_id="fixture", status=JobStatus.RUNNING)
    db.add_all([video, job])
    db.commit()
    monkeypatch.setattr(ytdlp, "fetch_video_info", Mock(side_effect=anfragelimit.Pause(123)))
    archive.hochstufen(db, job)
    assert job.status == JobStatus.PENDING
    assert job.error is None
    assert video.status == VideoStatus.ARCHIVED and video.retry_count == 1
    drosselung.melden.assert_not_called()
    db.close()


def test_channel_creation_returns_retry_after_without_server_error(monkeypatch):
    from fastapi import HTTPException

    from app.api import library

    monkeypatch.setattr(ytdlp, "fetch_channel", Mock(side_effect=anfragelimit.Pause(123)))
    with pytest.raises(HTTPException) as caught:
        library.kanal_anlegen(library.KanalAnlegen(url="@fixture"), SimpleNamespace())
    assert caught.value.status_code == 429
    assert caught.value.headers == {"Retry-After": "123"}
    assert "YouTube" in caught.value.detail


def test_cookie_probe_reports_request_pause_without_spending_video_budget(monkeypatch):
    from app.api import cookies

    anfragelimit.wartezeit.side_effect = lambda *, nur_anfragen=False: 60 if nur_anfragen else 120
    fetch = Mock()
    monkeypatch.setattr(ytdlp, "fetch_video_info", fetch)
    result = cookies.probelauf(SimpleNamespace())
    assert result["pausiert"] is True and result["erfolg"] is False
    fetch.assert_not_called()
    drosselung.melden.assert_not_called()
