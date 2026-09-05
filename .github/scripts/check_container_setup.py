"""Ersteinrichtung im echten CI-Container; keine Zugangsdaten ausgeben."""

import json
import re
import subprocess
import time
from http.cookies import SimpleCookie
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BASE = "http://127.0.0.1:8000"
CONTAINER = "pruefling"
PASSWORD = "CI temporary setup password 2026!"


def request(path, payload=None, cookie=None):
    headers = {}
    if payload is not None:
        headers = {"Content-Type": "application/json", "X-Vitrine-Request": "1"}
    if cookie:
        headers["Cookie"] = cookie
    req = Request(BASE + path, data=None if payload is None else json.dumps(payload).encode(), headers=headers)
    try:
        response = urlopen(req, timeout=5)
    except HTTPError as error:
        response = error
    with response:
        return response.status, response.headers, response.read()


def codes():
    result = subprocess.run(["docker", "logs", CONTAINER], capture_output=True, text=True, check=True)
    return re.findall(r"Vitrine-Einrichtungscode: ([A-Za-z0-9_-]{43})", result.stdout + result.stderr)


def sign_in():
    status, headers, _ = request("/api/auth/login", {"benutzer": "container-admin", "passwort": PASSWORD})
    assert status == 200, f"Anmeldung: HTTP {status}"
    parsed = SimpleCookie()
    parsed.load(headers["Set-Cookie"])
    session = parsed["vitrine_session"]
    assert session["secure"] and session["httponly"] and session["samesite"].lower() == "strict"
    # Die API-Pruefung laeuft nur auf Runner-Loopback. Browser/TLS-Verhalten
    # wird separat getestet; hier das Secure-Cookie ausdruecklich weitergeben.
    cookie = "vitrine_session=" + session.value
    status, headers, _ = request("/api/channels", cookie=cookie)
    assert status == 200 and headers["Cache-Control"] == "no-store"


def main():
    initial = codes()
    assert len(initial) == 1, "Genau ein Einrichtungscode muss beim ersten Start erscheinen"
    status, _, body = request("/api/auth/session")
    assert status == 200 and json.loads(body)["eingerichtet"] is False
    assert initial[0].encode() not in body, "Sitzungsstatus darf den Einrichtungscode nicht enthalten"
    payload = {"einrichtungscode": "falsch", "benutzer": "container-admin", "passwort": PASSWORD}
    assert request("/api/auth/setup", payload)[0] == 403
    payload["einrichtungscode"] = initial[0]
    assert request("/api/auth/setup", payload)[0] == 204
    assert request("/api/auth/setup", payload)[0] == 409
    assert request("/api/channels")[0] == 401  # Einrichtung ist keine Anmeldung.
    sign_in()

    subprocess.run(["docker", "restart", CONTAINER], capture_output=True, check=True, timeout=60)
    for _ in range(40):
        try:
            if request("/api/health")[0] == 200:
                break
        except (URLError, TimeoutError, OSError):
            pass
        time.sleep(0.5)
    else:
        raise AssertionError("Container nach Neustart nicht bereit")
    assert codes() == initial, "Vorhandener Administrator darf keinen neuen Einrichtungscode erhalten"
    assert json.loads(request("/api/auth/session")[2])["eingerichtet"] is True
    assert request("/api/auth/setup", payload)[0] == 409
    sign_in()
    print("Ersteinrichtung, Einmaligkeit, Anmeldung und Neustart mit bestehendem Konto erfolgreich.")


if __name__ == "__main__":
    main()
