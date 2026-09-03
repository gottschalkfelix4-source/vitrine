import { Link } from "react-router-dom";

import { useApi } from "../hooks/useApi";
import { api } from "../lib/api";
import type { LaufenderAuftrag } from "../lib/api";
import { AUFTRAG_TEXT, prozent, wartedauer } from "../lib/format";

/**
 * Zeigt an, was gerade läuft - immer sichtbar, auf jeder Seite.
 *
 * Ohne das passiert nach einem Klick auf "Jetzt abgleichen" sichtbar nichts:
 * Der Auftrag läuft zwar mit Fortschritt, aber nur auf der Warteschlangenseite
 * ist er zu sehen. Wer gerade auf der Kanalseite steht, hält das Programm für
 * eingefroren.
 *
 * Jeder laufende Auftrag bekommt eine eigene Zeile. Vorher stand hier nur der
 * erste, gefolgt von "+1 laufend" - bei zwei parallelen Downloads sah man also
 * genau einen davon und musste raten, was der andere tut. Die Zahl der
 * parallelen Downloads lässt sich einstellen; dann muss man auch sehen, was
 * sie bewirkt.
 *
 * Der abgefragte Endpunkt ist bewusst schmal, damit sich der Sekundentakt
 * lohnt; läuft nichts, verschwindet die Leiste ganz.
 */

const TAKT_MS = 2000;

function Zeile({ auftrag }: { auftrag: LaufenderAuftrag }) {
  // Ohne Fortschrittswert (etwa beim Auflisten eines Kanals) läuft der Balken
  // unbestimmt weiter, statt bei 0 % zu stehen und Stillstand vorzutäuschen.
  const unbestimmt = auftrag.fortschritt <= 0;
  return (
    <div className="fl-zeile">
      <div className="fl-balken" data-unbestimmt={unbestimmt}>
        <span style={unbestimmt ? undefined : { width: `${auftrag.fortschritt * 100}%` }} />
      </div>
      <div className="fl-text">
        <strong>{AUFTRAG_TEXT[auftrag.art] ?? auftrag.art}</strong>
        <span className="fl-titel">{auftrag.titel ?? auftrag.ziel}</span>
        {auftrag.meldung ? <span className="fl-meldung">{auftrag.meldung}</span> : null}
        {!unbestimmt ? <span className="fl-prozent">{prozent(auftrag.fortschritt)}</span> : null}
      </div>
    </div>
  );
}

export function Fortschrittsleiste() {
  const { daten } = useApi(() => api.aktiveAuftraege(), [], TAKT_MS);

  const laufend = daten?.laufend ?? [];
  const wartend = daten?.wartend ?? 0;
  const drosselung = daten?.drosselung;
  if (laufend.length === 0 && wartend === 0) return null;

  return (
    <Link className="fortschrittsleiste" to="/warteschlange" title="Zur Warteschlange">
      {laufend.map((a) => (
        <Zeile key={a.id} auftrag={a} />
      ))}
      {/*
        Ohne diese Zeile ist eine Zwangspause von einem hängenden Dienst nicht
        zu unterscheiden: In beiden Fällen stehen tausend Aufträge auf "wartet"
        und es läuft keiner. Das ist genau der Moment, in dem man anfängt, den
        Container neu zu starten - und die Sperre damit verlängert.
      */}
      {drosselung?.pausiert ? (
        <div className="fl-zeile">
          <div className="fl-text">
            <strong>Pause</strong>
            <span className="fl-titel">YouTube weist gerade ab</span>
            <span className="fl-meldung">weiter in {wartedauer(drosselung.rest_s)}</span>
          </div>
        </div>
      ) : laufend.length === 0 ? (
        <div className="fl-zeile">
          <div className="fl-text">
            <strong>Warteschlange</strong>
          </div>
        </div>
      ) : null}
      {wartend > 0 ? (
        <div className="fl-fuss">
          {wartend} {wartend === 1 ? "wartet" : "warten"}
          {laufend.length > 1 ? ` · ${laufend.length} laufen gleichzeitig` : ""}
        </div>
      ) : null}
    </Link>
  );
}
