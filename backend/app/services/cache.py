"""Heissspeicher: entpackte, abspielbare Dateien mit Verfallsdatum.

Das ist der Teil, der das Archiv klein haelt: Im Normalzustand existiert ein
Video nur als Buendel im Kaltspeicher. Erst wenn jemand es abspielen will und
ein Direktstream aus dem Buendel nicht geht (weil transkodiert werden muss),
entsteht eine Heisskopie - und die verschwindet wieder, sobald sie nicht mehr
gebraucht wird.

Wann eine Heisskopie verschwindet, entscheiden drei Regeln, die in dieser
Reihenfolge greifen:

1. *Lease*: Solange der Player Herzschlaege schickt, wird nichts geloescht.
2. *TTL*: Nach dem Ende der Wiedergabe eine kurze Frist, sonst die lange
   Standardfrist ab letztem Zugriff.
3. *Budget*: Ueberschreitet der Heissspeicher sein Limit, fliegt zusaetzlich
   das am laengsten Ungenutzte raus, auch wenn dessen TTL noch laeuft.

Zur Lease statt eines Zaehlers: Ein Zaehler "wieviele schauen gerade" leckt
zwangslaeufig - schliesst jemand den Tab, stuerzt der Browser ab oder faellt
das WLAN aus, wird nie heruntergezaehlt und die Datei bleibt ewig liegen. Eine
ablaufende Lease heilt sich dagegen von selbst.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import HotCopy, HotCopyStatus, Video, utcnow

log = logging.getLogger(__name__)

#: Wie lange ein einzelner Herzschlag die Wiedergabe am Leben haelt. Der Player
#: schlaegt haeufiger, als dieser Wert gross ist, damit ein verlorener
#: Herzschlag nicht sofort zum Abraeumen fuehrt.
LEASE_SECONDS = 90


def hot_path_for(video_id: str, variant: str, suffix: str) -> Path:
    return settings.cache_dir / f"{video_id}.{variant}{suffix}"


def _size_on_disk(p: Path) -> int:
    try:
        return p.stat().st_size
    except OSError:
        return 0


# ------------------------------------------------------------------ Lease/TTL


def heartbeat(db: Session, hot: HotCopy) -> HotCopy:
    """Player meldet: laeuft noch. Verlaengert Lease und Zugriffszeitpunkt."""
    now = utcnow()
    hot.last_access_at = now
    hot.active_until = now + timedelta(seconds=LEASE_SECONDS)
    # Waehrend der Wiedergabe die lange Frist ansetzen; das kurze Fenster
    # kommt erst beim ausdruecklichen Ende.
    hot.expires_at = now + timedelta(hours=settings.hot_ttl_hours)
    db.commit()
    return hot


def end_playback(db: Session, hot: HotCopy) -> HotCopy:
    """Player meldet: fertig. Setzt die kurze Frist an.

    Nicht sofort loeschen: Wer ein Video zu Ende schaut und gleich noch einmal
    hineinspringt, soll nicht auf ein erneutes Entpacken warten.
    """
    now = utcnow()
    hot.active_until = now
    hot.expires_at = now + timedelta(minutes=settings.hot_ttl_after_playback_minutes)
    db.commit()
    return hot


def touch(db: Session, hot: HotCopy) -> None:
    """Leichtgewichtige Zugriffsmarkierung fuer jeden Range-Request."""
    hot.last_access_at = utcnow()
    db.commit()


def is_leased(hot: HotCopy, now: datetime | None = None) -> bool:
    if hot.active_until is None:
        return False
    now = now or utcnow()
    au = hot.active_until
    if au.tzinfo is None:  # SQLite gibt naive Zeitstempel zurueck
        au = au.replace(tzinfo=UTC)
    return au > now


# -------------------------------------------------------------- Registrierung


def register(db: Session, video: Video, variant: str, path: Path, mime: str) -> HotCopy:
    """Traegt eine fertige Heisskopie ein bzw. aktualisiert einen Eintrag."""
    hot = db.scalar(select(HotCopy).where(HotCopy.video_id == video.id, HotCopy.variant == variant))
    now = utcnow()
    if hot is None:
        hot = HotCopy(video_id=video.id, variant=variant)
        db.add(hot)
    hot.path = str(path)
    hot.size_bytes = _size_on_disk(path)
    hot.mime_type = mime
    hot.status = HotCopyStatus.READY
    hot.error = None
    hot.created_at = now
    hot.last_access_at = now
    hot.expires_at = now + timedelta(hours=settings.hot_ttl_hours)
    db.commit()
    return hot


def mark_failed(db: Session, hot: HotCopy, error: str) -> None:
    hot.status = HotCopyStatus.FAILED
    hot.error = error[:2000]
    db.commit()


def drop(db: Session, hot: HotCopy) -> int:
    """Loescht Datei und Eintrag. Liefert die freigegebenen Bytes."""
    freed = 0
    p = Path(hot.path)
    try:
        freed = _size_on_disk(p)
        p.unlink(missing_ok=True)
        # Ein liegengebliebener Teil-Download aus einem Abbruch.
        p.with_suffix(p.suffix + ".part").unlink(missing_ok=True)
    except OSError as e:
        log.warning("Heisskopie %s liess sich nicht loeschen: %s", p, e)
        return 0
    db.delete(hot)
    db.commit()
    return freed


# -------------------------------------------------------------------- Reaper


def _as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def reap(db: Session, *, now: datetime | None = None) -> dict[str, int]:
    """Raeumt den Heissspeicher auf.

    Laeuft periodisch im Hintergrund und zusaetzlich nach jedem
    Wiedergabe-Ende. Gibt eine kleine Statistik zurueck, damit das UI zeigen
    kann, was passiert ist.
    """
    now = now or utcnow()
    grace = timedelta(seconds=settings.hot_grace_seconds)
    stats = {"abgelaufen": 0, "budget": 0, "verwaist": 0, "bytes_frei": 0}

    kopien = list(db.scalars(select(HotCopy)))

    # --- 1. Abgelaufene
    for hot in kopien:
        if hot.status == HotCopyStatus.PREPARING:
            continue  # laeuft gerade, Finger weg
        if is_leased(hot, now):
            continue
        last = _as_utc(hot.last_access_at)
        if last and now - last < grace:
            continue  # gerade eben noch gelesen - laufender Stream
        exp = _as_utc(hot.expires_at)
        if exp is not None and exp <= now:
            stats["bytes_frei"] += drop(db, hot)
            stats["abgelaufen"] += 1

    # --- 2. Budget
    if settings.hot_max_bytes > 0:
        alle = list(db.scalars(select(HotCopy)))
        # Das Limit misst den tatsaechlich belegten Platz, also alles auf der
        # Platte - auch das, was gerade laeuft und nicht geloescht werden darf.
        # Nur so raeumt der Reaper wirklich frei, wenn ein grosser Teil des
        # Budgets von einer laufenden Wiedergabe blockiert wird.
        belegt = sum(h.size_bytes or 0 for h in alle)
        if belegt > settings.hot_max_bytes:
            # Geraeumt wird dagegen nur, was fertig und nicht in Benutzung ist.
            uebrig = [
                h for h in alle if h.status == HotCopyStatus.READY and not is_leased(h, now)
            ]
            # Aeltester Zugriff zuerst raus (LRU).
            uebrig.sort(key=lambda h: _as_utc(h.last_access_at) or now)
            for hot in uebrig:
                if belegt <= settings.hot_max_bytes:
                    break
                last = _as_utc(hot.last_access_at)
                if last and now - last < grace:
                    continue
                groesse = hot.size_bytes or 0
                if drop(db, hot):
                    belegt -= groesse
                    stats["budget"] += 1
                    stats["bytes_frei"] += groesse

    # --- 3. Verwaiste Dateien im Cache-Verzeichnis
    bekannt = {Path(h.path).resolve() for h in db.scalars(select(HotCopy))}
    if settings.cache_dir.is_dir():
        for p in settings.cache_dir.iterdir():
            if not p.is_file():
                continue
            if p.resolve() in bekannt:
                continue
            # Abgebrochene Vorbereitungen erst nach der Gnadenfrist entfernen,
            # damit ein gerade laufendes Entpacken nicht abgeraeumt wird.
            try:
                alter = now - datetime.fromtimestamp(p.stat().st_mtime, tz=UTC)
            except OSError:
                continue
            if alter < grace:
                continue
            groesse = _size_on_disk(p)
            try:
                p.unlink()
            except OSError as e:
                log.warning("verwaiste Datei %s liess sich nicht loeschen: %s", p, e)
                continue
            stats["verwaist"] += 1
            stats["bytes_frei"] += groesse

    if any(v for k, v in stats.items() if k != "bytes_frei"):
        log.info(
            "Heissspeicher aufgeraeumt: %d abgelaufen, %d wegen Budget, %d verwaist, %.1f MB frei",
            stats["abgelaufen"], stats["budget"], stats["verwaist"], stats["bytes_frei"] / 1e6,
        )
    return stats


def usage(db: Session) -> dict[str, int]:
    """Aktuelle Belegung des Heissspeichers."""
    kopien = list(db.scalars(select(HotCopy)))
    return {
        "anzahl": len(kopien),
        "bytes": sum(h.size_bytes or 0 for h in kopien),
        "limit_bytes": settings.hot_max_bytes,
        "in_wiedergabe": sum(1 for h in kopien if is_leased(h)),
    }


def free_space() -> int:
    """Freier Platz auf dem Datentraeger des Heissspeichers."""
    try:
        return os.statvfs(settings.cache_dir).f_bavail * os.statvfs(settings.cache_dir).f_frsize
    except (AttributeError, OSError):
        # Windows kennt statvfs nicht.
        import shutil

        try:
            return shutil.disk_usage(settings.cache_dir).free
        except OSError:
            return 0
