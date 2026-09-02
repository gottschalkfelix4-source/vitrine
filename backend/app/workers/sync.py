"""Kanal- und Playlist-Abgleich.

Der Abgleich laeuft in zwei Geschwindigkeiten, weil YouTube-Requests knapp
sind:

*Schnellcheck* ueber den RSS-Feed. Geht an yt-dlp vorbei, zaehlt nicht gegen
das Drosselungsbudget, liefert aber nur die juengsten ~15 Eintraege. Kann
deshalb stuendlich laufen.

*Vollabgleich* ueber die ``UU``-Uploads-Playlist. Teuer, dafuer vollstaendig.
Laeuft selten.

Zur Playlist-Behandlung: Ein Video, das in der Kanaluebersicht und in drei
Playlists auftaucht, liegt trotzdem genau einmal auf der Platte. Playlists sind
reine Zuordnungen. Und sie zeigen **alle** Positionen, auch die nicht
archivierten - mit Zustandskennzeichnung. Das ist genau der Punkt, an dem
TubeArchivist scheitert ("only items archived will be able to show up"), und
der Grund, warum dort die Playlist-Treue als kaputt gilt.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import (
    Channel,
    Job,
    JobType,
    Playlist,
    PlaylistItem,
    PlaylistKind,
    Video,
    VideoStatus,
    utcnow,
)
from app.services import jobs, ytdlp

log = logging.getLogger(__name__)


def _ist_verschwunden(eintrag: ytdlp.ListedVideo) -> bool:
    """Erkennt geloeschte oder privat gestellte Videos in einer Playlist.

    Wird ein Video geloescht oder auf privat gestellt, bleibt sein Platz in
    fremden Playlists bestehen - YouTube zeigt dort "Deleted video" bzw.
    "Private video". yt-dlp liefert davon nur noch die ID: kein Titel, keine
    Dauer, kein Datum, keine Aufrufe.

    Genau diese Kombination ist das Erkennungsmerkmal. Ein einzelnes fehlendes
    Feld reicht bewusst nicht - ein Titel kann auch mal fehlen, waehrend das
    Video existiert. Erst wenn ueberhaupt nichts da ist, ist es eine Leiche.
    """
    return (
        eintrag.title in (None, "", "(ohne Titel)")
        and eintrag.duration_s is None
        and eintrag.upload_date is None
        and eintrag.view_count is None
    )


def _video_anlegen(
    db: Session,
    kanal_id: str | None,
    eintrag: ytdlp.ListedVideo,
    bekannt: dict[str, Video] | None = None,
) -> tuple[Video, bool]:
    """Legt ein Video an, falls es neu ist. Liefert (Video, war_neu).

    ``bekannt`` ist kein Tempo-Trick, sondern notwendig: Die Sitzung laeuft mit
    abgeschaltetem Autoflush, und ``db.get()`` sieht deshalb ein Video nicht,
    das in derselben Schleife bereits angelegt, aber noch nicht geschrieben
    wurde. Ohne diesen Zwischenspeicher entstuenden zwei Datensaetze mit
    derselben ID und der Abgleich braeche beim Schreiben ab.

    Das ist kein theoretischer Fall: YouTube liefert bei grossen Kanaelen
    gelegentlich unvollstaendige oder in sich doppelte Listen.
    """
    if bekannt is not None and eintrag.id in bekannt:
        return bekannt[eintrag.id], False

    v = db.get(Video, eintrag.id)
    if v is not None:
        if bekannt is not None:
            bekannt[eintrag.id] = v
        # Vorhandene Videos nur behutsam auffrischen - die Metadaten aendern
        # sich, der Archivzustand darf dabei nicht angefasst werden.
        #
        # Jedes Feld einzeln und nur, wenn die Quelle wirklich etwas liefert.
        # Das ist keine Vorsicht um ihrer selbst willen: Die beiden Quellen
        # sind unterschiedlich lueckenhaft, und keine darf die andere
        # ueberschreiben. Der RSS-Feed bringt ein Datum, aber nie eine Dauer;
        # das flache Auflisten bringt Dauer und Aufrufe, aber nie ein Datum.
        # Erst beide zusammen ergeben einen vollstaendigen Datensatz.
        #
        # Genau hier fehlte die Dauer: Ein Video, das der RSS-Schnellcheck
        # zuerst gesehen hatte, existierte bereits, wenn kurz darauf die
        # Uploads-Liste mit der Dauer kam - und dieser Zweig schrieb sie nie.
        # Die 14 juengsten Videos des Kanals standen deshalb dauerhaft ohne
        # Laufzeit da, obwohl YouTube sie bei jedem Abgleich mitgeliefert hat.
        if eintrag.title and eintrag.title != "(ohne Titel)":
            v.title = eintrag.title
        if eintrag.view_count is not None:
            v.view_count = eintrag.view_count
        if eintrag.duration_s is not None:
            v.duration_s = eintrag.duration_s
        if eintrag.upload_date is not None:
            v.upload_date = eintrag.upload_date
        # Ein Video, das seit dem letzten Abgleich verschwunden ist, wird
        # nachgezogen - aber nur, solange es noch nicht archiviert ist. Was
        # einmal im Archiv liegt, bleibt spielbar, auch wenn die Quelle es
        # zurueckzieht. Das ist der Sinn eines Archivs.
        if _ist_verschwunden(eintrag) and v.status in (VideoStatus.NEW, VideoStatus.QUEUED):
            v.status = VideoStatus.UNAVAILABLE
            v.status_message = "bei der Quelle geloescht oder privat"
        return v, False

    verschwunden = _ist_verschwunden(eintrag)
    v = Video(
        id=eintrag.id,
        channel_id=kanal_id,
        title=eintrag.title,
        duration_s=eintrag.duration_s,
        upload_date=eintrag.upload_date,
        view_count=eintrag.view_count,
        # Geloeschte und privat gestellte Videos gar nicht erst als "neu"
        # fuehren: Sie liessen sich nie herunterladen, und ein Laden-Knopf
        # daneben waere ein leeres Versprechen.
        status=VideoStatus.UNAVAILABLE if verschwunden else VideoStatus.NEW,
        status_message="bei der Quelle geloescht oder privat" if verschwunden else None,
    )
    db.add(v)
    if bekannt is not None:
        bekannt[eintrag.id] = v
    return v, True


def _soll_archiviert_werden(kanal: Channel, video: Video) -> bool:
    """Passt dieses Video zu den inhaltlichen Regeln des Kanals?

    Bewusst OHNE ``auto_archive``: Das steuert nur, ob der Abgleich von sich
    aus einreiht. Wer in der Oberflaeche auf "Alle laden" klickt, trifft eine
    eigene Entscheidung - die inhaltlichen Regeln (keine Shorts, keine
    Livestreams, nichts vor Datum X) gelten dort weiter, das Automatik-Flag
    nicht. Waere es hier mit drin, bliebe der Knopf bei jedem Kanal ohne
    Automatik wirkungslos.
    """
    if video.is_short and not kanal.archive_shorts:
        return False
    if video.was_live and not kanal.archive_live:
        return False
    if kanal.archive_since and video.upload_date and video.upload_date < kanal.archive_since:
        return False
    return True


def _sammlung_abgleichen(
    db: Session,
    kanal: Channel,
    playlist_id: str,
    titel: str,
    art: str,
    url: str,
    *,
    einreihen: bool,
) -> int:
    """Gleicht eine Sammlung ab und pflegt ihre Zuordnungen. Liefert die Zahl
    neuer Videos."""
    liste = db.get(Playlist, playlist_id)
    if liste is None:
        liste = Playlist(id=playlist_id, channel_id=kanal.id, kind=art, title=titel)
        db.add(liste)
    liste.title = titel
    liste.kind = art

    eintraege = ytdlp.list_entries(url)
    neu = 0
    # Ein Zwischenspeicher fuer die gesamte Schleife - siehe _video_anlegen.
    bekannt: dict[str, Video] = {}

    # Zuordnungen vollstaendig neu setzen: Reihenfolge und Zusammensetzung einer
    # Playlist aendern sich, entfernte Positionen muessen verschwinden.
    db.execute(
        PlaylistItem.__table__.delete().where(PlaylistItem.playlist_id == playlist_id)
    )

    for position, eintrag in enumerate(eintraege):
        video, war_neu = _video_anlegen(db, kanal.id, eintrag, bekannt)
        if art == PlaylistKind.SHORTS:
            video.is_short = True
        if art == PlaylistKind.LIVE:
            video.was_live = True
        # Nur die Uploads-Liste taugt als Zeitachse: Sie ist umgekehrt
        # chronologisch und vollstaendig. Eine vom Kanal angelegte Playlist ist
        # frei sortiert - ihre Position sagt ueber das Alter nichts aus.
        if art == PlaylistKind.UPLOADS:
            video.uploads_position = position
        neu += int(war_neu)
        db.add(PlaylistItem(playlist_id=playlist_id, video_id=eintrag.id, position=position))

    liste.item_count = len(eintraege)
    liste.last_synced_at = utcnow()
    db.commit()

    if einreihen and kanal.auto_archive:
        for eintrag in eintraege:
            video = db.get(Video, eintrag.id)
            if video and video.status == VideoStatus.NEW and _soll_archiviert_werden(kanal, video):
                video.status = VideoStatus.QUEUED
                jobs.enqueue_archive(db, video.id)
        db.commit()

    return neu


def _offene_einreihen(db: Session, kanal: Channel, *, nur_bei_automatik: bool = False) -> int:
    """Reiht alle noch unarchivierten Videos des Kanals ein, die den Regeln
    des Kanals entsprechen. Laeuft bewusst erst NACH der Kennzeichnung von
    Shorts und Livestreams.

    ``nur_bei_automatik`` unterscheidet die beiden Aufrufer: Der Abgleich
    setzt es und tut damit nichts, wenn der Kanal auf "nur erfassen" steht.
    Der Knopf in der Oberflaeche setzt es nicht - dort hat der Nutzer die
    Entscheidung gerade selbst getroffen.
    """
    if nur_bei_automatik and not kanal.auto_archive:
        return 0
    anzahl = 0
    for video in db.scalars(
        select(Video).where(Video.channel_id == kanal.id, Video.status == VideoStatus.NEW)
    ):
        if _soll_archiviert_werden(kanal, video):
            video.status = VideoStatus.QUEUED
            jobs.enqueue_archive(db, video.id)
            anzahl += 1
    db.commit()
    return anzahl


@jobs.register(JobType.CHANNEL_SYNC)
def kanal_abgleichen(db: Session, job: Job) -> None:
    kanal_id = job.target_id
    if not kanal_id:
        raise ValueError("Abgleichauftrag ohne Kanal-ID")

    kanal = db.get(Channel, kanal_id)
    if kanal is None:
        raise ValueError(f"Kanal {kanal_id} nicht in der Datenbank")

    voll = jobs.payload_of(job).get("voll", False)

    try:
        # ---- Schnellcheck: kostet keinen yt-dlp-Request
        jobs.fortschritt(db, job, 0.05, "Schnellcheck ueber RSS")
        neu_gesehen = 0
        try:
            aus_rss: dict[str, Video] = {}
            for eintrag in ytdlp.peek_recent(kanal_id):
                _, war_neu = _video_anlegen(db, kanal_id, eintrag, aus_rss)
                neu_gesehen += int(war_neu)
            db.commit()
        except ytdlp.YtdlpError as e:
            # Wichtig: zuruecksetzen, sonst haengen halb angelegte Videos in der
            # Sitzung und kollidieren gleich darauf mit dem Vollabgleich.
            db.rollback()
            log.info("RSS-Schnellcheck fuer %s fehlgeschlagen: %s", kanal_id, e)

        # Nichts Neues im Feed und kein Vollabgleich verlangt: fertig. Das ist
        # der Normalfall und kostet praktisch nichts.
        if not voll and neu_gesehen == 0:
            kanal.last_synced_at = utcnow()
            db.commit()
            jobs.erledigt(db, job, "keine Aenderung (RSS)")
            return

        # ---- Vollabgleich ueber die Sammelplaylists.
        # Die Uploads-Liste ist die vollstaendige Quelle, reiht aber noch nichts
        # ein: Erst muessen Shorts und Livestreams gekennzeichnet sein, sonst
        # wuerde ein Short aus den Uploads als normales Video geladen.
        jobs.fortschritt(db, job, 0.2, "Uploads werden gelesen")
        neu = _sammlung_abgleichen(
            db, kanal,
            playlist_id="UU" + kanal_id[2:],
            titel="Alle Uploads",
            art=PlaylistKind.UPLOADS,
            url=ytdlp.channel_auto_playlist(kanal_id, "uploads"),
            einreihen=False,
        )

        # Shorts- und Livestream-Listen werden IMMER gelesen, auch wenn der
        # Kanal sie nicht archivieren soll - gerade dann. Sie sind die einzige
        # verlaessliche Kennzeichnung; ohne sie liesse sich "keine Shorts"
        # gar nicht einhalten. Der Preis sind zwei Requests je Abgleich.
        schritt = 0.5
        for art, schluessel, praefix, titel in (
            (PlaylistKind.SHORTS, "shorts", "UUSH", "Shorts"),
            (PlaylistKind.LIVE, "live", "UULV", "Livestreams"),
        ):
            schritt += 0.15
            jobs.fortschritt(db, job, schritt, f"{titel} werden gelesen")
            try:
                _sammlung_abgleichen(
                    db, kanal,
                    playlist_id=praefix + kanal_id[2:],
                    titel=titel,
                    art=art,
                    url=ytdlp.channel_auto_playlist(kanal_id, schluessel),
                    einreihen=False,
                )
            except ytdlp.YtdlpError as e:
                # Nicht jeder Kanal hat Shorts oder Streams - kein Fehler.
                log.info("%s fuer %s nicht vorhanden: %s", titel, kanal_id, e)

        # Jetzt, mit vollstaendiger Kennzeichnung, einreihen.
        eingereiht = _offene_einreihen(db, kanal, nur_bei_automatik=True)
        log.info("%s: %d Videos zum Archivieren eingereiht", kanal_id, eingereiht)

        # ---- Die vom Kanal angelegten Playlists
        jobs.fortschritt(db, job, 0.8, "Playlists werden gelesen")
        kanal_url = f"https://www.youtube.com/channel/{kanal_id}"
        for p in ytdlp.list_channel_playlists(kanal_url):
            try:
                _sammlung_abgleichen(
                    db, kanal,
                    playlist_id=p.id,
                    titel=p.title,
                    art=PlaylistKind.PLAYLIST,
                    url=ytdlp.playlist_url(p.id),
                    # Playlists reihen nichts zusaetzlich ein: Ihre Videos
                    # stecken bereits in den Uploads. Sie stiften nur Ordnung.
                    einreihen=False,
                )
            except ytdlp.YtdlpError as e:
                log.warning("Playlist %s (%s) nicht lesbar: %s", p.id, p.title, e)

        kanal.last_synced_at = utcnow()
        db.commit()
        jobs.erledigt(db, job, f"{neu} neue Videos gefunden")

    except Exception as e:
        # jobs.gescheitert setzt die Sitzung selbst zurueck - noetig, weil ein
        # Schreibfehler sie sonst blockiert und die Fehlermeldung verschluckt.
        jobs.gescheitert(db, job, f"{type(e).__name__}: {e}")
        raise


@jobs.register(JobType.PLAYLIST_SYNC)
def playlist_abgleichen(db: Session, job: Job) -> None:
    playlist_id = job.target_id
    if not playlist_id:
        raise ValueError("Abgleichauftrag ohne Playlist-ID")

    liste = db.get(Playlist, playlist_id)
    if liste is None:
        raise ValueError(f"Playlist {playlist_id} nicht in der Datenbank")
    kanal = db.get(Channel, liste.channel_id) if liste.channel_id else None
    if kanal is None:
        raise ValueError(f"Playlist {playlist_id} hat keinen Kanal")

    _sammlung_abgleichen(
        db, kanal,
        playlist_id=playlist_id,
        titel=liste.title,
        art=liste.kind,
        url=ytdlp.playlist_url(playlist_id),
        einreihen=jobs.payload_of(job).get("einreihen", False),
    )
    jobs.erledigt(db, job, "abgeglichen")


def faellige_kanaele_einreihen() -> int:
    """Reiht alle Kanaele ein, deren Abgleich ansteht.

    Wird vom Zeitplaner aufgerufen. Kanaele ohne eigenen Rhythmus benutzen den
    globalen Standardwert.
    """
    from app.db import session_scope

    jetzt = utcnow()
    with session_scope() as db:
        anzahl = 0
        for kanal in db.scalars(select(Channel).where(Channel.sync_enabled.is_(True))):
            stunden = kanal.sync_interval_hours or settings.default_sync_interval_hours
            letzter = kanal.last_synced_at
            if letzter is not None and letzter.tzinfo is None:
                from datetime import UTC

                letzter = letzter.replace(tzinfo=UTC)
            if letzter is None or jetzt - letzter >= timedelta(hours=stunden):
                jobs.enqueue_channel_sync(db, kanal.id)
                anzahl += 1
        return anzahl
