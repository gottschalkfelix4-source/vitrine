"""Die Arbeiterstraenge.

Drei getrennte Gruppen statt eines gemeinsamen Pools, weil die drei Arten von
Arbeit voellig unterschiedliche Eigenschaften haben:

*Netz* - Downloads und Kanalabgleiche. Muss schmal bleiben, weil YouTube pro
IP-Adresse drosselt und nicht pro Prozess; als Gast liegt die Grenze bei rund
300 Videos je Stunde. Mehr Straenge machen nicht schneller fertig, sondern
voruebergehend gesperrt. Die einzige Ausnahme sind mehrere Adressen: Jeder
Strang holt sich vor dem Auftrag einen freien Ausgang - die eigene Leitung
oder einen WireGuard-Tunnel -, und mit vier Tunneln sind vier parallele
Downloads tatsaechlich vier getrennte Budgets statt eines geteilten.

*Vorbereitung* - Jemand sitzt davor und wartet auf sein Video. Braucht einen
eigenen Strang, sonst steht die Wiedergabe hinter einer stundenlangen
Recodierung in der Schlange.

*Recodierung* - Reine Rechenlast, laeuft tagelang. Darf beliebig warten, darf
aber nie die beiden anderen blockieren.

Wuerde man das in einen Pool werfen, waere der haeufigste Effekt genau der
aergerlichste: Man klickt auf ein Video und wartet, weil gerade drei
Recodierungen alle Plaetze belegen.
"""

from __future__ import annotations

import logging
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass

from app.config import settings
from app.db import session_scope
from app.models import JobType
from app.services import abbruch, jobs, pause, vpn
from app.services.ausgang import Ausgang

log = logging.getLogger(__name__)

#: Pause, wenn nichts zu tun ist. Kurz genug, dass sich eine Vorbereitung
#: nicht spuerbar verzoegert, lang genug, um nicht dauernd die Datenbank zu
#: befragen.
LEERLAUF_S = 2.0


#: Wie lange hoechstens am Stueck auf das Ende einer Drosselpause gewartet
#: wird. Die Pause selbst dauert Minuten bis Stunden; in kurzen Haeppchen zu
#: warten kostet nichts und haelt das Herunterfahren zuegig - sonst haenge der
#: Container beim Update bis zu einer Stunde in einem einzigen wait().
DROSSEL_TAKT_S = 5.0


@dataclass(slots=True)
class Gruppe:
    name: str
    typen: list[str]
    straenge: int
    #: Ob die Gruppe mit YouTube spricht. Nur sie waehlt einen Ausgang und
    #: pausiert bei einer Abweisung - Recodierung und Vorbereitung arbeiten auf
    #: bereits geladenen Dateien und haetten davon nichts als Stillstand.
    netz: bool = False


def _gruppen() -> list[Gruppe]:
    return [
        Gruppe(
            "netz",
            list(pause.NETZ_TYPEN),
            settings.download_concurrency,
            netz=True,
        ),
        # Immer genau einer: Mehr bringt nichts, weil ffmpeg ohnehin alle Kerne
        # nutzt, und zwei gleichzeitige Vorbereitungen machen beide langsam.
        Gruppe("vorbereitung", [JobType.VIDEO_PREPARE], 1),
        Gruppe("recodierung", [JobType.VIDEO_RECODE], settings.encode_concurrency),
    ]


class Arbeiterwerk:
    """Startet und beendet alle Arbeiterstraenge."""

    def __init__(self) -> None:
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._sperre = threading.Lock()
        #: Wie viele Straenge jede Gruppe haben soll. Wird bei jeder Aenderung
        #: der Einstellungen neu gesetzt.
        self._soll: dict[str, int] = {}
        #: Welche Platznummern gerade besetzt sind. Ohne diese Buchfuehrung
        #: koennte beim Hochsetzen ein Strang doppelt entstehen, waehrend der
        #: alte noch einen Download zu Ende bringt.
        self._belegt: dict[str, set[int]] = {}

    def start(self) -> None:
        # Import mit Nebenwirkung: Dabei tragen sich die Bearbeiter ueber den
        # @jobs.register-Dekorator in die Registrierung ein.
        from app.workers import archive, prepare, sync  # noqa: F401

        fehlend = [t for t in JobType if t not in jobs.HANDLERS]
        if fehlend:
            log.warning("Kein Bearbeiter fuer: %s", ", ".join(fehlend))

        # Ein Bearbeiter allein genuegt nicht - der Auftragstyp muss auch einer
        # Gruppe zugeordnet sein, sonst holt ihn niemand ab. Genau das ist beim
        # Hochstufen passiert: Der Bearbeiter war da, die Zuordnung fehlte, und
        # der Auftrag stand still auf "wartet", ohne Fehler und ohne Hinweis.
        zugeordnet = {t for g in _gruppen() for t in g.typen}
        heimatlos = [t for t in JobType if t not in zugeordnet]
        if heimatlos:
            log.error(
                "Keine Arbeitergruppe zustaendig fuer: %s - solche Auftraege bleiben "
                "unbearbeitet in der Warteschlange stehen.", ", ".join(heimatlos),
            )
        self.anpassen()

    def anpassen(self) -> dict[str, int]:
        """Gleicht die Zahl der Straenge an die Einstellungen an.

        Wird beim Start und nach jeder Aenderung in der Oberflaeche aufgerufen.
        Vorher stand bei "Parallele Downloads" ein Neustart-Hinweis - die Zahl
        wurde nur beim Hochfahren gelesen. Wer sie aendert, will aber sehen,
        dass es wirkt, und nicht den Container neu starten.

        Hochsetzen wirkt sofort: Die fehlenden Straenge werden gestartet und
        greifen sich den naechsten wartenden Auftrag.

        Heruntersetzen wirkt, sobald die ueberzaehligen Straenge ihren
        laufenden Auftrag beendet haben. Ein Download mitten im Lauf wird
        dafuer NICHT abgebrochen - man verloere Hunderte Megabyte, nur weil
        jemand eine Zahl verstellt hat. Bis dahin laufen kurzzeitig mehr
        Straenge als eingestellt.
        """
        gestartet: dict[str, int] = {}
        with self._sperre:
            for gruppe in _gruppen():
                soll = max(1, gruppe.straenge)
                self._soll[gruppe.name] = soll
                belegt = self._belegt.setdefault(gruppe.name, set())
                for nummer in range(soll):
                    if nummer in belegt:
                        continue
                    belegt.add(nummer)
                    t = threading.Thread(
                        target=self._schleife,
                        args=(gruppe, nummer),
                        name=f"{gruppe.name}-{nummer + 1}",
                        daemon=True,
                    )
                    t.start()
                    self._threads.append(t)
                    gestartet[gruppe.name] = gestartet.get(gruppe.name, 0) + 1
            # Beendete Straenge nicht ewig mitschleppen.
            self._threads = [t for t in self._threads if t.is_alive()]

        log.info(
            "Arbeiter: %s%s",
            ", ".join(f"{name}x{anzahl}" for name, anzahl in self._soll.items()),
            f" (neu gestartet: {gestartet})" if gestartet else "",
        )
        return dict(self._soll)

    def stop(self, timeout: float = 20.0) -> None:
        """Beendet die Straenge und wartet, bis sie wirklich draussen sind.

        Zuerst das Abbruchsignal: Ein Strang, der gerade laedt oder kodiert,
        merkt das ohne es gar nicht - er steckt tief in yt-dlp oder ffmpeg und
        wuerde erst nach Stunden das naechste Mal auf ``self._stop`` schauen.

        Danach wird gewartet, und zwar auf alle zusammen statt auf jeden
        einzeln mit einem Bruchteil der Zeit. Bei acht Straengen bekaeme sonst
        jeder zweieinhalb Sekunden - zu wenig, damit ffmpeg sich beendet, und
        der einzige, der ueberhaupt Zeit braucht, ist ohnehin nur einer.
        """
        abbruch.anfordern()
        self._stop.set()
        frist = time.monotonic() + timeout
        for t in self._threads:
            t.join(timeout=max(0.0, frist - time.monotonic()))
        noch_da = [t.name for t in self._threads if t.is_alive()]
        if noch_da:
            log.warning(
                "Arbeiter nicht rechtzeitig beendet: %s - sie werden mit dem "
                "Prozess abgeraeumt", ", ".join(noch_da),
            )

    def _schleife(self, gruppe: Gruppe, nummer: int) -> None:
        try:
            self._arbeiten(gruppe, nummer)
        finally:
            # Platz freigeben, damit ein spaeteres Hochsetzen ihn wieder
            # besetzen kann - auch wenn dieser Strang an einer Ausnahme endet.
            with self._sperre:
                self._belegt.get(gruppe.name, set()).discard(nummer)

    @staticmethod
    @contextmanager
    def _ausgang(gewaehlt: Ausgang | None):
        """Belegt den gewaehlten Ausgang - oder tut nichts.

        Der Zweig fuer ``None`` ist nicht Bequemlichkeit: Recodierung und
        Vorbereitung reden gar nicht mit YouTube. Sie sollen weder einen
        Tunnelplatz belegen noch bei einer Sperre stillstehen.
        """
        if gewaehlt is None:
            yield None
            return
        with vpn.benutzen(gewaehlt):
            yield gewaehlt

    def _arbeiten(self, gruppe: Gruppe, nummer: int) -> None:
        #: Nur fuer die Protokollierung: ohne das schreibt jeder Strang alle
        #: fuenf Sekunden dieselbe Zeile, eine Stunde lang.
        pausiert = False
        while not self._stop.is_set():
            # Zwischen zwei Auftraegen nachsehen, ob es diesen Platz noch gibt.
            # Nicht waehrend eines Auftrags: Ein laufender Download wird nicht
            # abgebrochen, nur weil jemand die Zahl heruntergesetzt hat.
            if nummer >= self._soll.get(gruppe.name, 0):
                log.info("[%s] Strang %d wird nicht mehr gebraucht", gruppe.name, nummer + 1)
                return

            # Weist YouTube gerade ab, wird gar nicht erst ein Auftrag geholt.
            # Ihn zu holen und scheitern zu lassen waere der teurere Weg: Bei
            # 1800 wartenden Videos brennt eine einzige Sperre binnen Minuten
            # die ganze Warteschlange ab, und jeder Versuch verlaengert sie.
            #
            # Gefragt wird nach einem freien AUSGANG, nicht nach einer Pause:
            # Mit mehreren WireGuard-Tunneln heisst "einer ist gesperrt" nicht
            # mehr "es geht nichts". Der Strang bekommt dann den naechsten
            # freien und arbeitet weiter; erst wenn keiner mehr frei ist, wird
            # gewartet.
            ausgang = None
            if gruppe.netz:
                ausgang = vpn.waehlen()
                if ausgang is None:
                    # Zwei verschiedene Lagen, die nicht zu verwechseln sind:
                    # Entweder sind alle Ausgaenge gesperrt - dann gibt es eine
                    # Restzeit -, oder es gibt gar keinen brauchbaren, weil
                    # kein Tunnel etwas durchlaesst. Das zweite waere mit einer
                    # Zeitangabe versehen schlicht falsch ("wartet 0 Minuten")
                    # und verschwiege den eigentlichen Grund.
                    rest = vpn.wartezeit()
                    if not pausiert:
                        if rest > 0:
                            log.info(
                                "[%s] Strang %d wartet %.0f Minuten - alle Ausgaenge gesperrt",
                                gruppe.name, nummer + 1, rest / 60,
                            )
                        else:
                            log.info(
                                "[%s] Strang %d wartet - kein Ausgang bereit. Bei "
                                "eingeschaltetem VPN heisst das: kein Tunnel laesst etwas "
                                "durch (siehe Einstellungen -> VPN-Tunnel).",
                                gruppe.name, nummer + 1,
                            )
                        pausiert = True
                    self._stop.wait(min(rest, DROSSEL_TAKT_S) if rest > 0 else DROSSEL_TAKT_S)
                    continue
                if pausiert:
                    log.info(
                        "[%s] Strang %d nimmt die Arbeit wieder auf, ueber %s",
                        gruppe.name, nummer + 1, ausgang.name,
                    )
                    pausiert = False
            try:
                with session_scope() as db:
                    job = jobs.claim_next(db, gruppe.typen)
                    if job is None:
                        # Auch bei einer unbefristeten manuellen Pause bleibt
                        # der Strang erreichbar. Waehrend des Wartens keine
                        # Lesetransaktion oder Datenbankverbindung festhalten.
                        db.rollback()
                        self._stop.wait(LEERLAUF_S)
                        continue

                    bearbeiter = jobs.HANDLERS.get(job.type)
                    if bearbeiter is None:
                        jobs.gescheitert(db, job, f"kein Bearbeiter fuer {job.type}")
                        continue

                    log.info(
                        "[%s] %s %s beginnt%s",
                        gruppe.name, job.type, job.target_id or "",
                        f" ueber {ausgang.name}" if ausgang and ausgang.ist_tunnel else "",
                    )
                    # Der Ausgang wird erst JETZT belegt, nicht schon beim
                    # Nachsehen. Sonst gilt ein Tunnel als beschaeftigt,
                    # waehrend der Strang nur alle zwei Sekunden in eine leere
                    # Warteschlange schaut - in der Oberflaeche stuende dann
                    # dauerhaft "laedt", und die Auswahl mied einen Tunnel, der
                    # in Wahrheit nichts tut.
                    #
                    # Er gilt dafuer fuer den GANZEN Auftrag. Mittendrin zu
                    # wechseln waere sinnlos und schaedlich: Einen halb
                    # geladenen Download ueber eine andere Adresse
                    # fortzusetzen ist genau das Muster, an dem YouTube eine
                    # Automatik erkennt.
                    try:
                        with self._ausgang(ausgang):
                            bearbeiter(db, job)
                    except abbruch.Abgebrochen:
                        # Der Bearbeiter hat den Auftrag bereits zurueck in die
                        # Warteschlange gelegt. Hier wird nur noch der Strang
                        # beendet - weiterzumachen waere sinnlos, das naechste
                        # Video traefe sofort wieder auf dasselbe Signal.
                        log.info(
                            "[%s] %s %s beim Herunterfahren unterbrochen",
                            gruppe.name, job.type, job.target_id or "",
                        )
                        return
                    except Exception:
                        # Der Bearbeiter hat den Auftrag bereits als gescheitert
                        # vermerkt; hier geht es nur noch darum, den Strang am
                        # Leben zu halten. Ein einzelnes kaputtes Video darf die
                        # Warteschlange nicht anhalten.
                        log.exception("[%s] %s %s abgebrochen", gruppe.name, job.type, job.target_id or "")
            except Exception:
                log.exception("[%s] Arbeiterschleife gestoert", gruppe.name)
                self._stop.wait(LEERLAUF_S)


werk = Arbeiterwerk()
