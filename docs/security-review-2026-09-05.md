# Sicherheitspruefung und Korrekturen vom 5. September 2026

Dieser Bericht dokumentiert auch fruehere Zwischenstaende. Der Betreiber hat
anschliessend ein oeffentliches Archiv mit geschuetzter Verwaltung angefordert.
Die aktuell geltenden Zugriffsregeln stehen im letzten Abschnitt und in der README.

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

## Ergaenzung: Ersteinrichtung im Browser

Die erste Admin-Einrichtung ist inzwischen auch ueber einen automatisch
geoeffneten Dialog moeglich. Dafuer erzeugt die Anwendung beim Start ohne Konto
einen zufaelligen Code mit 256 Bit Entropie und zeigt ihn ausschliesslich im
privaten Container-Protokoll. In der Datenbank liegt nur dessen SHA-256-Hash.
Ein weiterer Start ohne Konto ersetzt den Code. Eine bestehende Einrichtung
wird durch einen Neustart oder diesen Dialog niemals zurueckgesetzt.

Der Einrichtungsaufruf verlangt den Code, JSON und einen eigenen Request-Header,
prueft die Browser-Herkunft und begrenzt den Anfragekoerper auf 16 KiB. Falsche
Codes werden vor dem aufwendigen Passwort-Hashen abgewiesen. Das Konto wird
atomar nur angelegt, wenn es weiterhin fehlt und der Code weiterhin gilt;
gleichzeitige Versuche, Codewechsel oder eine Einrichtung per Konsole koennen
keine vorhandenen Zugangsdaten ueberschreiben. Erfolg verbraucht den Code und
oeffnet die normale Anmeldung, ohne bereits eine Sitzung auszustellen.

Der Code wird nicht ueber die API ausgegeben und nicht im Browser gespeichert.
Container-Protokolle muessen bis zum Abschluss der Einrichtung privat bleiben.
Der lokale Konsolenbefehl bleibt der Wiederherstellungsweg fuer vergessene
Passwoerter. Die oben beschriebene HTTPS-Voraussetzung gilt standardmaessig
fuer die Anmeldung; die Ersteinrichtung ist davon ausgenommen (siehe unten).

Nach dieser Ergaenzung bestanden lokal 686 Backendtests (ein bekannter
Windows-Dateisymlink-Test uebersprungen), 85 Frontendtests, Ruff und der
Produktionsbuild. Der isolierte Browsertest pruefte automatische Anzeige,
Fokuswechsel beim Codekopieren, Tastaturbedienung, falschen Code, Passwort-
Wiederholung, Herkunfts- und Netzwerkfehler, die Einrichtung bis zur ersten
Anmeldung sowie den erneuten Aufruf bei vorhandenem Konto. Desktop und
Mobilansichten bis 320 Pixel Breite wurden visuell kontrolliert.

Die Passwortregel wurde auf Wunsch des Betreibers angepasst: Neue Passwoerter
brauchen mindestens acht Zeichen, einen Grossbuchstaben und ein Sonderzeichen.
Einrichtung, Passwortwechsel und Konsolen-Reset verwenden dieselbe Regel;
Leerzeichen allein gelten nicht als Sonderzeichen. Bestehende Passwoerter
bleiben beim Anmelden gueltig. Die Containerpruefung richtet das Konto mit
einem genau acht Zeichen langen Testpasswort ein und prueft die Anmeldung
erneut nach einem Neustart.

Die Ersteinrichtung akzeptiert nun auch einen HTTP-Browser-Origin, unabhaengig
von `YTA_AUTH_COOKIE_SECURE`. Diese Ausnahme gilt ausschliesslich fuer
`POST /api/auth/setup`, nach derselben Pfadnormalisierung wie der Router.
Host und Port, Einmalcode, JSON, eigener Request-Header und Groessenlimit
werden weiterhin geprueft. Der Aufruf erzeugt keine Sitzung und kein Cookie;
bestehende Konten lassen sich damit weiterhin nicht ueberschreiben. Fuer die
spaetere Anmeldung bleibt der HTTPS-Standard erhalten. Der explizite lokale
HTTP-Betrieb mit `YTA_AUTH_COOKIE_SECURE=false` funktioniert weiterhin.
Die HTTP-Einrichtung ist fuer das eigene Netz gedacht: Einrichtungscode und
Passwort werden dabei unverschluesselt uebertragen. Die Containerpruefung
sendet jetzt auch den HTTP-Origin eines Browsers, damit dieser Unterschied
zu einem originlosen Konsolenaufruf bei Updates abgedeckt bleibt.

## Aktueller Stand: oeffentliches Archiv und Admin-Verwaltung

Auf ausdruecklichen Wunsch des Betreibers funktionieren Einrichtung,
Anmeldung und Nutzung jetzt ueber HTTP und HTTPS. Die fruehere Cookie-Variable
ist nur noch kompatibilitaetshalber vorhanden. HTTPS verwendet ein Secure-Cookie,
HTTP einen eigenen Cookie-Namen; beide bleiben HttpOnly und SameSite=Strict.
Ein validierter HTTPS-Origin erkennt TLS am Reverse Proxy, ohne beliebige
Forwarded-Header als vertrauenswuerdig zu behandeln. Host-/Portpruefung, CSRF
fuer Admin-Schreibzugriffe, Passwort-Hashing, Loginlimit und Einmalcode bleiben.

Oeffentliche Leserechte gelten explizit fuer archivierte Kanaele, Playlists,
Videos, Suche, Vorschaubilder und Untertitel sowie bereinigte Warteschlangen-
und Speicheransichten. Nicht archivierte Inhalte, Admin-Fortschritt, interne
Dateipfade, Konfiguration, Fehlermeldungen und VPN-Verbindungsdetails werden
Gaesten nicht ausgegeben. Gastfortschritt bleibt im Browserspeicher. Settings,
Kanalaufnahme, Download-/Loeschaktionen und das Stream-Dashboard bleiben
serverseitig durch Admin-Anmeldung und bei Aenderungen durch CSRF geschuetzt.

Wiedergabe-Sitzungen sind ausdruecklich auch fuer Gaeste verfuegbar. Ihre
oeffentlichen POST-Aufrufe benoetigen einen Anwendungskopf und passende Herkunft;
ein zufaelliger Sitzungstoken betrifft jeweils nur einen Player. Das Dashboard
verraet weder diese Tokens noch Admin-Cookies. Zuschauer-IP und Browser werden
nur dem Administrator angezeigt. Fuer echte IPs hinter einem Reverse Proxy
muss dessen konkrete Adresse in `FORWARDED_ALLOW_IPS` stehen.

Die Live-Transkodierung fuehrt keine beliebigen externen Quellen oder
Nutzereingaben als Befehle aus. FFmpeg liest einen validierten Medienbereich
des Archivbuendels und kodiert nur angeforderte Sechs-Sekunden-Abschnitte.
64 Sitzungen, 16 je Gegenadresse, zwei Encoderprozesse, eine Laufzeitgrenze,
maximale Abschnittsgroesse und ein 64-MiB-LRU-Cache begrenzen den Aufwand.
Abgebrochene Abschnittsabrufe stoppen ihren Prozess; Sitzungsschluss und
90 Sekunden ohne Lebenszeichen entfernen die jeweilige Sitzung. Diese
Kapazitaetsgrenzen begrenzen Last, garantieren aber keine Verfuegbarkeit unter
beliebiger verteilter Ueberlast. HTTP uebertraegt Zugangsdaten unverschluesselt;
die oeffentliche TLS-Terminierung liegt wie angefordert beim Reverse Proxy.

Gezielte Pruefungen umfassen HTTP-/HTTPS-Cookiewechsel, Admin-/Gasttrennung,
Suchfilter vor Trefferbegrenzung, gesperrte Mutationen, getrennten Fortschritt,
echtes FFmpeg-Lesen aus MP4-/MKV-Buendeln, Spulen zu spaeten Abschnitten,
parallele Zuschauer, Prozessgrenzen, Abbruch und ablaufende Sitzungen.

## Ergaenzung: lokale Geo-IP-Livekarte

Die Standortzuordnung ergaenzt ausschliesslich die vorhandene Admin-Antwort
`GET /api/streams`. Es gibt keinen frei aufrufbaren Lookup-Endpunkt und keine
zusaetzliche Freigabe fuer Gaeste. Verwendet wird nur die bereits beim
Wiedergabestart erfasste Verbindungsadresse, nach der bestehenden
Uvicorn-Proxy-Vertrauenspruefung; rohe Forwarded-Header werden nicht ausgewertet.

DB-IP City Lite liegt lesbar im Container-Image. Der Build prueft die gepinnte
Download-Pruefsumme und die Lesbarkeit der Datenbank. Lookup und Weltkarte
brauchen keine externen Anfragen; Zuschaueradressen verlassen den Server
dadurch nicht. Der begrenzte Lookup-Cache bleibt im Arbeitsspeicher. Private,
reservierte und nicht zuordenbare Adressen bekommen keine Koordinaten. Fehlende
oder defekte Datenbanken werden als nicht verfuegbar gemeldet, ohne Dateipfade
oder interne Fehlermeldungen an den Browser weiterzugeben.

Die Anreicherung erfolgt nach dem Stream-Snapshot ausserhalb dessen Sperre.
Die Gegenpruefung hat Admin-Zugriff, abgewiesene fremde Lookup-Pfade,
IPv4/IPv6-Sonderfaelle, endende Streams und die Unabhaengigkeit der
Wiedergabe-Lebenszeichen von einem blockierten Geo-IP-Reader geprueft.
Standortangaben sind ausdruecklich Naeherungen und kein genauer Aufenthaltsort.
