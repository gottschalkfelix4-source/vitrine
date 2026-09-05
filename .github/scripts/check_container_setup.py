"""HTTP/HTTPS-Zugang und Gastwiedergabe im echten CI-Container."""

import json
import re
import subprocess
import time
from http.cookies import SimpleCookie
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BASE = "http://127.0.0.1:8000"
CONTAINER = "pruefling"
PASSWORD = "Ci!test8"  # Genau acht Zeichen; nur fuer diesen kurzlebigen Testcontainer.


def request(path, payload=None, cookie=None, *, origin=None, extra_headers=None):
    headers = {}
    if payload is not None:
        headers = {"Content-Type": "application/json", "X-Vitrine-Request": "1"}
    if cookie:
        headers["Cookie"] = cookie
    if origin is not None:
        headers["Origin"] = origin
    headers.update(extra_headers or {})
    req = Request(BASE + path, data=None if payload is None else json.dumps(payload).encode(), headers=headers)
    try:
        response = urlopen(req, timeout=60)
    except HTTPError as error:
        response = error
    with response:
        return response.status, response.headers, response.read()


def codes():
    result = subprocess.run(["docker", "logs", CONTAINER], capture_output=True, text=True, check=True)
    return re.findall(r"Vitrine-Einrichtungscode: ([A-Za-z0-9_-]{43})", result.stdout + result.stderr)


def sign_in(secure=False):
    origin = BASE.replace("http:", "https:") if secure else BASE
    status, headers, _ = request("/api/auth/login", {"benutzer": "container-admin", "passwort": PASSWORD}, origin=origin)
    assert status == 200, f"Anmeldung: HTTP {status}"
    parsed = SimpleCookie()
    parsed.load(headers["Set-Cookie"])
    name = "vitrine_session" if secure else "vitrine_session_http"
    session = parsed[name]
    assert bool(session["secure"]) == secure and session["httponly"] and session["samesite"].lower() == "strict"
    cookie = name + "=" + session.value
    status, headers, _ = request("/api/settings", cookie=cookie)
    assert status == 200 and headers["Cache-Control"] == "no-store"
    for path in ("/api/jobs", "/api/jobs/aktiv", "/api/storage"):
        status, headers, _ = request(path, cookie=cookie)
        assert status == 200 and headers["Cache-Control"] == "no-store"
        assert request(path)[0] == 401
    return cookie


def check_playback(admin_cookie):
    # Ausschliesslich im kurzlebigen Testcontainer ein kleines Testbuendel
    # erzeugen. Keine echten Archivdaten oder externen Downloads erforderlich.
    seed = '''
from pathlib import Path
import subprocess
from app.db import session_scope
from app.models import Channel, Video
from app.services.bundle import BundleManifest, write_bundle
from app.services.geoip import locator
geo = locator.lookup('8.8.8.8')
assert geo['status'] == 'located' and geo['country_code'] == 'US'
assert -90 <= geo['latitude'] <= 90 and -180 <= geo['longitude'] <= 180
media = Path('/tmp/vitrine-ci-demo.mp4')
subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'testsrc2=size=160x90:rate=15',
                '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000', '-t', '15',
                '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p', '-c:a', 'aac', str(media)], check=True)
target = Path('/data/bundles/UCci/ci-demo.zip')
manifest = BundleManifest(schema_version=1, video_id='ci-demo', channel_id='UCci', title='CI-Video',
    media_name='', media_bytes=0, mime_type='', video_codec='h264', audio_codec='aac',
    width=160, height=90, duration_s=15)
write_bundle(target, manifest=manifest, media_file=media, root=Path('/data/bundles'))
with session_scope() as db:
    db.add(Channel(id='UCci', name='CI-Kanal', sync_enabled=False, auto_archive=False))
    db.flush()
    db.add(Video(id='ci-demo', channel_id='UCci', title='CI-Video', status='archived',
                 bundle_file=str(target), duration_s=15, video_codec='h264', audio_codec='aac'))
media.unlink()
'''
    subprocess.run(["docker", "exec", CONTAINER, "gosu", "archiv", "python", "-c", seed],
                   check=True, capture_output=True, timeout=60)
    assert json.loads(request("/api/videos")[2])[0]["id"] == "ci-demo"
    assert request("/api/videos/ci-demo/stream", extra_headers={"Range": "bytes=0-99"})[0] == 206
    assert request("/api/streams")[0] == 401
    payload = {"support": "mp4,h264,aac", "force_transcode": True}
    status, _, body = request("/api/videos/ci-demo/playback", payload, origin=BASE)
    assert status == 200
    live = json.loads(body)
    assert live["mode"] == "transcode"
    status, _, body = request(live["url"])
    assert status == 200 and b"#EXT-X-ENDLIST" in body
    # Direkt den letzten Abschnitt abrufen: kein Vollencode vor dem Spulen.
    path = "/api/playback/" + live["token"]
    status, _, data = request(path + "/segments/2.ts")
    assert status == 200 and len(data) > 188 and data[0] == 0x47
    assert request(path + "/heartbeat", {"position_s": 13, "state": "playing"}, origin=BASE)[0] == 204
    overview = json.loads(request("/api/streams", cookie=admin_cookie)[2])
    assert overview["geoip"]["available"] is True and overview["geoip"]["database_date"]
    streams = overview["streams"]
    assert len(streams) == 1 and streams[0]["position_s"] == 13 and streams[0]["segments_ready"] == 1
    assert streams[0]["geo"]["status"] == "private"
    assert streams[0]["geo"]["latitude"] is None and streams[0]["geo"]["longitude"] is None
    assert live["token"] not in json.dumps(streams)
    assert request(path + "/ended", {}, origin=BASE)[0] == 204
    assert json.loads(request("/api/streams", cookie=admin_cookie)[2])["streams"] == []


def main():
    initial = codes()
    assert len(initial) == 1, "Genau ein Einrichtungscode muss beim ersten Start erscheinen"
    status, _, body = request("/api/auth/session")
    assert status == 200 and json.loads(body)["eingerichtet"] is False
    assert initial[0].encode() not in body, "Sitzungsstatus darf den Einrichtungscode nicht enthalten"
    payload = {"einrichtungscode": "falsch", "benutzer": "container-admin", "passwort": PASSWORD}
    assert request("/api/auth/setup", payload, origin=BASE)[0] == 403
    payload["einrichtungscode"] = initial[0]
    assert request("/api/auth/setup", payload, origin="http://fremd.example")[0] == 403
    # Echte Browser senden einen Origin-Header. Trotz Secure-Cookie-Vorgabe
    # muss die Ersteinrichtung ueber die direkte HTTP-Adresse funktionieren.
    status, headers, body = request("/api/auth/setup", payload, origin=BASE)
    assert status == 204 and not body and "Set-Cookie" not in headers
    assert request("/api/auth/setup", payload, origin=BASE)[0] == 409
    assert request("/api/channels")[0] == 200
    assert request("/api/settings")[0] == 401  # Einrichtung ist keine Anmeldung.
    assert request("/api/streams")[0] == 401
    check_playback(sign_in())
    sign_in(secure=True)  # HTTPS-Origin hinter einem intern per HTTP angebundenen Proxy.

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
    assert request("/api/auth/setup", payload, origin=BASE)[0] == 409
    sign_in()
    print("Gastarchiv, HTTP/HTTPS-Anmeldung, Live-Transkodierung, Stream-Dashboard, lokale GeoIP-Datenbank und Neustart erfolgreich.")


if __name__ == "__main__":
    main()
