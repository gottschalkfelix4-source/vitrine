"""Zentrale Konfiguration.

Alles ueber Umgebungsvariablen mit Praefix ``YTA_`` steuerbar, damit derselbe
Container lokal (Docker Desktop) und spaeter auf Unraid ohne Codeaenderung laeuft.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


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
    )

    # ------------------------------------------------------------------ App
    app_name: str = "Vitrine"
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"
    timezone: str = "Europe/Berlin"
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])

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
    write_subtitles: bool = True
    write_auto_subtitles: bool = False
    subtitle_languages: list[str] = Field(default_factory=lambda: ["de", "en"])
    write_comments: bool = False
    sponsorblock: bool = True

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

    @field_validator("cors_origins", "subtitle_languages", mode="before")
    @classmethod
    def _split_csv(cls, v: object) -> object:
        """Erlaubt sowohl JSON-Listen als auch simple Kommalisten in der .env."""
        if isinstance(v, str) and not v.strip().startswith("["):
            return [item.strip() for item in v.split(",") if item.strip()]
        return v

    def format_selector(self) -> str:
        """Der yt-dlp-Format-Selektor, abgeleitet aus Mindest- und Hoechsthoehe.

        Die Kette faellt von links nach rechts durch: erst bestes Video ab der
        Mindesthoehe, dann - falls die Quelle das nicht hat - das beste
        vorhandene, zuletzt ein fertig gemischtes Format. "Mindestens 1080p"
        heisst also NICHT "hoechstens 1080p": Bietet die Quelle 4K, wird 4K
        geladen, solange keine Obergrenze gesetzt ist.

        Bei hochkantigen Videos zaehlt yt-dlp die lange Seite als Hoehe. Ein
        Short in voller Qualitaet ist 1080x1920 - eine Obergrenze von 1080
        haette davon die 608x1080-Fassung gewaehlt.
        """
        if self.ytdlp_format:
            return self.ytdlp_format
        deckel = f"[height<={self.archive_max_height}]" if self.archive_max_height > 0 else ""
        stufen: list[str] = []
        if self.archive_min_height > 0:
            stufen.append(f"bestvideo[height>={self.archive_min_height}]{deckel}+bestaudio")
        stufen.append(f"bestvideo{deckel}+bestaudio")
        stufen.append(f"best{deckel}")
        stufen.append("best")
        return "/".join(stufen)

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.bundle_dir, self.cache_dir, self.thumb_dir, self.tmp_dir):
            d.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
