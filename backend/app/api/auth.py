"""Anmeldung fuer das ausschliesslich lokal eingerichtete Administratorkonto."""

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from app.config import settings
from app.services import auth

router = APIRouter(prefix="/api/auth", tags=["anmeldung"])


class Login(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    benutzer: str = Field(min_length=1, max_length=64)
    passwort: str = Field(min_length=1, max_length=auth.MAX_PASSWORD)


class PasswordChange(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    aktuelles_passwort: str = Field(min_length=1, max_length=auth.MAX_PASSWORD)
    neues_passwort: str = Field(min_length=auth.MIN_PASSWORD, max_length=auth.MAX_PASSWORD)


def clear_cookie(response: Response) -> None:
    response.headers["Clear-Site-Data"] = '"cache"'
    response.delete_cookie(auth.COOKIE_NAME, path="/", secure=settings.auth_cookie_secure,
                           httponly=True, samesite="strict")


@router.get("/session")
def session(request: Request, response: Response) -> dict[str, object]:
    # Alte Versionen lieferten Bilder mit public/max-age. Neue no-store-Antworten
    # entfernen diese vorhandenen HTTP-Cache-Eintraege nicht rueckwirkend.
    if request.cookies.get("vitrine_cache_policy") != "v2":
        response.headers["Clear-Site-Data"] = '"cache"'
        response.set_cookie("vitrine_cache_policy", "v2", max_age=31536000, path="/",
                            secure=settings.auth_cookie_secure, httponly=True, samesite="strict")
    return auth.status(request.state.admin_session)


@router.post("/login")
def login(daten: Login, response: Response) -> dict[str, object]:
    try:
        token, identity = auth.login(daten.benutzer, daten.passwort)
    except auth.AuthError as error:
        raise HTTPException(error.status, str(error)) from error
    response.set_cookie(
        auth.COOKIE_NAME, token, max_age=settings.auth_session_hours * 3600,
        expires=identity.expires_at, path="/", secure=settings.auth_cookie_secure,
        httponly=True, samesite="strict",
    )
    return auth.status(identity)


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response) -> None:
    auth.logout(request.state.admin_session)
    clear_cookie(response)


@router.post("/password", status_code=204)
def password(daten: PasswordChange, request: Request, response: Response) -> None:
    try:
        auth.change_password(request.state.admin_session, daten.aktuelles_passwort, daten.neues_passwort)
    except auth.AuthError as error:
        raise HTTPException(error.status, str(error)) from error
    clear_cookie(response)
