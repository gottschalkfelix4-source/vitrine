# Vitrine

Ein selbst gehostetes YouTube-Archiv mit eigener Oberflaeche. Archiviert ganze
Kanaele samt Playlist-Gliederung und haelt den Speicherbedarf durch eine
Kalt/Heiss-Trennung klein.

> Der Name ist eine Platzhalter-Setzung und laesst sich mit `YTA_APP_NAME`
> aendern.

## Warum nicht TubeArchivist

TubeArchivist ist das naechstliegende Vergleichsprodukt. Drei Punkte, die hier
anders laufen:

- **Oberflaeche.** Der dortige Maintainer lehnt Design-Beitraege strukturell ab
  ("this project doesn't take new feature request") und sagt dem YouTube-artigen
  Ablauf ausdruecklich ab. Genau der ist hier das Ziel.
- **Betriebsaufwand.** Dort drei Container samt Elasticsearch mit fest
  verdrahtetem 1-GB-Java-Heap. Hier ein Container mit SQLite.
- **Speicher.** Kein bestehendes Projekt in dieser Kategorie hat einen
  Kalt/Heiss-Lebenszyklus. Der ist hier der Kern.

## Die Speicher-Architektur

### Kaltspeicher: ein ZIP-Buendel je Video

Jedes Video liegt als eine einzige Datei vor, die Video, Metadaten, Vorschaubild
und Untertitel zusammenhaelt:

```
bundles/<kanal-id>/<video-id>.zip
  manifest.json      eigene Metadaten (Codecs, Groessen, Schema-Version)
  info.json          unveraenderte yt-dlp-Metadaten
  media/<name>       die Mediendatei                       -- unkomprimiert
  thumbnail.<ext>    Vorschaubild
  subs/<lang>.vtt    Untertitel
```

**Das ZIP komprimiert nicht - es buendelt.** Gemessen an einer H.264-Datei:

| Verfahren | Ersparnis |
|---|---|
| ZIP/deflate -9 | 0,01 % |
| bzip2 -9 | −0,40 % (wird groesser) |
| LZMA/xz | 0,05 % |

Videocodecs sind bereits entropiekodiert; ein Allzweckkompressor findet darin
nichts mehr. Die Platzersparnis kommt deshalb aus der **Recodierung nach AV1**,
nicht aus dem Behaelter.

### Warum die Mediendatei unkomprimiert im ZIP liegt

Weil sie dann zusammenhaengend in der Datei steht und sich an beliebiger Stelle
lesen laesst. Der Player kann damit **direkt aus dem Buendel streamen**, ohne
dass irgendetwas entpackt wird.

Wichtig ist dabei, `zipfile.ZipExtFile.seek()` zu meiden - das liest bis zur
Zielposition durch. Gemessen an derselben Stelle einer Datei:

| Zugriffsart | Dauer |
|---|---|
| Offset direkt berechnet | ~0,5 ms, unabhaengig von der Sprungweite |
| `zipfile.seek()` | 53 ms bei 28 MB, waechst linear mit der Dateigroesse |

Der Offset ergibt sich aus `header_offset + 30 + len(name) + len(extra)` und
gilt auch bei ZIP64.

### Heissspeicher: nur wenn noetig

Eine entpackte Datei entsteht ausschliesslich dann, wenn der Client den
Archivcodec nicht abspielen kann. Sie verschwindet nach drei Regeln:

1. **Lease** - solange der Player Herzschlaege schickt, wird nichts geloescht.
2. **Frist** - kurze Frist nach Wiedergabeende, sonst die lange ab letztem Zugriff.
3. **Budget** - reisst der Heissspeicher sein Limit, fliegt zusaetzlich das am
   laengsten Ungenutzte raus.

Die Lease ist bewusst kein Zaehler: Ein Zaehler leckt, sobald jemand den Tab
schliesst oder der Browser abstuerzt, und die Datei bliebe fuer immer liegen.

Welcher Browser was kann, entscheidet nicht der Server per User-Agent-Raterei,
sondern der Client meldet es selbst (`MediaSource.isTypeSupported`).

## Was die Recodierung wirklich bringt

Die verbreiteten "AV1 spart 50 %"-Zahlen gelten gegenueber einem
unkomprimierten Master - **nicht** gegenueber einer bereits auf 2-4 Mbit/s
gedrueckten YouTube-Datei. Gemessen gegen eine H.264-Quelle:

| CRF | Ersparnis | Bewertung |
|---|---|---|
| 22 | ~0 % | visuell nicht unterscheidbar, aber sinnlos |
| 26 | ~23 % | |
| **30** | **~40 %** | Voreinstellung |
| 34 | ~55 % | |

Eine Platzersparnis gibt es also nur mit bewusst in Kauf genommener
Qualitaetsminderung. Das ist kein Fehler des Verfahrens, sondern die
unvermeidliche Rechnung.

**Nach Quellcodec unterscheiden spart mehr als jede Preset-Optimierung:**
H.264-Quellen werden recodiert, VP9- und AV1-Quellen unveraendert abgelegt -
dort waere der Re-Encode fast reiner Generationsverlust.

Encode-Tempo auf einer 6-Kern-CPU, 1080p30, je Stunde Video:

| Preset | Dauer | Ersparnis |
|---|---|---|
| 4 | ~80 min | am dichtesten |
| **6** | ~51 min | Voreinstellung |
| 8 | ~25 min | |
| 10 | ~16 min | fuer Massenarchivierung |

## Betrieb

```bash
docker compose up -d --build
```

Danach `http://localhost:8000`.

### Der stille 360p-Fehler

Der gefaehrlichste Fehler im Betrieb kuendigt sich nicht an. Zwei Ursachen
fuehren dazu, dass yt-dlp Erfolg meldet und trotzdem nur eine Notfassung holt:

- **Fehlende JavaScript-Laufzeit.** yt-dlp braucht seit 2025.11.12 eine externe
  JS-Runtime. Fehlt sie, bricht nichts ab - es kommt stillschweigend eine
  reduzierte Formatauswahl. Das Dockerfile bringt Deno mit. Wer ausserhalb des
  Containers entwickelt, braucht es ebenfalls.
- **`yt-dlp[default]`, nicht `yt-dlp`.** Das Extra zieht die
  Challenge-Solver-Skripte nach. Ohne sie dasselbe Problem.

Beides aeussert sich gleich: yt-dlp faellt auf Format 18 zurueck, ein
360p-Gemisch aus H.264 und AAC. Wer das nicht prueft, archiviert wochenlang
360p und merkt es erst beim Zuschauen - wenn die Quelle laengst geloescht ist.

`check_not_degraded()` faengt das nach jedem Download ab. Ein so entstandenes
Video wird **nicht** als archiviert verbucht, sondern bleibt in der
Warteschlange, bis die Kette wieder steht.

### Kanalabgleich in zwei Geschwindigkeiten

Der Feed `https://www.youtube.com/feeds/videos.xml?channel_id=UC...` geht nicht
durch yt-dlp, kostet keinen der knappen Requests und zaehlt nicht gegen das
Drosselungsbudget. Damit kann stuendlich bei jedem Kanal nachgesehen werden;
der teure Vollabgleich ueber die `UU`-Playlist laeuft dann nur noch
woechentlich. Der Feed liefert allerdings nur rund 15 Eintraege und keine
Dauer - er ersetzt den Vollabgleich nicht, er verschiebt ihn.

### Nebenlaeufigkeit

YouTube drosselt pro IP-Adresse, nicht pro Prozess; als Gast liegt die Grenze
bei rund 300 Videos je Stunde. `YTA_DOWNLOAD_CONCURRENCY` hochzudrehen macht
nicht schneller fertig, sondern voruebergehend gesperrt.

### Hardware-Encoder

Intel 11. bis 13. Generation (UHD 730/770) kann AV1 **nur dekodieren**. Fuer
AV1-Encode in Hardware braucht es Intel Arc, Meteor/Lunar/Arrow Lake, NVIDIA
RTX 40/50 oder RDNA3+. Software-Encode bleibt ohnehin die bessere Wahl fuer ein
Archiv, das einmal geschrieben und lange behalten wird.

## Zwei Warteschlangen statt einer

Download und Recodierung laufen getrennt. Der Grund ist eine Zahl: Ein Kanal
mit 500 Stunden 1080p-Material braucht zum Recodieren rund 425 CPU-Stunden -
etwa 18 Tage Dauerlast. Steckte das im Archivierungsschritt, waere ein Video
erst nach Tagen sichtbar.

So laeuft es stattdessen:

1. **Herunterladen** und in einen browsertauglichen Behaelter umpacken. Dauert
   Sekunden bis Minuten. Danach ist das Video vollstaendig, gesichert und
   abspielbar.
2. **Recodieren** als eigener Auftrag ganz hinten in der Rangfolge. Laeuft im
   Hintergrund und macht das Buendel kleiner, ohne dass jemand darauf wartet.

Das Umpacken in Schritt 1 ist kein Beiwerk: yt-dlp fuehrt Video und Ton nach
MKV zusammen, und **kein Browser spielt MKV ab**. Ohne diesen Schritt muesste
jede einzelne Wiedergabe durch den Transkodierpfad.

Drei getrennte Arbeiterstraenge sorgen dafuer, dass eine tagelange Recodierung
weder einen Download noch eine wartende Wiedergabe blockiert.

## Entwicklung

```bash
cd backend
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements-dev.txt
.venv/Scripts/python.exe -m pytest
.venv/Scripts/python.exe -m uvicorn app.main:app --reload
```

Frontend getrennt, mit Weiterleitung der API-Pfade an das Backend:

```bash
cd frontend && npm install && npm run dev
```

Ein Beispielarchiv zum Ansehen der Oberflaeche, ohne etwas herunterzuladen -
der Netzzugriff wird ersetzt, der Rest laeuft echt durch die Arbeiter:

```bash
cd backend && ./.venv/Scripts/python.exe tools/demo_befuellen.py <ordner-mit-seed1.mkv>
```

## Aufbau

```
backend/app/
  config.py              Einstellungen (Umgebungsvariablen mit Praefix YTA_)
  models.py              Datenmodell
  db.py                  SQLite mit WAL
  services/
    bundle.py            Kaltspeicher: ZIP schreiben, lesen, Direktzugriff
    cache.py             Heissspeicher: Lease, Fristen, Aufraeumen
    playback.py          Entscheidung Direktstream oder Transkodieren
    ranges.py            HTTP-Bereichsanforderungen
    media.py             ffmpeg/ffprobe
    ytdlp.py             yt-dlp-Anbindung
    jobs.py              Auftragswarteschlange
    jobs.py              Auftragswarteschlange
  workers/
    archive.py           Herunterladen, Umpacken, Buendeln; Recodierung
    prepare.py           Heisskopie herstellen
    sync.py              Kanal- und Playlist-Abgleich
    runner.py            Drei getrennte Arbeitergruppen
  api/
    stream.py            Auslieferung und Wiedergabe-Lease
    library.py           Kanaele, Playlists, Videos, Warteschlange, Speicher
frontend/src/
  lib/capabilities.ts    Codec-Erkennung im Browser
  components/Player.tsx  Player samt Vorbereitungsanzeige
  pages/                 Start, Kanal, Playlist, Wiedergabe, Warteschlange, Speicher
```
