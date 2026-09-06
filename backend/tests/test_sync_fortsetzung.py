"""Ein pausierter RSS-Abgleich muss die bereits begonnenen Sammlungsschritte beenden."""

from __future__ import annotations

from contextlib import nullcontext

import pytest
from sqlalchemy import select

from app.config import settings
from app.models import Channel, Job, JobStatus, JobType, Playlist, PlaylistItem, Video, VideoStatus
from app.services import abbruch, anfragelimit, jobs, ytdlp
from app.workers import sync
from tests.conftest import neue_sitzung


def _video(video_id: str) -> ytdlp.ListedVideo:
    return ytdlp.ListedVideo(
        id=video_id, title=video_id, duration_s=120, upload_date=None, view_count=10,
    )


@pytest.mark.parametrize("unterbrechung", ["budget", "abweisung", "shutdown"])
@pytest.mark.parametrize("schritt", ["uploads", "shorts", "playlists"])
def test_rss_neufund_bleibt_nach_pause_als_unvollstaendiger_abgleich_erkennbar(
    tmp_path, monkeypatch, unterbrechung, schritt,
):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    db = neue_sitzung()
    db.add(Channel(id="UCtest", name="Testkanal", auto_archive=True, archive_shorts=False))
    db.commit()
    rss = [_video("neu")]
    monkeypatch.setattr(ytdlp, "peek_recent", lambda _: rss)
    unterbrochen = False
    aufrufe = []

    def pause():
        nonlocal unterbrochen
        unterbrochen = True
        if unterbrechung == "budget":
            raise anfragelimit.Pause(3600)
        if unterbrechung == "shutdown":
            raise abbruch.Abgebrochen("Dienst faehrt herunter")
        raise ytdlp.Gedrosselt("HTTP Error 429")

    def eintraege(url, limit=None):
        kennung = url.split("list=")[-1]
        aufrufe.append(kennung)
        if not unterbrochen and (
            (schritt == "uploads" and kennung == "UUtest")
            or (schritt == "shorts" and kennung == "UUSHtest")
        ):
            pause()
        if kennung == "UUtest":
            return [_video("neu"), _video("kurz")]
        if kennung == "UUSHtest":
            return [_video("kurz")]
        if kennung == "PLtest":
            return [_video("neu")]
        return []

    def playlists(_):
        if not unterbrochen and schritt == "playlists":
            pause()
        return [ytdlp.ListedPlaylist(id="PLtest", title="Eigene Playlist", item_count=1)]

    monkeypatch.setattr(ytdlp, "list_entries", eintraege)
    monkeypatch.setattr(ytdlp, "list_channel_playlists", playlists)
    job = jobs.enqueue(db, JobType.CHANNEL_SYNC, "UCtest", payload={"test_metadatum": "behalten"})
    job_id = job.id
    with pytest.raises(abbruch.Abgebrochen) if unterbrechung == "shutdown" else nullcontext():
        sync.kanal_abgleichen(db, jobs.claim_next(db, [JobType.CHANNEL_SYNC]))
    assert job.status == JobStatus.PENDING
    assert job.error is None
    assert job.finished_at is None
    assert job.started_at is None
    assert job.progress == 0.0
    assert db.get(Video, "neu") is not None, "RSS-Neufund ist bereits dauerhaft gespeichert"
    assert db.get(Video, "neu").retry_count == 0
    assert db.get(Channel, "UCtest").last_synced_at is None

    # Nur gespeicherte Daten übernehmen: keine Referenzen oder lokalen Zähler
    # aus dem ersten Lauf dürfen zum erfolgreichen Fortsetzen erforderlich sein.
    db.expunge_all()
    job = db.get(Job, job_id)
    assert jobs.payload_of(job) == {"test_metadatum": "behalten", "sammlungen_offen": True}
    aufrufe.clear()
    sync.kanal_abgleichen(db, jobs.claim_next(db, [JobType.CHANNEL_SYNC]))

    assert job.status == JobStatus.DONE
    assert aufrufe == ["UUtest", "UUSHtest", "UULVtest", "PLtest"]
    assert db.get(Channel, "UCtest").last_synced_at is not None
    assert db.get(Playlist, "PLtest").last_synced_at is not None
    assert db.get(Video, "neu").status == VideoStatus.QUEUED
    assert db.get(Video, "kurz").is_short is True
    assert db.get(Video, "kurz").status == VideoStatus.NEW
    assert list(db.scalars(select(PlaylistItem.video_id).where(PlaylistItem.playlist_id == "PLtest"))) == ["neu"]
    assert jobs.payload_of(job) == {"test_metadatum": "behalten"}, "Marker erst nach dem vollständigen Abgleich entfernen"
    db.close()


def test_playlist_shutdown_stellt_ohne_fehlversuch_zurueck_und_erhaelt_die_sammlung(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    db = neue_sitzung()
    db.add(Channel(id="UCtest", name="Testkanal"))
    db.add(Playlist(id="PLtest", channel_id="UCtest", title="Vorhandene Playlist", kind="playlist", item_count=1))
    db.add(Video(id="alt", channel_id="UCtest", title="Altes Video", retry_count=2))
    db.add(PlaylistItem(playlist_id="PLtest", video_id="alt", position=0))
    db.commit()
    payload = {"einreihen": False, "test_metadatum": "behalten"}
    job_id = jobs.enqueue(db, JobType.PLAYLIST_SYNC, "PLtest", payload=payload).id

    def unterbrechen(_):
        # Auch bereits ausstehende ORM-Aenderungen muessen zurueckgerollt
        # werden, bevor der Job wieder dauerhaft in die Warteschlange kommt.
        db.get(Playlist, "PLtest").title = "Unfertige Aenderung"
        raise abbruch.Abgebrochen("Dienst faehrt herunter")

    monkeypatch.setattr(ytdlp, "list_entries", unterbrechen)
    with pytest.raises(abbruch.Abgebrochen):
        sync.playlist_abgleichen(db, jobs.claim_next(db, [JobType.PLAYLIST_SYNC]))

    db.expunge_all()
    job = db.get(Job, job_id)
    assert job.status == JobStatus.PENDING
    assert job.started_at is None
    assert job.finished_at is None
    assert job.error is None
    assert job.progress == 0.0
    assert jobs.payload_of(job) == payload
    assert db.get(Video, "alt").retry_count == 2
    assert db.get(Playlist, "PLtest").title == "Vorhandene Playlist"
    assert db.get(Playlist, "PLtest").last_synced_at is None
    assert list(db.scalars(select(PlaylistItem.video_id).where(PlaylistItem.playlist_id == "PLtest"))) == ["alt"]

    monkeypatch.setattr(ytdlp, "list_entries", lambda _: [_video("neu"), _video("alt")])
    sync.playlist_abgleichen(db, jobs.claim_next(db, [JobType.PLAYLIST_SYNC]))
    assert job.status == JobStatus.DONE
    assert db.get(Playlist, "PLtest").last_synced_at is not None
    assert list(db.scalars(
        select(PlaylistItem.video_id).where(PlaylistItem.playlist_id == "PLtest").order_by(PlaylistItem.position)
    )) == ["neu", "alt"]
    assert db.get(Video, "alt").retry_count == 2
    db.close()


def test_unveraenderter_rss_feed_bleibt_ohne_fortsetzungsmarker_ein_schnellcheck(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    db = neue_sitzung()
    db.add(Channel(id="UCtest", name="Testkanal"))
    db.add(Video(id="bekannt", channel_id="UCtest", title="Bekannt"))
    db.commit()
    monkeypatch.setattr(ytdlp, "peek_recent", lambda _: [_video("bekannt")])
    monkeypatch.setattr(ytdlp, "list_entries", lambda *_: pytest.fail("Unveränderter Feed braucht keinen Vollabgleich"))
    job = jobs.enqueue_channel_sync(db, "UCtest")
    sync.kanal_abgleichen(db, jobs.claim_next(db, [JobType.CHANNEL_SYNC]))
    assert job.status == JobStatus.DONE
    assert job.message == "keine Aenderung (RSS)"
    assert jobs.payload_of(job) == {}
    db.close()
