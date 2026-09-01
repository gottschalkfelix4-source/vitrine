"""Tests des Kaltspeichers.

Der wichtigste Teil ist der Direktzugriff: Wenn ``media_data_offset`` falsch
rechnet, liefert der Player stillschweigend Datenmuell statt Video - ein Fehler,
der ohne Test erst beim Zuschauen auffaellt.
"""

from __future__ import annotations

import json
import os
import zipfile
from pathlib import Path

import pytest

from app.services.bundle import (
    BundleError,
    BundleManifest,
    BundleReader,
    bundle_path_for,
    verify_bundle,
    write_bundle,
)


@pytest.fixture
def media(tmp_path: Path) -> Path:
    """Pseudozufaellige Nutzdaten - inkompressibel wie echtes Videomaterial und
    so beschaffen, dass ein Off-by-one im Offset sofort auffaellt."""
    p = tmp_path / "video.mp4"
    p.write_bytes(os.urandom(3 * 1024 * 1024 + 1234))
    return p


@pytest.fixture
def manifest() -> BundleManifest:
    return BundleManifest(
        schema_version=1,
        video_id="dQw4w9WgXcQ",
        channel_id="UCtest",
        title="Ein Test mit Umlauten: Groesse & Aehnliches",
        media_name="",  # wird von write_bundle gesetzt
        media_bytes=0,
        mime_type="",
        duration_s=212.0,
    )


def _build(tmp_path: Path, media: Path, manifest: BundleManifest, **kw) -> Path:
    dest = tmp_path / "out" / "dQw4w9WgXcQ.zip"
    return write_bundle(dest, manifest=manifest, media_file=media, **kw)


def test_medien_werden_unkomprimiert_abgelegt(tmp_path: Path, media: Path, manifest: BundleManifest):
    """STORED ist die Voraussetzung fuer den Direktzugriff - ohne das faellt
    die gesamte Wiedergabearchitektur auf den Entpack-Pfad zurueck."""
    b = _build(tmp_path, media, manifest)
    with zipfile.ZipFile(b) as z:
        info = z.getinfo("media/video.mp4")
        assert info.compress_type == zipfile.ZIP_STORED
        # Textbeiwerk dagegen schon komprimiert
        assert z.getinfo("manifest.json").compress_type == zipfile.ZIP_DEFLATED


def test_direktzugriff_liefert_exakt_die_quelldaten(tmp_path: Path, media: Path, manifest: BundleManifest):
    b = _build(tmp_path, media, manifest)
    original = media.read_bytes()
    with BundleReader(b) as r:
        assert r.media_size == len(original)
        gelesen = b"".join(r.media_range())
    assert gelesen == original


@pytest.mark.parametrize("start,length", [(0, 100), (1024, 4096), (1_500_000, 500_000), (3_000_000, None)])
def test_beliebige_bereiche_stimmen(tmp_path: Path, media: Path, manifest: BundleManifest, start, length):
    b = _build(tmp_path, media, manifest)
    original = media.read_bytes()
    erwartet = original[start:] if length is None else original[start : start + length]
    with BundleReader(b) as r:
        assert b"".join(r.media_range(start, length)) == erwartet


def test_bereich_am_dateiende(tmp_path: Path, media: Path, manifest: BundleManifest):
    """Ein Range bis exakt ans Ende darf nicht ueber den Eintrag hinauslesen -
    sonst landet das ZIP-Zentralverzeichnis im Videostream."""
    b = _build(tmp_path, media, manifest)
    original = media.read_bytes()
    with BundleReader(b) as r:
        letzte = b"".join(r.media_range(len(original) - 10))
        assert letzte == original[-10:]
        # Mehr anfordern als da ist, liefert nur bis zum Ende
        assert b"".join(r.media_range(len(original) - 5, 1000)) == original[-5:]
        # Start genau am Ende: leer, kein Fehler
        assert b"".join(r.media_range(len(original))) == b""


def test_start_ausserhalb_wird_abgewiesen(tmp_path: Path, media: Path, manifest: BundleManifest):
    b = _build(tmp_path, media, manifest)
    with BundleReader(b) as r, pytest.raises(BundleError):
        list(r.media_range(r.media_size + 1))


def test_offset_ist_unabhaengig_von_vorherigen_eintraegen(tmp_path: Path, manifest: BundleManifest):
    """Die Mediendatei liegt hinter Manifest, info.json, Thumbnail und
    Untertiteln. Wird deren Laenge falsch beruecksichtigt, verschiebt sich der
    Offset - genau dagegen sichert dieser Test."""
    media = tmp_path / "mit_sehr_langem_dateinamen_der_den_header_verlaengert.mkv"
    media.write_bytes(os.urandom(1_000_000))
    thumb = tmp_path / "t.jpg"
    thumb.write_bytes(os.urandom(50_000))
    sub_de = tmp_path / "de.vtt"
    sub_de.write_text("WEBVTT\n\n00:00.000 --> 00:02.000\nHallo Welt\n", encoding="utf-8")

    b = _build(
        tmp_path,
        media,
        manifest,
        info_json={"id": "x", "beschreibung": "ae oe ue " * 500},
        thumbnail=thumb,
        subtitles=[("de", False, sub_de)],
    )
    with BundleReader(b) as r:
        assert b"".join(r.media_range()) == media.read_bytes()


def test_manifest_und_beiwerk_lesbar(tmp_path: Path, media: Path, manifest: BundleManifest):
    thumb = tmp_path / "t.webp"
    thumb.write_bytes(b"\x00" * 1000)
    sub_de = tmp_path / "de.vtt"
    sub_de.write_bytes(b"WEBVTT\n")
    sub_en = tmp_path / "en.vtt"
    sub_en.write_bytes(b"WEBVTT\n")

    b = _build(
        tmp_path,
        media,
        manifest,
        info_json={"id": "dQw4w9WgXcQ", "title": "Titel"},
        thumbnail=thumb,
        subtitles=[("de", False, sub_de), ("en", True, sub_en)],
    )
    with BundleReader(b) as r:
        m = r.manifest
        assert m.video_id == "dQw4w9WgXcQ"
        assert m.title.startswith("Ein Test mit Umlauten")
        assert m.mime_type == "video/mp4"
        assert m.thumbnail_name == "thumbnail.webp"
        assert {(s.language, s.is_auto) for s in m.subtitles} == {("de", False), ("en", True)}
        assert r.info_json()["title"] == "Titel"
        assert r.read("subs/en.auto.vtt") == b"WEBVTT\n"


def test_fehlende_untertiteldatei_verschiebt_nichts(tmp_path: Path, media: Path, manifest: BundleManifest):
    """Fehlt eine angekuendigte Untertiteldatei, darf das Buendel trotzdem
    korrekt sein und kein falsches Namenspaar entstehen."""
    da = tmp_path / "de.vtt"
    da.write_bytes(b"WEBVTT\n")
    fehlt = tmp_path / "gibtsnicht.vtt"

    b = _build(tmp_path, media, manifest, subtitles=[("de", False, da), ("fr", False, fehlt)])
    with BundleReader(b) as r:
        assert [s.language for s in r.manifest.subtitles] == ["de"]
        assert r.read("subs/de.orig.vtt") == b"WEBVTT\n"
        assert b"".join(r.media_range()) == media.read_bytes()


def test_abbruch_hinterlaesst_kein_halbes_buendel(tmp_path: Path, manifest: BundleManifest):
    dest = tmp_path / "out" / "kaputt.zip"
    with pytest.raises(BundleError):
        write_bundle(dest, manifest=manifest, media_file=tmp_path / "gibtsnicht.mp4")
    assert not dest.exists()
    assert not dest.with_suffix(".zip.part").exists()


def test_entpacken_ergibt_identische_datei(tmp_path: Path, media: Path, manifest: BundleManifest):
    b = _build(tmp_path, media, manifest)
    ziel = tmp_path / "heiss" / "video.mp4"
    with BundleReader(b) as r:
        r.extract_media(ziel)
    assert ziel.read_bytes() == media.read_bytes()
    assert not ziel.with_suffix(".mp4.part").exists()


def test_verify_erkennt_gutes_und_kaputtes_buendel(tmp_path: Path, media: Path, manifest: BundleManifest):
    b = _build(tmp_path, media, manifest)
    ok, msg = verify_bundle(b)
    assert ok, msg

    kaputt = tmp_path / "kaputt.zip"
    kaputt.write_bytes(b"das ist kein zip")
    ok, msg = verify_bundle(kaputt)
    assert not ok and "BadZipFile" in msg

    ohne_manifest = tmp_path / "ohne.zip"
    with zipfile.ZipFile(ohne_manifest, "w") as z:
        z.writestr("irgendwas.txt", "hallo")
    ok, msg = verify_bundle(ohne_manifest)
    assert not ok


def test_komprimiert_abgelegte_medien_werden_erkannt(tmp_path: Path, manifest: BundleManifest):
    """Ein von Hand oder von aelterer Software gebautes Buendel mit
    DEFLATE-Medien darf nicht stillschweigend Muell liefern."""
    p = tmp_path / "deflate.zip"
    m = BundleManifest(
        schema_version=1, video_id="x", channel_id=None, title="t",
        media_name="media/v.mp4", media_bytes=10, mime_type="video/mp4",
    )
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("manifest.json", m.to_json())
        z.writestr("media/v.mp4", b"x" * 10_000, compress_type=zipfile.ZIP_DEFLATED)
    with BundleReader(p) as r, pytest.raises(BundleError, match="komprimiert"):
        r.media_data_offset()


def test_ablagepfad(tmp_path: Path):
    assert bundle_path_for(tmp_path, "UCabc", "vid1") == tmp_path / "UCabc" / "vid1.zip"
    assert bundle_path_for(tmp_path, None, "vid1") == tmp_path / "_lose" / "vid1.zip"


def test_manifest_rundreise():
    m = BundleManifest(
        schema_version=1, video_id="a", channel_id="c", title="t",
        media_name="media/v.mkv", media_bytes=5, mime_type="video/x-matroska",
        source_bytes=99, recoded=True,
    )
    wieder = BundleManifest.from_dict(json.loads(m.to_json()))
    assert wieder == m


def test_beschaedigtes_buendel_liefert_keine_fremden_bytes(tmp_path: Path, media: Path, manifest: BundleManifest):
    """Wird ein Buendel abgeschnitten, darf der Player nicht stillschweigend
    Teile des ZIP-Zentralverzeichnisses als Videodaten bekommen."""
    b = _build(tmp_path, media, manifest)
    roh = b.read_bytes()
    b.write_bytes(roh[: len(roh) // 2])  # Datei halbieren
    with pytest.raises((BundleError, zipfile.BadZipFile)), BundleReader(b) as r:
        r.media_data_offset()


def test_grossgeschriebene_endung_gilt_auch_als_medium(tmp_path: Path, manifest: BundleManifest):
    """Manche Quellen liefern .MP4 - das muss trotzdem unkomprimiert abgelegt
    werden, sonst faellt der Direktzugriff aus."""
    quelle = tmp_path / "FILM.MP4"
    quelle.write_bytes(os.urandom(50_000))
    b = _build(tmp_path, quelle, manifest)
    with zipfile.ZipFile(b) as z:
        assert z.getinfo("media/FILM.MP4").compress_type == zipfile.ZIP_STORED


def test_nicht_serialisierbare_metadaten_scheitern_vor_dem_schreiben(
    tmp_path: Path, media: Path, manifest: BundleManifest
):
    """Regression: yt-dlp liefert nach einem Download ein Info-Dict mit lebenden
    Python-Objekten. Frueher flog dabei ein nackter TypeError - und zwar erst
    NACH dem vollstaendigen Download. Jetzt gibt es eine verstaendliche Meldung,
    und es bleibt keine halbe Datei zurueck."""

    class Unserialisierbar:
        pass

    dest = tmp_path / "out" / "x.zip"
    with pytest.raises(BundleError, match="sanitize_info"):
        write_bundle(
            dest,
            manifest=manifest,
            media_file=media,
            info_json={"id": "x", "__postprocessors": [Unserialisierbar()]},
        )
    assert not dest.exists()
    assert not dest.with_suffix(".zip.part").exists()
