"""Kaltspeicher: ein ZIP-Buendel je Video.

Aufbau eines Buendels::

    manifest.json        eigene Metadaten (Schema-Version, Codecs, Groessen)
    info.json            unveraenderte yt-dlp-Metadaten
    media/<name>         die Mediendatei                       -- STORED
    thumbnail.<ext>      Vorschaubild                          -- STORED
    subs/<lang>.vtt      Untertitel                            -- DEFLATE

Warum ZIP, obwohl Kompression bei Video nichts bringt (gemessen: 0,01 % bei
H.264): Das Buendel ist hier ein *Behaelter*, keine Kompression. Es haelt Video,
Metadaten, Vorschaubild und Untertitel als eine einzige, in sich geschlossene
Datei zusammen - gut fuers Backup, gut fuer ein Array mit vielen Dateien, und
ein Video ist entweder ganz da oder gar nicht. Die eigentliche Platzersparnis
kommt aus der Recodierung nach AV1, nicht aus dem ZIP.

Der entscheidende Trick: Die Mediendatei wird unkomprimiert (STORED) abgelegt.
Dadurch liegen ihre Bytes zusammenhaengend und unveraendert in der ZIP-Datei und
lassen sich mit einem einzigen Datei-Seek an beliebiger Stelle lesen. Damit kann
der Player Range-Requests direkt gegen das Buendel fahren, ohne vorher etwas zu
entpacken.

Achtung, Fallstrick: ``zipfile.ZipExtFile.seek()`` taugt dafuer nicht - es liest
bis zur Zielposition durch (gemessen 53 ms fuer 28 MB, linear mit der Groesse).
:func:`BundleReader.media_data_offset` berechnet den Offset stattdessen einmal
aus dem lokalen Dateikopf; der Zugriff kostet dann rund 0,5 ms, unabhaengig von
der Sprungweite.
"""

from __future__ import annotations

import json
import logging
import shutil
import struct
import zipfile
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import IO, Any

from app.services import paths

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1

MANIFEST_NAME = "manifest.json"
INFO_NAME = "info.json"
MEDIA_PREFIX = "media/"
SUBS_PREFIX = "subs/"
THUMB_STEM = "thumbnail"

#: Bereits komprimierte Formate nochmal durch DEFLATE zu schicken kostet CPU
#: und bringt nichts. Fuer diese Endungen wird immer STORED verwendet.
_INCOMPRESSIBLE = {
    ".mp4", ".mkv", ".webm", ".m4a", ".mp3", ".opus", ".ogg", ".aac", ".flac",
    ".jpg", ".jpeg", ".png", ".webp", ".avif", ".gif",
}

_LOCAL_HEADER_SIG = 0x04034B50
_LOCAL_HEADER_STRUCT = struct.Struct("<IHHHHHIIIHH")


class BundleError(RuntimeError):
    pass


def _compress_type_for(name: str) -> int:
    suffix = Path(name).suffix.lower()
    return zipfile.ZIP_STORED if suffix in _INCOMPRESSIBLE else zipfile.ZIP_DEFLATED


@dataclass(slots=True)
class SubtitleEntry:
    language: str
    is_auto: bool
    name_in_bundle: str


@dataclass(slots=True)
class BundleManifest:
    """Das, was wir ueber ein Buendel wissen, ohne es zu oeffnen."""

    schema_version: int
    video_id: str
    channel_id: str | None
    title: str
    #: Pfad der Mediendatei innerhalb des Buendels, inkl. ``media/``-Praefix.
    media_name: str
    media_bytes: int
    mime_type: str
    #: Groesse der Originaldatei vor der Recodierung, fuer die Ersparnis-Anzeige.
    source_bytes: int | None = None
    recoded: bool = False
    video_codec: str | None = None
    audio_codec: str | None = None
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    duration_s: float | None = None
    thumbnail_name: str | None = None
    subtitles: list[SubtitleEntry] = field(default_factory=list)
    created_at: str | None = None

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=2)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> BundleManifest:
        subs = [SubtitleEntry(**s) for s in d.get("subtitles", [])]
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        payload = {k: v for k, v in d.items() if k in known and k != "subtitles"}
        return cls(subtitles=subs, **payload)


def bundle_path_for(root: Path, channel_id: str | None, video_id: str) -> Path:
    """Ablageort eines Buendels.

    Nach Kanal gruppiert, damit sich ein Kanal im Dateisystem als ein
    Verzeichnis loeschen oder verschieben laesst.
    """
    channel_dir = paths.child(root, channel_id if channel_id is not None else "_lose")
    return paths.child(channel_dir, f"{paths.component(video_id)}.zip")


def mime_for(name: str) -> str:
    return {
        ".mp4": "video/mp4",
        ".mkv": "video/x-matroska",
        ".webm": "video/webm",
        ".m4a": "audio/mp4",
        ".opus": "audio/ogg",
        ".ogg": "audio/ogg",
        ".mp3": "audio/mpeg",
    }.get(Path(name).suffix.lower(), "application/octet-stream")


# ------------------------------------------------------------------ Schreiben


def write_bundle(
    dest: Path,
    *,
    manifest: BundleManifest,
    media_file: Path,
    info_json: dict[str, Any] | None = None,
    thumbnail: Path | None = None,
    subtitles: list[tuple[str, bool, Path]] | None = None,
    root: Path | None = None,
) -> Path:
    """Schreibt ein vollstaendiges Buendel.

    Es wird zuerst in eine ``.part``-Datei geschrieben und erst am Ende an den
    Zielnamen umbenannt. Bricht der Vorgang ab (Absturz, Stromausfall), bleibt
    kein halbes Buendel zurueck, das spaeter faelschlich als gueltig gilt.

    ``subtitles`` ist eine Liste aus ``(sprache, ist_automatisch, pfad)``.
    """
    if not media_file.is_file():
        raise BundleError(f"Mediendatei fehlt: {media_file}")

    # Die Metadaten frueh pruefen, bevor irgendetwas geschrieben wird.
    #
    # Hintergrund: yt-dlp liefert nach einem Download ein Info-Dict, das lebende
    # Python-Objekte enthaelt (die ffmpeg-Nachbearbeiter unter
    # "__postprocessors"). Ohne sanitize_info fliegt hier ein TypeError - und
    # zwar erst, nachdem das Video vollstaendig heruntergeladen wurde. Diese
    # Vorpruefung macht daraus wenigstens eine verstaendliche Meldung.
    if info_json is not None:
        try:
            vorgemerkte_metadaten = json.dumps(info_json, ensure_ascii=False)
        except (TypeError, ValueError) as e:
            raise BundleError(
                f"Metadaten von {manifest.video_id} sind nicht als JSON darstellbar: {e}. "
                "Bei yt-dlp-Ergebnissen hilft YoutubeDL.sanitize_info()."
            ) from e
    else:
        vorgemerkte_metadaten = None

    # Produktive Aufrufer uebergeben die konfigurierte Ablage als Wurzel.
    # Auch die temporaere Datei muss vor der ersten Aenderung geprueft sein.
    root = root if root is not None else dest.parent
    dest = paths.contained(root, dest)
    tmp = paths.contained(root, dest.with_suffix(dest.suffix + ".part"))
    dest.parent.mkdir(parents=True, exist_ok=True)
    if tmp.exists():
        tmp.unlink()

    media_name = f"{MEDIA_PREFIX}{media_file.name}"
    manifest.media_name = media_name
    manifest.media_bytes = media_file.stat().st_size
    manifest.mime_type = mime_for(media_file.name)
    manifest.schema_version = SCHEMA_VERSION

    # Fehlende Untertiteldateien hier herausfiltern, damit Eintraege und Pfade
    # paarweise zusammenbleiben - sonst schreiben wir spaeter den falschen Namen.
    present_subs = [(lang, is_auto, p) for lang, is_auto, p in (subtitles or []) if p.is_file()]
    sub_entries = [
        SubtitleEntry(
            language=lang,
            is_auto=is_auto,
            name_in_bundle=f"{SUBS_PREFIX}{lang}.{'auto' if is_auto else 'orig'}{p.suffix}",
        )
        for lang, is_auto, p in present_subs
    ]
    manifest.subtitles = sub_entries

    thumb_name: str | None = None
    if thumbnail and thumbnail.is_file():
        thumb_name = f"{THUMB_STEM}{thumbnail.suffix.lower()}"
    manifest.thumbnail_name = thumb_name

    try:
        # allowZip64 ist Standard, aber hier bewusst explizit: Videos ueber 4 GB
        # sind bei langen Streams keine Seltenheit.
        with zipfile.ZipFile(tmp, "w", allowZip64=True) as z:
            # Manifest zuerst, damit es ohne weites Springen lesbar ist.
            z.writestr(MANIFEST_NAME, manifest.to_json(), compress_type=zipfile.ZIP_DEFLATED)
            if vorgemerkte_metadaten is not None:
                z.writestr(INFO_NAME, vorgemerkte_metadaten, compress_type=zipfile.ZIP_DEFLATED)
            if thumbnail and thumb_name:
                z.write(thumbnail, thumb_name, compress_type=zipfile.ZIP_STORED)
            for entry, (_lang, _auto, path) in zip(sub_entries, present_subs, strict=True):
                z.write(path, entry.name_in_bundle, compress_type=zipfile.ZIP_DEFLATED)
            # Medien zuletzt und immer STORED - siehe Modulkopf.
            z.write(media_file, media_name, compress_type=zipfile.ZIP_STORED)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise

    tmp.replace(dest)
    return dest


# --------------------------------------------------------------------- Lesen


class BundleReader:
    """Lesender Zugriff auf ein Buendel.

    Als Kontextmanager benutzen; haelt intern die ZIP-Struktur offen.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._zip: zipfile.ZipFile | None = None
        self._manifest: BundleManifest | None = None
        self._media_offset: int | None = None

    def __enter__(self) -> BundleReader:
        self.open()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def open(self) -> None:
        if self._zip is None:
            if not self.path.is_file():
                raise BundleError(f"Buendel nicht gefunden: {self.path}")
            self._zip = zipfile.ZipFile(self.path, "r")

    def close(self) -> None:
        if self._zip is not None:
            self._zip.close()
            self._zip = None

    @property
    def zip(self) -> zipfile.ZipFile:
        if self._zip is None:
            raise BundleError("Buendel ist nicht geoeffnet")
        return self._zip

    # -- Metadaten -------------------------------------------------------

    @property
    def manifest(self) -> BundleManifest:
        if self._manifest is None:
            try:
                raw = self.zip.read(MANIFEST_NAME)
            except KeyError as e:
                raise BundleError(f"{self.path} hat kein {MANIFEST_NAME}") from e
            self._manifest = BundleManifest.from_dict(json.loads(raw))
        return self._manifest

    def info_json(self) -> dict[str, Any] | None:
        try:
            return json.loads(self.zip.read(INFO_NAME))
        except KeyError:
            return None

    def read(self, name: str) -> bytes:
        return self.zip.read(name)

    def names(self) -> list[str]:
        return self.zip.namelist()

    # -- Direktzugriff auf die Mediendaten -------------------------------

    def media_data_offset(self) -> int:
        """Absoluter Byte-Offset der Mediendaten in der ZIP-Datei.

        Der lokale Dateikopf ist 30 Byte lang, gefolgt von Name und Extra-Feld
        variabler Laenge. Deren Laengen stehen nur im Kopf selbst, also einmal
        nachschlagen und merken. Das Ergebnis ist auch bei ZIP64 korrekt, weil
        nur die beiden Laengenfelder gelesen werden - die stehen nie im
        Extra-Block.
        """
        if self._media_offset is not None:
            return self._media_offset

        info = self._media_info()
        if info.compress_type != zipfile.ZIP_STORED:
            raise BundleError(
                f"{self.path}: Medien sind komprimiert abgelegt "
                f"(compress_type={info.compress_type}) - Direktzugriff nicht moeglich"
            )

        fp = self.zip.fp
        if fp is None:
            raise BundleError("ZIP-Dateiobjekt nicht verfuegbar")
        fp.seek(info.header_offset)
        head = fp.read(_LOCAL_HEADER_STRUCT.size)
        if len(head) < _LOCAL_HEADER_STRUCT.size:
            raise BundleError(f"{self.path}: lokaler Dateikopf abgeschnitten")
        sig, _ver, flags, *_rest, name_len, extra_len = _LOCAL_HEADER_STRUCT.unpack(head)
        if sig != _LOCAL_HEADER_SIG:
            raise BundleError(f"{self.path}: ungueltige Kopf-Signatur {sig:#x}")

        offset = info.header_offset + _LOCAL_HEADER_STRUCT.size + name_len + extra_len

        # Bit 3 der Zusatzflags bedeutet: Groessen und Pruefsumme stehen nicht im
        # lokalen Kopf, sondern in einem Nachsatz hinter den Daten. Das passiert,
        # wenn ein ZIP auf einen nicht springbaren Strom geschrieben wurde
        # (Pipe, HTTP-Antwort). Die Offset-Rechnung stimmt zwar auch dann, weil
        # sie nur die beiden Laengenfelder benutzt - aber es ist ein Hinweis auf
        # ein fremd erzeugtes Buendel, bei dem wir lieber genauer hinsehen.
        # write_bundle schreibt immer in eine echte Datei und erzeugt das nie.
        if flags & 0x08:
            log.warning(
                "%s: Medieneintrag hat einen Daten-Nachsatz (Bit 3) - vermutlich nicht "
                "von write_bundle erzeugt. Offset wird geprueft.",
                self.path,
            )

        # Plausibilitaet: Die Daten muessen vollstaendig innerhalb der Datei
        # liegen. Ohne diese Pruefung wuerde ein beschaedigtes Buendel bei der
        # Wiedergabe stillschweigend Teile des Zentralverzeichnisses ausliefern.
        dateigroesse = self.path.stat().st_size
        if offset + info.file_size > dateigroesse:
            raise BundleError(
                f"{self.path}: Mediendaten reichen ueber das Dateiende hinaus "
                f"({offset} + {info.file_size} > {dateigroesse})"
            )

        self._media_offset = offset
        return self._media_offset

    def _media_info(self) -> zipfile.ZipInfo:
        name = self.manifest.media_name
        try:
            return self.zip.getinfo(name)
        except KeyError as e:
            raise BundleError(f"{self.path}: Mediendatei {name!r} fehlt im Buendel") from e

    @property
    def media_size(self) -> int:
        return self._media_info().file_size

    def media_range(self, start: int = 0, length: int | None = None, chunk_size: int = 512 * 1024) -> Iterator[bytes]:
        """Liefert einen Byte-Bereich der Mediendatei als Haeppchen.

        Genau das, was ein HTTP-Range-Request braucht. Oeffnet die ZIP-Datei
        ein zweites Mal roh, damit gleichzeitige Streams sich nicht gegenseitig
        den Dateizeiger verstellen.
        """
        size = self.media_size
        if start < 0 or start > size:
            raise BundleError(f"Startposition {start} liegt ausserhalb von 0..{size}")
        remaining = size - start if length is None else min(length, size - start)
        base = self.media_data_offset()

        with open(self.path, "rb", buffering=0) as raw:
            raw.seek(base + start)
            while remaining > 0:
                chunk = raw.read(min(chunk_size, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    def extract_media(self, dest: Path) -> Path:
        """Entpackt die Mediendatei in den Heissspeicher.

        Wird nur gebraucht, wenn direkt gestreamt nicht geht - etwa weil noch
        transkodiert werden muss oder das Buendel auf langsamem Speicher liegt.
        Schreibt ueber eine ``.part``-Datei, damit nie eine halbe Datei als
        fertig gilt.
        """
        root = dest.parent
        dest = paths.contained(root, dest)
        tmp = paths.contained(root, dest.with_suffix(dest.suffix + ".part"))
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self.zip.open(self.manifest.media_name) as src, open(tmp, "wb") as out:
                shutil.copyfileobj(src, out, length=4 * 1024 * 1024)
        except Exception:
            tmp.unlink(missing_ok=True)
            raise
        tmp.replace(dest)
        return dest

    def open_media(self) -> IO[bytes]:
        """Dateiaehnliches Objekt auf die Mediendaten.

        Nicht fuer wahlfreien Zugriff benutzen - dafuer ist
        :meth:`media_range` da. Gut fuer ffmpeg-Pipes und sequenzielles Lesen.
        """
        return self.zip.open(self.manifest.media_name)


def verify_bundle(path: Path) -> tuple[bool, str]:
    """Prueft ein Buendel auf Verwendbarkeit.

    Prueft Struktur und Direktzugriff, nicht die CRC-Summen des gesamten
    Inhalts - das wuerde bedeuten, jedes Video vollstaendig zu lesen.
    """
    try:
        with BundleReader(path) as r:
            m = r.manifest
            if m.schema_version > SCHEMA_VERSION:
                return False, f"Buendel-Schema {m.schema_version} ist neuer als {SCHEMA_VERSION}"
            if m.media_name not in r.names():
                return False, f"Mediendatei {m.media_name!r} fehlt"
            offset = r.media_data_offset()
            size = r.media_size
            if offset + size > path.stat().st_size:
                return False, "Mediendaten reichen ueber das Dateiende hinaus"
            first = next(r.media_range(0, 16), b"")
            if not first:
                return False, "Mediendaten sind leer"
        return True, "ok"
    except (BundleError, zipfile.BadZipFile, OSError) as e:
        return False, f"{type(e).__name__}: {e}"
