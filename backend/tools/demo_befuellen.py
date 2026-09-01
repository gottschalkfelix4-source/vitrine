"""Befuellt ein Datenverzeichnis mit einem kleinen Beispielarchiv.

Nur fuer Entwicklung und zum Vorfuehren gedacht: Der Download wird durch
vorhandene Dateien ersetzt, alles andere - Umpacken, Buendeln, Datenbank -
laeuft echt durch die regulaeren Arbeiter.

    python tools/demo_befuellen.py <quellordner-mit-seed*.mkv>
"""

from __future__ import annotations

import shutil
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.db import init_db, session_scope  # noqa: E402
from app.models import (  # noqa: E402
    Channel,
    JobType,
    Playlist,
    PlaylistItem,
    PlaylistKind,
    Video,
    VideoStatus,
)
from app.services import jobs, ytdlp  # noqa: E402

TITEL = [
    "Wie ich meinen Heimserver aufgeraeumt habe",
    "Sechs Werkzeuge, die ich taeglich benutze",
    "Warum ZIP bei Videos nichts bringt",
    "Ein Jahr Selbstgehostet - was blieb",
    "Der Fehler, den ich dreimal gemacht habe",
]

KANAELE = [
    ("UCdemo0000000000000001", "Werkstattfunk", "@werkstattfunk"),
    ("UCdemo0000000000000002", "Kellerlabor", "@kellerlabor"),
]


def main() -> None:
    quelle = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    dateien = sorted(quelle.glob("seed*.mkv"))
    if not dateien:
        print(f"Keine seed*.mkv in {quelle} gefunden.")
        raise SystemExit(1)

    init_db()
    print(f"Datenverzeichnis: {settings.data_dir}")

    with session_scope() as db:
        for kanal_id, name, handle in KANAELE:
            if db.get(Channel, kanal_id) is None:
                db.add(Channel(id=kanal_id, name=name, handle=handle, auto_archive=True))
        db.commit()

        # Eine Playlist, in der bewusst auch nicht archivierte Positionen stehen -
        # das ist der Unterschied, den die Oberflaeche zeigen soll.
        liste_id = "PLdemo000000000000001"
        if db.get(Playlist, liste_id) is None:
            db.add(Playlist(
                id=liste_id, channel_id=KANAELE[0][0], kind=PlaylistKind.PLAYLIST,
                title="Serie: Heimserver von vorn", item_count=len(TITEL),
            ))
        db.commit()

        for i, titel in enumerate(TITEL):
            vid = f"demo{i:07d}"
            kanal_id = KANAELE[i % len(KANAELE)][0]
            if db.get(Video, vid) is None:
                db.add(Video(
                    id=vid, channel_id=kanal_id, title=titel,
                    description=f"Beispieleintrag {i + 1} fuer die Vorfuehrung.\n\n"
                                "Dieses Archiv ist eine eigene Interpretation von YouTube - "
                                "mit dem Unterschied, dass hier sichtbar ist, was fehlt.",
                    upload_date=datetime.now(UTC) - timedelta(days=i * 9 + 2),
                    view_count=(i + 1) * 4137, like_count=(i + 1) * 96,
                    status=VideoStatus.NEW,
                ))
                db.add(PlaylistItem(playlist_id=liste_id, video_id=vid, position=i))
        db.commit()

        # Nur die ersten Videos bekommen wirklich eine Datei. Die uebrigen
        # bleiben absichtlich unarchiviert, damit die Playlist beides zeigt.
        for i, datei in enumerate(dateien):
            vid = f"demo{i:07d}"
            vorschau = datei.with_suffix(".jpg")

            def falscher_download(video_id, ziel, *, format_selector=None, fortschritt=None,
                                  _datei=datei, _vorschau=vorschau, _i=i):
                ziel.mkdir(parents=True, exist_ok=True)
                kopie = ziel / f"{video_id}.mkv"
                shutil.copy2(_datei, kopie)
                thumb = None
                if _vorschau.is_file():
                    thumb = ziel / f"{video_id}.jpg"
                    shutil.copy2(_vorschau, thumb)
                v = db.get(Video, video_id)
                return ytdlp.DownloadResult(
                    path=kopie,
                    info={
                        "id": video_id, "title": v.title, "description": v.description,
                        # Muss zur tatsaechlichen Aufloesung der Datei passen -
                        # sonst greift zu Recht die Pruefung auf stille
                        # Qualitaetsminderung und lehnt den Download ab.
                        "duration": 6, "height": 720, "format_id": "136+140",
                        "vcodec": "avc1", "acodec": "mp4a",
                        "view_count": v.view_count, "like_count": v.like_count,
                        "upload_date": v.upload_date.strftime("%Y%m%d"),
                        "chapters": [
                            {"start_time": 0.0, "end_time": 2.0, "title": "Einleitung"},
                            {"start_time": 2.0, "end_time": 4.0, "title": "Der Kern"},
                            {"start_time": 4.0, "end_time": 6.0, "title": "Fazit"},
                        ],
                    },
                    thumbnail=thumb, subtitles=[],
                )

            ytdlp.download_video = falscher_download

            from app.workers.archive import archivieren

            jobs.enqueue_archive(db, vid)
            job = jobs.claim_next(db, [JobType.VIDEO_ARCHIVE])
            archivieren(db, job)
            v = db.get(Video, vid)
            print(f"  {vid}  {v.status:<10} {v.media_name}  {(v.bundle_bytes or 0) / 1e6:.2f} MB")

        fertig = db.query(Video).filter(Video.status == VideoStatus.ARCHIVED).count()
        offen = db.query(Video).filter(Video.status == VideoStatus.NEW).count()
        kaputt = db.query(Video).filter(Video.status == VideoStatus.FAILED).count()
        print(f"\n{fertig} archiviert, {offen} bewusst offen gelassen "
              "(damit die Playlist beide Zustaende zeigt)"
              + (f", {kaputt} FEHLGESCHLAGEN" if kaputt else ""))


if __name__ == "__main__":
    main()
