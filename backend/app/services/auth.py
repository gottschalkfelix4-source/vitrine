"""Lokale Administratoridentitaet und widerrufbare Serversitzungen."""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
import secrets
import sys
import threading
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from app import db as database
from app.config import settings
from app.models import AdminAccount, AdminBootstrap, AdminLoginLimit, AdminSession

COOKIE_NAME = "vitrine_session"
MIN_PASSWORD = 8
MAX_PASSWORD = 256
LOGIN_ATTEMPTS = 10
LOGIN_WINDOW_SECONDS = 900
_HASH_SLOTS = threading.BoundedSemaphore(2)
_TOKEN = re.compile(r"^[A-Za-z0-9_-]{43}$")
_USERNAME = re.compile(r"^[A-Za-z0-9_.@-]{1,64}$")
_SCRYPT_N = 2**15
log = logging.getLogger(__name__)


class AuthError(ValueError):
    def __init__(self, message: str, status: int = 401):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class SessionIdentity:
    username: str
    token_hash: str
    csrf_token: str
    revision: int
    expires_at: datetime


def now() -> datetime:
    return datetime.now(UTC)


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _derive(password: str, salt: bytes) -> bytes:
    try:
        encoded = password.encode("utf-8")
    except UnicodeEncodeError:
        # JSON kann einzelne UTF-16-Surrogate transportieren; weder Hashen
        # noch Fehlerprotokolle sollen an diesen Eingaben scheitern.
        raise AuthError("Das Passwort enthaelt ungueltige Zeichen.", 400) from None
    return hashlib.scrypt(
        encoded, salt=salt, n=_SCRYPT_N, r=8, p=3,
        maxmem=64 * 1024 * 1024, dklen=32,
    )


def validate_password(password: str) -> None:
    if not MIN_PASSWORD <= len(password) <= MAX_PASSWORD:
        raise AuthError(f"Das Passwort muss {MIN_PASSWORD} bis {MAX_PASSWORD} Zeichen lang sein.", 400)
    if not any(unicodedata.category(char) == "Lu" for char in password):
        raise AuthError("Das Passwort muss mindestens einen Grossbuchstaben enthalten.", 400)
    if not any(unicodedata.category(char)[0] in {"P", "S"} for char in password):
        raise AuthError("Das Passwort muss mindestens ein Sonderzeichen enthalten.", 400)


def hash_password(password: str) -> str:
    validate_password(password)
    salt = secrets.token_bytes(16)
    return f"scrypt-v1${salt.hex()}${_derive(password, salt).hex()}"


def _verify(password: str, encoded: str | None) -> bool:
    # Gleicher Aufwand fuer unbekannte Benutzer; keine Klartext-Dummy-Zugangsdaten.
    salt, expected = bytes(16), bytes(32)
    valid = False
    if encoded:
        try:
            version, salt_hex, digest_hex = encoded.split("$")
            if version == "scrypt-v1":
                salt, expected = bytes.fromhex(salt_hex), bytes.fromhex(digest_hex)
                valid = len(salt) == 16 and len(expected) == 32
        except ValueError:
            pass
    actual = _derive(password, salt if len(salt) == 16 else bytes(16))
    return hmac.compare_digest(actual, expected) and valid


def configured() -> bool:
    with database.SessionLocal() as db:
        return db.get(AdminAccount, 1) is not None


def prepare_bootstrap() -> None:
    """Rotiert beim Appstart den nur lokal ausgegebenen Eigentumsnachweis."""
    with database.SessionLocal() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        db.execute(delete(AdminBootstrap))
        if db.get(AdminAccount, 1) is not None:
            db.commit()
            return
        code = secrets.token_urlsafe(32)
        db.add(AdminBootstrap(id=1, code_hash=hashlib.sha256(code.encode("ascii")).hexdigest()))
        db.flush()
        # Ausgabe noch innerhalb der Schreibtransaktion: Bei gleichzeitigen
        # Starts gehoert die zuletzt ausgegebene Zeile auch zur letzten Rotation.
        # Ein anschliessender CLI-Reset macht den Code nur ungueltig, nie umgekehrt.
        if log.isEnabledFor(logging.WARNING):
            log.warning("Vitrine-Einrichtungscode: %s", code)
        else:
            # Dieser einmalige Eigentumsnachweis muss auch bei LOG_LEVEL=ERROR
            # im lokalen Container-Log auffindbar bleiben, ohne doppelte Ausgabe.
            print(f"Vitrine-Einrichtungscode: {code}", file=sys.stderr, flush=True)
        db.commit()


def _check_bootstrap(db: Session, digest: str | None) -> None:
    if db.get(AdminAccount, 1) is not None:
        raise AuthError("Der Administrator ist bereits eingerichtet. Bitte anmelden.", 409)
    bootstrap = db.get(AdminBootstrap, 1)
    if (digest is None or bootstrap is None
            or not hmac.compare_digest(bootstrap.code_hash, digest)):
        raise AuthError("Der Einrichtungscode ist falsch oder abgelaufen. "
                        "Bitte den aktuellen Code aus dem Container-Log verwenden.", 403)


def validate_username(username: str) -> None:
    if not _USERNAME.fullmatch(username):
        raise AuthError("Benutzername: 1 bis 64 Zeichen, nur Buchstaben, Zahlen und . _ @ -.", 400)


def complete_setup(code: str, username: str, password: str) -> None:
    digest = hashlib.sha256(code.encode("ascii")).hexdigest() if _TOKEN.fullmatch(code) else None
    # Falsche Codes werden ohne scrypt und ohne globale, fremd ausloesbare
    # Einrichtungssperre abgewiesen. Der Code besitzt 256 Bit Zufallsentropie.
    with database.SessionLocal() as db:
        _check_bootstrap(db, digest)
    validate_username(username)
    validate_password(password)
    if not _HASH_SLOTS.acquire(blocking=False):
        raise AuthError("Die Einrichtung ist gerade ausgelastet. Bitte erneut versuchen.", 429)
    try:
        encoded = hash_password(password)
        with database.SessionLocal() as db:
            db.execute(text("BEGIN IMMEDIATE"))
            # Nach dem Hashen nochmals pruefen: Ein paralleler Submit, CLI-Reset
            # oder Appstart darf weder ueberschrieben noch rueckgaengig werden.
            _check_bootstrap(db, digest)
            db.add(AdminAccount(id=1, username=username, password_hash=encoded, revision=1))
            db.execute(delete(AdminBootstrap))
            db.execute(delete(AdminSession))
            db.execute(delete(AdminLoginLimit))
            db.commit()
    finally:
        _HASH_SLOTS.release()


def session_for(token: str | None) -> SessionIdentity | None:
    if token is None or not _TOKEN.fullmatch(token):
        return None
    digest = hashlib.sha256(token.encode("ascii")).hexdigest()
    with database.SessionLocal() as db:
        session = db.get(AdminSession, digest)
        account = db.get(AdminAccount, 1)
        if (session is None or account is None or session.account_revision != account.revision
                or _utc(session.expires_at) <= now()):
            return None
        return SessionIdentity(
            account.username, digest, session.csrf_token, account.revision, _utc(session.expires_at)
        )


def status(identity: SessionIdentity | None) -> dict[str, object]:
    return {
        "eingerichtet": configured(), "angemeldet": identity is not None,
        "benutzer": identity.username if identity else None,
        "csrf_token": identity.csrf_token if identity else None,
    }


def _reserve_attempt() -> tuple[str, str, int]:
    with database.SessionLocal() as db:
        # Reservierung VOR teurem Hashen; serialisiert auch mehrere Prozesse.
        db.execute(text("BEGIN IMMEDIATE"))
        account = db.get(AdminAccount, 1)
        if account is None:
            raise AuthError("Der Administrator muss zuerst eingerichtet werden.", 503)
        clock = now()
        limit = db.get(AdminLoginLimit, 1)
        if limit is None:
            limit = AdminLoginLimit(id=1, window_started=clock, attempts=0)
            db.add(limit)
        elif _utc(limit.window_started) + timedelta(seconds=LOGIN_WINDOW_SECONDS) <= clock:
            limit.window_started, limit.attempts = clock, 0
        if limit.attempts >= LOGIN_ATTEMPTS:
            raise AuthError("Zu viele Anmeldeversuche. Bitte in 15 Minuten erneut versuchen.", 429)
        limit.attempts += 1
        snapshot = account.username, account.password_hash, account.revision
        db.commit()
        return snapshot


def login(username: str, password: str) -> tuple[str, SessionIdentity]:
    if len(username) > 64 or len(password) > MAX_PASSWORD:
        raise AuthError("Benutzername oder Passwort ist falsch.")
    if not _HASH_SLOTS.acquire(blocking=False):
        raise AuthError("Die Anmeldung ist gerade ausgelastet. Bitte erneut versuchen.", 429)
    try:
        saved_name, encoded, revision = _reserve_attempt()
        correct = _verify(password, encoded if username == saved_name else None)
        if not correct or username != saved_name:
            raise AuthError("Benutzername oder Passwort ist falsch.")
        token = secrets.token_urlsafe(32)
        digest = hashlib.sha256(token.encode("ascii")).hexdigest()
        csrf = secrets.token_urlsafe(32)
        clock = now()
        expires = clock + timedelta(hours=settings.auth_session_hours)
        with database.SessionLocal() as db:
            db.execute(text("BEGIN IMMEDIATE"))
            account = db.get(AdminAccount, 1)
            # Ein gleichzeitiger CLI-/Passwort-Reset darf keine alte Sitzung wiederbeleben.
            if account is None or account.revision != revision or account.password_hash != encoded:
                raise AuthError("Die Zugangsdaten wurden geaendert. Bitte erneut anmelden.")
            db.execute(delete(AdminSession).where(AdminSession.expires_at <= clock))
            existing = list(db.scalars(select(AdminSession).order_by(AdminSession.created_at)))
            for old in existing[:max(0, len(existing) - 9)]:
                db.delete(old)
            db.add(AdminSession(token_hash=digest, csrf_token=csrf, account_revision=revision,
                                created_at=clock, expires_at=expires))
            limit = db.get(AdminLoginLimit, 1)
            if limit is not None:
                limit.attempts = 0
            db.commit()
        return token, SessionIdentity(saved_name, digest, csrf, revision, expires)
    finally:
        _HASH_SLOTS.release()


def logout(identity: SessionIdentity) -> None:
    with database.SessionLocal() as db:
        db.execute(delete(AdminSession).where(AdminSession.token_hash == identity.token_hash))
        db.commit()


def set_account(username: str, password: str, *, expected_revision: int | None = None) -> None:
    validate_username(username)
    encoded = hash_password(password)
    with database.SessionLocal() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        account = db.get(AdminAccount, 1)
        if expected_revision is not None and (account is None or account.revision != expected_revision):
            raise AuthError("Die Zugangsdaten wurden zwischenzeitlich geaendert.")
        if account is None:
            db.add(AdminAccount(id=1, username=username, password_hash=encoded, revision=1))
        else:
            account.username, account.password_hash = username, encoded
            account.revision += 1
        db.execute(delete(AdminSession))
        db.execute(delete(AdminLoginLimit))
        db.execute(delete(AdminBootstrap))
        db.commit()


def change_password(identity: SessionIdentity, current: str, new: str) -> None:
    validate_password(new)
    if len(current) > MAX_PASSWORD:
        raise AuthError("Das aktuelle Passwort ist falsch.", 400)
    if not _HASH_SLOTS.acquire(blocking=False):
        raise AuthError("Die Anmeldung ist gerade ausgelastet. Bitte erneut versuchen.", 429)
    try:
        username, encoded, revision = _reserve_attempt()
        if revision != identity.revision or not _verify(current, encoded):
            raise AuthError("Das aktuelle Passwort ist falsch.", 400)
        set_account(username, new, expected_revision=revision)
    finally:
        _HASH_SLOTS.release()
