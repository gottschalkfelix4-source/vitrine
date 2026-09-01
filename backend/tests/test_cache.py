"""Tests der Heissspeicher-Verwaltung.

Hier steckt die Logik, die entscheidet, wann eine entpackte Datei wieder
verschwindet. Ein Fehler faellt im Betrieb entweder gar nicht auf (der Speicher
laeuft langsam voll) oder sehr unangenehm (die Datei wird mitten in der
Wiedergabe geloescht) - beides Gruende, das hier ordentlich abzudecken.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.models import Base, Channel, HotCopy, HotCopyStatus, Video, utcnow
from app.services import cache


@pytest.fixture
def datendir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "hot_ttl_hours", 24.0)
    monkeypatch.setattr(settings, "hot_ttl_after_playback_minutes", 30.0)
    monkeypatch.setattr(settings, "hot_max_bytes", 0)  # Budget aus, sofern nicht gesetzt
    monkeypatch.setattr(settings, "hot_grace_seconds", 120)
    settings.ensure_dirs()
    return tmp_path


@pytest.fixture
def db(datendir: Path) -> Session:
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng, expire_on_commit=False)()
    s.add(Channel(id="UCtest", name="Testkanal"))
    s.commit()
    return s


def _video(db: Session, vid: str = "v1") -> Video:
    v = Video(id=vid, channel_id="UCtest", title=f"Video {vid}")
    db.add(v)
    db.commit()
    return v


def _kopie(db: Session, vid: str, groesse: int = 1000, variant: str = "source") -> HotCopy:
    v = _video(db, vid)
    p = settings.cache_dir / f"{vid}.{variant}.mp4"
    p.write_bytes(b"x" * groesse)
    return cache.register(db, v, variant, p, "video/mp4")


# ----------------------------------------------------------------- Lebenszyklus


def test_registrieren_setzt_lange_frist(db: Session):
    hot = _kopie(db, "v1")
    assert hot.status == HotCopyStatus.READY
    assert hot.size_bytes == 1000
    spanne = cache._as_utc(hot.expires_at) - utcnow()
    assert timedelta(hours=23) < spanne <= timedelta(hours=24)


def test_wiedergabeende_verkuerzt_die_frist(db: Session):
    hot = _kopie(db, "v1")
    cache.end_playback(db, hot)
    spanne = cache._as_utc(hot.expires_at) - utcnow()
    assert spanne <= timedelta(minutes=30)
    assert not cache.is_leased(hot)


def test_herzschlag_verlaengert_lease_und_frist(db: Session):
    hot = _kopie(db, "v1")
    cache.end_playback(db, hot)  # kurze Frist
    cache.heartbeat(db, hot)  # wieder aufgenommen
    assert cache.is_leased(hot)
    assert cache._as_utc(hot.expires_at) - utcnow() > timedelta(hours=23)


# ---------------------------------------------------------------------- Reaper


def test_laufende_wiedergabe_wird_nicht_geloescht(db: Session):
    """Der wichtigste Fall: Die Datei darf unter dem laufenden Player nicht
    weggezogen werden, auch wenn die Frist formal abgelaufen ist."""
    hot = _kopie(db, "v1")
    cache.heartbeat(db, hot)
    hot.expires_at = utcnow() - timedelta(hours=1)  # Frist kuenstlich abgelaufen
    db.commit()

    stats = cache.reap(db)
    assert stats["abgelaufen"] == 0
    assert Path(hot.path).exists()


def test_abgelaufene_kopie_verschwindet(db: Session):
    hot = _kopie(db, "v1")
    pfad = Path(hot.path)
    hot.expires_at = utcnow() - timedelta(minutes=1)
    hot.last_access_at = utcnow() - timedelta(hours=2)  # ausserhalb der Gnadenfrist
    db.commit()

    stats = cache.reap(db)
    assert stats["abgelaufen"] == 1
    assert stats["bytes_frei"] == 1000
    assert not pfad.exists()
    assert db.scalar(select(HotCopy).where(HotCopy.video_id == "v1")) is None


def test_gnadenfrist_schuetzt_frisch_gelesene_datei(db: Session):
    """Ein Range-Request ohne Herzschlag - etwa ein Client, der die Lease nicht
    unterstuetzt - darf nicht mitten im Lesen abgeraeumt werden."""
    hot = _kopie(db, "v1")
    hot.expires_at = utcnow() - timedelta(hours=1)
    hot.last_access_at = utcnow()  # gerade eben gelesen
    db.commit()

    assert cache.reap(db)["abgelaufen"] == 0
    assert Path(hot.path).exists()


def test_kopie_in_vorbereitung_bleibt_unberuehrt(db: Session):
    hot = _kopie(db, "v1")
    hot.status = HotCopyStatus.PREPARING
    hot.expires_at = utcnow() - timedelta(hours=5)
    hot.last_access_at = utcnow() - timedelta(hours=5)
    db.commit()

    assert cache.reap(db)["abgelaufen"] == 0
    assert Path(hot.path).exists()


# ---------------------------------------------------------------------- Budget


def test_budget_raeumt_nach_lru(db: Session, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "hot_max_bytes", 2500)
    alt = _kopie(db, "alt", 1000)
    mittel = _kopie(db, "mittel", 1000)
    neu = _kopie(db, "neu", 1000)
    # Gesamt 3000 > 2500, also muss die aelteste weichen.
    for hot, stunden in ((alt, 10), (mittel, 5), (neu, 1)):
        hot.last_access_at = utcnow() - timedelta(hours=stunden)
    db.commit()

    stats = cache.reap(db)
    assert stats["budget"] == 1
    assert not Path(alt.path).exists()
    assert Path(mittel.path).exists() and Path(neu.path).exists()


def test_budget_verschont_laufende_wiedergabe(db: Session, monkeypatch: pytest.MonkeyPatch):
    """Selbst wenn das Budget gerissen ist, darf nichts abgeraeumt werden, das
    gerade laeuft - lieber kurzzeitig ueber dem Limit."""
    monkeypatch.setattr(settings, "hot_max_bytes", 1500)
    laeuft = _kopie(db, "laeuft", 1000)
    ruht = _kopie(db, "ruht", 1000)
    cache.heartbeat(db, laeuft)
    laeuft.last_access_at = utcnow() - timedelta(hours=10)  # aeltester Zugriff
    ruht.last_access_at = utcnow() - timedelta(hours=1)
    db.commit()

    cache.reap(db)
    assert Path(laeuft.path).exists(), "laufende Wiedergabe wurde abgeraeumt"
    assert not Path(ruht.path).exists()


def test_budget_null_bedeutet_unbegrenzt(db: Session, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "hot_max_bytes", 0)
    for i in range(5):
        k = _kopie(db, f"v{i}", 10_000)
        k.last_access_at = utcnow() - timedelta(hours=i + 1)
    db.commit()
    assert cache.reap(db)["budget"] == 0


# -------------------------------------------------------------------- Verwaiste


def test_verwaiste_dateien_werden_entfernt(db: Session):
    """Nach einem harten Neustart koennen Dateien ohne Datenbankeintrag
    zurueckbleiben. Die faende sonst nie jemand wieder."""
    import os
    import time

    verwaist = settings.cache_dir / "unbekannt.mp4"
    verwaist.write_bytes(b"y" * 500)
    alt = time.time() - 3600
    os.utime(verwaist, (alt, alt))  # aelter als die Gnadenfrist

    stats = cache.reap(db)
    assert stats["verwaist"] == 1
    assert not verwaist.exists()


def test_frische_verwaiste_datei_bleibt(db: Session):
    """Ein gerade laufendes Entpacken hat noch keinen Eintrag - das darf der
    Reaper nicht unter sich wegloeschen."""
    frisch = settings.cache_dir / "gerade_entpackt.mp4.part"
    frisch.write_bytes(b"z" * 100)
    assert cache.reap(db)["verwaist"] == 0
    assert frisch.exists()


def test_registrierte_datei_gilt_nicht_als_verwaist(db: Session):
    hot = _kopie(db, "v1")
    assert cache.reap(db)["verwaist"] == 0
    assert Path(hot.path).exists()


# --------------------------------------------------------------------- Sonstiges


def test_belegung(db: Session, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "hot_max_bytes", 5000)
    _kopie(db, "a", 1000)
    b = _kopie(db, "b", 2000)
    cache.heartbeat(db, b)

    u = cache.usage(db)
    assert u == {"anzahl": 2, "bytes": 3000, "limit_bytes": 5000, "in_wiedergabe": 1}


def test_erneutes_registrieren_ueberschreibt(db: Session):
    hot = _kopie(db, "v1", 1000)
    erste_id = hot.id
    v = db.get(Video, "v1")
    p = settings.cache_dir / "v1.source.mp4"
    p.write_bytes(b"x" * 4000)
    wieder = cache.register(db, v, "source", p, "video/mp4")
    assert wieder.id == erste_id
    assert wieder.size_bytes == 4000
    assert len(list(db.scalars(select(HotCopy)))) == 1


def test_drop_entfernt_auch_teildatei(db: Session):
    hot = _kopie(db, "v1")
    teil = Path(hot.path + ".part")
    teil.write_bytes(b"halb")
    cache.drop(db, hot)
    assert not Path(hot.path).exists()
    assert not teil.exists()
