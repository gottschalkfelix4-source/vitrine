"""Manuelle Warteschlangenpause: echte SQLite-Verbindungen, keine Netzaufrufe."""

from __future__ import annotations

import threading
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.sql.dml import Update

from app.api import library
from app.db import get_db, set_pragmas
from app.models import Base, Job, JobStatus, JobType, Setting
from app.services import drosselung, einstellungen, jobs, pause
from app.services.ausgang import DIREKT, Ausgang
from app.workers import runner


@pytest.fixture
def sitzungen(tmp_path):
    # Datei statt StaticPool: API und Worker muessen unabhaengige
    # Verbindungen haben, damit das Rennen zwischen SELECT und UPDATE echt ist.
    engine = create_engine(
        f"sqlite:///{tmp_path / 'pause.db'}", connect_args={"check_same_thread": False}
    )
    event.listens_for(engine, "connect")(set_pragmas)
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    engine.dispose()


@pytest.fixture
def client(sitzungen):
    app = FastAPI()
    app.include_router(library.router)

    def sitzung():
        with sitzungen() as db:
            yield db

    app.dependency_overrides[get_db] = sitzung
    with TestClient(app) as client:
        yield client


def test_api_unpausiert_und_idempotentes_fortsetzen(client):
    erwartet = {"aktiv": False, "bis": None, "rest_s": 0, "laufend": 0}
    assert client.get("/api/jobs/aktiv").json()["pause"] == erwartet
    assert client.post("/api/jobs/resume").json() == erwartet


@pytest.mark.parametrize("minuten", [None, 15, 30, 60, 120, pause.MAX_MINUTEN])
def test_api_pause_und_fortsetzen(client, minuten):
    antwort = client.post("/api/jobs/pause", json={"minuten": minuten})
    assert antwort.status_code == 200
    z = antwort.json()
    assert z["aktiv"] is True and z["laufend"] == 0
    if minuten is None:
        assert z["bis"] is None and z["rest_s"] is None
    else:
        assert datetime.fromisoformat(z["bis"]).utcoffset() == timedelta(0)
        assert 0 < z["rest_s"] <= minuten * 60
    assert client.get("/api/jobs/aktiv").json()["pause"]["aktiv"] is True
    assert client.post("/api/jobs/resume").json()["aktiv"] is False


@pytest.mark.parametrize("minuten", [0, -1, pause.MAX_MINUTEN + 1, 1.5, 15.0, True, "15", [], {}])
def test_api_ungueltige_dauer_veraendert_bestehende_pause_nicht(client, minuten):
    client.post("/api/jobs/pause", json={"minuten": None})
    assert client.post("/api/jobs/pause", json={"minuten": minuten}).status_code == 422
    assert client.get("/api/jobs/aktiv").json()["pause"]["rest_s"] is None


@pytest.mark.parametrize("eingabe", [{}, {"minuten": 15, "dauer": 60}, None])
def test_api_ungueltiger_body_wird_abgelehnt(client, eingabe):
    assert client.post("/api/jobs/pause", json=eingabe).status_code == 422
    assert client.get("/api/jobs/aktiv").json()["pause"]["aktiv"] is False


@pytest.mark.parametrize("minuten", [None, 60])
def test_pause_ueberlebt_neue_engine_ohne_initialisierung(sitzungen, minuten):
    with sitzungen() as db:
        jobs.enqueue_archive(db, "video")
        vorher = pause.pausieren(db, minuten)
        url = db.get_bind().url

    engine = create_engine(url)
    try:
        frisch = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
        with frisch() as db:
            assert pause.zustand(db)["bis"] == vorher["bis"]
            assert pause.zustand(db)["aktiv"] is True
            assert jobs.claim_next(db) is None
    finally:
        engine.dispose()


def test_lange_sitzung_sieht_pause_und_fortsetzen(sitzungen):
    with sitzungen() as worker, sitzungen() as api:
        pause.pausieren(api, None)
        alt = worker.get(Setting, pause.SETTING_KEY)
        assert alt is not None
        worker.commit()
        pause.fortsetzen(api)
        # Der gespeicherte ORM-Wert bleibt absichtlich in der Identity-Map.
        assert alt.value == pause.UNBEFRISTET
        assert pause.zustand(worker)["aktiv"] is False
        worker.commit()
        pause.pausieren(api, 15)
        assert pause.zustand(worker)["bis"] is not None


def test_zeitpause_endet_exakt_und_ohne_bereinigungsjob(sitzungen, monkeypatch):
    uhr = [datetime(2026, 9, 5, 10, 0, tzinfo=UTC)]
    monkeypatch.setattr(pause, "utcnow", lambda: uhr[0])
    with sitzungen() as db:
        job = jobs.enqueue_archive(db, "video")
        assert pause.pausieren(db, 15)["rest_s"] == 900
        uhr[0] += timedelta(seconds=899, microseconds=900000)
        assert pause.zustand(db)["rest_s"] == 1
        assert jobs.claim_next(db) is None
        uhr[0] += timedelta(microseconds=100000)
        assert pause.zustand(db) == {"aktiv": False, "bis": None, "rest_s": 0, "laufend": 0}
        assert jobs.claim_next(db).id == job.id


@pytest.mark.parametrize("typ", pause.NETZ_TYPEN)
def test_keine_neuen_netzauftraege_waehrend_pause(sitzungen, typ):
    with sitzungen() as db:
        job = jobs.enqueue(db, typ, "ziel", payload={"voll": True})
        pause.pausieren(db, None)
        assert jobs.claim_next(db, [typ]) is None
        db.refresh(job)
        assert job.status == JobStatus.PENDING
        assert job.started_at is None
        assert jobs.payload_of(job) == {"voll": True}
        pause.fortsetzen(db)
        assert jobs.claim_next(db, [typ]).id == job.id


def test_lokale_arbeit_ueberholt_pausierte_netzauftraege(sitzungen):
    with sitzungen() as db:
        netz = jobs.enqueue(db, JobType.VIDEO_ARCHIVE, "netz", priority=1)
        lokal = [
            jobs.enqueue(db, JobType.VIDEO_PREPARE, "vorbereiten", priority=10),
            jobs.enqueue(db, JobType.VIDEO_RECODE, "recodieren", priority=900),
        ]
        pause.pausieren(db, None)
        assert [jobs.claim_next(db).id, jobs.claim_next(db).id] == [j.id for j in lokal]
        assert jobs.claim_next(db) is None
        assert db.get(Job, netz.id).status == JobStatus.PENDING


@pytest.mark.parametrize("selbe_sitzung", [False, True])
def test_pause_zwischen_kandidatensuche_und_update_verhindert_claim(sitzungen, monkeypatch, selbe_sitzung):
    with sitzungen() as worker, sitzungen() as api:
        if selbe_sitzung:
            api = worker
        job = jobs.enqueue_archive(worker, "video")
        ausfuehren = worker.execute
        dazwischen = []

        def mit_pause(anweisung, *args, **kwargs):
            if isinstance(anweisung, Update) and anweisung.table.name == "jobs" and not dazwischen:
                dazwischen.append(True)
                assert pause.pausieren(api, None)["laufend"] == 0
            return ausfuehren(anweisung, *args, **kwargs)

        monkeypatch.setattr(worker, "execute", mit_pause)
        assert jobs.claim_next(worker) is None
        assert dazwischen, "Die Pause muss erst nach der Kandidatensuche eintreffen."
        worker.refresh(job)
        assert job.status == JobStatus.PENDING
        assert job.started_at is None


def test_pause_ist_keine_normale_einstellung(sitzungen):
    with sitzungen() as db:
        pause.pausieren(db, None)
        assert pause.SETTING_KEY not in einstellungen.gespeicherte(db)
        assert pause.SETTING_KEY not in einstellungen.als_json(db)
        assert einstellungen.anwenden(db) == 0
        assert einstellungen.zuruecksetzen(db, [pause.SETTING_KEY]) == []
        einstellungen.zuruecksetzen(db)
        assert pause.zustand(db)["aktiv"] is True


def _worker_einrichten(sitzungen, monkeypatch):
    @contextmanager
    def sitzung():
        with sitzungen() as db:
            yield db

    monkeypatch.setattr(runner, "session_scope", sitzung)
    monkeypatch.setattr(runner, "LEERLAUF_S", 0.01)
    monkeypatch.setattr(runner.vpn, "waehlen", lambda: Ausgang(id=DIREKT, name="Direkt"))
    werk = runner.Arbeiterwerk()
    werk._soll["netz"] = 1
    gruppe = next(g for g in runner._gruppen() if g.name == "netz")
    return werk, gruppe


def test_laufender_auftrag_endet_dann_wartet_worker_bis_zum_fortsetzen(sitzungen, client, monkeypatch):
    werk, gruppe = _worker_einrichten(sitzungen, monkeypatch)
    begonnen, fertig, freigabe, im_leerlauf = (threading.Event() for _ in range(4))
    abgeholt = jobs.claim_next
    bearbeitet = []

    def holen(db, typen):
        job = abgeholt(db, typen)
        if job is None:
            im_leerlauf.set()
        return job

    def bearbeiten(db, job):
        bearbeitet.append(job.target_id)
        if job.target_id == "erster":
            begonnen.set()
            assert freigabe.wait(3), "Testfreigabe fehlt"
        jobs.erledigt(db, job)
        fertig.set()
        if job.target_id == "zweiter":
            werk._stop.set()

    monkeypatch.setattr(runner.jobs, "claim_next", holen)
    monkeypatch.setattr(runner.jobs, "HANDLERS", {JobType.VIDEO_ARCHIVE: bearbeiten})
    with sitzungen() as db:
        jobs.enqueue_archive(db, "erster")
        jobs.enqueue_archive(db, "zweiter")
        # Lokale laufende Arbeit darf die Restzahl der Downloads nicht erhoehen.
        jobs.enqueue(db, JobType.VIDEO_RECODE, "lokal")
        jobs.claim_next(db, [JobType.VIDEO_RECODE])
    thread = threading.Thread(target=werk._arbeiten, args=(gruppe, 0), daemon=True)
    thread.start()
    try:
        assert begonnen.wait(3)
        z = client.post("/api/jobs/pause", json={"minuten": None}).json()
        assert z["aktiv"] is True and z["laufend"] == 1
        assert client.get("/api/jobs/aktiv").json()["pause"]["laufend"] == 1
        freigabe.set()
        assert fertig.wait(3) and im_leerlauf.wait(3)
        assert bearbeitet == ["erster"]
        assert thread.is_alive(), "Pause darf den Arbeiter nicht beenden."
        with sitzungen() as db:
            assert db.scalar(select(Job.status).where(Job.target_id == "erster")) == JobStatus.DONE
            assert db.scalar(select(Job.status).where(Job.target_id == "zweiter")) == JobStatus.PENDING
        assert client.get("/api/jobs/aktiv").json()["pause"]["laufend"] == 0
        client.post("/api/jobs/resume")
        thread.join(3)
        assert not thread.is_alive()
        assert bearbeitet == ["erster", "zweiter"]
    finally:
        freigabe.set()
        werk._stop.set()
        thread.join(3)


def test_fortsetzen_uebergeht_keine_automatische_ip_sperre(sitzungen, monkeypatch):
    drosselung.zuruecksetzen()
    try:
        drosselung.melden("Bot-Pruefung", ausgang=DIREKT)
        with sitzungen() as db:
            pause.pausieren(db, None)
            pause.fortsetzen(db)
        assert drosselung.wartezeit(DIREKT) > 0
        assert drosselung.zustand()["stufe"] == 1

        monkeypatch.setattr(runner.vpn.settings, "vpn_aktiv", False)
        geholt = []
        monkeypatch.setattr(runner.jobs, "claim_next", lambda *a: geholt.append(True))
        werk = runner.Arbeiterwerk()
        werk._soll["netz"] = 1
        monkeypatch.setattr(werk._stop, "wait", lambda *_: werk._stop.set())
        gruppe = next(g for g in runner._gruppen() if g.name == "netz")
        werk._arbeiten(gruppe, 0)
        assert geholt == []
    finally:
        drosselung.zuruecksetzen()


def test_pausierter_worker_haelt_beim_warten_keine_transaktion(sitzungen, monkeypatch):
    werk, gruppe = _worker_einrichten(sitzungen, monkeypatch)
    with sitzungen() as db:
        jobs.enqueue_archive(db, "video")
        pause.pausieren(db, None)

    offen = []

    @contextmanager
    def sitzung():
        with sitzungen() as db:
            offen.append(db)
            yield db

    def warten(*_):
        assert offen and all(not db.in_transaction() for db in offen)
        werk._stop.set()

    monkeypatch.setattr(runner, "session_scope", sitzung)
    monkeypatch.setattr(werk._stop, "wait", warten)
    werk._arbeiten(gruppe, 0)
