# Vitrine

[![CI](https://github.com/gottschalkfelix4-source/vitrine/actions/workflows/ci.yml/badge.svg)](https://github.com/gottschalkfelix4-source/vitrine/actions/workflows/ci.yml)

Ein selbst gehostetes YouTube-Archiv mit eigener, YouTube-artiger Oberflaeche.
Archiviert ganze Kanaele samt Playlist-Gliederung und haelt den Speicherbedarf
durch eine Kalt/Heiss-Trennung klein. Ein Container, SQLite, kein
Elasticsearch.

> Der Name ist ein Platzhalter und laesst sich mit `YTA_APP_NAME` aendern.

## Was es kann

- **Ganze Kanaele** aufnehmen - Videos, Shorts, Livestreams und die vom Kanal
  angelegten Playlists, in der Gliederung des Kanals. Ein Video, das in drei
  Playlists steht, liegt trotzdem genau einmal auf der Platte.
- **Playlists zeigen alles**, auch nicht archivierte Positionen, mit
  Zustandskennzeichnung. Wer sehen will, was fehlt, kann es sehen.
- **Kalt/Heiss-Speicher.** Jedes Video ist ein Buendel im Kaltspeicher, aus dem
  der Player direkt streamt - ohne Entpacken. Eine Heisskopie entsteht nur,
  wenn ein Browser den Archivcodec nicht kann, und raeumt sich selbst wieder auf.
- **Eigener Player** mit Kapitelmarken, Puffer-Anzeige, Tempo, Untertiteln,
  Kinomodus, Bild-im-Bild, Vollbild und den gewohnten Tastenkuerzeln.
- **Volltextsuche** ueber Titel, Beschreibungen und das gesprochene Wort. Ein
  Untertitel-Treffer sagt nicht "kommt vor", sondern "faellt bei 4:32" - ein
  Klick springt dorthin. Findet deutsche Komposita und alle Umlautschreibweisen.
- **Qualitaet als Untergrenze**, nicht als Deckel: mindestens 1080p, nach oben
  offen. Ein stiller Rueckfall auf 360p wird erkannt und verworfen.
- **Regeln je Kanal**: nur Videos, keine Shorts (Standard), Livestreams
  optional, Archivierung automatisch oder nur auf Klick.
- **Einzelne Videos** gezielt holen oder wieder aus dem Archiv nehmen; Kanaele
  samt allem entfernen.
- **Warteschlange und Speicher** als eigene Ansichten - was laeuft, was wartet,
  was fehlgeschlagen ist, wie viel wo liegt.

## Schnellstart

### Docker Compose

```bash
docker compose up -d --build
```

Danach `http://localhost:8000`. Die Daten liegen unter `./data`.

### Unraid

Es gibt ein fertiges Template unter `unraid/vitrine.xml`. Das Image kommt von
GitHub Container Registry, `ghcr.io/gottschalkfelix4-source/vitrine:latest`,
und wird von der CI bei jedem Push auf `main` gebaut.

1. Docker-Tab, ganz unten **Template repositories**, dort eintragen:
   `https://github.com/gottschalkfelix4-source/vitrine` - speichern.
2. **Add Container**, im Template-Menue unter *User templates* **Vitrine**
   waehlen.
3. Zwei Pfade pruefen, der Rest hat Voreinstellungen:

| Im Container | Vorschlag | Was dort liegt |
|---|---|---|
| `/data` | `/mnt/user/appdata/vitrine` | Datenbank, Vorschaubilder, Heissspeicher - klein, oft gelesen, gehoert auf den Cache-Pool |
| `/data/bundles` | `/mnt/user/vitrine` | Die Videos - gross, selten angefasst, gehoert auf das Array, damit die Platten schlafen koennen |

Die Trennung bildet die Kalt/Heiss-Architektur auf Array und Cache-Pool ab.
Ein eigener Share fuer die Videos macht Backups einfach: ein Verzeichnis, ein
Buendel je Video.

`PUID`/`PGID` stehen auf 99/100 (nobody:users), damit die Dateien im Share dem
gehoeren, der sie ueber SMB sieht. Der Container startet als root, setzt die
IDs und gibt die Rechte ab. Umgeschrieben werden nur die Verzeichnisse selbst,
nicht rekursiv - bei Terabytes an Buendeln dauerte das sonst Minuten je Start.

Das Paket auf GitHub Container Registry erbt die Sichtbarkeit des Repos und ist
oeffentlich; Unraid zieht es ohne Anmeldung. Sollte das Repo einmal privat
werden, muss das Paket von Hand auf *public* gestellt werden (Repo →
*Packages* → *Package settings*) - eine API gibt es dafuer nicht.

### Der erste Kanal

Oben rechts **+ Kanal**, dann Handle oder URL (`@name`, `youtube.com/@name`
oder `UC…`). Zwei Hinweise:

- **Erst erfassen, dann entscheiden.** Ohne den Haken "Videos gleich
  archivieren" wird der Kanal nur erfasst; einzelne Videos holt man dann ueber
  den **Laden**-Knopf auf der Kachel. Ein Kanal mit 3000 Videos braucht zum
  Erfassen rund drei Minuten - zum Herunterladen Tage, bei einem Download
  gleichzeitig (siehe Nebenlaeufigkeit).
- **Groesse vorher festlegen.** Mit offener Hoechsthoehe kommt 4K, wo es 4K
  gibt - grob das Fuenffache von 1080p. `YTA_ARCHIVE_MAX_HEIGHT=1440` ist eine
  Zeile.

## Konfiguration

Alles ueber Umgebungsvariablen mit Praefix `YTA_`. Die wichtigsten:

| Variable | Standard | Bedeutung |
|---|---|---|
| `PUID` / `PGID` | 1000 / 1000 | Nutzer, unter dem geschrieben wird (Unraid: 99/100) |
| `YTA_ARCHIVE_MIN_HEIGHT` | 1080 | Untergrenze, kein Deckel. Bietet die Quelle weniger, wird das Beste genommen |
| `YTA_ARCHIVE_MAX_HEIGHT` | 0 (offen) | Obergrenze, z. B. 1440 |
| `YTA_ARCHIVE_CODEC` | av1 | `av1`, `hevc` oder `copy` (nichts umkodieren) |
| `YTA_AV1_CRF` | 30 | Qualitaet der Recodierung, siehe Messwerte unten |
| `YTA_AV1_PRESET` | 6 | Tempo 0-13; fuer grosse Kanaele 8-10 |
| `YTA_HWACCEL` | none | `qsv`, `nvenc`, `vaapi` - nur mit passender GPU |
| `YTA_HOT_MAX_BYTES` | 50 GiB | Limit des Heissspeichers (Unraid-Template: 20 GB) |
| `YTA_HOT_TTL_HOURS` | 24 | Frist einer Heisskopie ab letztem Zugriff |
| `YTA_HOT_TTL_AFTER_PLAYBACK_MINUTES` | 30 | Kuerzere Frist nach Wiedergabeende |
| `YTA_DOWNLOAD_CONCURRENCY` | 1 | Bewusst 1 - YouTube drosselt pro IP |
| `YTA_ENCODE_CONCURRENCY` | 1 | Ein Encode nutzt ohnehin alle Kerne |
| `YTA_DEFAULT_SYNC_INTERVAL_HOURS` | 12 | Rhythmus des Kanalabgleichs |
| `YTA_SUBTITLE_LANGUAGES` | de,en | Kommaliste; wird je Sprechzeile durchsuchbar |
| `YTA_YTDLP_COOKIES_FILE` | - | Pfad im Container, nur fuer altersbeschraenkte Inhalte, nur mit Wegwerf-Konto |
| `YTA_YTDLP_FORMAT` | - | Eigener yt-dlp-Selektor; ueberschreibt Min/Max |
| `YTA_TIMEZONE` | Europe/Berlin | |
| `YTA_LOG_LEVEL` | INFO | |

Vollstaendige Liste mit Erklaerungen in `backend/app/config.py`.

## Wie es arbeitet

### Kaltspeicher: ein ZIP-Buendel je Video

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
nichts mehr. Die Platzersparnis kommt aus der **Recodierung nach AV1**, nicht
aus dem Behaelter.

Die Mediendatei liegt **unkomprimiert** im ZIP, damit sie zusammenhaengend in
der Datei steht und sich an beliebiger Stelle lesen laesst. So kann der Player
direkt aus dem Buendel streamen. Wichtig dabei: `zipfile.ZipExtFile.seek()`
meiden - das liest bis zur Zielposition durch.

| Zugriffsart | Dauer |
|---|---|
| Offset direkt berechnet | ~0,5 ms, unabhaengig von der Sprungweite |
| `zipfile.seek()` | 53 ms bei 28 MB, waechst linear mit der Dateigroesse |

Der Offset ergibt sich aus `header_offset + 30 + len(name) + len(extra)` und
gilt auch bei ZIP64.

### Heissspeicher: nur wenn noetig

Eine entpackte oder transkodierte Datei entsteht ausschliesslich dann, wenn der
Client den Archivcodec nicht abspielen kann - rund 91 % der Browsersitzungen
koennen AV1, der Rest sind im Wesentlichen aeltere Apple-Geraete und alte
Fernseher. Welcher Browser was kann, meldet der Client selbst
(`MediaSource.isTypeSupported`); der Server raet nicht am User-Agent herum.

Eine Heisskopie verschwindet nach drei Regeln:

1. **Lease** - solange der Player Herzschlaege schickt, wird nichts geloescht.
2. **Frist** - kurze Frist nach Wiedergabeende, sonst die lange ab letztem Zugriff.
3. **Budget** - reisst der Heissspeicher sein Limit, fliegt zusaetzlich das am
   laengsten Ungenutzte raus.

Die Lease ist bewusst kein Zaehler: Ein Zaehler leckt, sobald jemand den Tab
schliesst oder der Browser abstuerzt, und die Datei bliebe fuer immer liegen.

### Was die Recodierung wirklich bringt

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
Qualitaetsminderung. **Nach Quellcodec unterscheiden spart mehr als jede
Preset-Optimierung:** H.264-Quellen werden recodiert, VP9- und AV1-Quellen
unveraendert abgelegt - dort waere der Re-Encode fast reiner Generationsverlust.

Encode-Tempo auf einer 6-Kern-CPU, 1080p30, je Stunde Video:

| Preset | Dauer | Ersparnis |
|---|---|---|
| 4 | ~80 min | am dichtesten |
| **6** | ~51 min | Voreinstellung |
| 8 | ~25 min | |
| 10 | ~16 min | fuer Massenarchivierung |

### Zwei Warteschlangen statt einer

Download und Recodierung laufen getrennt. Der Grund ist eine Zahl: Ein Kanal
mit 500 Stunden 1080p-Material braucht zum Recodieren rund 425 CPU-Stunden -
etwa 18 Tage Dauerlast. Steckte das im Archivierungsschritt, waere ein Video
erst nach Tagen sichtbar.

1. **Herunterladen** und in einen browsertauglichen Behaelter umpacken. Danach
   ist das Video vollstaendig, gesichert und abspielbar - nach Sekunden.
2. **Recodieren** als eigener Auftrag ganz hinten in der Rangfolge, im
   Hintergrund.

Das Umpacken ist kein Beiwerk: yt-dlp fuehrt Video und Ton nach MKV zusammen,
und **kein Browser spielt MKV ab**. Drei getrennte Arbeiterstraenge (Netz,
Vorbereitung, Recodierung) sorgen dafuer, dass eine tagelange Recodierung
weder einen Download noch eine wartende Wiedergabe blockiert.

### Kanalabgleich

Zwei Geschwindigkeiten, weil YouTube-Requests knapp sind:

- **Schnellcheck** ueber den RSS-Feed des Kanals. Geht an yt-dlp vorbei, kostet
  keinen Request, zaehlt nicht gegen das Drosselungsbudget - kann stuendlich
  laufen. Liefert aber nur rund 15 Eintraege.
- **Vollabgleich** ueber die `UU`-Uploads-Playlist als vollstaendige Quelle.
  Dazu werden die Shorts- und Livestream-Listen (`UUSH`, `UULV`) **immer**
  gelesen, auch wenn der Kanal sie nicht archivieren soll - gerade dann. Sie
  sind die einzige verlaessliche Kennzeichnung; erst danach wird eingereiht.
  Als letzte Sperre verwirft der Worker ein nachweislich hochkantiges Video
  vor dem Buendeln, wenn der Kanal keine Shorts will.

Ein Video, das in Uploads und mehreren Playlists steht, ist ein Datensatz und
eine Datei; Playlists sind reine Zuordnungen mit Position. Dasselbe Video darf
darin auch mehrfach vorkommen - echte Playlists tun das.

### Qualitaet: Minimum, kein Deckel

`YTA_ARCHIVE_MIN_HEIGHT` (1080) ist eine **Untergrenze**. Bietet die Quelle 4K,
wird 4K geladen, solange `YTA_ARCHIVE_MAX_HEIGHT` nicht gesetzt ist.

Der Unterschied ist nicht akademisch: Ein Selektor mit `height<=1080` waehlt
bei einem hochkantigen Short die 608x1080-Fassung statt der vollen 1080x1920,
weil yt-dlp die lange Seite als Hoehe zaehlt. Genau so war es in der ersten
Fassung.

Bietet eine Quelle nichts in Mindesthoehe, wird das Beste genommen und das am
Video vermerkt. Liefert der Download dagegen weniger, obwohl die Quelle mehr
anbietet, gilt die Kette als gestoert und das Video wird nicht als archiviert
verbucht.

### Der stille 360p-Fehler

Der gefaehrlichste Fehler im Betrieb kuendigt sich nicht an. Zwei Ursachen
fuehren dazu, dass yt-dlp Erfolg meldet und trotzdem nur eine Notfassung holt:

- **Fehlende JavaScript-Laufzeit.** yt-dlp braucht seit 2025.11.12 eine externe
  JS-Runtime. Fehlt sie, bricht nichts ab - es kommt stillschweigend eine
  reduzierte Formatauswahl. Das Image bringt Deno mit.
- **`yt-dlp[default]`, nicht `yt-dlp`.** Das Extra zieht die
  Challenge-Solver-Skripte nach. Ohne sie dasselbe Problem.

Beides aeussert sich gleich: yt-dlp faellt auf Format 18 zurueck, 360p. Wer das
nicht prueft, archiviert wochenlang 360p und merkt es erst beim Zuschauen - wenn
die Quelle laengst geloescht ist. `check_not_degraded()` faengt das nach jedem
Download ab; ein so entstandenes Video bleibt in der Warteschlange, bis die
Kette wieder steht. Die CI prueft im Container ausdruecklich, ob ffmpeg, Deno
und die EJS-Solver vorhanden sind.

### Nebenlaeufigkeit und Hardware

YouTube drosselt pro IP-Adresse, nicht pro Prozess; als Gast liegt die Grenze
bei rund 300 Videos je Stunde. `YTA_DOWNLOAD_CONCURRENCY` hochzudrehen macht
nicht schneller fertig, sondern voruebergehend gesperrt.

Intel 11. bis 13. Generation (UHD 730/770) kann AV1 **nur dekodieren**. Fuer
AV1-Encode in Hardware braucht es Intel Arc, Meteor/Lunar/Arrow Lake, NVIDIA
RTX 40/50 oder RDNA3+. Software-Encode bleibt ohnehin die bessere Wahl fuer ein
Archiv, das einmal geschrieben und lange behalten wird.

### Suche

Gesucht wird in Titeln, Beschreibungen und im gesprochenen Wort; Untertitel
werden je Sprechzeile indiziert. Zwei Entscheidungen dahinter, beide wegen der
deutschen Sprache:

**Trigram statt Wortzerlegung.** Der uebliche Tokenizer findet nur ganze
Woerter und Praefixe:

| Suche nach | mit Wortzerlegung | mit Trigram |
|---|---|---|
| `server` in "Heimserver" | nein | ja |
| `konfiguration` in "Netzwerkkonfiguration" | nein | ja |
| `groesse` in "Dateigroessen" | nein | ja |

**Symmetrische Umschrift.** Umlaute werden beim Indizieren *und* beim Suchen zu
ae/oe/ue/ss - "Groesse", "Größe" und "Grösse" finden dasselbe. Reines
`remove_diacritics` reicht nicht; es kennt das scharfe S gar nicht.

Der Index enthaelt nichts, was nicht auch in Datenbank oder Buendeln steht. Er
muss weder gesichert noch migriert werden (`POST /api/search/reindex`).

## Warum nicht TubeArchivist

TubeArchivist ist das naechstliegende Vergleichsprodukt. Drei Punkte, die hier
anders laufen:

- **Oberflaeche.** Der dortige Maintainer lehnt Design-Beitraege strukturell ab
  und sagt dem YouTube-artigen Ablauf ausdruecklich ab. Genau der ist hier das
  Ziel - inklusive Playlists, die auch das Fehlende zeigen.
- **Betriebsaufwand.** Dort drei Container samt Elasticsearch mit fest
  verdrahtetem 1-GB-Java-Heap. Hier ein Container mit SQLite und FTS5.
- **Speicher.** Kein bestehendes Projekt in dieser Kategorie hat einen
  Kalt/Heiss-Lebenszyklus.

## Entwicklung

```bash
cd backend
python -m venv .venv
# Linux/macOS: .venv/bin/python   Windows: .venv/Scripts/python.exe
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest
.venv/bin/python -m uvicorn app.main:app --reload
```

Ausserhalb des Containers braucht es ffmpeg im Pfad und eine
JavaScript-Laufzeit (Deno oder Node) - sonst liefert yt-dlp stillschweigend
reduzierte Formate.

Frontend getrennt, mit Weiterleitung der API-Pfade an das Backend:

```bash
cd frontend && npm install && npm run dev
```

Ein Beispielarchiv zum Ansehen der Oberflaeche, ohne etwas herunterzuladen -
der Netzzugriff wird ersetzt, der Rest laeuft echt durch die Arbeiter:

```bash
cd backend && .venv/bin/python tools/demo_befuellen.py <ordner-mit-seed1.mkv>
```

Die CI fuehrt Tests, Linter und Typpruefung aus, baut das Image, prueft darin
die Werkzeuge und veroeffentlicht es bei einem Push auf `main`.

## Was gegen echte Daten geprueft ist

Nicht alles laesst sich mit erzeugten Testdaten pruefen. Mehrere Fehler zeigten
sich erst im Ernstfall - darunter ein nicht serialisierbares Info-Dict, ein
Video, das doppelt in einer Playlist steht, und eine Fehlerbehandlung, die den
Fehler verdeckte. Deshalb steht hier, was tatsaechlich gelaufen ist:

| Geprueft gegen | Ergebnis |
|---|---|
| Metadaten eines echten Videos | 54 Formate bis 2160p, Codecs av01/avc1/vp09 |
| Vollstaendige Archivierung (CC-BY-Video) | 15 s, Buendel geprueft, Direktstream mit 206 |
| Kanal-Vollabgleich, 3363 Videos, 265 Sammlungen | 165 s; danach 3259 Videos, 100 Shorts, 4 Livestreams korrekt getrennt |
| Format-Auswahl (Trockenlauf) | Short: 1080x1920 statt 608x1080; 4K-Quelle: 3840x2160 |
| Container mit PUID/PGID 99/100 | Prozess und Datenbank entstehen unter 99:100 |

## Bekannte Grenzen

- **Keine Datenbank-Migrationen.** Alembic liegt in den Abhaengigkeiten, ist
  aber nicht eingerichtet; Schemaaenderungen brauchen bisher eine frische
  Datenbank. Vor dem ersten Dauerbetrieb der naechste sinnvolle Schritt.
- **Eine Fassung je Video.** Es gibt keine Qualitaetswahl im Player - das ist
  Absicht eines Archivs.
- **Keine Vorschaubilder beim Spulen.** Dafuer braeuchte es YouTubes
  Storyboards, die nicht archiviert werden.
- **Fremde Videos in Playlists** werden dem Kanal zugeordnet, ueber dessen
  Playlist sie gefunden wurden - fuer die Speicherstatistik je Kanal ungenau.

## Aufbau

```
backend/app/
  config.py              Einstellungen (Umgebungsvariablen mit Praefix YTA_)
  models.py              Datenmodell
  db.py                  SQLite mit WAL und Pragmas
  services/
    bundle.py            Kaltspeicher: ZIP schreiben, lesen, Direktzugriff
    cache.py             Heissspeicher: Lease, Fristen, Aufraeumen
    playback.py          Entscheidung Direktstream oder Transkodieren
    ranges.py            HTTP-Bereichsanforderungen
    media.py             ffmpeg/ffprobe, Behaelterwahl, Recodierung
    ytdlp.py             yt-dlp-Anbindung, RSS, Qualitaetspruefung
    suche.py             FTS5-Volltextsuche, VTT-Zerlegung
    reindex.py           Neuaufbau des Suchindex
    jobs.py              Auftragswarteschlange
  workers/
    archive.py           Herunterladen, Umpacken, Buendeln; Recodierung
    prepare.py           Heisskopie herstellen
    sync.py              Kanal- und Playlist-Abgleich
    runner.py            Drei getrennte Arbeitergruppen
  api/
    stream.py            Auslieferung und Wiedergabe-Lease
    library.py           Kanaele, Playlists, Videos, Suche, Warteschlange, Speicher
frontend/src/
  lib/capabilities.ts    Codec-Erkennung im Browser
  lib/api.ts             API-Client
  hooks/useApi.ts        Laden, Blaettern
  components/Player.tsx  Player mit eigener Steuerung
  pages/                 Start, Kanaele, Kanal, Playlist, Wiedergabe, Suche,
                         Warteschlange, Speicher
docker/entrypoint.sh     PUID/PGID zur Laufzeit
unraid/                  Template und Icon
```
