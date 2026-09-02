"""Datenmodell des Archivs.

Leitgedanke: Die Datenbank haelt ausschliesslich Metadaten und Zustand. Der
eigentliche Medieninhalt liegt im Kaltspeicher (ein ZIP-Buendel je Video) und
wird bei Bedarf in den Heissspeicher entpackt. Es gibt daher zwei getrennte
Groessenangaben je Video: die des Buendels und die der Originalquelle.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


# --------------------------------------------------------------------- Enums


class VideoStatus(StrEnum):
    """Lebenszyklus eines Videos im Archiv."""

    NEW = "new"  # entdeckt, noch nichts geladen
    QUEUED = "queued"  # zum Archivieren vorgemerkt
    DOWNLOADING = "downloading"
    REMUXING = "remuxing"
    ENCODING = "encoding"
    BUNDLING = "bundling"
    ARCHIVED = "archived"  # Buendel liegt vollstaendig im Kaltspeicher
    FAILED = "failed"
    UNAVAILABLE = "unavailable"  # bei der Quelle geloescht/privat
    SKIPPED = "skipped"  # bewusst nicht archiviert


class PlaylistKind(StrEnum):
    """Auch die Kanal-Tabs werden als Playlist gefuehrt, damit das UI alles
    ueber denselben Weg rendern kann."""

    UPLOADS = "uploads"
    SHORTS = "shorts"
    LIVE = "live"
    PLAYLIST = "playlist"  # echte, vom Kanal angelegte Playlist


class JobType(StrEnum):
    CHANNEL_SYNC = "channel_sync"
    PLAYLIST_SYNC = "playlist_sync"
    VIDEO_ARCHIVE = "video_archive"
    #: Recodierung eines bereits archivierten Videos. Bewusst eine eigene
    #: Warteschlange: Ein Kanal mit 500 Stunden Material braucht rund 425
    #: CPU-Stunden zum Recodieren. Waere das Teil des Archivierens, waere das
    #: Video erst nach Tagen sichtbar - so ist es sofort da und wird spaeter
    #: im Hintergrund verkleinert.
    VIDEO_RECODE = "video_recode"
    VIDEO_PREPARE = "video_prepare"  # Heisskopie herstellen


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


class HotCopyStatus(StrEnum):
    PREPARING = "preparing"
    READY = "ready"
    FAILED = "failed"


# ------------------------------------------------------------------- Tabellen


class Channel(Base):
    __tablename__ = "channels"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # UC...
    handle: Mapped[str | None] = mapped_column(String(128), index=True)
    name: Mapped[str] = mapped_column(String(512))
    description: Mapped[str | None] = mapped_column(Text)
    avatar_file: Mapped[str | None] = mapped_column(String(512))
    banner_file: Mapped[str | None] = mapped_column(String(512))
    subscriber_count: Mapped[int | None] = mapped_column(BigInteger)

    # Abgleich
    subscribed: Mapped[bool] = mapped_column(Boolean, default=True)
    sync_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    sync_interval_hours: Mapped[float | None] = mapped_column(Float)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Archivierungs-Regeln je Kanal (ueberschreiben die globalen Defaults)
    archive_codec: Mapped[str | None] = mapped_column(String(16))
    format_selector: Mapped[str | None] = mapped_column(String(512))
    #: Beim Abgleich gefundene Videos sofort zum Herunterladen einreihen.
    #:
    #: Standard ist bewusst AUS: Ein Kanal mit tausenden Videos wuerde sonst
    #: beim Aufnehmen eine Warteschlange erzeugen, die tagelang laeuft und
    #: hunderte Gigabyte belegt - bevor man ueberhaupt gesehen hat, was der
    #: Kanal enthaelt. Erst erfassen, dann entscheiden.
    auto_archive: Mapped[bool] = mapped_column(Boolean, default=False)
    archive_shorts: Mapped[bool] = mapped_column(Boolean, default=False)
    archive_live: Mapped[bool] = mapped_column(Boolean, default=False)
    #: Nur Videos ab diesem Datum holen. Schuetzt vor dem versehentlichen
    #: Herunterladen eines 10-Jahre-Archivs.
    archive_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    keep_last_n: Mapped[int | None] = mapped_column(Integer)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    videos: Mapped[list[Video]] = relationship(
        back_populates="channel", cascade="all, delete-orphan"
    )
    playlists: Mapped[list[Playlist]] = relationship(
        back_populates="channel", cascade="all, delete-orphan"
    )


class Playlist(Base):
    __tablename__ = "playlists"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    channel_id: Mapped[str | None] = mapped_column(
        ForeignKey("channels.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(16), default=PlaylistKind.PLAYLIST)
    title: Mapped[str] = mapped_column(String(512))
    description: Mapped[str | None] = mapped_column(Text)
    thumb_file: Mapped[str | None] = mapped_column(String(512))
    #: Anzahl laut Quelle - kann groesser sein als die Zahl archivierter Videos.
    item_count: Mapped[int] = mapped_column(Integer, default=0)
    sync_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    channel: Mapped[Channel | None] = relationship(back_populates="playlists")
    items: Mapped[list[PlaylistItem]] = relationship(
        back_populates="playlist",
        cascade="all, delete-orphan",
        order_by="PlaylistItem.position",
    )


class Video(Base):
    __tablename__ = "videos"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # 11-stellige YouTube-ID
    channel_id: Mapped[str | None] = mapped_column(
        ForeignKey("channels.id", ondelete="CASCADE"), index=True
    )

    title: Mapped[str] = mapped_column(String(1024))
    description: Mapped[str | None] = mapped_column(Text)
    duration_s: Mapped[int | None] = mapped_column(Integer)
    upload_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    view_count: Mapped[int | None] = mapped_column(BigInteger)
    like_count: Mapped[int | None] = mapped_column(BigInteger)
    thumb_file: Mapped[str | None] = mapped_column(String(512))
    is_short: Mapped[bool] = mapped_column(Boolean, default=False)
    was_live: Mapped[bool] = mapped_column(Boolean, default=False)
    tags: Mapped[str | None] = mapped_column(Text)  # JSON-Liste

    # ---- Archivzustand
    status: Mapped[str] = mapped_column(String(16), default=VideoStatus.NEW, index=True)
    status_message: Mapped[str | None] = mapped_column(Text)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retry_count: Mapped[int] = mapped_column(Integer, default=0)

    # ---- Kaltspeicher
    bundle_file: Mapped[str | None] = mapped_column(String(512))
    bundle_bytes: Mapped[int | None] = mapped_column(BigInteger)
    #: Groesse der heruntergeladenen Quelldatei vor Recodierung. Nur zusammen
    #: mit bundle_bytes laesst sich die tatsaechliche Ersparnis ausweisen.
    source_bytes: Mapped[int | None] = mapped_column(BigInteger)
    #: Name der Mediendatei innerhalb des ZIP.
    media_name: Mapped[str | None] = mapped_column(String(512))
    video_codec: Mapped[str | None] = mapped_column(String(32))
    audio_codec: Mapped[str | None] = mapped_column(String(32))
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    fps: Mapped[float | None] = mapped_column(Float)
    recoded: Mapped[bool] = mapped_column(Boolean, default=False)

    # ---- Wiedergabezustand
    watched: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    progress_s: Mapped[float] = mapped_column(Float, default=0.0)
    last_watched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    watch_count: Mapped[int] = mapped_column(Integer, default=0)
    favorite: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    channel: Mapped[Channel | None] = relationship(back_populates="videos")
    chapters: Mapped[list[Chapter]] = relationship(
        back_populates="video", cascade="all, delete-orphan", order_by="Chapter.start_s"
    )
    subtitles: Mapped[list[Subtitle]] = relationship(
        back_populates="video", cascade="all, delete-orphan"
    )
    hot_copies: Mapped[list[HotCopy]] = relationship(
        back_populates="video", cascade="all, delete-orphan"
    )
    playlist_items: Mapped[list[PlaylistItem]] = relationship(
        back_populates="video", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_videos_channel_upload", "channel_id", "upload_date"),
        Index("ix_videos_status_created", "status", "created_at"),
    )

    @property
    def saved_bytes(self) -> int | None:
        if self.source_bytes is None or self.bundle_bytes is None:
            return None
        return self.source_bytes - self.bundle_bytes


class PlaylistItem(Base):
    __tablename__ = "playlist_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    playlist_id: Mapped[str] = mapped_column(
        ForeignKey("playlists.id", ondelete="CASCADE"), index=True
    )
    video_id: Mapped[str] = mapped_column(ForeignKey("videos.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)

    playlist: Mapped[Playlist] = relationship(back_populates="items")
    video: Mapped[Video] = relationship(back_populates="playlist_items")

    #: Eindeutig ist die POSITION, nicht das Video.
    #:
    #: Naheliegend waere (playlist_id, video_id) - aber echte Playlists
    #: enthalten dasselbe Video durchaus mehrfach, etwa einen Vorspann am
    #: Anfang und am Ende. Mit der falschen Regel bricht der Kanalabgleich
    #: mitten im Lauf ab; auf der Blender-Kanalseite passiert genau das.
    __table_args__ = (UniqueConstraint("playlist_id", "position", name="uq_playlist_position"),)


class Chapter(Base):
    __tablename__ = "chapters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    video_id: Mapped[str] = mapped_column(ForeignKey("videos.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(512))
    start_s: Mapped[float] = mapped_column(Float)
    end_s: Mapped[float | None] = mapped_column(Float)

    video: Mapped[Video] = relationship(back_populates="chapters")


class Subtitle(Base):
    __tablename__ = "subtitles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    video_id: Mapped[str] = mapped_column(ForeignKey("videos.id", ondelete="CASCADE"), index=True)
    language: Mapped[str] = mapped_column(String(16))
    is_auto: Mapped[bool] = mapped_column(Boolean, default=False)
    #: Dateiname innerhalb des ZIP-Buendels.
    name_in_bundle: Mapped[str] = mapped_column(String(512))

    video: Mapped[Video] = relationship(back_populates="subtitles")

    __table_args__ = (UniqueConstraint("video_id", "language", "is_auto", name="uq_sub_lang"),)


class HotCopy(Base):
    """Eine entpackte, abspielbare Datei mit Ablaufdatum.

    Je Video kann es mehrere Varianten geben (z.B. das AV1-Original direkt aus
    dem Buendel und zusaetzlich eine H.264-Fassung fuer alte Clients), deshalb
    eine eigene Tabelle statt Feldern am Video.
    """

    __tablename__ = "hot_copies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    video_id: Mapped[str] = mapped_column(ForeignKey("videos.id", ondelete="CASCADE"), index=True)
    #: "source" = unveraendert aus dem Buendel, sonst Zielcodec z.B. "h264".
    variant: Mapped[str] = mapped_column(String(32), default="source")
    path: Mapped[str] = mapped_column(String(1024))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    mime_type: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default=HotCopyStatus.PREPARING, index=True)
    error: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    #: Jeder Range-Request des Players frischt diesen Zeitstempel auf.
    last_access_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    #: Wiedergabe-Lease statt Zaehler. Ein Zaehler leckt, sobald ein Client
    #: abstuerzt oder den Tab schliesst, ohne sich abzumelden - die Datei waere
    #: dann fuer immer "in Benutzung". Jeder Herzschlag des Players schiebt
    #: diesen Zeitpunkt nach vorn; laeuft er ab, gilt die Wiedergabe als beendet.
    active_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)

    video: Mapped[Video] = relationship(back_populates="hot_copies")

    __table_args__ = (UniqueConstraint("video_id", "variant", name="uq_hot_variant"),)


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    type: Mapped[str] = mapped_column(String(32), index=True)
    #: Kanal-, Playlist- oder Video-ID, je nach Typ.
    target_id: Mapped[str | None] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(16), default=JobStatus.PENDING, index=True)
    priority: Mapped[int] = mapped_column(Integer, default=100)  # kleiner = wichtiger
    progress: Mapped[float] = mapped_column(Float, default=0.0)  # 0..1
    message: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[str | None] = mapped_column(Text)  # JSON

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_jobs_status_priority", "status", "priority", "created_at"),)


class Setting(Base):
    """Zur Laufzeit im UI aenderbare Einstellungen. Umgebungsvariablen bleiben
    der Startwert, dieser Tabelleneintrag gewinnt zur Laufzeit."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
