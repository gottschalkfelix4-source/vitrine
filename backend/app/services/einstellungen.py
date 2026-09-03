"""Einstellungen zur Laufzeit aendern.

Die Werte kommen aus drei Quellen, in dieser Rangfolge:

1. **Datenbank** - was in der Oberflaeche eingestellt wurde. Gewinnt.
2. **Umgebung** - was im Unraid-Template oder in der compose-Datei steht.
3. **Standard** - was im Code hinterlegt ist.

Dass die Datenbank gewinnt, ist eine bewusste Entscheidung und der Grund,
warum jedes Feld seine Herkunft mitliefert: Wer im Unraid-Template eine
Variable setzt, die er vorher im UI geaendert hat, wuerde sich sonst wundern,
warum das Feld nichts bewirkt. Die Oberflaeche zeigt das an und bietet
"zuruecksetzen" - danach gilt wieder Umgebung bzw. Standard.

Aenderungen werden direkt auf das Einstellungsobjekt geschrieben und wirken
damit sofort fuer alles, was den Wert zur Laufzeit liest - Qualitaet, Codec,
Fristen. Was nur beim Start gelesen wird, ist als ``neustart`` markiert; die
Oberflaeche sagt es dann dazu, statt es stillschweigend wirkungslos zu lassen.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import ArchiveCodec, HardwareAccel, settings
from app.models import Setting

log = logging.getLogger(__name__)

Art = Literal["int", "float", "bool", "text", "auswahl", "liste"]


@dataclass(slots=True)
class Feld:
    """Eine in der Oberflaeche aenderbare Einstellung."""

    name: str  # Feldname in Settings
    gruppe: str
    titel: str
    beschreibung: str
    art: Art
    #: Nur beim naechsten Start wirksam - die Oberflaeche weist darauf hin.
    neustart: bool = False
    min: float | None = None
    max: float | None = None
    auswahl: list[str] = field(default_factory=list)
    #: Anzeige-Einheit. Der Wert wird durch ``faktor`` geteilt gezeigt und beim
    #: Speichern wieder multipliziert - Bytes will niemand von Hand eintippen.
    einheit: str | None = None
    faktor: int = 1
    #: Zusatzpruefung ueber die Bereichsgrenzen hinaus.
    pruefen: Callable[[Any], str | None] | None = None


def _codec_pruefen(wert: Any) -> str | None:
    if wert in ("qsv", "vaapi"):
        return None  # Geraetefreigabe kann hier nicht geprueft werden
    return None


FELDER: list[Feld] = [
    # ------------------------------------------------------------- Qualitaet
    Feld("archive_min_height", "Qualität", "Mindesthöhe", "Untergrenze, KEIN Deckel: Bietet die Quelle mehr, wird mehr geladen. Bietet sie weniger, wird das Beste genommen, was sie hat.", "int", min=144, max=4320, einheit="px"),
    Feld("archive_max_height", "Qualität", "Höchsthöhe", "Obergrenze für den Download, 0 = offen. 4K belegt grob das Fünffache von 1080p.", "int", min=0, max=4320, einheit="px"),
    Feld("ytdlp_format", "Qualität", "Eigener Format-Selektor", "Für Fortgeschrittene: überschreibt Mindest- und Höchsthöhe vollständig. Leer lassen, wenn unklar.", "text"),

    # ---------------------------------------------------------- Kaltspeicher
    Feld("archive_codec", "Kaltspeicher", "Codec", "av1 macht die kleinsten Dateien, braucht aber Rechenzeit. copy lässt alles unverändert. H.264-Quellen werden umkodiert, VP9 und AV1 nie – dort brächte es nur Generationsverlust.", "auswahl", auswahl=[c.value for c in ArchiveCodec]),
    Feld("av1_crf", "Kaltspeicher", "AV1-Qualität (CRF)", "Der stärkere Hebel als das Preset. Gegen eine H.264-Quelle gemessen: 26 ≈ 23 % kleiner, 30 ≈ 40 %, 34 ≈ 55 %. Niedriger heißt besser und größer.", "int", min=0, max=63),
    Feld("av1_preset", "Kaltspeicher", "AV1-Tempo (Preset)", "0 langsam und dicht bis 13 schnell. Auf 6 Kernen je Stunde 1080p: Preset 6 ≈ 51 Minuten, 8 ≈ 25, 10 ≈ 16. Für große Kanäle sind 8–10 vernünftiger.", "int", min=0, max=13),
    Feld("hevc_crf", "Kaltspeicher", "HEVC-Qualität (CRF)", "Nur wirksam, wenn der Codec auf hevc steht.", "int", min=0, max=51),
    Feld("hwaccel", "Kaltspeicher", "Hardware-Encoder", "Achtung: Intel 11.–13. Generation kann AV1 nur DEkodieren. Für qsv/vaapi muss /dev/dri in den Container gereicht sein.", "auswahl", auswahl=[h.value for h in HardwareAccel], pruefen=_codec_pruefen),
    Feld("recode_min_height", "Kaltspeicher", "Recodieren ab Höhe", "Kleinere Quellen nicht umkodieren – lohnt sich nicht. Gilt zugleich als absoluter Boden für die Qualitätsprüfung.", "int", min=0, max=2160, einheit="px"),
    Feld("audio_bitrate_kbps", "Kaltspeicher", "Ton-Bitrate", "Opus, beim Umkodieren.", "int", min=32, max=512, einheit="kbit/s"),
    Feld("keep_original_if_larger", "Kaltspeicher", "Größere Encodes verwerfen", "Wird die neu kodierte Datei größer als das Original, das Original behalten.", "bool"),

    # ---------------------------------------------------------- Heissspeicher
    Feld("hot_max_bytes", "Heißspeicher", "Limit", "Obergrenze für entpackte Kopien. 0 = unbegrenzt. Darüber wird nach ältestem Zugriff aufgeräumt; laufende Wiedergaben bleiben verschont.", "int", min=0, max=4096, einheit="GB", faktor=1024**3),
    Feld("hot_ttl_hours", "Heißspeicher", "Frist ab letztem Zugriff", "", "float", min=0.1, max=720, einheit="Std."),
    Feld("hot_ttl_after_playback_minutes", "Heißspeicher", "Frist nach Wiedergabeende", "Kürzer, weil das Video gerade zu Ende geschaut wurde – aber nicht null, damit ein erneutes Hineinspringen nicht wieder entpacken muss.", "float", min=1, max=1440, einheit="Min."),
    Feld("hot_grace_seconds", "Heißspeicher", "Schonfrist", "So lange nach dem letzten Lesezugriff wird nichts gelöscht. Schützt Streams, die keine Herzschläge senden.", "int", min=10, max=3600, einheit="Sek."),

    # --------------------------------------------------------------- Download
    Feld("write_subtitles", "Download", "Untertitel laden", "Werden je Sprechzeile durchsuchbar.", "bool"),
    Feld("write_auto_subtitles", "Download", "Automatische Untertitel laden", "Von YouTube erzeugt, oft fehlerhaft – für die Suche trotzdem nützlich.", "bool"),
    Feld("subtitle_languages", "Download", "Sprachen", "Kommaliste, z. B. de,en", "liste"),
    Feld("sponsorblock", "Download", "SponsorBlock-Kapitel", "Markiert Werbeabschnitte als Kapitel. Es wird nichts herausgeschnitten – ein Archiv soll das Original bewahren.", "bool"),
    Feld("write_comments", "Download", "Kommentare mitsichern", "Kann bei großen Videos sehr lange dauern.", "bool"),
    Feld("ytdlp_ratelimit", "Download", "Bandbreitenlimit", "Zum Beispiel 5M. Leer = unbegrenzt.", "text"),
    Feld("ytdlp_sleep_interval", "Download", "Pause zwischen Videos", "Entschärft die Drosselung.", "float", min=0, max=120, einheit="Sek."),
    Feld("ytdlp_sleep_requests", "Download", "Pause zwischen Anfragen", "Der wirksamste Hebel gegen „Sign in to confirm you’re not a bot“. Wirkt zwischen den einzelnen Anfragen, nicht nur zwischen Videos – und gezählt werden die Anfragen: Ein Download stellt ein Dutzend davon. 1 bis 3 sind ein guter Anfang, 0 ist aus.", "float", min=0, max=30, einheit="Sek."),
    Feld("ytdlp_player_clients", "Download", "YouTube-Clients", "Leer lassen. Notausgang für den Fall, dass YouTube einen Client dichtmacht und yt-dlp noch nicht nachgezogen ist, z. B. tv,web_safari. Falsch gesetzt liefert ein nicht mehr bedienter Client nur noch 360p.", "liste"),

    # ----------------------------------------------------------------- Worker
    Feld("download_concurrency", "Arbeiter", "Parallele Downloads", "Wirkt sofort. Hochsetzen greift beim nächsten wartenden Auftrag, Heruntersetzen, sobald die überzähligen Stränge fertig sind – ein laufender Download wird dafür nicht abgebrochen. YouTube drosselt pro IP-Adresse bei rund 300 Videos je Stunde, nicht pro Prozess: Hochdrehen macht nicht schneller fertig, sondern vorübergehend gesperrt.", "int", min=1, max=16),
    Feld("encode_concurrency", "Arbeiter", "Parallele Recodierungen", "Wirkt sofort. Ein Encode nutzt ohnehin alle Kerne – mehr als 1 lohnt nur mit Hardware-Encoder.", "int", min=1, max=16),
    Feld("default_sync_interval_hours", "Arbeiter", "Kanalabgleich alle", "Standardrhythmus. Der Schnellcheck läuft über den RSS-Feed und kostet keinen yt-dlp-Request.", "float", min=0.5, max=720, einheit="Std."),
    Feld("reaper_interval_seconds", "Arbeiter", "Aufräumlauf alle", "", "int", min=30, max=86400, einheit="Sek."),
]

NACH_NAME = {f.name: f for f in FELDER}


# ------------------------------------------------------------------- Umwandlung


def _aus_text(feld: Feld, roh: str) -> Any:
    """Wandelt den gespeicherten Text in den Zielwert."""
    if feld.art == "int":
        return int(roh)
    if feld.art == "float":
        return float(roh)
    if feld.art == "bool":
        return roh.lower() in ("1", "true", "ja", "yes", "on")
    if feld.art == "liste":
        return [t.strip() for t in roh.split(",") if t.strip()]
    return roh


def _nach_text(feld: Feld, wert: Any) -> str:
    if feld.art == "liste":
        return ",".join(wert or [])
    if feld.art == "bool":
        return "true" if wert else "false"
    return "" if wert is None else str(wert)


def gespeicherte(db: Session) -> dict[str, str]:
    return {s.key: s.value for s in db.scalars(select(Setting))}


# -------------------------------------------------------------------- Anwenden


def anwenden(db: Session) -> int:
    """Legt die gespeicherten Werte ueber das Einstellungsobjekt.

    Wird beim Start aufgerufen und nach jeder Aenderung. Ein unbrauchbarer
    Eintrag - etwa nach einem Schemawechsel - wird uebersprungen und
    protokolliert, statt den Start zu verhindern.
    """
    anzahl = 0
    for schluessel, roh in gespeicherte(db).items():
        feld = NACH_NAME.get(schluessel)
        if feld is None:
            log.warning("Unbekannte gespeicherte Einstellung %r wird ignoriert", schluessel)
            continue
        try:
            setattr(settings, feld.name, _aus_text(feld, roh))
            anzahl += 1
        except Exception:
            log.warning("Gespeicherte Einstellung %r=%r ist unbrauchbar", schluessel, roh, exc_info=True)
    if anzahl:
        log.info("%d gespeicherte Einstellungen angewandt", anzahl)
    return anzahl


def _umgebungswert(feld: Feld) -> str | None:
    import os

    return os.environ.get(f"YTA_{feld.name.upper()}")


def _standardwert(feld: Feld) -> Any:
    from app.config import Settings

    info = Settings.model_fields[feld.name]
    if info.default_factory is not None:
        return info.default_factory()  # type: ignore[call-arg]
    return info.default


def lesen(db: Session) -> list[dict[str, Any]]:
    """Alle Felder samt aktuellem Wert und Herkunft."""
    gesetzt = gespeicherte(db)
    aus = []
    for feld in FELDER:
        wert = getattr(settings, feld.name)
        if feld.name in gesetzt:
            herkunft = "datenbank"
        elif _umgebungswert(feld) is not None:
            herkunft = "umgebung"
        else:
            herkunft = "standard"

        # Enums als reinen Text ausliefern, damit das UI vergleichen kann.
        if hasattr(wert, "value"):
            wert = wert.value

        anzeige = wert
        if feld.faktor != 1 and isinstance(wert, int | float):
            anzeige = round(wert / feld.faktor, 3)

        aus.append({
            "name": feld.name,
            "gruppe": feld.gruppe,
            "titel": feld.titel,
            "beschreibung": feld.beschreibung,
            "art": feld.art,
            "wert": anzeige,
            "herkunft": herkunft,
            "neustart": feld.neustart,
            "min": feld.min,
            "max": feld.max,
            "auswahl": feld.auswahl,
            "einheit": feld.einheit,
            "standard": _standardwert(feld) if feld.faktor == 1 else _standardwert(feld) / feld.faktor,
        })
    return aus


class Ungueltig(ValueError):
    pass


def _pruefen_und_wandeln(feld: Feld, wert: Any) -> Any:
    """Prueft einen eingehenden Wert und bringt ihn in die interne Form."""
    if feld.art in ("int", "float"):
        try:
            zahl = float(wert)
        except (TypeError, ValueError) as e:
            raise Ungueltig(f"{feld.titel}: Zahl erwartet, bekommen {wert!r}") from e
        if feld.min is not None and zahl < feld.min:
            raise Ungueltig(f"{feld.titel}: mindestens {feld.min}{' ' + feld.einheit if feld.einheit else ''}")
        if feld.max is not None and zahl > feld.max:
            raise Ungueltig(f"{feld.titel}: höchstens {feld.max}{' ' + feld.einheit if feld.einheit else ''}")
        zahl *= feld.faktor
        return int(zahl) if feld.art == "int" else zahl

    if feld.art == "bool":
        return bool(wert)

    if feld.art == "auswahl":
        if str(wert) not in feld.auswahl:
            raise Ungueltig(f"{feld.titel}: erlaubt sind {', '.join(feld.auswahl)}")
        return str(wert)

    if feld.art == "liste":
        if isinstance(wert, list):
            teile = [str(t).strip() for t in wert if str(t).strip()]
        else:
            teile = [t.strip() for t in str(wert).split(",") if t.strip()]
        if not teile:
            raise Ungueltig(f"{feld.titel}: mindestens ein Eintrag")
        return teile

    text = str(wert).strip()
    return text or None


def schreiben(db: Session, aenderungen: dict[str, Any]) -> dict[str, Any]:
    """Speichert Aenderungen und wendet sie sofort an.

    Erst wird alles geprueft, dann geschrieben: Eine Eingabe mit einem Fehler
    darin soll nicht die Haelfte der Aenderungen hinterlassen.
    """
    unbekannt = set(aenderungen) - set(NACH_NAME)
    if unbekannt:
        raise Ungueltig(f"Unbekannte Einstellungen: {', '.join(sorted(unbekannt))}")

    geprueft: dict[str, Any] = {}
    for name, roh in aenderungen.items():
        feld = NACH_NAME[name]
        geprueft[name] = _pruefen_und_wandeln(feld, roh)

    neustart_noetig = []
    for name, wert in geprueft.items():
        feld = NACH_NAME[name]
        eintrag = db.get(Setting, name)
        text = _nach_text(feld, wert)
        if eintrag is None:
            db.add(Setting(key=name, value=text))
        else:
            eintrag.value = text
        if feld.neustart and getattr(settings, name) != wert:
            neustart_noetig.append(feld.titel)
    db.commit()

    anwenden(db)
    log.info("Einstellungen geaendert: %s", ", ".join(sorted(geprueft)))
    return {"geaendert": sorted(geprueft), "neustart_noetig": neustart_noetig}


def zuruecksetzen(db: Session, namen: list[str] | None = None) -> list[str]:
    """Entfernt gespeicherte Werte; danach gilt wieder Umgebung bzw. Standard.

    Das Zuruecksetzen wirkt erst nach einem Neustart vollstaendig: Der
    urspruengliche Wert steht nur in der Umgebung, und das Einstellungsobjekt
    im laufenden Prozess ist bereits ueberschrieben. Deshalb wird er hier aus
    Umgebung bzw. Standard neu hergeleitet.
    """
    zu_loeschen = namen if namen is not None else list(NACH_NAME)
    entfernt = []
    for name in zu_loeschen:
        eintrag = db.get(Setting, name)
        if eintrag is None:
            continue
        db.delete(eintrag)
        entfernt.append(name)

        feld = NACH_NAME[name]
        roh = _umgebungswert(feld)
        try:
            wert = _aus_text(feld, roh) if roh is not None else _standardwert(feld)
            setattr(settings, name, wert)
        except Exception:
            log.warning("Konnte %r nicht zuruecksetzen", name, exc_info=True)
    db.commit()
    if entfernt:
        log.info("Einstellungen zurueckgesetzt: %s", ", ".join(sorted(entfernt)))
    return sorted(entfernt)


def als_json(db: Session) -> str:
    """Alle gespeicherten Werte als JSON - fuer ein Backup der Einstellungen."""
    return json.dumps(gespeicherte(db), ensure_ascii=False, indent=2)
