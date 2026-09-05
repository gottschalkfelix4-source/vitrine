# Sicherheitspruefung und Korrekturen vom 5. September 2026

Ausgangspunkt war Commit `3a846bb73dd93c8b0c246f125a8ab236350620ff` vor dem
Admin-Login. Geprueft wurden alle 115 versionierten Textdateien: Backend,
Frontend, Tests, Container, CI und Dokumentation. Sieben statische PNG-Symbole
wurden als nicht ausfuehrbare Dateien ausgenommen. Die Pruefung umfasste einen
unabhaengigen Gesamtdurchgang und gezielte Gegenpruefungen der Vertrauensgrenzen.
Es wurden keine Angriffe gegen YouTube oder einen laufenden Unraid-Server ausgefuehrt.

## Befunde am Ausgangsstand und ihre Behandlung

| Befund | Schweregrad | Korrektur |
|---|---|---|
| Archiv und Verwaltungs-API ohne Anmeldung erreichbar | Hoch | Serverseitige Anmeldung vor saemtlichen Archiv-, Medien- und Verwaltungsrouten; ohne lokales Admin-Setup bleibt der Zugriff gesperrt. |
| Beliebige Kanal-URLs konnten serverseitige Netzwerkzugriffe ausloesen | Mittel | Kanaladressen auf gepruefte YouTube-Hosts und Kanalpfade begrenzt; nur der YouTube-Kanalextraktor ist zugelassen. |
| Vorschaubild-Pfade konnten unter Windows das Bildverzeichnis verlassen | Mittel | Gemeinsame Pfadpruefung gegen Verzeichniswechsel, Laufwerkspfade, Links und Junctions; nur Rasterbild-Endungen. |
| Cookie- und VPN-Uploads wurden vor der Groessenpruefung vollstaendig gelesen | Mittel | Begrenzung des tatsaechlichen Anfragekoerpers vor dem Multipart-Parser; zusaetzliche begrenzte Datei-Leseoperationen. |
| Manipulierte Kanal-IDs konnten beim Entfernen uebergeordnete Archivordner treffen | Hoch | Externe Kanal-IDs werden vor dem Speichern validiert; alle Loeschziele werden vor Datenbankaenderungen auf ihre Ablage begrenzt. |
| Fremde konnten Wiedergabevorbereitung und Transkodierungen ausloesen und Speicher binden | Mittel | Vorbereitung, Streamzugriff und Wiedergabe-Meldungen verlangen eine aktive Admin-Sitzung. Das verbleibende Kapazitaetsverhalten fuer den vertrauenswuerdigen Administrator ist unten beschrieben. |

Die Schweregrade beschreiben den Ausgangsstand vor den Korrekturen. Der
abgeschlossene Standard-Scan ist unter der Kennung
`a32415fa-18dc-45de-a4e5-e9f04792b358` erfasst. Seine Tokenmessung war nicht
verfuegbar. Dieses Dokument beschreibt die anschliessende Umsetzung und
ersetzt nicht die unveraenderten Originalartefakte des Scans.

## Anmeldung und Nachpruefung

Passwoerter werden mit individuellem Salt und scrypt gespeichert. Sitzungstoken
sind zufaellig und werden in der Datenbank nur gehasht abgelegt. Das
Sitzungscookie ist HttpOnly, SameSite=Strict und standardmaessig Secure.
Schreibende Anfragen verlangen eine Sicherheitskennung und werden auf fremde
Browser-Herkunft geprueft. Abmelden widerruft die aktuelle Sitzung;
Passwortaenderung oder lokaler Reset widerrufen alle Sitzungen.

Gezielte Tests pruefen insbesondere nicht angemeldete API- und Medienaufrufe,
fremde Herkunft und fehlende CSRF-Kennung, Ablauf und Widerruf von Sitzungen,
gleichzeitige Anmeldung und Passwort-Reset, ungueltige gespeicherte Dateipfade,
Windows-Junctions, manipulierte Kanal-URLs sowie Uploads ohne verlaessliche
Content-Length-Angabe. Die vorhandenen Archiv-, Warteschlangen- und
Wiedergabetests bleiben Bestandteil der vollstaendigen Testsuite.
Die abschliessende lokale Backendpruefung ergab 669 bestandene Tests und einen
uebersprungenen Dateisymlink-Test wegen fehlender Windows-Berechtigung;
Windows-Junction-Tests liefen erfolgreich. Ruff meldete keine Fehler.
Alle 76 Frontendtests und der Produktionsbuild bestanden ebenfalls.

Die Gegenpruefung der neuen Anmeldung deckte auch API-Routen mit einem
ASGI-Pfadpraefix ab. Die Zugangspruefung verwendet dieselbe Pfadnormalisierung
wie der Router. CacheStorage prueft vor dem Speichern zusaetzlich den
no-store-Header; damit werden auch kodierte API-Pfade nicht versehentlich
als oeffentliche Oberflaeche gespeichert.

Geschuetzte API- und Medienantworten tragen `Cache-Control: no-store`.
Der Service Worker entfernt den bisherigen Bildcache; die Anwendung entfernt
Archivansichten und Videopuffer beim Sitzungsende. Der Server fordert beim
Update und Abmelden ausserdem die Bereinigung alter HTTP-Cache-Eintraege an.
Passwortwerte werden nicht in Validierungsfehlern zurueckgegeben.

Ein isolierter Headless-Browsertest mit ausschliesslich lokalen Demodaten
pruefte Anmeldung und Fehlerfaelle, echte Videowiedergabe, Warteschlangenpause,
Abmelden in mehreren Tabs, Browser-Zurueck, Passwortwechsel und die erneute
Anmeldung. Ein unterbrochener Logout meldet den Netzwerkfehler und behauptet
keinen erfolgreichen Widerruf. Desktop- und Mobilansichten wurden visuell
bis hinunter zu 320 Pixel Breite kontrolliert. Die lokale Vorschau nutzte ausdruecklich die HTTP-Testoption;
Secure-Cookies und HTTPS-Proxy-Herkunft wurden in den Backendtests geprueft.

Die getrennte Abhaengigkeitspruefung mit npm audit und pip-audit meldete keine
bekannten Schwachstellen in den anschliessend installierten JavaScript- und
Python-Paketen. Ein Fund im lokalen Python-Bauwerkzeug setuptools wurde durch
Aktualisieren dieses Entwicklungswerkzeugs behoben. Diese Abfrage ist eine
Momentaufnahme und keine Pruefung aller Betriebssystempakete des Containers.

## Grenzen fuer den Betrieb

- Vor einer oeffentlichen Freigabe HTTPS am Reverse Proxy einrichten, den Host
  erhalten und den direkten Container-Port privat halten. Einrichtung und
  Wiederherstellung stehen in der [Betriebsanleitung](../README.md#admin-zugang-und-oeffentlicher-betrieb).
- Ein Konto mit hoechstens zehn Sitzungen; keine oeffentlichen Zuschauer oder
  Mehrbenutzerrechte. Die Sitzungsdauer betraegt standardmaessig zwoelf Stunden.
- Das globale Limit von zehn Anmeldeversuchen in 15 Minuten bleibt bei einem
  Neustart erhalten. Login-Spam kann eigene neue Anmeldungen voruebergehend
  blockieren; bestehende Sitzungen bleiben nutzbar. Der lokale Reset hebt die
  Sperre auf. Vorgeschaltete Anfragelimits sind bei Internetbetrieb sinnvoll.
- Das Heissspeicherbudget ist weiterhin ein Aufraeumziel. Aktive Wiedergaben
  koennen es voruebergehend ueberschreiten. Fuer einen spaeteren oeffentlichen
  Zuschauerbetrieb waeren eigene Kapazitaets- und Berechtigungsregeln noetig.
- Datenverzeichnis, YouTube-Cookies, VPN-Schluessel und Backups bleiben privat.
  Bereits gespeicherte Downloads oder Aufnahmen lassen sich nicht durch einen
  Logout zurueckholen. Lokale Schreibrechte auf das Archiv gelten als vertraut.
- Die VPN-Auswahl gilt fuer Netzauftraege der Warteschlange, nicht als
  verbindliche Netzsperre fuer den gesamten Container. Direkte Kanalabfragen,
  Cookie-Tests und Vorschaubildabrufe koennen die eigene Verbindung verwenden.

Die Pruefung ist eine begrenzte Quellcode- und Funktionspruefung. Die konkrete
oeffentliche Domain, TLS-Konfiguration und laufende Unraid-Installation wurden
nicht eingerichtet oder aus dem Internet getestet.
