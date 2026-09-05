"""Zentrale Konfiguration.

Alles ueber Umgebungsvariablen mit Praefix ``YTA_`` steuerbar, damit derselbe
Container lokal (Docker Desktop) und spaeter auf Unraid ohne Codeaenderung laeuft.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class ArchiveCodec(StrEnum):
    """Codec des Kaltspeichers."""

    AV1 = "av1"
    HEVC = "hevc"
    COPY = "copy"  # keine Recodierung, Originalstream unveraendert uebernehmen


class HardwareAccel(StrEnum):
    NONE = "none"
    QSV = "qsv"  # Intel Quick Sync
    NVENC = "nvenc"  # NVIDIA
    VAAPI = "vaapi"  # generisch Linux


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="YTA_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        # Ohne das bleibt eine Zuweisung zur Laufzeit ungeprueft, und genau
        # daran ist der Hardware-Encoder gescheitert: Die Oberflaeche schreibt
        # den rohen Text - ``settings.hwaccel = "vaapi"`` - und das Feld war
        # danach ein schlichter str statt eines HardwareAccel.
        #
        # Weil HardwareAccel und ArchiveCodec StrEnum sind, faellt das fast
        # nirgends auf: Vergleiche mit == und Nachschlagen in einem dict
        # funktionieren weiter. Nur ``is`` nicht. Der Encoder wurde also
        # richtig gewaehlt, waehrend Geraet und Filter stillschweigend
        # wegfielen - ffmpeg brach mit einer Meldung ueber Filterformate ab,
        # die auf die eigentliche Ursache in keiner Weise hindeutet.
        validate_assignment=True,
    )

    # ------------------------------------------------------------------ App
    app_name: str = "Vitrine"
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"
    timezone: str = "Europe/Berlin"
    # Altinstallationen duerfen die Variable weiter setzen. Sitzungscookies
    # folgen jetzt automatisch HTTP/HTTPS; dieser Wert bleibt nur kompatibel.
    auth_cookie_secure: bool = True
    auth_session_hours: int = Field(default=12, ge=1, le=24)
    geoip_database: Path = Path("/app/geoip/dbip-city-lite.mmdb")
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:5173"]
    )

    # -------------------------------------------------------------- Ablagen
    data_dir: Path = Path("/data")

    @property
    def bundle_dir(self) -> Path:
        """Kaltspeicher: ein ZIP-Buendel je Video."""
        return self.data_dir / "bundles"

    @property
    def cache_dir(self) -> Path:
        """Heissspeicher: entpackte, abspielbare Dateien mit Ablaufdatum."""
        return self.data_dir / "cache"

    @property
    def thumb_dir(self) -> Path:
        """Thumbnails/Avatare - bleiben ausserhalb der Buendel, damit das UI
        Uebersichten rendern kann, ohne ein ZIP anzufassen."""
        return self.data_dir / "thumbs"

    @property
    def tmp_dir(self) -> Path:
        """Arbeitsverzeichnis fuer Downloads und Encodes."""
        return self.data_dir / "tmp"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "vitrine.db"

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.db_path}"

    # ------------------------------------------------------- Kaltspeicher
    archive_codec: ArchiveCodec = ArchiveCodec.AV1
    hwaccel: HardwareAccel = HardwareAccel.NONE
    #: Render-Knoten der Grafikkarte im Container. Nur fuer vaapi noetig; qsv
    #: und nvenc finden ihr Geraet selbst.
    #:
    #: renderD128 ist der erste Knoten. Steckt neben der Arc noch eine
    #: iGPU im Rechner, kann die Arc auch renderD129 sein - welcher der
    #: richtige ist, sagt die Hardware-Pruefung in den Einstellungen.
    hwaccel_device: str = "/dev/dri/renderD128"

    #: SVT-AV1 Preset (0=langsamst/beste Dichte .. 13=schnellst).
    #: Gemessen auf einer 6-Kern-NAS-CPU, 1080p30: Preset 4 braucht 80 Minuten
    #: je Stunde Video, Preset 6 rund 51, Preset 8 rund 25, Preset 10 rund 16.
    #: 6 ist der uebliche Kompromiss; bei Massenarchivierung ist 8 bis 10
    #: vernuenftiger, denn die Ersparnis faellt dabei nur um wenige Prozentpunkte.
    av1_preset: int = Field(default=6, ge=0, le=13)
    #: CRF fuer SVT-AV1 - der weitaus staerkere Hebel als das Preset.
    #:
    #: Wichtig zur Erwartungshaltung: Gemessen gegen eine typische
    #: H.264-YouTube-Quelle spart CRF 22 (visuell nicht unterscheidbar) exakt
    #: nichts, CRF 26 rund 23 %, CRF 30 rund 40 %, CRF 34 rund 55 %. Eine
    #: Platzersparnis gibt es also nur mit bewusst in Kauf genommener
    #: Qualitaetsminderung. 30 ist der Punkt, an dem das Verhaeltnis stimmt.
    av1_crf: int = Field(default=30, ge=0, le=63)
    #: x265 Gegenstueck, falls archive_codec=hevc.
    hevc_preset: str = "medium"
    hevc_crf: int = Field(default=24, ge=0, le=51)

    #: Audio wird immer nach Opus umgesetzt - deutlich effizienter als AAC.
    audio_codec: str = "libopus"
    audio_bitrate_kbps: int = 128
    #: Quellen unterhalb dieser Hoehe nicht recodieren (lohnt sich nicht).
    recode_min_height: int = 480
    #: Wenn der Encode groesser wird als das Original, Original behalten.
    keep_original_if_larger: bool = True

    # -------------------------------------------------------- Heissspeicher
    #: Lebensdauer einer entpackten Datei ab letztem Zugriff.
    hot_ttl_hours: float = 24.0
    #: Kuerzere Frist, sobald die Wiedergabe sauber beendet wurde.
    hot_ttl_after_playback_minutes: float = 30.0
    #: Obergrenze des Heissspeichers. 0 = unbegrenzt. Bei Ueberschreitung
    #: wird nach LRU aufgeraeumt, auch wenn die TTL noch nicht abgelaufen ist.
    hot_max_bytes: int = 50 * 1024**3
    #: Intervall des Aufraeum-Laeufers.
    reaper_interval_seconds: int = 300
    #: Sicherheitsabstand: nie loeschen, solange ein Stream juenger als X Sekunden
    #: darauf zugegriffen hat (schuetzt laufende Wiedergabe vor dem Reaper).
    hot_grace_seconds: int = 120

    # -------------------------------------------------------------- yt-dlp
    #: Mindesthoehe fuer das Archiv. Gibt es bei der Quelle nichts in dieser
    #: Hoehe, wird das Beste genommen, was sie hat - ein altes 720p-Video
    #: soll nicht ungesichert bleiben. Gibt es aber mehr und der Download
    #: liefert trotzdem weniger, gilt das als gestoerte Kette und wird
    #: verworfen (siehe check_not_degraded).
    archive_min_height: int = 1080
    #: Obergrenze, 0 = keine. Ein 4K-Video belegt grob das Drei- bis
    #: Vierfache von 1080p - wer Platz sparen will, setzt hier 1440 oder 1080.
    archive_max_height: int = 0
    #: Eigener yt-dlp-Format-Selektor. Leer = aus den beiden Hoehen abgeleitet.
    ytdlp_format: str | None = None
    ytdlp_cookies_file: Path | None = None
    ytdlp_concurrent_fragments: int = 4
    #: Bandbreitenlimit je Download, z.B. "5M". Leer = unbegrenzt.
    ytdlp_ratelimit: str | None = None
    #: Wartezeit zwischen Videos, entschaerft Rate-Limiting.
    ytdlp_sleep_interval: float = 2.0
    ytdlp_max_sleep_interval: float = 6.0
    #: Wartezeit zwischen einzelnen HTTP-Anfragen an YouTube, nicht nur zwischen
    #: Videos. Der wirksamere Hebel gegen "Sign in to confirm you're not a bot":
    #: Ein einziger Download stellt ein Dutzend Anfragen, und gezaehlt werden
    #: die, nicht die Videos. 0 = aus.
    ytdlp_sleep_requests: float = 0.0
    #: Welche YouTube-Clients yt-dlp anfragen soll, z.B. "tv,web_safari".
    #: Leer = yt-dlp entscheidet selbst, und das ist der Normalfall.
    #:
    #: Ein Notausgang, kein Regler: Wenn YouTube einen Client dichtmacht und
    #: yt-dlp noch nicht nachgezogen hat, laesst sich hier ohne neues Image
    #: umschalten. Falsch gesetzt richtet er Schaden an - ein Client, den
    #: YouTube nicht mehr bedient, liefert nur noch 360p.
    ytdlp_player_clients: Annotated[list[str], NoDecode] = Field(default_factory=list)
    write_subtitles: bool = True
    write_auto_subtitles: bool = False
    subtitle_languages: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["de", "en"]
    )
    write_comments: bool = False
    sponsorblock: bool = True

    # ----------------------------------------------------------------- VPN
    #: Hauptschalter fuer die WireGuard-Tunnel. Aus heisst: genau ein Ausgang,
    #: naemlich die eigene Leitung - der Zustand vor dieser Funktion.
    #:
    #: Der Sinn ist Bandbreite, nicht Verschleierung: YouTube zaehlt je
    #: IP-Adresse und laesst als Gast rund 300 Videos in der Stunde durch. Vier
    #: Tunnel sind vier Adressen. Faellt einer in die Sperre, wird gewechselt
    #: statt angehalten.
    vpn_aktiv: bool = False
    #: Bei eingeschaltetem VPN die eigene Leitung NICHT mitbenutzen.
    #:
    #: Standard an, und das ist die vorsichtige Vorgabe: Sonst faellt das
    #: Archiv beim Ausfall aller Tunnel stillschweigend auf die Hausanschluss-
    #: adresse zurueck - also genau auf die, die man aus dem Spiel nehmen
    #: wollte, und ohne dass es jemandem auffiele.
    vpn_nur_tunnel: bool = True
    #: Programm, das WireGuard im Benutzerraum spricht und als SOCKS5-Proxy
    #: anbietet. Im mitgelieferten Container liegt es unter /usr/local/bin.
    wireproxy_path: str = "wireproxy"

    # ------------------------------------------------------------- Worker
    #: Parallele Downloads. Bewusst niedrig: YouTube drosselt pro IP-Adresse,
    #: nicht pro Prozess - als Gast liegt die Grenze bei rund 300 Videos je
    #: Stunde. Wer hier hochdreht, wird nicht schneller fertig, sondern
    #: voruebergehend gesperrt.
    download_concurrency: int = Field(default=1, ge=1, le=16)
    encode_concurrency: int = Field(default=1, ge=1, le=16)
    #: Standardintervall fuer den Kanal-Abgleich in Stunden.
    default_sync_interval_hours: float = 12.0
    sync_scheduler_interval_seconds: int = 600

    # -------------------------------------------------------------- Extras
    ffmpeg_path: str = "ffmpeg"
    ffprobe_path: str = "ffprobe"

    @model_validator(mode="before")
    @classmethod
    def _leere_werte_verwerfen(cls, daten: object) -> object:
        """Behandelt eine leer gelassene Umgebungsvariable als "nicht gesetzt".

        Unraid uebergibt jede Variable seines Templates an den Container, auch
        die, die der Nutzer gar nicht ausgefuellt hat - dann eben als leerer
        String. Ohne diese Bereinigung ist ein leeres Feld nicht dasselbe wie
        ein fehlendes: ``YTA_YTDLP_COOKIES_FILE=""`` wurde zu ``Path(".")``,
        und da ein Path immer wahr ist, hat yt-dlp anschliessend das
        Arbeitsverzeichnis als Cookie-Datei zu lesen versucht. Ergebnis war
        ein Serverfehler bei jedem Kanal, den man hinzufuegen wollte.

        Bei Zahlenfeldern waere es noch frueher aufgefallen: ``YTA_AV1_CRF=""``
        haette den Dienst gar nicht erst starten lassen.
        """
        if isinstance(daten, dict):
            return {
                k: v for k, v in daten.items() if not (isinstance(v, str) and not v.strip())
            }
        return daten

    @field_validator("cors_origins", "subtitle_languages", "ytdlp_player_clients", mode="before")
    @classmethod
    def _split_csv(cls, v: object) -> object:
        """Nimmt Kommalisten und JSON-Listen gleichermassen an.

        Die Felder tragen dafuer ``NoDecode``, und das ist kein Beiwerk: Ohne
        das versucht pydantic-settings bei Listen-Feldern zuerst selbst
        ``json.loads`` - noch bevor dieser Validator ueberhaupt laeuft. Eine
        ganz normale Eingabe wie ``de,en`` fuehrt dann zu einem
        JSONDecodeError, an dem der Dienst schon beim Start scheitert.

        Genau so ist der erste Unraid-Start gescheitert: Im Template stand
        ``YTA_SUBTITLE_LANGUAGES=de,en``.
        """
        if isinstance(v, str):
            text = v.strip()
            if text.startswith("["):
                import json

                return json.loads(text)
            return [item.strip() for item in text.split(",") if item.strip()]
        return v

    def format_selector(self) -> str:
        """Der yt-dlp-Format-Selektor, abgeleitet aus Mindest- und Hoechsthoehe.

        Die Kette faellt von links nach rechts durch: erst bestes Video ab der
        Mindesthoehe, dann - falls die Quelle das nicht hat - das beste
        vorhandene, zuletzt ein fertig gemischtes Format. "Mindestens 1080p"
        heisst also NICHT "hoechstens 1080p": Bietet die Quelle 4K, wird 4K
        geladen, solange keine Obergrenze gesetzt ist.

        Gemessen wird die **kurze Seite**, nicht die Hoehe - so, wie YouTube
        selbst zaehlt: Es nennt das Format 1080x1920 eines hochkantigen Videos
        ``1080p``. yt-dlp meldet dafuer ``height: 1920``.

        Ein reiner Hoehenfilter ist deshalb bei Hochkant falsch, und zwar in
        beide Richtungen. ``[height>=1080]`` liess bei einem senkrechten Video
        die 720er-Fassung (720x1280) durch, weil 1280 groesser als 1080 ist -
        genau daran sind bei einem echten Kanalabgleich reihenweise Downloads
        gescheitert. Und ``[height<=1080]`` haette umgekehrt die volle
        1080er-Fassung ausgeschlossen, weil sie 1920 hoch ist.

        Die Loesung fuer die Untergrenze ist einfach: "kurze Seite >= n" ist
        dasselbe wie "beide Seiten >= n". Fuer die Obergrenze geht das nicht in
        einem Ausdruck - yt-dlp kennt kein ODER innerhalb eines Filters -,
        deshalb zwei Zweige: erst quer (Hoehe ist die kurze Seite), dann
        hochkant (Breite ist die kurze Seite). Der erste passende gewinnt.
        """
        if self.ytdlp_format:
            return self.ytdlp_format

        n, c = self.archive_min_height, self.archive_max_height
        mind = f"[height>={n}][width>={n}]" if n > 0 else ""
        stufen: list[str] = []

        if c > 0:
            if mind:
                stufen.append(f"bestvideo{mind}[height<={c}]+bestaudio")
                stufen.append(f"bestvideo{mind}[width<={c}]+bestaudio")
            stufen.append(f"bestvideo[height<={c}]+bestaudio")
            stufen.append(f"bestvideo[width<={c}]+bestaudio")
        elif mind:
            stufen.append(f"bestvideo{mind}+bestaudio")

        # Bietet die Quelle die Untergrenze nicht, wird das Beste genommen, was
        # es gibt. Ein altes 720p-Video soll nicht ungesichert bleiben.
        stufen.append("bestvideo+bestaudio")
        stufen.append("best")
        return "/".join(stufen)

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.bundle_dir, self.cache_dir, self.thumb_dir, self.tmp_dir):
            d.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
