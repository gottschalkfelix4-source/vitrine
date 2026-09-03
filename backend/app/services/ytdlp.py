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
from app.services import abbruch, cookies

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


class Gedrosselt(YtdlpError):
    """YouTube weist die IP-Adresse ab - kein Fehler dieses Videos.

    Die bekannteste Auspraegung ist "Sign in to confirm you're not a bot",
    daneben HTTP 429. Beides gilt der Adresse, nicht dem Video: Das naechste
    Video traefe auf dieselbe Wand, und jeder weitere Versuch verlaengert die
    Sperre eher, als dass er sie loest.

    Deshalb ist das ausdruecklich kein Fehlschlag. Der Auftrag geht unbewertet
    zurueck in die Warteschlange, und alle Netzauftraege pausieren - siehe
    :mod:`app.services.drosselung`.
    """


#: Textmerkmale einer Abweisung durch YouTube.
#:
#: Bewusst knapp und woertlich gehalten. Ein weit gefasstes Muster waere hier
#: teuer: Jeder Fehltreffer legt die gesamte Warteschlange fuer Minuten still.
#: "not a bot" trifft die Bot-Pruefung in jeder Formulierung und faellt
#: insbesondere nicht auf "Sign in to confirm your age" herein - eine
#: Altersschranke ist eine Sache des Kontos, keine Drosselung, und wuerde von
#: einer Pause kein Stueck besser.
_DROSSEL_MARKER = ("not a bot", "http error 429", "too many requests")

#: Textmerkmale eines endgueltig verschwundenen Videos.
_WEG_MARKER = ("private", "unavailable", "removed", "deleted", "terminated")


def _fehlerklasse(meldung: str) -> type[YtdlpError]:
    """Ordnet einen Fehlertext einer unserer Bedeutungen zu.

    Die Reihenfolge ist nicht beliebig: Erst die Drosselung, dann das
    verschwundene Video. Eine Abweisung wegen Drosselung enthaelt gelegentlich
    ebenfalls das Wort "unavailable" - waere sie als :class:`VideoUnavailable`
    eingeordnet, gaelte das Video als bei der Quelle geloescht und wuerde nie
    wieder angefasst. Genau der Verlust, den ein Archiv nicht machen darf.
    """
    text = meldung.lower()
    if any(w in text for w in _DROSSEL_MARKER):
        return Gedrosselt
    if any(w in text for w in _WEG_MARKER):
        return VideoUnavailable
    return YtdlpError


def _einordnen(e: YoutubeDLError) -> YtdlpError:
    """Uebersetzt eine yt-dlp-Ausnahme in unsere Bedeutungen."""
    return _fehlerklasse(str(e))(str(e))


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
    # Erst pruefen, dann setzen: yt-dlp bricht bei einem unlesbaren cookiefile
    # jeden Aufruf ab, auch das blosse Auflisten eines Kanals. Ein Tippfehler im
    # Pfad soll eine Warnung im Log sein, kein Ausfall.
    if settings.ytdlp_cookies_file and not settings.ytdlp_cookies_file.is_file():
        log.warning(
            "Cookie-Datei %s gibt es nicht - es wird ohne Cookies gearbeitet.",
            settings.ytdlp_cookies_file,
        )
    # Welche Datei gilt, entscheidet der Cookie-Assistent: eine ausdruecklich
    # gesetzte Umgebungsvariable, sonst die ueber die Oberflaeche hochgeladene.
    if (cookie_datei := cookies.aktiver_pfad()) is not None:
        opts["cookiefile"] = str(cookie_datei)
    if settings.ytdlp_ratelimit:
        opts["ratelimit"] = settings.ytdlp_ratelimit
    if settings.ytdlp_sleep_requests > 0:
        # Wirkt zwischen den einzelnen HTTP-Anfragen, nicht nur zwischen
        # Videos. Gegen die Bot-Pruefung ist das der wirksamere Hebel: Ein
        # Download stellt ein Dutzend Anfragen, und YouTube zaehlt die, nicht
        # die Videos.
        opts["sleep_interval_requests"] = settings.ytdlp_sleep_requests
    if settings.ytdlp_player_clients:
        opts["extractor_args"] = {
            "youtube": {"player_client": list(settings.ytdlp_player_clients)}
        }
    return opts


class _Mitschrift:
    """Faengt die Meldungen von yt-dlp ab, statt sie nur zu verwerfen.

    Noetig wegen ``ignoreerrors``. Beim Auflisten eines Kanals ist die Option
    unverzichtbar - ein einziges gesperrtes Video darf einen Abgleich ueber
    tausend Videos nicht abbrechen. Sie hat aber eine unangenehme Kehrseite:
    yt-dlp wirft dann nicht mehr, sondern schreibt den Fehler ins Log und gibt
    ``None`` zurueck.

    Damit war eine Abweisung durch YouTube beim Auflisten nicht mehr von einem
    kaputten Kanal zu unterscheiden - beide endeten als "keine Metadaten fuer
    ...". Der Abgleich galt als gescheitert, die Drosselung blieb unerkannt und
    die uebrigen Kanaele liefen munter weiter in dieselbe Wand.

    Der Mitschnitt macht den Grund wieder lesbar. Er ersetzt zugleich die
    Optionen ``quiet``/``no_warnings``: yt-dlp schreibt bei gesetztem Logger
    nichts mehr selbst auf die Konsole.
    """

    #: Hoechstens so viele Meldungen werden aufgehoben. Beim Auflisten eines
    #: Kanals mit tausenden Videos meldet yt-dlp jedes geloeschte einzeln -
    #: alle mitzuschleppen ergaebe eine Fehlermeldung von der Laenge eines
    #: Romans, in der Datenbank abgeschnitten und in der Oberflaeche unlesbar.
    #: Die ersten paar sagen dasselbe wie alle.
    GRENZE = 5

    def __init__(self) -> None:
        self.fehler: list[str] = []
        self.gezaehlt = 0

    def debug(self, msg: str) -> None:  # pragma: no cover - Rauschen
        pass

    def info(self, msg: str) -> None:  # pragma: no cover - Rauschen
        pass

    def warning(self, msg: str) -> None:
        log.debug("yt-dlp: %s", msg)

    def error(self, msg: str) -> None:
        self.gezaehlt += 1
        if len(self.fehler) < self.GRENZE:
            self.fehler.append(str(msg))
        log.debug("yt-dlp-Fehler: %s", msg)

    def text(self) -> str:
        text = " | ".join(self.fehler)
        weitere = self.gezaehlt - len(self.fehler)
        return f"{text} (und {weitere} weitere)" if weitere > 0 else text


def _extract(url: str, opts: dict[str, Any]) -> dict[str, Any]:
    mitschrift = _Mitschrift()
    opts = opts | {"logger": mitschrift}
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
        raise _einordnen(e) from e
    if info is None:
        # Hierher fuehrt der Weg nur mit ``ignoreerrors``: yt-dlp hat den Grund
        # ins Log geschrieben statt zu werfen. Ohne den Mitschnitt stuende hier
        # nur "keine Metadaten" - und eine Drosselung saehe aus wie ein
        # kaputter Kanal.
        grund = mitschrift.text()
        if grund:
            raise _fehlerklasse(grund)(f"keine Metadaten fuer {url}: {grund}")
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
        raise _einordnen(e) from e
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


class QualitaetVerfehlt(YtdlpError):
    """Weniger Qualitaet bekommen, als die Quelle anbietet.

    Bewusst kein :class:`DegradedDownload`: Dort ist die Datei unbrauchbar und
    wird verworfen. Hier ist sie in Ordnung, nur schlechter als moeglich - der
    richtige Umgang ist ein erneuter Versuch auf einer benannten Stufe, nicht
    das Wegwerfen eines fertigen Downloads.
    """

    def __init__(self, meldung: str, *, erhalten: int, angeboten: int) -> None:
        super().__init__(meldung)
        self.erhalten = erhalten
        self.angeboten = angeboten


#: Die uebliche Qualitaetsleiter, absteigend. Fuer den Rueckfall: Wird eine
#: Stufe nicht erreicht, wird die naechste darunter ausdruecklich angefordert.
STUFEN = (4320, 2160, 1440, 1080, 720, 480, 360, 240, 144)


def naechste_stufe(unter: int) -> int | None:
    """Die naechstniedrigere Stufe unterhalb von ``unter``."""
    for s in STUFEN:
        if s < unter:
            return s
    return None


def guete(info: dict[str, Any]) -> int | None:
    """Die Qualitaet in der Zaehlweise, die auch YouTube benutzt.

    Gemessen wird die **kurze Seite**, nicht die Hoehe. Das ist keine
    Feinheit, sondern die Ursache eines ganzen Schwungs falscher Fehlschlaege:

    YouTube liefert fuer ein hochkantiges Video das Format 1080x1920 mit der
    Angabe ``format_note: "1080p"`` - benennt es also nach der kurzen Seite.
    yt-dlp meldet dafuer ``height: 1920``. Wer die Hoehe als Qualitaet liest,
    haelt ein hochkantiges 1080p-Video fuer "1920p" und eine 720er-Fassung
    (720x1280) fuer "1280p". Beim Kanalabgleich eines echten Kanals hat das
    reihenweise einwandfreie Downloads verworfen, mit Meldungen wie
    "nur 1280p erhalten, obwohl die Quelle 1920p anbietet".

    Bei Querformat ist die kurze Seite die Hoehe - dort aendert sich nichts.
    """
    breite, hoehe = info.get("width"), info.get("height")
    if breite and hoehe:
        return min(int(breite), int(hoehe))
    return int(hoehe) if hoehe else (int(breite) if breite else None)


def angebotene_guete(info: dict[str, Any]) -> int | None:
    """Die beste Qualitaet, die die Quelle laut Formatliste anbietet."""
    werte = [g for f in info.get("formats") or []
             if f.get("vcodec") not in (None, "none") and (g := guete(f)) is not None]
    return max(werte) if werte else None


#: So viele getrennte Videospuren muss die Formatliste mindestens enthalten,
#: damit sie als unbeschaedigt gilt. Zwei sind eine sehr milde Huerde - ein
#: echtes YouTube-Video hat ein Dutzend.
_MINDEST_ADAPTIVE_FORMATE = 2


def _formatliste_glaubwuerdig(info: dict[str, Any]) -> bool:
    """Unterscheidet ein wirklich kleines Video von einer gestoerten Sitzung.

    Beide behaupten dasselbe: "mehr gibt es nicht". Der Unterschied steht in
    der Formatliste.

    Funktioniert die Auslieferung, liefert YouTube getrennte Spuren fuer Bild
    und Ton (DASH) - bei einem Video von 2005 sind das immer noch ein Dutzend
    Eintraege, nur eben alle klein. Bricht die PO-Token- oder JavaScript-Kette
    zusammen, bleiben nur die alten, fest zusammengemischten Formate uebrig,
    allen voran die Nummer 18. Dort steht in jedem Eintrag eine Tonspur.

    Das Vorhandensein reiner Videospuren ist deshalb ein brauchbares Zeichen
    dafuer, dass die Liste vollstaendig ist und man ihr glauben darf.
    """
    nur_video = [
        f for f in info.get("formats") or []
        if f.get("vcodec") not in (None, "none") and f.get("acodec") in (None, "none")
    ]
    return len(nur_video) >= _MINDEST_ADAPTIVE_FORMATE


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
    obwohl die Quelle mehr anbietet, wird :class:`QualitaetVerfehlt` geworfen -
    kein Fehlschlag, sondern die Aufforderung, es eine Stufe tiefer erneut zu
    versuchen. Bietet die Quelle selbst nicht mehr - ein altes 720p-Video -,
    wird das Beste genommen, was es gibt, und das als Hinweis zurueckgegeben.
    Ein altes Video soll nicht ungesichert bleiben, nur weil es keine 1080p hat.

    Gemessen wird durchgehend die kurze Seite, siehe :func:`guete`.

    Liefert ``None`` oder einen Hinweistext fuer die Statuszeile.
    """
    hoehe = guete(info)
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
        angebot = angebotene_guete(info)
        if angebot is not None and angebot > hoehe:
            # Es gaebe Besseres - dann ist das Misstrauen berechtigt.
            raise DegradedDownload(
                f"nur {hoehe}p erhalten, obwohl die Quelle {angebot}p anbietet - "
                "unterhalb des absoluten Bodens, vermutlich gestoerte Sitzung"
            )
        if _formatliste_glaubwuerdig(info):
            # Das Video ist wirklich so klein. "Me at the zoo" von 2005 gibt es
            # in hoechstens 240p; es zu verwerfen hiesse, dass sich gerade die
            # aeltesten Videos - die am ehesten verschwinden - nicht sichern
            # lassen. Genau die will ein Archiv aber haben.
            return f"Quelle bietet hoechstens {hoehe}p (gewuenscht: {mindesthoehe}p)"
        raise DegradedDownload(
            f"nur {hoehe}p erhalten und keine glaubwuerdige Formatliste - "
            "vermutlich eingeschraenkte Formatauswahl"
        )

    if hoehe < mindesthoehe:
        angebot = angebotene_guete(info)
        if angebot is not None and angebot > hoehe:
            # Es gibt Besseres. Nicht verwerfen - die Datei ist ja in Ordnung -,
            # sondern eine Stufe gezielt nachfordern. Der Aufrufer entscheidet,
            # wie oft er das versucht, und behaelt am Ende das Beste.
            raise QualitaetVerfehlt(
                f"{hoehe}p erhalten, die Quelle bietet {angebot}p",
                erhalten=hoehe, angeboten=angebot,
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
        meldung = f"RSS-Feed von {channel_id} nicht lesbar: {e}"
        if any(w in str(e).lower() for w in _DROSSEL_MARKER):
            # Selbst der RSS-Feed wird abgewiesen. Dann ist die Adresse
            # gesperrt und nicht der Feed kaputt - und der Abgleich soll
            # warten statt es im Minutentakt erneut zu versuchen.
            raise Gedrosselt(meldung) from e
        raise YtdlpError(meldung) from e

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
