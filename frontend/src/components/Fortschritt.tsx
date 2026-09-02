import { Link } from "react-router-dom";

import { useApi } from "../hooks/useApi";
import { api } from "../lib/api";
import { AUFTRAG_TEXT, prozent } from "../lib/format";

/**
 * Zeigt an, was gerade laeuft - immer sichtbar, auf jeder Seite.
 *
 * Ohne das passiert nach einem Klick auf "Jetzt abgleichen" sichtbar nichts:
 * Der Auftrag laeuft zwar mit Fortschritt, aber nur auf der Warteschlangenseite
 * ist er zu sehen. Wer gerade auf der Kanalseite steht, haelt das Programm fuer
 * eingefroren.
 *
 * Der abgefragte Endpunkt ist bewusst schmal, damit sich der Sekundentakt
 * lohnt; laeuft nichts, verschwindet die Leiste ganz.
 */

const TAKT_MS = 2000;

export function Fortschrittsleiste() {
  const { daten } = useApi(() => api.aktiveAuftraege(), [], TAKT_MS);

  const laufend = daten?.laufend ?? [];
  const wartend = daten?.wartend ?? 0;
  if (laufend.length === 0 && wartend === 0) return null;

  const erster = laufend[0];
  // Ohne Fortschrittswert (etwa beim Auflisten eines Kanals) läuft der Balken
  // unbestimmt weiter, statt bei 0 % zu stehen und Stillstand vorzutäuschen.
  const unbestimmt = !erster || erster.fortschritt <= 0;

  return (
    <Link className="fortschrittsleiste" to="/warteschlange" title="Zur Warteschlange">
      <div className="fl-balken" data-unbestimmt={unbestimmt}>
        <span style={unbestimmt ? undefined : { width: `${erster.fortschritt * 100}%` }} />
      </div>
      <div className="fl-text">
        {erster ? (
          <>
            <strong>{AUFTRAG_TEXT[erster.art] ?? erster.art}</strong>
            <span className="fl-titel">{erster.titel ?? erster.ziel}</span>
            {erster.meldung ? <span className="fl-meldung">{erster.meldung}</span> : null}
            {!unbestimmt ? <span className="fl-prozent">{prozent(erster.fortschritt)}</span> : null}
          </>
        ) : (
          <strong>Warteschlange</strong>
        )}
        {laufend.length > 1 ? <span className="fl-rest">+{laufend.length - 1} laufend</span> : null}
        {wartend > 0 ? <span className="fl-rest">{wartend} wartend</span> : null}
      </div>
    </Link>
  );
}
