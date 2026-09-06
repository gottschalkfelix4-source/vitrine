# Oberflächenprüfung vom 6. September 2026

Die Oberfläche wurde im eingebetteten Browser mit wechselnden Fenstergrößen
geprüft. Wiedergabetests liefen gegen den aktuellen Backend-Code mit isolierten
Testdaten und lokal erzeugten Videos; das bestehende Archiv wurde nicht verändert.

## Automatisierte Prüfungen

- Frontend: `npm test` — 192 Tests in 18 Dateien erfolgreich.
- Produktionsbuild: `npm run build` — TypeScript und Vite erfolgreich.
- Backend: 135 Tests für Bibliothek, Suche und öffentlichen Zugriff erfolgreich;
  Ruff für die geänderten Python-Dateien erfolgreich.
- `git diff --check` — keine Whitespace-Fehler.

Die Tests decken unter anderem gleichzeitige Ladeanforderungen, verspätete
Antworten nach Filterwechseln, StrictMode, Wiederholungen nach Fehlern,
Suchpagination, Fokusführung in Dialogen, mobile Navigation, Player-Menüs und
den Erhalt ungespeicherter Einstellungen ab.

## Browserprüfungen

- Telefon mit 320 und 390 Pixel Breite: Startseite, Suche, Kanäle, Kanalansicht,
  Navigation und Wiedergabe ohne horizontalen Seitenüberlauf.
- Tablet mit 768 und 1024 Pixel Breite und mobiles Querformat mit 844 × 390 Pixeln:
  passendes Raster, erreichbare Player-Steuerung und innerhalb des Bildschirms
  angeordnete Menüs.
- Desktop mit 1440 × 900 Pixeln: Videoraster, Suche und Kinomodus;
  Startseite mit vorhandenen Archivvideos zusätzlich bei 1920 × 1080 Pixeln.
- Automatisches Nachladen mit 137 Einträgen: 60 → 120 → 137, ohne Duplikate.
  Ein absichtlicher Fehler auf der zweiten Seite erhält die ersten 60 Videos;
  Wiederholen lädt die nächste Seite erfolgreich.
- Suche mit 87 Videos und 87 Untertiteltreffern: 40 → 80 → 87;
  Bereichswechsel erhält bereits geladene Ergebnisse.
- Player: direkte Wiedergabe, HLS-Qualitätswechsel unter Erhalt der Position,
  Untertitel, Kapitelsprung, Vollbild sowie Hochkantvideo geprüft. Direkte
  Range-Antwort, HLS-Manifest und Segmente kommen vom aktuellen Backend.
- Mobile Schublade: Fokus beim Öffnen, Tab-Umlauf, Escape und Rückgabe des Fokus.
- Helles und dunkles Design geprüft.

## Betriebsstand und Grenzen

Die Fenstergrößenprüfung ersetzt keinen Test auf physischen iOS- und
Android-Geräten. Native Vollbild- und Bild-im-Bild-Unterstützung hängt vom Browser
ab; abgelehnte Aufrufe werden abgefangen.

Der laufende Container auf Port 8000 verwendet einen älteren Backend-Stand mit
anderen Wiedergabe-Endpunkten. Zur Übernahme muss das Container-Image aus diesem
Arbeitsstand neu gebaut und der Dienst aktualisiert werden. Die aktuelle
Oberfläche wurde deshalb zusätzlich zusammen mit dem aktuellen Backend in einer
isolierten Umgebung geprüft.

Vite meldet weiterhin die Größe des separat geladenen HLS-Chunks (rund 575 kB
vor gzip). Der Player und die übrigen großen Seiten werden erst bei Bedarf geladen.
