"""Echte App-Grenze: Zugang vor Parsing, Sitzung, CSRF und widerrufbare Identitaet."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from app import admin, main
from app import db as database
from app.config import settings
from app.models import AdminAccount, AdminBootstrap, AdminLoginLimit, AdminSession, Base
from app.security import BODY_LIMIT, LOGIN_BODY_LIMIT, SecurityMiddleware, same_origin
from app.services import auth

PASSWORD = "Ein sehr gutes Passwort 2026!"
NEW_PASSWORD = "Ein anderes gutes Passwort 2026!"
SETUP_HEADERS = {"X-Vitrine-Request": "1", "Origin": "https://testserver"}


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
    bad = client.post("/api/auth/password", json={"aktuelles_passwort": PASSWORD, "neues_passwort": "Kurz!12"},
                      headers={"X-CSRF-Token": csrf})
    assert bad.status_code == 422
    assert "Kurz!12" not in bad.text and PASSWORD not in bad.text


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
    for password, expected_revision in [("A!abcdef", 1), ("B!abcdef", 2)]:
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


def bootstrap_code(caplog):
    caplog.set_level(logging.WARNING, logger="app.services.auth")
    start = len(caplog.records)
    auth.prepare_bootstrap()
    messages = [record.getMessage() for record in caplog.records[start:]
                if record.name == "app.services.auth"]
    assert len(messages) == 1
    prefix = "Vitrine-Einrichtungscode: "
    assert messages[0].startswith(prefix)
    code = messages[0][len(prefix):]
    assert auth._TOKEN.fullmatch(code)
    return code


def submit_setup(client, code, password=PASSWORD, username="admin", path="/api/auth/setup"):
    return client.post(path, json={"einrichtungscode": code, "benutzer": username, "passwort": password},
                       headers=SETUP_HEADERS)


def test_setup_consumes_local_code_without_disclosure_or_autologin(environment, caplog):
    client, factory, _ = environment
    code = bootstrap_code(caplog)
    with factory() as db:
        record = db.get(AdminBootstrap, 1)
        assert record.code_hash == hashlib.sha256(code.encode()).hexdigest()
        assert record.code_hash != code
    for path in ["/api/auth/session", "/api/health", "/api/auth/setup", "/api/auth/setup/code"]:
        response = client.get(path)
        assert code not in response.text
    response = submit_setup(client, code)
    assert response.status_code == 204 and response.content == b""
    assert "set-cookie" not in response.headers
    assert response.headers["cache-control"] == "no-store"
    assert client.get("/api/auth/session").json() == {
        "eingerichtet": True, "angemeldet": False, "benutzer": None, "csrf_token": None,
    }
    with factory() as db:
        assert db.get(AdminBootstrap, 1) is None
        assert db.scalar(select(AdminSession)) is None
    assert submit_setup(client, code, NEW_PASSWORD).status_code == 409
    assert submit_setup(client, "wrong", NEW_PASSWORD).status_code == 409
    assert sign_in(client).status_code == 200


@pytest.mark.parametrize(("base_url", "root_path"), [
    ("http://192.168.1.8:8000", ""),
    ("http://vitrine.local", ""),
    ("http://[fd00::2]:8000", "/vitrine"),
    ("https://archiv.example", ""),
    ("https://archiv.example:8443", "/vitrine"),
])
def test_setup_accepts_browser_http_and_https_with_secure_cookie_default(environment, caplog, base_url, root_path):
    secure_client, factory, _ = environment
    code = bootstrap_code(caplog)
    payload = {"einrichtungscode": code, "benutzer": "admin", "passwort": "A!abcdef"}
    headers = {"X-Vitrine-Request": "1", "Origin": base_url, "Host": base_url.split("://", 1)[1]}
    # Starlettes httpx-Testtransport kann keine IPv6-URL parsen. Die echten
    # IPv6-Browserheader trotzdem unveraendert durch die ASGI-Grenze pruefen.
    transport_url = "http://testserver:8000" if "[" in base_url else base_url
    client = TestClient(main.app, base_url=transport_url, root_path=root_path)
    try:
        response = client.post(root_path + "/api/auth/setup", json=payload, headers=headers)
        assert response.status_code == 204 and response.content == b""
        assert "set-cookie" not in response.headers
        assert response.headers["cache-control"] == "no-store"
        assert settings.auth_cookie_secure is True
        assert client.get(root_path + "/api/auth/session").json() == {
            "eingerichtet": True, "angemeldet": False, "benutzer": None, "csrf_token": None,
        }
        assert client.get(root_path + "/api/channels").status_code == 401
        assert client.post(root_path + "/api/auth/setup", json=payload, headers=headers).status_code == 409
        with factory() as db:
            assert db.get(AdminBootstrap, 1) is None
            assert db.scalar(select(AdminSession)) is None
        # Die Ausnahme zur Einrichtung aendert keine bestehenden Login-Regeln.
        if base_url.startswith("http:"):
            assert client.post(root_path + "/api/auth/login", json={"benutzer": "admin", "passwort": "A!abcdef"},
                               headers=headers).status_code == 403
        login = sign_in(secure_client, "A!abcdef")
        assert login.status_code == 200 and "Secure" in login.headers["set-cookie"]
        if base_url.startswith("http:"):
            # Auch ein absichtlich mitgesendetes Cookie samt CSRF-Token erlaubt
            # keine HTTP-Schreibzugriffe auf die bestehende HTTPS-Sitzung.
            guarded = {**headers, "X-CSRF-Token": login.json()["csrf_token"],
                       "Cookie": f"{auth.COOKIE_NAME}={secure_client.cookies.get(auth.COOKIE_NAME)}"}
            assert client.post(root_path + "/api/auth/logout", headers=guarded).status_code == 403
            assert secure_client.get("/api/auth/session").json()["angemeldet"] is True
    finally:
        client.close()


@pytest.mark.parametrize("origin_headers", [
    {"Origin": "http://fremd.example:8000"},
    {"Origin": "http://vitrine.local"},  # Gleicher Host, anderer Port.
    {"Origin": "http://vitrine.local:8001"},
    {"Origin": "null"},
    {"Origin": "ftp://vitrine.local:8000"},
    {"Origin": "http://user@vitrine.local:8000"},
    {"Origin": "http://vitrine.local:8000", "Sec-Fetch-Site": "cross-site"},
    {"Origin": "http://fremd.example:8000", "X-Forwarded-Host": "fremd.example:8000",
     "X-Forwarded-Proto": "http"},
])
def test_http_setup_still_rejects_foreign_origins(environment, caplog, origin_headers):
    code = bootstrap_code(caplog)
    payload = {"einrichtungscode": code, "benutzer": "admin", "passwort": PASSWORD}
    client = TestClient(main.app, base_url="http://vitrine.local:8000")
    try:
        headers = {"X-Vitrine-Request": "1", **origin_headers}
        assert client.post("/api/auth/setup", json=payload, headers=headers).status_code == 403
        assert not auth.configured()
        # Abgewiesene Browseranfragen verbrauchen auch den richtigen Code nicht.
        headers["Origin"] = "http://vitrine.local:8000"
        headers.pop("Sec-Fetch-Site", None)
        assert client.post("/api/auth/setup", json=payload, headers=headers).status_code == 204
    finally:
        client.close()


def test_http_setup_and_login_with_explicit_http_cookie_mode(environment, caplog, monkeypatch):
    monkeypatch.setattr(settings, "auth_cookie_secure", False)
    code = bootstrap_code(caplog)
    client = TestClient(main.app, base_url="http://vitrine.local:8000")
    headers = {"X-Vitrine-Request": "1", "Origin": "http://vitrine.local:8000"}
    try:
        payload = {"einrichtungscode": code, "benutzer": "admin", "passwort": "A!abcdef"}
        assert client.post("/api/auth/setup", json=payload, headers=headers).status_code == 204
        login = client.post("/api/auth/login", json={"benutzer": "admin", "passwort": "A!abcdef"}, headers=headers)
        assert login.status_code == 200
        assert "Secure" not in login.headers["set-cookie"]
        assert "HttpOnly" in login.headers["set-cookie"] and "SameSite=strict" in login.headers["set-cookie"]
        assert client.get("/api/channels").status_code == 200
        assert client.post("/api/auth/logout", headers=headers).status_code == 403
        assert client.post("/api/auth/logout", headers={**headers, "X-CSRF-Token": login.json()["csrf_token"]}).status_code == 204
        assert client.get("/api/channels").status_code == 401
    finally:
        client.close()


def test_wrong_setup_code_is_cheap_and_cannot_lock_owner_out(environment, caplog, monkeypatch):
    client, factory, _ = environment
    code = bootstrap_code(caplog)
    original = auth.hash_password

    def no_hash(_password):
        raise AssertionError("Falscher Einrichtungscode darf kein Passwort-Hashen ausloesen")

    monkeypatch.setattr(auth, "hash_password", no_hash)
    auth._HASH_SLOTS.acquire()
    auth._HASH_SLOTS.acquire()
    try:
        for wrong in ["wrong", "x" * 43, code[:-1], "\u00fc" * 43] * 4:
            assert submit_setup(client, wrong).status_code == 403
    finally:
        auth._HASH_SLOTS.release()
        auth._HASH_SLOTS.release()
    with factory() as db:
        assert db.get(AdminLoginLimit, 1) is None
        assert db.get(AdminAccount, 1) is None
    monkeypatch.setattr(auth, "hash_password", original)
    assert submit_setup(client, code).status_code == 204


def test_missing_code_and_setup_validation_never_reflect_secrets(environment, caplog):
    client, _, _ = environment
    code = bootstrap_code(caplog)
    for payload in [
        {"benutzer": "admin", "passwort": PASSWORD},
        {"einrichtungscode": code, "benutzer": "admin", "passwort": "Priv!12"},
        {"einrichtungscode": code * 7, "benutzer": "admin", "passwort": PASSWORD},
    ]:
        response = client.post("/api/auth/setup", json=payload, headers=SETUP_HEADERS)
        assert response.status_code == 422
        assert code not in response.text and PASSWORD not in response.text
        assert "Priv!12" not in response.text and '"input"' not in response.text


@pytest.mark.parametrize("headers", [
    {}, {"X-Vitrine-Request": "1", "Origin": "https://evil.test"},
    {"X-Vitrine-Request": "1", "Origin": "null"},
    {"X-Vitrine-Request": "1", "Sec-Fetch-Site": "cross-site"},
])
def test_setup_requires_same_origin_and_custom_header(environment, caplog, headers):
    client, _, _ = environment
    code = bootstrap_code(caplog)
    result = client.post("/api/auth/setup", json={"einrichtungscode": code, "benutzer": "admin", "passwort": PASSWORD},
                         headers=headers)
    assert result.status_code == 403
    result = client.post("/api/auth/setup", content=b"not JSON", headers={"X-Vitrine-Request": "1"})
    assert result.status_code == 403
    assert not auth.configured()


def test_setup_limit_and_root_path(environment, caplog):
    code = bootstrap_code(caplog)
    client = TestClient(main.app, base_url="https://testserver", root_path="/vitrine")
    try:
        response = client.post("/vitrine/api/auth/setup", content=b"x" * (LOGIN_BODY_LIMIT + 1),
                               headers={**SETUP_HEADERS, "Content-Type": "application/json"})
        assert response.status_code == 413
        assert client.get("/vitrine/api/channels").status_code == 401
        invalid = submit_setup(client, code, "short", path="/vitrine/api/auth/setup")
        assert invalid.status_code == 422 and code not in invalid.text
        assert submit_setup(client, code, path="/vitrine/api/auth/setup").status_code == 204
    finally:
        client.close()
    status, calls = raw_request([(b"content-type", b"application/json"), (b"x-vitrine-request", b"1")],
                               [{"type": "http.request", "body": b"x" * (LOGIN_BODY_LIMIT + 1), "more_body": True}],
                               path="/api/auth/setup")
    assert status == 413 and calls["downstream"] == 0


def test_restart_rotates_code_and_cli_setup_invalidates_it(environment, caplog):
    client, factory, _ = environment
    first = bootstrap_code(caplog)
    second = bootstrap_code(caplog)
    assert first != second
    assert submit_setup(client, first).status_code == 403
    auth.set_account("admin", PASSWORD)
    with factory() as db:
        assert db.get(AdminBootstrap, 1) is None
    assert submit_setup(client, second, NEW_PASSWORD).status_code == 409
    assert sign_in(client).status_code == 200


def test_parallel_setup_creates_exactly_one_account(environment, caplog, monkeypatch):
    _, factory, _ = environment
    code = bootstrap_code(caplog)
    original_hash = auth.hash_password
    rendezvous = threading.Barrier(2)

    def simultaneous_hash(password):
        result = original_hash(password)
        rendezvous.wait(timeout=15)
        return result

    monkeypatch.setattr(auth, "hash_password", simultaneous_hash)

    def attempt(number):
        client = TestClient(main.app, base_url="https://testserver")
        try:
            return submit_setup(client, code, username=f"admin{number}").status_code
        finally:
            client.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, range(2)))
    assert sorted(results) == [204, 409]
    with factory() as db:
        assert len(list(db.scalars(select(AdminAccount)))) == 1
        assert db.get(AdminBootstrap, 1) is None


@pytest.mark.parametrize("interference", ["cli", "restart"])
def test_setup_rechecks_state_after_hashing(environment, caplog, monkeypatch, interference):
    client, _, _ = environment
    code = bootstrap_code(caplog)
    original_hash = auth.hash_password

    def interfere(password):
        result = original_hash(password)
        if interference == "cli":
            # Nur den Test-Hook fuer den echten lokalen Reset aussetzen.
            monkeypatch.setattr(auth, "hash_password", original_hash)
            auth.set_account("owner", NEW_PASSWORD)
        else:
            auth.prepare_bootstrap()
        return result

    monkeypatch.setattr(auth, "hash_password", interfere)
    response = submit_setup(client, code)
    assert response.status_code == (409 if interference == "cli" else 403)
    if interference == "cli":
        with environment[1]() as db:
            account = db.get(AdminAccount, 1)
            assert account.username == "owner" and auth._verify(NEW_PASSWORD, account.password_hash)
    else:
        assert not auth.configured()


def test_configured_startup_deletes_bootstrap_without_generating_or_logging_code(environment, caplog, monkeypatch):
    _, factory, _ = environment
    auth.set_account("admin", PASSWORD)
    with factory() as db:
        db.add(AdminBootstrap(id=1, code_hash="a" * 64))
        db.commit()
    caplog.clear()

    def forbidden_generation(_size):
        raise AssertionError("Ein eingerichtetes Konto bekommt keinen Einrichtungscode")

    monkeypatch.setattr(auth.secrets, "token_urlsafe", forbidden_generation)
    auth.prepare_bootstrap()
    assert "Vitrine-Einrichtungscode:" not in caplog.text
    with factory() as db:
        assert db.get(AdminBootstrap, 1) is None


def test_real_app_startup_calls_bootstrap_once_without_starting_test_workers(environment, caplog, monkeypatch):
    monkeypatch.setattr(main, "init_db", lambda: None)
    monkeypatch.setattr(main, "_werkzeuge_pruefen", lambda: None)
    monkeypatch.setattr(main, "_reaper_loop", lambda: None)
    monkeypatch.setattr(main, "_scheduler_loop", lambda: None)
    monkeypatch.setattr(main, "_vpn_wache_loop", lambda: None)
    monkeypatch.setattr(main, "_stop", threading.Event())
    monkeypatch.setattr(main.werk, "start", lambda: None)
    monkeypatch.setattr(main.werk, "stop", lambda: None)
    monkeypatch.setattr(main.vpn_dienst, "laden", lambda _db: None)
    monkeypatch.setattr(main.vpn_dienst, "alles_beenden", lambda: None)
    caplog.clear()
    with TestClient(main.app, base_url="https://testserver") as client:
        messages = [r.getMessage() for r in caplog.records if "Vitrine-Einrichtungscode:" in r.getMessage()]
        assert len(messages) == 1
        code = messages[0].split(": ", 1)[1]
        assert submit_setup(client, code).status_code == 204
    caplog.clear()
    with TestClient(main.app, base_url="https://testserver") as client:
        assert client.get("/api/auth/session").json()["eingerichtet"] is True
        assert "Vitrine-Einrichtungscode:" not in caplog.text


@pytest.mark.parametrize("level", [logging.ERROR, logging.CRITICAL])
def test_bootstrap_code_remains_local_and_visible_with_high_log_level(environment, caplog, capsys, level):
    client, factory, _ = environment
    caplog.set_level(level, logger="app.services.auth")
    caplog.clear()
    auth.prepare_bootstrap()
    output = capsys.readouterr()
    assert output.out == ""
    lines = output.err.splitlines()
    assert len(lines) == 1 and lines[0].startswith("Vitrine-Einrichtungscode: ")
    code = lines[0].split(": ", 1)[1]
    assert auth._TOKEN.fullmatch(code)
    assert "Vitrine-Einrichtungscode:" not in caplog.text
    with factory() as db:
        assert db.get(AdminBootstrap, 1).code_hash == hashlib.sha256(code.encode()).hexdigest()
    assert submit_setup(client, code).status_code == 204
    auth.prepare_bootstrap()
    assert capsys.readouterr().err == ""


def test_concurrent_startups_publish_codes_in_database_order(environment, caplog, monkeypatch):
    client, factory, _ = environment
    logged = []
    first_logged = threading.Event()
    second_started = threading.Event()

    def record_warning(_message, code):
        if not logged:
            first_logged.set()
            assert second_started.wait(timeout=10)
        logged.append(code)

    monkeypatch.setattr(auth.log, "warning", record_warning)
    caplog.set_level(logging.WARNING, logger="app.services.auth")

    def second_start():
        assert first_logged.wait(timeout=10)
        second_started.set()
        auth.prepare_bootstrap()

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(auth.prepare_bootstrap)
        second = pool.submit(second_start)
        first.result(timeout=15)
        second.result(timeout=15)
    assert len(logged) == 2 and logged[0] != logged[1]
    with factory() as db:
        assert db.get(AdminBootstrap, 1).code_hash == hashlib.sha256(logged[-1].encode()).hexdigest()
    assert submit_setup(client, logged[0]).status_code == 403
    assert submit_setup(client, logged[1]).status_code == 204


@pytest.mark.parametrize("password", ["A!abcdef", "ABCDEFG!", "Ä€abcdef", "Ä😀abcdef", "A!" + "a" * 254])
def test_new_password_accepts_eight_characters_uppercase_and_special(password):
    auth.validate_password(password)


@pytest.mark.parametrize(("password", "message"), [
    ("A!abcde", "8 bis 256"),
    ("a!bcdefg", "Grossbuchstaben"),
    ("Abcdefgh", "Sonderzeichen"),
    ("Abcdefg ", "Sonderzeichen"),
    ("A!" + "a" * 255, "8 bis 256"),
])
def test_new_password_rejects_missing_requirements_without_echo(password, message):
    with pytest.raises(auth.AuthError, match=message) as error:
        auth.validate_password(password)
    assert password not in str(error.value)


def test_legacy_password_can_still_log_in(environment):
    client, factory, _ = environment
    legacy = "legacy old password"
    salt = bytes(range(16))
    encoded = f"scrypt-v1${salt.hex()}${auth._derive(legacy, salt).hex()}"
    with factory() as db:
        db.add(AdminAccount(id=1, username="admin", password_hash=encoded, revision=1))
        db.commit()
    assert sign_in(client, legacy).status_code == 200


def test_setup_and_password_change_accept_eight_characters(environment, caplog):
    client, _, _ = environment
    code = bootstrap_code(caplog)
    for invalid in ("a!bcdefg", "Abcdefgh"):
        response = submit_setup(client, code, invalid)
        assert response.status_code == 400 and invalid not in response.text
        assert client.get("/api/auth/session").json()["eingerichtet"] is False
    assert submit_setup(client, code, "A!abcdef").status_code == 204
    csrf = sign_in(client, "A!abcdef").json()["csrf_token"]
    headers = {"X-CSRF-Token": csrf}
    for invalid in ("a!bcdefg", "Abcdefg "):
        response = client.post("/api/auth/password", headers=headers,
                               json={"aktuelles_passwort": "A!abcdef", "neues_passwort": invalid})
        assert response.status_code == 400 and invalid not in response.text
        assert client.get("/api/auth/session").json()["angemeldet"] is True
    response = client.post("/api/auth/password", headers=headers,
                           json={"aktuelles_passwort": "A!abcdef", "neues_passwort": "B!abcdef"})
    assert response.status_code == 204
    assert sign_in(client, "A!abcdef").status_code == 401
    assert sign_in(client, "B!abcdef").status_code == 200
