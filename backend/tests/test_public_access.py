"""Gastansicht und automatischer HTTP/HTTPS-Zugang durch die echte Middleware."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import main
from app.config import settings
from app.models import Channel, Job, JobStatus, JobType, Playlist, PlaylistItem, Video, VideoStatus
from app.services import auth, suche
from tests.test_auth import NEW_PASSWORD, PASSWORD, raw_request, setup
from tests.test_auth import environment as environment


@pytest.fixture
def archive(environment):
    client, factory, _ = environment
    settings.ensure_dirs()
    with factory() as db:
        db.add_all([Channel(id="public", name="Archiv"), Channel(id="private", name="Nur vorgemerkt")])
        db.flush()
        for video_id, channel, state in [
            ("archived001", "public", VideoStatus.ARCHIVED),
            ("private0001", "public", VideoStatus.QUEUED),
            ("private0002", "private", VideoStatus.NEW),
        ]:
            db.add(Video(id=video_id, channel_id=channel, title=f"Suchwort {video_id}", status=state,
                         watched=True, progress_s=45, duration_s=100, thumb_file=f"{video_id}.jpg",
                         status_message="/private/secret/file", bundle_file="/private/archive.zip"))
            (settings.thumb_dir / f"{video_id}.jpg").write_bytes(b"image")
        db.add_all([Playlist(id="mixed", channel_id="public", title="Reihe", item_count=2),
                    Playlist(id="hidden", channel_id="private", title="Geheim", item_count=1)])
        db.flush()
        db.add_all([PlaylistItem(playlist_id="mixed", video_id="archived001", position=0),
                    PlaylistItem(playlist_id="mixed", video_id="private0001", position=1),
                    PlaylistItem(playlist_id="hidden", video_id="private0002", position=0)])
        db.add(Job(type=JobType.VIDEO_ARCHIVE, target_id="private0001", status=JobStatus.RUNNING,
                   message="/private/job/path", error="cookie=private-value"))
        db.commit()
        suche.schema_anlegen(db)
        for video in db.scalars(select(Video)):
            suche.video_indizieren(db, video_id=video.id, titel=video.title, beschreibung="Suchwort", kanal="Archiv")
            suche.untertitel_indizieren(db, video.id, "de", "WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nSuchwort\n")
        db.commit()
    return client, factory


def test_guests_only_see_archived_channels_videos_and_playlist_entries(archive):
    client, _ = archive
    channels = client.get("/api/channels").json()
    assert [row["id"] for row in channels] == ["public"]
    assert channels[0]["videos_gesamt"] == channels[0]["videos_archiviert"] == 1
    channel = client.get("/api/channels/public").json()
    assert channel["zaehler"]["videos"] == 1
    assert "regeln" not in channel and channel["sammlungen"][0]["anzahl"] == 1
    playlist = client.get("/api/playlists/mixed").json()
    assert playlist["anzahl_quelle"] == playlist["anzahl_archiviert"] == 1
    assert [row["video"]["id"] for row in playlist["positionen"]] == ["archived001"]
    for path in ["/api/channels/private", "/api/playlists/hidden", "/api/videos/private0001"]:
        assert client.get(path).status_code == 404
    for suffix in ["", "?nur_archiviert=false", "?status=archived"]:
        assert [v["id"] for v in client.get("/api/videos" + suffix).json()] == ["archived001"]
    assert client.get("/api/videos?status=queued&nur_archiviert=false").json() == []
    detail = client.get("/api/videos/archived001").json()
    assert detail["statusmeldung"] is None
    assert detail["video"]["gesehen"] is False
    assert detail["video"]["fortschritt_s"] == 0 and detail["video"]["fortschritt_anteil"] is None


def test_public_search_filters_metadata_and_subtitle_hits(archive):
    client, _ = archive
    result = client.get("/api/search?q=Suchwort").json()
    assert [v["id"] for v in result["videos"]] == ["archived001"]
    assert [hit["video"]["id"] for hit in result["im_gesprochenen"]] == ["archived001"]
    assert result["videos"][0]["fortschritt_s"] == 0
    assert [v["id"] for v in client.get("/api/videos?suche=Suchwort&nur_archiviert=false").json()] == ["archived001"]
    # Nicht archivierte Treffer duerfen auch bei kleinem Limit keine sichtbaren
    # Archivtreffer aus der Ergebnismenge draengen.
    limited = client.get("/api/search?q=Suchwort&limit=1").json()
    assert [v["id"] for v in limited["videos"]] == ["archived001"]
    assert [hit["video"]["id"] for hit in limited["im_gesprochenen"]] == ["archived001"]


def test_guest_media_only_uses_existing_archive_and_never_fetches_source(archive, monkeypatch):
    client, _ = archive

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Gast hat externen Abruf oder Buendelzugriff ausgeloest")

    monkeypatch.setattr("urllib.request.urlopen", forbidden)
    monkeypatch.setattr("app.services.bundle.BundleReader", forbidden)
    assert client.get("/api/thumbs/archived001.jpg").status_code == 200
    for path in ["/api/thumbs/private0001.jpg", "/api/thumbs/quelle/private0001",
                 "/api/thumbs/quelle/archived001", "/api/videos/private0001/subtitles/de"]:
        assert client.get(path).status_code == 404
    (settings.thumb_dir / "orphan.jpg").write_bytes(b"not public")
    assert client.get("/api/thumbs/orphan.jpg").status_code == 404


def test_public_queue_storage_are_sanitized_but_admin_keeps_management_view(archive, monkeypatch):
    client, _ = archive
    monkeypatch.setattr("app.services.vpn.ausgang_ids", list)
    queue = client.get("/api/jobs").json()
    assert queue[0]["meldung"] is None and queue[0]["fehler"] is None
    active = client.get("/api/jobs/aktiv").json()
    assert active["laufend"][0]["meldung"] is None
    assert "ausgaenge" not in active and "drosselung" not in active
    storage = client.get("/api/storage")
    assert storage.status_code == 200
    assert str(settings.data_dir) not in storage.text
    assert all(row["pfad"] in {"Archiv", "Wiedergabe", "Daten und Videos"} for row in storage.json()["traeger"])
    setup(client)
    assert len(client.get("/api/channels").json()) == 2
    assert len(client.get("/api/playlists/mixed").json()["positionen"]) == 2
    assert client.get("/api/videos/private0001").status_code == 200
    assert client.get("/api/videos/archived001").json()["video"]["fortschritt_s"] == 45
    assert client.get("/api/jobs").json()[0]["fehler"] == "cookie=private-value"


@pytest.mark.parametrize(("method", "path"), [
    ("GET", "/api/settings"), ("GET", "/api/cookies"), ("GET", "/api/vpn"),
    ("GET", "/api/hardware"), ("GET", "/api/streams"), ("POST", "/api/channels"),
    ("POST", "/api/channels/public/sync"), ("DELETE", "/api/channels/public"),
    ("DELETE", "/api/videos/archived001"), ("PUT", "/api/videos/archived001/progress"),
    ("POST", "/api/jobs/pause"), ("POST", "/api/search/reindex"),
])
def test_guest_cannot_manage_or_overwrite_admin_progress(archive, method, path):
    client, factory = archive
    result = client.request(method, path, content=b"invalid body", follow_redirects=False)
    assert result.status_code == 401
    with factory() as db:
        assert db.get(Video, "archived001").progress_s == 45


@pytest.mark.parametrize("path", [
    "/api/videos/archived001/playback", "/api/playback/" + "a" * 43 + "/heartbeat",
    "/api/playback/" + "a" * 43 + "/ended",
])
def test_public_playback_posts_require_origin_and_custom_header_before_parsing(environment, path):
    base = [(b"host", b"testserver"), (b"content-type", b"application/json")]
    status_code, calls = raw_request(base, [], path=path, forbidden_receive=True)
    assert status_code == 403 and calls["receive"] == 0
    headers = [*base, (b"x-vitrine-request", b"1"), (b"origin", b"http://evil.example")]
    status_code, calls = raw_request(headers, [], path=path, forbidden_receive=True)
    assert status_code == 403 and calls["receive"] == 0
    headers[-1] = (b"origin", b"http://testserver")
    status_code, calls = raw_request(headers, [{"type": "http.request", "body": b"{}", "more_body": False}], path=path)
    assert status_code == 204 and calls["downstream"] == 1


@pytest.mark.parametrize("legacy_setting", [True, False])
@pytest.mark.parametrize(("transport", "origin", "secure"), [
    ("http://testserver", "http://testserver", False),
    ("https://testserver", "https://testserver", True),
    ("http://testserver", "https://testserver", True),
])
def test_login_automatically_follows_browser_protocol_even_behind_proxy(environment, monkeypatch,
                                                                      legacy_setting, transport, origin, secure):
    monkeypatch.setattr(settings, "auth_cookie_secure", legacy_setting)
    auth.set_account("admin", PASSWORD)
    client = TestClient(main.app, base_url=transport)
    try:
        result = client.post("/api/auth/login", json={"benutzer": "admin", "passwort": PASSWORD},
                             headers={"X-Vitrine-Request": "1", "Origin": origin})
        assert result.status_code == 200
        assert ("Secure" in result.headers["set-cookie"]) == secure
        name = auth.COOKIE_NAME if secure else auth.HTTP_COOKIE_NAME
        token = client.cookies.get(name)
        assert token
        # Am Proxy kommt der Cookie-Header des externen HTTPS-Browsers an.
        headers = {"Cookie": f"{name}={token}", "Origin": origin, "X-CSRF-Token": result.json()["csrf_token"]}
        assert client.get("/api/settings", headers=headers).status_code == 200
        assert client.post("/api/auth/logout", headers=headers).status_code == 204
        assert auth.session_for(token) is None
    finally:
        client.close()


def test_http_login_does_not_trust_forwarded_proto_and_replaces_stale_secure_session(environment):
    auth.set_account("admin", PASSWORD)
    old_token, _ = auth.login("admin", PASSWORD)
    client = TestClient(main.app, base_url="http://testserver")
    try:
        result = client.post("/api/auth/login", json={"benutzer": "admin", "passwort": PASSWORD},
                             headers={"X-Vitrine-Request": "1", "X-Forwarded-Proto": "https",
                                      "Cookie": f"{auth.COOKIE_NAME}={old_token}"})
        assert result.status_code == 200 and "Secure" not in result.headers["set-cookie"]
        assert auth.session_for(old_token) is None
        new_token = client.cookies.get(auth.HTTP_COOKIE_NAME)
        both = {"Cookie": f"{auth.COOKIE_NAME}={old_token}; {auth.HTTP_COOKIE_NAME}={new_token}"}
        assert client.get("/api/auth/session", headers=both).json()["csrf_token"] == result.json()["csrf_token"]
        assert client.post("/api/auth/logout", headers={**both, "X-CSRF-Token": result.json()["csrf_token"]}).status_code == 204
    finally:
        client.close()


@pytest.mark.parametrize("operation", ["logout", "password"])
def test_logout_and_password_reset_revoke_both_protocol_sessions(environment, operation):
    client, _, _ = environment
    auth.set_account("admin", PASSWORD)
    secure_token, secure_identity = auth.login("admin", PASSWORD)
    http_token, _ = auth.login("admin", PASSWORD)
    headers = {"Cookie": f"{auth.COOKIE_NAME}={secure_token}; {auth.HTTP_COOKIE_NAME}={http_token}",
               "Origin": "https://testserver", "X-CSRF-Token": secure_identity.csrf_token}
    body = {"aktuelles_passwort": PASSWORD, "neues_passwort": NEW_PASSWORD} if operation == "password" else None
    response = client.post(f"/api/auth/{operation}", json=body, headers=headers)
    assert response.status_code == 204
    assert auth.session_for(secure_token) is None and auth.session_for(http_token) is None
    cookies = response.headers.get_list("set-cookie")
    assert any(row.startswith(auth.COOKIE_NAME + "=") and "Max-Age=0" in row for row in cookies)
    assert any(row.startswith(auth.HTTP_COOKIE_NAME + "=") and "Max-Age=0" in row for row in cookies)
