"""Echte App-Grenze: Zugang vor Parsing, Sitzung, CSRF und widerrufbare Identitaet."""

from __future__ import annotations

import asyncio
import hashlib
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from app import admin, main
from app import db as database
from app.config import settings
from app.models import AdminAccount, AdminLoginLimit, AdminSession, Base
from app.security import BODY_LIMIT, LOGIN_BODY_LIMIT, SecurityMiddleware, same_origin
from app.services import auth

PASSWORD = "Ein sehr gutes Passwort 2026!"
NEW_PASSWORD = "Ein anderes gutes Passwort 2026!"


@pytest.fixture
def environment(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'auth-test.db'}", connect_args={"check_same_thread": False})
    event.listen(engine, "connect", database.set_pragmas)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(database, "SessionLocal", factory)
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "auth_cookie_secure", True)
    monkeypatch.setattr(settings, "auth_session_hours", 12)
    # Kein TestClient-Kontext: echte Router/Middleware, aber keine produktiven Worker.
    client = TestClient(main.app, base_url="https://testserver")
    yield client, factory, engine
    client.close()
    engine.dispose()


def sign_in(client, password=PASSWORD):
    return client.post("/api/auth/login", json={"benutzer": "admin", "passwort": password},
                       headers={"X-Vitrine-Request": "1", "Origin": "https://testserver"})


def setup(client):
    auth.set_account("admin", PASSWORD)
    response = sign_in(client)
    assert response.status_code == 200
    return response


def test_unconfigured_closed_and_health_minimal(environment):
    client, _, _ = environment
    assert client.get("/api/auth/session").json() == {
        "eingerichtet": False, "angemeldet": False, "benutzer": None, "csrf_token": None,
    }
    assert sign_in(client).status_code == 503
    assert client.get("/api/channels").status_code == 401
    response = client.get("/api/health")
    assert response.json() == {"status": "ok"}
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert "script-src 'self'" in response.headers["content-security-policy"]
    assert main.app.openapi_url is None and main.app.docs_url is None and main.app.redoc_url is None


def test_login_cookie_hash_and_logout(environment):
    client, factory, _ = environment
    response = setup(client)
    cookie = response.headers["set-cookie"]
    assert all(value in cookie for value in ["HttpOnly", "Secure", "SameSite=strict", "Path=/", "Max-Age=43200"])
    token = client.cookies.get(auth.COOKIE_NAME)
    assert response.json()["angemeldet"] is True
    csrf = response.json()["csrf_token"]
    assert csrf
    with factory() as db:
        session = db.scalar(select(AdminSession))
        assert session.token_hash == hashlib.sha256(token.encode()).hexdigest()
        assert session.token_hash != token
        assert db.get(AdminAccount, 1).password_hash != PASSWORD
    assert client.get("/api/channels").status_code == 200
    assert client.post("/api/auth/logout").status_code == 403
    assert client.post("/api/auth/logout", headers={"X-CSRF-Token": csrf}).status_code == 204
    client.cookies.set(auth.COOKIE_NAME, token)
    assert client.get("/api/channels").status_code == 401


def test_bad_password_and_forged_or_expired_cookie(environment):
    client, factory, _ = environment
    setup(client)
    assert sign_in(client, "not the password").status_code == 401
    token = client.cookies.get(auth.COOKIE_NAME)
    with factory() as db:
        db.scalar(select(AdminSession)).expires_at = auth.now() - timedelta(seconds=1)
        db.commit()
    assert client.get("/api/channels").status_code == 401
    for forged in ["invalid", "a" * 43, token + "bad"]:
        client.cookies.clear()
        client.cookies.set(auth.COOKIE_NAME, forged)
        assert client.get("/api/channels").status_code == 401


def test_password_change_and_cli_reset_revoke_every_session(environment):
    client, _, _ = environment
    first = setup(client).json()
    original = client.cookies.get(auth.COOKIE_NAME)
    second = sign_in(client).json()
    csrf = {"X-CSRF-Token": second["csrf_token"]}
    wrong = client.post("/api/auth/password", json={"aktuelles_passwort": "wrong", "neues_passwort": NEW_PASSWORD},
                        headers=csrf)
    assert wrong.status_code == 400
    result = client.post("/api/auth/password", json={"aktuelles_passwort": PASSWORD, "neues_passwort": NEW_PASSWORD},
                         headers=csrf)
    assert result.status_code == 204
    assert auth.session_for(original) is None
    assert first["csrf_token"] != second["csrf_token"]
    assert sign_in(client, PASSWORD).status_code == 401
    assert sign_in(client, NEW_PASSWORD).status_code == 200
    latest = client.cookies.get(auth.COOKIE_NAME)
    auth.set_account("admin", PASSWORD)
    assert auth.session_for(latest) is None


@pytest.mark.parametrize(("method", "path"), [
    ("GET", "/api/channels"), ("POST", "/api/channels"), ("DELETE", "/api/channels/UCtest"),
    ("GET", "/api/videos/test/stream"), ("GET", "/api/videos/test/subtitles/de"),
    ("GET", "/api/thumbs/quelle/dQw4w9WgXcQ"), ("GET", "/api/thumbs/test.jpg"),
    ("GET", "/api/cookies"), ("POST", "/api/cookies"), ("POST", "/api/cookies/test"),
    ("POST", "/api/vpn"), ("POST", "/api/vpn/test-direkt"), ("POST", "/api/hardware/test"),
    ("GET", "/api/settings"), ("PUT", "/api/settings"), ("POST", "/api/search/reindex"),
    ("POST", "/api/jobs/pause"), ("POST", "/api/videos/test/heartbeat"),
    ("GET", "/api/health/"), ("POST", "/api/auth/session"),
])
def test_every_surface_locked_before_router_parsing(environment, method, path):
    client, _, _ = environment
    result = client.request(method, path, content=b"definitely not valid JSON", follow_redirects=False)
    assert result.status_code == 401
    assert result.headers["cache-control"] == "no-store"


def test_csrf_for_json_multipart_and_raw_requests(environment):
    client, _, _ = environment
    csrf = setup(client).json()["csrf_token"]
    assert client.put("/api/settings", json={}).status_code == 403
    assert client.post("/api/cookies", files={"datei": ("cookies.txt", b"bad")}).status_code == 403
    assert client.post("/api/jobs/pause", content=b"{}").status_code == 403
    assert client.put("/api/settings", json={}, headers={"X-CSRF-Token": "wrong"}).status_code == 403
    result = client.post("/api/auth/logout", headers={"X-CSRF-Token": csrf, "Origin": "https://evil.test"})
    assert result.status_code == 403
    assert client.get("/api/auth/session").json()["angemeldet"]


@pytest.mark.parametrize("headers", [
    {}, {"X-Vitrine-Request": "1", "Origin": "https://evil.test"},
    {"X-Vitrine-Request": "1", "Origin": "null"},
    {"X-Vitrine-Request": "1", "Origin": "https://testserver.evil.test"},
    {"X-Vitrine-Request": "1", "Origin": "http://testserver"},
    {"X-Vitrine-Request": "1", "Sec-Fetch-Site": "cross-site"},
])
def test_cross_origin_login_denied(environment, headers):
    client, _, _ = environment
    auth.set_account("admin", PASSWORD)
    assert client.post("/api/auth/login", json={"benutzer": "admin", "passwort": PASSWORD},
                       headers=headers).status_code == 403


def test_cli_login_header_and_https_origin_behind_http_proxy(environment):
    from starlette.datastructures import Headers

    client, _, _ = environment
    auth.set_account("admin", PASSWORD)
    assert client.post("/api/auth/login", json={"benutzer": "admin", "passwort": PASSWORD},
                       headers={"X-Vitrine-Request": "1"}).status_code == 200
    # TLS endet am Proxy; nur erhaltener Host, kein Vertrauen in Forwarded/XFF.
    assert same_origin(Headers({"Host": "archiv.example", "Origin": "https://archiv.example"}))
    assert not same_origin(Headers({"Host": "archiv.example", "Origin": "https://evil.example",
                                    "X-Forwarded-Host": "evil.example", "X-Forwarded-Proto": "https"}))


def test_auth_validation_does_not_reflect_password(environment):
    client, _, _ = environment
    auth.set_account("admin", PASSWORD)
    secret = "z" * 257
    bad = client.post("/api/auth/login", json={"benutzer": "admin", "passwort": secret},
                      headers={"X-Vitrine-Request": "1"})
    assert bad.status_code == 422
    assert secret not in bad.text and '"input"' not in bad.text
    csrf = sign_in(client).json()["csrf_token"]
    bad = client.post("/api/auth/password", json={"aktuelles_passwort": PASSWORD, "neues_passwort": "short-secret"},
                      headers={"X-CSRF-Token": csrf})
    assert bad.status_code == 422
    assert "short-secret" not in bad.text and PASSWORD not in bad.text


def test_login_budget_persists_and_reservations_are_atomic(environment, monkeypatch):
    client, factory, engine = environment
    auth.set_account("admin", PASSWORD)
    monkeypatch.setattr(auth, "_verify", lambda *_args: False)

    def attempt(_number):
        try:
            auth._reserve_attempt()
            return True
        except auth.AuthError:
            return False

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(attempt, range(20)))
    assert sum(results) == auth.LOGIN_ATTEMPTS
    engine.dispose()
    for spoof in ["1.2.3.4", "8.8.8.8"]:
        result = client.post("/api/auth/login", json={"benutzer": "admin", "passwort": PASSWORD},
                             headers={"X-Vitrine-Request": "1", "X-Forwarded-For": spoof})
        assert result.status_code == 429
    with factory() as db:
        assert db.get(AdminLoginLimit, 1).attempts == auth.LOGIN_ATTEMPTS


def test_reset_during_login_cannot_resurrect_old_credentials(environment, monkeypatch):
    client, factory, _ = environment
    auth.set_account("admin", PASSWORD)
    original_verify = auth._verify

    def reset_after_verify(password, encoded):
        correct = original_verify(password, encoded)
        auth.set_account("admin", NEW_PASSWORD)
        return correct

    monkeypatch.setattr(auth, "_verify", reset_after_verify)
    assert sign_in(client).status_code == 401
    with factory() as db:
        assert db.scalar(select(AdminSession)) is None


def raw_request(headers, chunks, *, path="/api/cookies", forbidden_receive=False):
    calls = {"downstream": 0, "receive": 0}
    messages = []

    async def downstream(scope, receive, send):
        calls["downstream"] += 1
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    pending = iter(chunks)

    async def receive():
        calls["receive"] += 1
        if forbidden_receive:
            raise AssertionError("Unautorisierte Anfrage wurde gelesen")
        return next(pending, {"type": "http.disconnect"})

    async def send(message):
        messages.append(message)

    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST",
             "scheme": "http", "path": path, "raw_path": path.encode(), "query_string": b"",
             "headers": headers, "client": ("127.0.0.1", 1234), "server": ("testserver", 80)}
    asyncio.run(SecurityMiddleware(downstream)(scope, receive, send))
    return messages[0]["status"], calls


def test_unauthorized_upload_body_never_consumed(environment):
    code, calls = raw_request([(b"content-length", b"999999999")], [], forbidden_receive=True)
    assert code == 401 and calls["receive"] == 0 and calls["downstream"] == 0


def test_oversize_before_parser_declared_and_chunked(environment):
    client, _, _ = environment
    csrf = setup(client).json()["csrf_token"]
    base = [(b"cookie", f"{auth.COOKIE_NAME}={client.cookies.get(auth.COOKIE_NAME)}".encode()),
            (b"x-csrf-token", csrf.encode())]
    code, calls = raw_request([*base, (b"content-length", str(BODY_LIMIT + 1).encode())], [], forbidden_receive=True)
    assert code == 413 and calls["receive"] == 0 and calls["downstream"] == 0
    chunks = [{"type": "http.request", "body": b"x" * (BODY_LIMIT // 2), "more_body": True}] * 3
    code, calls = raw_request(base, chunks)
    assert code == 413 and calls["downstream"] == 0
    # Auch eine gelogene kleine Inhaltslaenge umgeht die gemessene Grenze nicht.
    code, calls = raw_request([*base, (b"content-length", b"10")], chunks)
    assert code == 413 and calls["downstream"] == 0


def test_non_ascii_csrf_is_403_and_login_cap(environment):
    client, _, _ = environment
    setup(client)
    cookie = f"{auth.COOKIE_NAME}={client.cookies.get(auth.COOKIE_NAME)}".encode()
    code, calls = raw_request([(b"cookie", cookie), (b"x-csrf-token", b"\xff")], [], forbidden_receive=True)
    assert code == 403 and calls["receive"] == 0
    code, calls = raw_request([(b"content-type", b"application/json"), (b"x-vitrine-request", b"1")],
                             [{"type": "http.request", "body": b"x" * (LOGIN_BODY_LIMIT + 1), "more_body": False}],
                             path="/api/auth/login")
    assert code == 413 and calls["downstream"] == 0


def test_cli_setup_never_prints_password(environment, monkeypatch, capsys):
    monkeypatch.setattr(admin, "init_db", lambda: None)
    monkeypatch.setattr("builtins.input", lambda _prompt: "")
    monkeypatch.setattr(admin.getpass, "getpass", lambda _prompt: PASSWORD)
    admin.main()
    assert PASSWORD not in capsys.readouterr().out
    assert auth.configured()
    assert sign_in(environment[0]).status_code == 200


def test_explicit_local_http_cookie_mode(environment, monkeypatch):
    monkeypatch.setattr(settings, "auth_cookie_secure", False)
    auth.set_account("admin", PASSWORD)
    client = TestClient(main.app, base_url="http://testserver")
    try:
        result = client.post("/api/auth/login", json={"benutzer": "admin", "passwort": PASSWORD},
                             headers={"X-Vitrine-Request": "1", "Origin": "http://testserver"})
        assert result.status_code == 200
        assert "Secure" not in result.headers["set-cookie"]
        assert "HttpOnly" in result.headers["set-cookie"]
        assert client.get("/api/channels").status_code == 200
    finally:
        client.close()


def test_password_hash_work_has_no_unbounded_wait_queue(environment):
    auth._HASH_SLOTS.acquire()
    auth._HASH_SLOTS.acquire()
    try:
        with pytest.raises(auth.AuthError) as failure:
            auth.login("admin", PASSWORD)
        assert failure.value.status == 429
    finally:
        auth._HASH_SLOTS.release()
        auth._HASH_SLOTS.release()


def test_legacy_http_cache_purged_once_and_on_logout(environment):
    client, _, _ = environment
    initial = client.get("/api/auth/session")
    assert initial.headers["clear-site-data"] == '"cache"'
    assert "clear-site-data" not in client.get("/api/auth/session").headers
    csrf = setup(client).json()["csrf_token"]
    response = client.post("/api/auth/logout", headers={"X-CSRF-Token": csrf})
    assert response.status_code == 204
    assert response.headers["clear-site-data"] == '"cache"'


def test_reverse_proxy_root_path_cannot_bypass_api_guard(environment):
    client = TestClient(main.app, base_url="https://testserver", root_path="/vitrine")
    try:
        result = client.get("/vitrine/api/channels")
        assert result.status_code == 401
        assert result.headers["cache-control"] == "no-store"
        assert client.get("/vitrine/api/health").json() == {"status": "ok"}
        secret = "root-path-password-" * 20
        invalid = client.post("/vitrine/api/auth/login", json={"benutzer": "admin", "passwort": secret},
                              headers={"X-Vitrine-Request": "1", "Origin": "https://testserver"})
        assert invalid.status_code == 422 and secret not in invalid.text
        oversized = client.post("/vitrine/api/auth/login", content=b"x" * (LOGIN_BODY_LIMIT + 1),
                                headers={"X-Vitrine-Request": "1", "Content-Type": "application/json"})
        assert oversized.status_code == 413
    finally:
        client.close()


def test_invalid_json_and_unpaired_unicode_do_not_echo_password(environment):
    client, _, _ = environment
    auth.set_account("admin", PASSWORD)
    headers = {"Content-Type": "application/json", "X-Vitrine-Request": "1"}
    result = client.post("/api/auth/login", content='{"benutzer":"admin","passwort":"private-value",',
                         headers=headers)
    assert result.status_code == 422 and "private-value" not in result.text
    result = client.post("/api/auth/login", content='{"benutzer":"admin","passwort":"private-value\\ud800"}',
                         headers=headers)
    assert result.status_code == 422 and "private-value" not in result.text


def test_documented_cli_module_persists_and_resets_account(tmp_path):
    data = tmp_path / "cli-data"
    process_env = {**os.environ, "YTA_DATA_DIR": str(data), "YTA_AUTH_COOKIE_SECURE": "false"}
    for password, expected_revision in [(PASSWORD, 1), (NEW_PASSWORD, 2)]:
        result = subprocess.run(
            # getpass liest unter Windows unmittelbar vom Terminal. Nur die
            # Eingabe fuer den Subprozesstest auf seine Pipe umleiten.
            [sys.executable, "-c", "import getpass, runpy; getpass.getpass = lambda prompt: input(); "
             "runpy.run_module('app.admin', run_name='__main__')"], input=f"\n{password}\n{password}\n",
            capture_output=True, text=True, timeout=30, check=False, env=process_env,
        )
        assert result.returncode == 0
        assert password not in result.stdout and password not in result.stderr
        engine = create_engine(f"sqlite:///{data / 'vitrine.db'}")
        try:
            with sessionmaker(bind=engine)() as db:
                account = db.get(AdminAccount, 1)
                assert account.username == "admin" and account.revision == expected_revision
                assert auth._verify(password, account.password_hash)
                assert db.scalar(select(AdminSession)) is None
                if expected_revision == 1:
                    db.add(AdminSession(token_hash="a" * 64, account_revision=1, csrf_token="old",
                                        created_at=auth.now(), expires_at=auth.now() + timedelta(hours=12)))
                    db.commit()
        finally:
            engine.dispose()
