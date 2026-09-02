"""Anbindung an yt-dlp.

yt-dlp wird als Bibliothek eingebunden, nicht als Unterprozess. Das gibt uns
Fortschritts-Hooks, saubere Ausnahmen und die Metadaten direkt als dict, statt
sie aus stdout zu fischen. Der Preis: Ein Fehler in yt-dlp kann den Prozess
mitreissen, weshalb alle Aufrufe hier gekapselt sind und
:class:`YtdlpError` werfen.

Zur Aktualitaet: yt-dlp ist die einzige Abhaengigkeit, die absichtlich nicht
hart gepinnt ist. YouTube aendert die Auslieferung regelmaessig; eine
festgenagelte Version macht das Archiv binnen Wochen funktionsunfaehig.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yt_dlp
from yt_dlp.utils import UnsupportedError, YoutubeDLError

from app.config import settings
from app.services import abbruch

log = logging.getLogger(__name__)

#: Kanal-Unterseiten, die als eigene Sammlung gefuehrt werden.
CHANNEL_TABS = {
    "uploads": "videos",
    "shorts": "shorts",
    "live": "streams",
}


class YtdlpError(RuntimeError):
    pass


class VideoUnavailable(YtdlpError):
    """Video ist geloescht, privat oder gesperrt - kein Grund zum Wiederholen."""


@dataclass(slots=True)
class DownloadResult:
    path: Path
    info: dict[str, Any]
    thumbnail: Path | None = None
    subtitles: list[tuple[str, bool, Path]] = field(default_factory=list)


def _base_opts() -> dict[str, Any]:
    """Optionen, die fuer jeden Aufruf gelten."""
    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        # Ein einzelnes fehlerhaftes Video darf einen Kanalabgleich ueber
        # tausend Videos nicht abbrechen.
        "ignoreerrors": True,
        "retries": 5,
        "fragment_retries": 10,
        "extractor_retries": 3,
        # Entschaerft das Rate-Limiting - YouTube sperrt sonst zeitweise die IP.
        "sleep_interval": settings.ytdlp_sleep_interval,
        "max_sleep_interval": settings.ytdlp_max_sleep_interval,
    }
    if settings.ytdlp_cookies_file:
        # Erst pruefen, dann setzen: yt-dlp bricht bei einem unlesbaren
        # cookiefile jeden Aufruf ab, auch das blosse Auflisten eines Kanals.
        # Ein Tippfehler im Pfad soll eine Warnung im Log sein, kein Ausfall.
        if settings.ytdlp_cookies_file.is_file():
            opts["cookiefile"] = str(settings.ytdlp_cookies_file)
        else:
            log.warning(
                "Cookie-Datei %s gibt es nicht - es wird ohne Cookies gearbeitet.",
                settings.ytdlp_cookies_file,
            )
    if settings.ytdlp_ratelimit:
        opts["ratelimit"] = settings.ytdlp_ratelimit
    return opts


def _extract(url: str, opts: dict[str, Any]) -> dict[str, Any]:
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            # Entfernt interne Objekte und macht das Ergebnis JSON-tauglich -
            # es landet unveraendert im Buendel.
            if info is not None:
                info = ydl.sanitize_info(info)
    except UnsupportedError as e:
        raise YtdlpError(f"URL wird nicht unterstuetzt: {url}") from e
    except YoutubeDLError as e:
        # Bewusst die Basisklasse und nicht DownloadError/ExtractorError: Die
        # decken nicht alles ab. Ein leerer oder falscher Cookie-Pfad etwa
        # wirft CookieLoadError, und der kam ungefiltert als Serverfehler beim
        # Nutzer an, statt als lesbare Meldung.
        text = str(e).lower()
        if any(w in text for w in ("private", "unavailable", "removed", "deleted", "terminated")):
            raise VideoUnavailable(str(e)) from e
        raise YtdlpError(str(e)) from e
    if info is None:
        raise YtdlpError(f"keine Metadaten fuer {url}")
    return info


# ------------------------------------------------------------------- Auflisten


@dataclass(slots=True)
class ChannelInfo:
    id: str
    name: str
    handle: str | None
    description: str | None
    avatar_url: str | None
    banner_url: str | None
    subscriber_count: int | None


def _pick_thumb(eintraege: list[dict[str, Any]] | None, stichwort: str) -> str | None:
    """Sucht das groesste Bild einer Art aus der Thumbnail-Liste."""
    if not eintraege:
        return None
    passend = [t for t in eintraege if stichwort in (t.get("id") or "")]
    kandidaten = passend or eintraege
    kandidaten = sorted(kandidaten, key=lambda t: (t.get("width") or 0), reverse=True)
    return kandidaten[0].get("url") if kandidaten else None


def fetch_channel(url: str) -> ChannelInfo:
    """Holt die Stammdaten eines Kanals.

    ``extract_flat`` verhindert, dass yt-dlp die komplette Videoliste
    aufloest - fuer einen Kanal mit tausenden Videos waere das ein Vielfaches
    der Laufzeit, obwohl wir hier nur Name und Bild brauchen.
    """
    opts = _base_opts() | {"extract_flat": "in_playlist", "playlist_items": "0"}
    info = _extract(url, opts)

    kanal_id = info.get("channel_id") or info.get("uploader_id") or info.get("id")
    if not kanal_id:
        raise YtdlpError(f"keine Kanal-ID in {url}")

    return ChannelInfo(
        id=kanal_id,
        name=info.get("channel") or info.get("uploader") or info.get("title") or kanal_id,
        handle=info.get("uploader_id") if str(info.get("uploader_id", "")).startswith("@") else None,
        description=info.get("description"),
        avatar_url=_pick_thumb(info.get("thumbnails"), "avatar"),
        banner_url=_pick_thumb(info.get("thumbnails"), "banner"),
        subscriber_count=info.get("channel_follower_count"),
    )


@dataclass(slots=True)
class ListedVideo:
    id: str
    title: str
    duration_s: int | None
    upload_date: datetime | None
    view_count: int | None


def _to_datetime(info: dict[str, Any]) -> datetime | None:
    ts = info.get("timestamp")
    if ts:
        return datetime.fromtimestamp(ts, tz=UTC)
    roh = info.get("upload_date")  # Form: JJJJMMTT
    if roh and len(str(roh)) == 8:
        try:
            return datetime.strptime(str(roh), "%Y%m%d").replace(tzinfo=UTC)
        except ValueError:
            return None
    return None


def list_entries(url: str, limit: int | None = None) -> list[ListedVideo]:
    """Listet die Videos einer Sammlung, ohne sie einzeln aufzuloesen.

    Das ist der Arbeitspferd-Aufruf fuer den Kanalabgleich: Ein Durchlauf
    liefert alle IDs, und nur fuer wirklich neue Videos werden anschliessend
    die vollen Metadaten geholt.
    """
    opts = _base_opts() | {"extract_flat": "in_playlist"}
    if limit:
        opts["playlistend"] = limit
    info = _extract(url, opts)

    ergebnis: list[ListedVideo] = []
    for e in info.get("entries") or []:
        if not e:  # ignoreerrors laesst None-Eintraege stehen
            continue
        if e.get("_type") == "playlist":  # verschachtelte Sammlung
            continue
        vid = e.get("id")
        if not vid:
            continue
        ergebnis.append(
            ListedVideo(
                id=vid,
                title=e.get("title") or "(ohne Titel)",
                duration_s=int(e["duration"]) if e.get("duration") else None,
                upload_date=_to_datetime(e),
                view_count=e.get("view_count"),
            )
        )
    return ergebnis


@dataclass(slots=True)
class ListedPlaylist:
    id: str
    title: str
    item_count: int | None


def list_channel_playlists(channel_url: str) -> list[ListedPlaylist]:
    """Die vom Kanal angelegten Playlists.

    Das ist der Teil, den die meisten Archivierer weglassen - dabei ist die
    Playlist-Gliederung oft die einzige Ordnung, die ein Kanal seinen Videos
    gibt.
    """
    url = channel_url.rstrip("/") + "/playlists"
    opts = _base_opts() | {"extract_flat": True}
    try:
        info = _extract(url, opts)
    except YtdlpError as e:
        log.info("keine Playlists fuer %s: %s", channel_url, e)
        return []

    listen: list[ListedPlaylist] = []
    for e in info.get("entries") or []:
        if not e:
            continue
        # Manche Kanaele gruppieren Playlists in Sektionen - eine Ebene tiefer.
        unter = e.get("entries") if e.get("_type") == "playlist" and e.get("entries") else [e]
        for p in unter:
            if not p or not p.get("id"):
                continue
            if not str(p["id"]).startswith(("PL", "UU", "OL", "LL", "FL", "RD")):
                continue
            listen.append(
                ListedPlaylist(
                    id=p["id"],
                    title=p.get("title") or "(ohne Titel)",
                    item_count=p.get("playlist_count"),
                )
            )
    return listen


def fetch_video_info(video_id: str) -> dict[str, Any]:
    """Vollstaendige Metadaten eines Videos."""
    opts = _base_opts() | {"ignoreerrors": False}
    if settings.write_comments:
        opts["getcomments"] = True
    return _extract(f"https://www.youtube.com/watch?v={video_id}", opts)


# ----------------------------------------------------------------- Herunterladen


def download_video(
    video_id: str,
    ziel: Path,
    *,
    format_selector: str | None = None,
    fortschritt: Callable[[float, str], None] | None = None,
) -> DownloadResult:
    """Laedt ein Video samt Beiwerk in ``ziel``.

    Zusammengefuehrt wird nach MKV, obwohl kein Browser das abspielt - hier ist
    das egal, weil die Datei anschliessend ohnehin durch den Encoder geht und im
    Buendel als WebM oder MP4 landet. MKV nimmt als Zwischenschritt jede
    Codec-Kombination an, ohne dass ffmpeg sich beschwert.
    """
    ziel.mkdir(parents=True, exist_ok=True)

    def _hook(d: dict[str, Any]) -> None:
        # Die einzige Stelle, an der wir waehrend eines laufenden Downloads
        # ueberhaupt zum Zug kommen - yt-dlp ruft sie mehrmals je Sekunde auf.
        # Die Ausnahme verlaesst den Download sofort und laesst die
        # .part-Dateien liegen, aus denen der naechste Lauf fortsetzt.
        abbruch.pruefen()
        if not fortschritt:
            return
        if d.get("status") == "downloading":
            gesamt = d.get("total_bytes") or d.get("total_bytes_estimate")
            fertig = d.get("downloaded_bytes") or 0
            if gesamt:
                fortschritt(fertig / gesamt, f"Lade {d.get('_percent_str', '').strip()}")
        elif d.get("status") == "finished":
            fortschritt(1.0, "Zusammenfuehren")

    opts = _base_opts() | {
        "ignoreerrors": False,
        "format": format_selector or settings.format_selector(),
        "merge_output_format": "mkv",
        "outtmpl": {"default": str(ziel / "%(id)s.%(ext)s")},
        "concurrent_fragment_downloads": settings.ytdlp_concurrent_fragments,
        "writethumbnail": True,
        "writesubtitles": settings.write_subtitles,
        "writeautomaticsub": settings.write_auto_subtitles,
        "subtitleslangs": settings.subtitle_languages,
        "subtitlesformat": "vtt",
        "progress_hooks": [_hook],
        "postprocessors": [],
    }
    if settings.sponsorblock:
        # Nur markieren, nicht schneiden: Ein Archiv soll das Original bewahren.
        # Die Kapitel landen in den Metadaten, das UI kann sie zum Ueberspringen
        # anbieten.
        opts["postprocessors"].append(
            {"key": "SponsorBlock", "categories": ["sponsor", "selfpromo", "interaction"], "when": "after_filter"}
        )

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=True)
            # Zwingend: Das Ergebnis eines Downloads enthaelt lebende
            # Python-Objekte - unter "__postprocessors" etwa die
            # ffmpeg-Nachbearbeiter. Ohne sanitize_info scheitert spaeter das
            # Schreiben der info.json ins Buendel mit
            # "Object of type FFmpegMergerPP is not JSON serializable", und
            # zwar erst nach dem vollstaendigen Download.
            info = ydl.sanitize_info(info)
    except abbruch.Abgebrochen:
        # Muss VOR YoutubeDLError stehen und ungefiltert durch: Das ist kein
        # Downloadfehler, sondern das Herunterfahren. Als YtdlpError verpackt
        # wuerde der Auftrag als gescheitert vermerkt statt fortgesetzt.
        raise
    except YoutubeDLError as e:
        text = str(e).lower()
        if any(w in text for w in ("private", "unavailable", "removed", "deleted")):
            raise VideoUnavailable(str(e)) from e
        raise YtdlpError(str(e)) from e
    if info is None:
        raise YtdlpError(f"Download von {video_id} lieferte nichts")

    medien = _find_media(ziel, video_id)
    if medien is None:
        raise YtdlpError(f"heruntergeladene Datei zu {video_id} nicht auffindbar")

    return DownloadResult(
        path=medien,
        info=info,
        thumbnail=_find_one(ziel, video_id, (".jpg", ".jpeg", ".png", ".webp")),
        subtitles=_find_subtitles(ziel, video_id),
    )


class DegradedDownload(YtdlpError):
    """Der Download hat formal geklappt, aber nur eine Notfassung geliefert."""


def angebotene_hoehe(info: dict[str, Any]) -> int | None:
    """Die groesste Hoehe, die die Quelle laut Formatliste anbietet."""
    hoehen = [
        f.get("height")
        for f in info.get("formats") or []
        if f.get("vcodec") not in (None, "none") and f.get("height")
    ]
    return max(hoehen) if hoehen else None


def check_not_degraded(
    info: dict[str, Any], mindesthoehe: int = 1080, boden: int = 480
) -> str | None:
    """Prueft, ob wirklich die gewuenschte Qualitaet ankam.

    Das ist der gefaehrlichste stille Fehler des ganzen Projekts. Wenn die
    PO-Token- oder JavaScript-Kette nicht funktioniert, bricht yt-dlp nicht ab -
    es faellt auf Format 18 zurueck, ein 360p-Gemisch aus H.264 und AAC, und
    meldet Erfolg. Ohne diese Pruefung archiviert man wochenlang 360p-Dateien
    und merkt es erst beim Zuschauen, wenn die Quelle laengst geloescht ist.

    Zwei Schwellen, mit unterschiedlicher Bedeutung:

    ``boden`` ist absolut. Darunter wird immer verworfen - auch wenn die
    Formatliste behauptet, es gaebe nichts Besseres. Genau das behauptet eine
    gestoerte Sitzung naemlich auch; die Liste ist dann selbst Teil des
    Problems und kein verlaesslicher Zeuge.

    ``mindesthoehe`` ist relativ zum Angebot. Liegt der Download darunter,
    obwohl die Quelle mehr anbietet, ist die Kette gestoert. Bietet die Quelle
    selbst nicht mehr - ein altes 720p-Video -, wird das Beste genommen, was es
    gibt, und das als Hinweis zurueckgegeben statt als Fehler geworfen. Ein
    altes Video soll nicht ungesichert bleiben, nur weil es keine 1080p hat.

    Liefert ``None`` oder einen Hinweistext fuer die Statuszeile.
    """
    hoehe = info.get("height")
    format_id = str(info.get("format_id") or "")
    vcodec = str(info.get("vcodec") or "")

    # Format 18 ist der klassische Notfall-Rueckfall: 360p, Video und Ton in
    # einem Strom. Wer bestes Video plus bestes Audio angefordert hat, bekommt
    # das nie freiwillig.
    if format_id.split("+")[0].strip() == "18":
        raise DegradedDownload(
            "yt-dlp ist auf Format 18 (360p) zurueckgefallen - typisches Zeichen fuer "
            "fehlende PO-Tokens oder JavaScript-Laufzeit. Video wurde NICHT als "
            "archiviert verbucht."
        )

    if vcodec in ("none", "") and info.get("acodec") not in ("none", "", None):
        # Reines Audio ist bei einem Video-Archiv fast immer ein Fehlgriff.
        raise DegradedDownload("nur eine Tonspur erhalten, kein Video")

    if not hoehe:
        return None

    if hoehe < boden:
        raise DegradedDownload(
            f"nur {hoehe}p erhalten - unterhalb des absoluten Bodens von {boden}p, "
            "vermutlich eingeschraenkte Formatauswahl"
        )

    if hoehe < mindesthoehe:
        angebot = angebotene_hoehe(info)
        if angebot is not None and angebot >= mindesthoehe:
            raise DegradedDownload(
                f"nur {hoehe}p erhalten, obwohl die Quelle {angebot}p anbietet - "
                "Kette gestoert, Video wurde NICHT als archiviert verbucht"
            )
        return f"Quelle bietet hoechstens {angebot or hoehe}p (gewuenscht: {mindesthoehe}p)"

    return None


_MEDIEN_ENDUNGEN = (".mkv", ".mp4", ".webm", ".m4a", ".opus", ".mp3", ".ogg")


def _find_media(ordner: Path, video_id: str) -> Path | None:
    treffer = [
        p for p in ordner.iterdir()
        if p.is_file() and p.stem == video_id and p.suffix.lower() in _MEDIEN_ENDUNGEN
    ]
    # Groesste Datei gewinnt - Reste eines abgebrochenen Formats sind kleiner.
    return max(treffer, key=lambda p: p.stat().st_size, default=None)


def _find_one(ordner: Path, video_id: str, endungen: tuple[str, ...]) -> Path | None:
    for p in ordner.iterdir():
        if p.is_file() and p.stem == video_id and p.suffix.lower() in endungen:
            return p
    return None


def _find_subtitles(ordner: Path, video_id: str) -> list[tuple[str, bool, Path]]:
    """Findet die abgelegten Untertitel.

    yt-dlp benennt sie ``<id>.<sprache>.vtt``; automatisch erzeugte tragen
    dieselbe Form, weshalb wir sie nur ueber die Einstellung unterscheiden
    koennen, mit der sie angefordert wurden.
    """
    gefunden: list[tuple[str, bool, Path]] = []
    for p in sorted(ordner.iterdir()):
        if not p.is_file() or p.suffix.lower() != ".vtt":
            continue
        teile = p.name.split(".")
        if len(teile) < 3 or teile[0] != video_id:
            continue
        sprache = teile[-2]
        ist_auto = settings.write_auto_subtitles and not settings.write_subtitles
        gefunden.append((sprache, ist_auto, p))
    return gefunden


def playlist_url(playlist_id: str) -> str:
    return f"https://www.youtube.com/playlist?list={playlist_id}"


#: Praefixe der von YouTube automatisch gefuehrten Kanal-Playlists. Sie
#: entstehen, indem man das ``UC`` der Kanal-ID ersetzt.
_AUTO_PLAYLIST_PREFIX = {
    "uploads": "UU",  # alles, was der Kanal hochgeladen hat
    "videos": "UULF",  # nur die langen Videos
    "shorts": "UUSH",
    "live": "UULV",
}


def channel_auto_playlist(channel_id: str, art: str = "uploads") -> str:
    """Die von YouTube gefuehrte Sammel-Playlist eines Kanals.

    Der Abgleich laeuft ueber diese Playlists statt ueber die Tab-Seiten
    (``/videos``, ``/shorts``, ``/streams``). Zwei Gruende: Die Uploads-Playlist
    ``UU...`` ist die vollstaendige Liste - Tab-Seiten blenden je nach
    Kanal-Einstellung Teile aus. Und sie laesst sich ohne Umweg ueber die
    Kanalseite direkt ansteuern, was einen Request je Abgleich spart.
    """
    if not channel_id.startswith("UC"):
        raise YtdlpError(f"unerwartete Kanal-ID {channel_id!r} - erwartet wird UC...")
    rest = channel_id[2:]
    return playlist_url(_AUTO_PLAYLIST_PREFIX.get(art, "UU") + rest)


def channel_tab_url(channel_id: str, tab: str) -> str:
    """Adresse einer Kanal-Unterseite - nur noch fuer die Playlist-Uebersicht.

    Fuer Videolisten ist :func:`channel_auto_playlist` vorzuziehen.
    """
    pfad = CHANNEL_TABS.get(tab, "videos")
    return f"https://www.youtube.com/channel/{channel_id}/{pfad}"


# ------------------------------------------------------- Billiger Schnellcheck


def rss_url(channel_id: str) -> str:
    return f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"


def peek_recent(channel_id: str, timeout: float = 15.0) -> list[ListedVideo]:
    """Holt die juengsten Videos eines Kanals ueber den RSS-Feed.

    Der eigentliche Kniff am Kanalabgleich: Dieser Aufruf geht nicht durch
    yt-dlp, kostet keinen der knappen YouTube-Requests und zaehlt nicht gegen
    das Drosselungsbudget von rund 300 Videos je Stunde. Damit kann der Dienst
    stuendlich bei jedem abonnierten Kanal nachsehen, statt nur ein- bis zweimal
    am Tag - und der teure Vollabgleich laeuft nur noch woechentlich.

    Der Feed liefert allerdings nur etwa 15 Eintraege und keine Dauer. Er
    ersetzt den Vollabgleich also nicht, er verschiebt ihn nur nach hinten.
    """
    import urllib.error
    import urllib.request
    from xml.etree import ElementTree

    ns = {
        "atom": "http://www.w3.org/2005/Atom",
        "yt": "http://www.youtube.com/xml/schemas/2015",
        "media": "http://search.yahoo.com/mrss/",
    }
    try:
        with urllib.request.urlopen(rss_url(channel_id), timeout=timeout) as antwort:
            baum = ElementTree.parse(antwort)
    except (urllib.error.URLError, OSError, ElementTree.ParseError) as e:
        raise YtdlpError(f"RSS-Feed von {channel_id} nicht lesbar: {e}") from e

    ergebnis: list[ListedVideo] = []
    for eintrag in baum.getroot().findall("atom:entry", ns):
        vid = eintrag.findtext("yt:videoId", namespaces=ns)
        if not vid:
            continue
        veroeffentlicht = eintrag.findtext("atom:published", namespaces=ns)
        wann: datetime | None = None
        if veroeffentlicht:
            try:
                wann = datetime.fromisoformat(veroeffentlicht)
            except ValueError:
                wann = None
        gruppe = eintrag.find("media:group", ns)
        aufrufe = None
        if gruppe is not None:
            statistik = gruppe.find("media:community/media:statistics", ns)
            if statistik is not None and statistik.get("views"):
                try:
                    aufrufe = int(statistik.get("views", ""))
                except ValueError:
                    aufrufe = None
        ergebnis.append(
            ListedVideo(
                id=vid,
                title=eintrag.findtext("atom:title", default="(ohne Titel)", namespaces=ns),
                duration_s=None,  # der Feed kennt keine Dauer
                upload_date=wann,
                view_count=aufrufe,
            )
        )
    return ergebnis
