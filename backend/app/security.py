"""ASGI-Grenze vor Routern, Upload-Parsern und Medienzugriffen."""

from __future__ import annotations

import hmac
import logging
import re
from urllib.parse import urlsplit

from starlette._utils import get_route_path
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import Headers, MutableHeaders
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.services import auth

BODY_LIMIT = 2 * 1024 * 1024
LOGIN_BODY_LIMIT = 16 * 1024
_SAFE = {"GET", "HEAD", "OPTIONS"}
_PUBLIC = {("GET", "/api/health"), ("GET", "/api/auth/session"),
           ("POST", "/api/auth/login"), ("POST", "/api/auth/setup")}
_CREDENTIAL_PATHS = {"/api/auth/login", "/api/auth/setup"}
_PUBLIC_READ = re.compile(
    r"/api/(?:channels(?:/[^/]+)?|playlists/[^/]+|videos(?:/[^/]+(?:/(?:stream|playback-state|subtitles/[^/]+))?)?"
    r"|search|thumbs/(?:quelle/)?[^/]+"
    r"|playback/[A-Za-z0-9_-]{43}/(?:index\.m3u8|segments/[0-9]+\.ts))"
)
_PUBLIC_PLAYBACK = re.compile(
    r"/api/(?:videos/[^/]+/playback|playback/[A-Za-z0-9_-]{43}/(?:heartbeat|ended))"
)
_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; media-src 'self' blob:; connect-src 'self'; "
    "font-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; "
    "form-action 'self'; worker-src 'self'; manifest-src 'self'"
)
log = logging.getLogger(__name__)


def same_origin(headers: Headers) -> bool:
    if headers.get("sec-fetch-site", "").lower() == "cross-site":
        return False
    origin = headers.get("origin")
    if origin is None:
        # CLI-Aufrufe haben keinen Browser-Origin. Sie brauchen dennoch CSRF
        # bzw. den absichtlich nicht per HTML-Formular setzbaren Login-Header.
        return True
    try:
        parsed = urlsplit(origin)
        expected = urlsplit("//" + headers.get("host", ""))
        return (
            parsed.scheme in {"http", "https"} and not parsed.username and not parsed.password
            and parsed.path in ("", "/") and not parsed.query and not parsed.fragment
            and parsed.hostname is not None and parsed.hostname == expected.hostname
            and (parsed.port or (443 if parsed.scheme == "https" else 80))
            == (expected.port or (443 if parsed.scheme == "https" else 80))
        )
    except ValueError:
        return False


def cookie_secure(request: Request) -> bool:
    """Browser-Origin erkennt TLS-Terminierung ohne rohe Proxyheader zu vertrauen."""
    origin = request.headers.get("origin")
    if origin is not None and same_origin(request.headers):
        return urlsplit(origin).scheme == "https"
    return request.url.scheme == "https"


class SecurityMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        # Dieselbe Normalisierung wie Starlettes Router, auch bei ASGI root_path.
        path, method = get_route_path(scope), scope["method"]
        api = path == "/api" or path.startswith("/api/")
        started = False

        async def secure_send(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                headers = MutableHeaders(scope=message)
                headers["Content-Security-Policy"] = _CSP
                headers["X-Content-Type-Options"] = "nosniff"
                headers["Referrer-Policy"] = "no-referrer"
                headers["X-Frame-Options"] = "DENY"
                if api:
                    headers["Cache-Control"] = "no-store"
                    headers["Pragma"] = "no-cache"
            await send(message)

        async def reject(code: int, message: str) -> None:
            await JSONResponse({"detail": message}, status_code=code)(scope, receive, secure_send)

        headers = Headers(scope=scope)
        if api:
            credential_request = method == "POST" and path in _CREDENTIAL_PATHS
            public_playback = method == "POST" and _PUBLIC_PLAYBACK.fullmatch(path) is not None
            public_read = method in {"GET", "HEAD"} and _PUBLIC_READ.fullmatch(path) is not None
            if method not in _SAFE and not same_origin(headers):
                await reject(403, "Anfragen von einer fremden Herkunft sind nicht erlaubt.")
                return
            if credential_request and (headers.get("x-vitrine-request") != "1"
                          or headers.get("content-type", "").split(";", 1)[0].strip().lower()
                          != "application/json"):
                await reject(403, "Die Anmeldung muss aus der Anwendung erfolgen.")
                return
            if public_playback and (headers.get("x-vitrine-request") != "1"
                    or (not path.endswith("/ended") and
                        headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json")):
                await reject(403, "Die Wiedergabe muss aus der Anwendung erfolgen.")
                return
            request = Request(scope)
            # Secure-Cookies koennen auf HTTP nicht ueberschrieben werden. Beide
            # Protokolle erhalten daher eigene Namen; HTTPS hat bei beiden Vorrang.
            identities = []
            for name in (auth.COOKIE_NAME, auth.HTTP_COOKIE_NAME):
                candidate = await run_in_threadpool(auth.session_for, request.cookies.get(name))
                if candidate is not None:
                    identities.append(candidate)
            identity = identities[0] if identities else None
            scope.setdefault("state", {})["admin_session"] = identity
            scope["state"]["admin_sessions"] = identities
            if (method, path) not in _PUBLIC and not public_read and not public_playback:
                if identity is None:
                    await reject(401, "Bitte als Administrator anmelden.")
                    return
                if method not in _SAFE:
                    token = headers.get("x-csrf-token", "")
                    if not hmac.compare_digest(token.encode("utf-8"), identity.csrf_token.encode("ascii")):
                        await reject(403, "Die Sicherheitskennung fehlt oder ist ungueltig.")
                        return

        limit = LOGIN_BODY_LIMIT if path in _CREDENTIAL_PATHS else BODY_LIMIT
        length = headers.get("content-length")
        if length is not None:
            try:
                declared = int(length)
            except ValueError:
                await reject(400, "Ungueltige Inhaltslaenge.")
                return
            if declared < 0:
                await reject(400, "Ungueltige Inhaltslaenge.")
                return
            if declared > limit:
                await reject(413, "Die Anfrage ist zu gross.")
                return

        # Erst nach der Berechtigungspruefung lesen. Maximal limit Bytes behalten
        # und erst vollstaendig gepruefte Daten an den Multipart-Parser geben.
        chunks = bytearray()
        size = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body = message.get("body", b"")
            size += len(body)
            if size > limit:
                await reject(413, "Die Anfrage ist zu gross.")
                return
            chunks.extend(body)
            if not message.get("more_body", False):
                break
        buffered = bytes(chunks)
        chunks.clear()
        delivered = False

        async def bounded_receive() -> Message:
            nonlocal delivered, buffered
            if not delivered:
                delivered = True
                body, buffered = buffered, b""
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        try:
            await self.app(scope, bounded_receive, secure_send)
        except Exception:
            if started:
                raise
            log.exception("Anfrage fehlgeschlagen")
            await reject(500, "Interner Serverfehler.")
