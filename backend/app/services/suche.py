"""Volltextsuche ueber SQLite FTS5.

Zwei Entscheidungen praegen dieses Modul, beide wegen der deutschen Sprache.

**Trigram statt Wortzerlegung.** Der uebliche ``unicode61``-Tokenizer zerlegt
Text in Woerter und findet dann nur ganze Woerter oder Praefixe. Im Deutschen
faellt das auf die Nase: Wer "Konfiguration" sucht, findet
"Netzwerkkonfiguration" nicht, und "Groesse" nicht "Dateigroessen". Der
Trigram-Tokenizer indiziert Zeichenfolgen und findet Treffer mitten im Wort -
genau das, was Komposita brauchen. Der Preis ist ein groesserer Index und eine
Mindestlaenge von drei Zeichen.

**Symmetrische Umschrift.** Umlaute werden auf beiden Seiten gleich behandelt:
beim Indizieren und beim Suchen wird ae/oe/ue/ss geschrieben. Dadurch findet
"grosse", "groesse" und "groesse" alle dasselbe - egal, wie jemand tippt oder
wie es im Titel steht. Reines ``remove_diacritics`` reicht dafuer nicht, weil
es aus "ae" kein "ä" macht und das scharfe S gar nicht kennt.

Untertitel werden je Sprechzeile indiziert, nicht je Video. Dadurch liefert ein
Treffer nicht nur "kommt in diesem Video vor", sondern "kommt bei 4:32 vor".
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

#: Kuerzeste sinnvolle Anfrage. Trigram kann prinzipbedingt nichts darunter.
MIN_LAENGE = 3

_UMSCHRIFT = str.maketrans(
    {
        "ä": "ae", "ö": "oe", "ü": "ue",
        "Ä": "ae", "Ö": "oe", "Ü": "ue",
        "ß": "ss",
        "à": "a", "á": "a", "â": "a", "ã": "a", "å": "a",
        "è": "e", "é": "e", "ê": "e", "ë": "e",
        "ì": "i", "í": "i", "î": "i", "ï": "i",
        "ò": "o", "ó": "o", "ô": "o", "õ": "o", "ø": "o",
        "ù": "u", "ú": "u", "û": "u",
        "ç": "c", "ñ": "n", "ý": "y",
    }
)


def normalisieren(s: str | None) -> str:
    """Bringt Text in die Form, in der verglichen wird."""
    return (s or "").lower().translate(_UMSCHRIFT)


SCHEMA = [
    """
    CREATE VIRTUAL TABLE IF NOT EXISTS video_suche USING fts5(
        video_id UNINDEXED,
        titel,
        beschreibung,
        kanal,
        tokenize = 'trigram'
    )
    """,
    """
    CREATE VIRTUAL TABLE IF NOT EXISTS untertitel_suche USING fts5(
        video_id UNINDEXED,
        start_s UNINDEXED,
        sprache UNINDEXED,
        zeile,
        tokenize = 'trigram'
    )
    """,
]


def schema_anlegen(db: Session) -> None:
    for anweisung in SCHEMA:
        db.execute(text(anweisung))
    db.commit()


def verfuegbar(db: Session) -> bool:
    """Prueft, ob FTS5 in dieser SQLite-Fassung vorhanden ist.

    Die allermeisten Python-Installationen bringen es mit; sollte es fehlen,
    faellt die Suche auf einfaches Vergleichen zurueck, statt abzustuerzen.
    """
    try:
        db.execute(text("SELECT 1 FROM video_suche LIMIT 1"))
        return True
    except Exception:
        # Zuruecksetzen, sonst bleibt die Sitzung nach dem fehlgeschlagenen
        # Zugriff in einem Zustand, in dem jede weitere Abfrage scheitert.
        db.rollback()
        return False


# ------------------------------------------------------------------ Befuellen


def video_indizieren(
    db: Session,
    *,
    video_id: str,
    titel: str | None,
    beschreibung: str | None,
    kanal: str | None,
) -> None:
    db.execute(text("DELETE FROM video_suche WHERE video_id = :v"), {"v": video_id})
    db.execute(
        text(
            "INSERT INTO video_suche (video_id, titel, beschreibung, kanal) "
            "VALUES (:v, :t, :b, :k)"
        ),
        {
            "v": video_id,
            "t": normalisieren(titel),
            "b": normalisieren(beschreibung),
            "k": normalisieren(kanal),
        },
    )


_ZEITMARKE = re.compile(
    r"(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{1,3})\s*-->\s*(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{1,3})"
)
_TAGS = re.compile(r"<[^>]+>")


@dataclass(slots=True)
class Sprechzeile:
    start_s: float
    text: str


def vtt_zerlegen(inhalt: str) -> list[Sprechzeile]:
    """Zerlegt eine WebVTT-Datei in Sprechzeilen mit Startzeit.

    Bewusst nachsichtig geschrieben: Automatisch erzeugte Untertitel von YouTube
    enthalten Positionsangaben, Karaoke-Auszeichnungen und ueberlappende
    Bloecke, die ein strenger Parser ablehnen wuerde. Hier zaehlt nur, dass
    Text und ungefaehre Zeit zusammenfinden.
    """
    zeilen: list[Sprechzeile] = []
    aktuelle_zeit: float | None = None
    puffer: list[str] = []

    def abschliessen() -> None:
        if aktuelle_zeit is None:
            return
        inhalt = " ".join(puffer).strip()
        if inhalt:
            zeilen.append(Sprechzeile(aktuelle_zeit, inhalt))

    for rohzeile in inhalt.splitlines():
        zeile = rohzeile.strip()
        treffer = _ZEITMARKE.search(zeile)
        if treffer:
            abschliessen()
            puffer = []
            std, minute, sek, ms = treffer.group(1), treffer.group(2), treffer.group(3), treffer.group(4)
            aktuelle_zeit = (
                int(std or 0) * 3600 + int(minute) * 60 + int(sek) + int(ms.ljust(3, "0")) / 1000
            )
            continue
        if not zeile or zeile in ("WEBVTT",) or zeile.startswith(("NOTE", "STYLE", "REGION")):
            continue
        if aktuelle_zeit is not None:
            puffer.append(_TAGS.sub("", zeile))

    abschliessen()

    # Automatische Untertitel wiederholen jede Zeile im naechsten Block, damit
    # der Text mitlaeuft. Ungefiltert stuende jeder Satz doppelt im Index.
    entdoppelt: list[Sprechzeile] = []
    for z in zeilen:
        if entdoppelt and entdoppelt[-1].text == z.text:
            continue
        entdoppelt.append(z)
    return entdoppelt


def untertitel_indizieren(db: Session, video_id: str, sprache: str, vtt_inhalt: str) -> int:
    """Nimmt eine Untertitelspur in den Index auf. Liefert die Zahl der Zeilen."""
    db.execute(
        text("DELETE FROM untertitel_suche WHERE video_id = :v AND sprache = :s"),
        {"v": video_id, "s": sprache},
    )
    zeilen = vtt_zerlegen(vtt_inhalt)
    if not zeilen:
        return 0
    db.execute(
        text(
            "INSERT INTO untertitel_suche (video_id, start_s, sprache, zeile) "
            "VALUES (:v, :t, :s, :z)"
        ),
        [
            {"v": video_id, "t": z.start_s, "s": sprache, "z": normalisieren(z.text)}
            for z in zeilen
        ],
    )
    return len(zeilen)


def entfernen(db: Session, video_id: str) -> None:
    db.execute(text("DELETE FROM video_suche WHERE video_id = :v"), {"v": video_id})
    db.execute(text("DELETE FROM untertitel_suche WHERE video_id = :v"), {"v": video_id})


def alle_entfernen(db: Session, video_ids: list[str]) -> None:
    """Entfernt viele Videos auf einmal aus dem Index.

    In Bloecken, weil SQLite die Zahl der Platzhalter je Anweisung begrenzt -
    ein Kanal mit tausenden Videos wuerde die Grenze sonst reissen.
    """
    for i in range(0, len(video_ids), 500):
        block = video_ids[i : i + 500]
        platzhalter = ",".join(f":v{n}" for n in range(len(block)))
        werte = {f"v{n}": vid for n, vid in enumerate(block)}
        db.execute(text(f"DELETE FROM video_suche WHERE video_id IN ({platzhalter})"), werte)
        db.execute(
            text(f"DELETE FROM untertitel_suche WHERE video_id IN ({platzhalter})"), werte
        )


# --------------------------------------------------------------------- Suchen


@dataclass(slots=True)
class Untertitelfund:
    video_id: str
    start_s: float
    sprache: str
    zeile: str


def _als_phrase(anfrage: str) -> str:
    """Baut den FTS5-Ausdruck.

    Anfuehrungszeichen sind hier Pflicht, nicht Kosmetik: Ohne sie deutet FTS5
    Zeichen wie ``-``, ``*``, ``:`` und ``(`` als Suchsyntax, und eine ganz
    normale Eingabe wie "C++ vs. Rust" fuehrt zu einem Syntaxfehler statt zu
    einem Ergebnis.
    """
    return '"' + normalisieren(anfrage).replace('"', '""') + '"'


def video_treffer(db: Session, anfrage: str, limit: int = 60, offset: int = 0,
                  *, archived_only: bool = False) -> list[str]:
    """Video-IDs, nach Relevanz sortiert.

    Die Gewichtung stellt den Titel voran: Ein Wort im Titel sagt mehr ueber
    das Video aus als eines irgendwo in einer langen Beschreibung.
    """
    if len(normalisieren(anfrage).strip()) < MIN_LAENGE:
        return []
    zeilen = db.execute(
        text(
            "SELECT video_id FROM video_suche "
            "WHERE video_suche MATCH :q "
            "AND (:archived = 0 OR video_id IN (SELECT id FROM videos WHERE status = 'archived')) "
            "ORDER BY bm25(video_suche, 0.0, 10.0, 1.0, 3.0) "
            "LIMIT :l OFFSET :o"
        ),
        {"q": _als_phrase(anfrage), "l": limit, "o": offset, "archived": int(archived_only)},
    ).fetchall()
    return [z[0] for z in zeilen]


def untertitel_treffer(db: Session, anfrage: str, limit: int = 40,
                      *, archived_only: bool = False) -> list[Untertitelfund]:
    """Fundstellen in gesprochenem Text, mit Zeitangabe.

    Je Video hoechstens eine Fundstelle: Wer ein Wort sucht, das in einem Vortrag
    dreissigmal faellt, will nicht dreissig Zeilen desselben Videos sehen.
    """
    if len(normalisieren(anfrage).strip()) < MIN_LAENGE:
        return []
    zeilen = db.execute(
        text(
            "SELECT video_id, start_s, sprache, zeile, MIN(rang) FROM ("
            "  SELECT video_id, start_s, sprache, zeile, bm25(untertitel_suche) AS rang"
            "  FROM untertitel_suche WHERE untertitel_suche MATCH :q"
            "  AND (:archived = 0 OR video_id IN (SELECT id FROM videos WHERE status = 'archived'))"
            "  ORDER BY rang LIMIT 500"
            ") GROUP BY video_id ORDER BY MIN(rang) LIMIT :l"
        ),
        {"q": _als_phrase(anfrage), "l": limit, "archived": int(archived_only)},
    ).fetchall()
    return [Untertitelfund(z[0], float(z[1]), z[2], z[3]) for z in zeilen]


def statistik(db: Session) -> dict[str, int]:
    def zaehle(tabelle: str) -> int:
        try:
            return int(db.execute(text(f"SELECT count(*) FROM {tabelle}")).scalar() or 0)
        except Exception:
            return 0

    return {
        "videos_im_index": zaehle("video_suche"),
        "untertitelzeilen": zaehle("untertitel_suche"),
    }
