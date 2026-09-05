import { useState } from "react";

import { api, type WarteschlangenPause } from "../lib/api";
import { wartedauer } from "../lib/format";
import { Icon } from "./Icons";

export function DownloadPause({ pause, aufAenderung }: {
  pause: WarteschlangenPause | undefined;
  aufAenderung: (pause: WarteschlangenPause) => void;
}) {
  const [dauer, setDauer] = useState("60");
  const [arbeitet, setArbeitet] = useState(false);
  const [fehler, setFehler] = useState<string | null>(null);

  async function umschalten() {
    if (!pause || arbeitet) return;
    setArbeitet(true);
    setFehler(null);
    try {
      const zustand = pause.aktiv
        ? await api.warteschlangeFortsetzen()
        : await api.warteschlangePausieren(dauer === "unbegrenzt" ? null : Number(dauer));
      aufAenderung(zustand);
    } catch (e) {
      setFehler(e instanceof Error ? e.message : String(e));
    } finally {
      setArbeitet(false);
    }
  }

  return (
    <section className="download-pause" aria-labelledby="download-pause-titel" data-pausiert={pause?.aktiv}>
      <div className="download-pause-text">
        <h2 id="download-pause-titel">{pause?.aktiv ? "Downloads pausiert" : "Downloads pausieren"}</h2>
        {pause?.aktiv ? (
          <>
            <p>
              {pause.bis && pause.rest_s !== null
                ? `Die manuelle Pause endet in ${wartedauer(pause.rest_s)} (${new Date(pause.bis).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" })} Uhr).`
                : "Pausiert, bis du die Downloads wieder fortsetzt."}
              {pause.laufend > 0
                ? ` ${pause.laufend} ${pause.laufend === 1 ? "laufender Download oder Kanalabgleich wird" : "laufende Downloads oder Kanalabgleiche werden"} noch abgeschlossen.`
                : " Es starten keine neuen Downloads oder Kanalabgleiche."}
            </p>
            <p>Abspielen und Umwandeln bleiben verfügbar. Eine bestehende IP-Sperre wird durch Fortsetzen nicht aufgehoben.</p>
          </>
        ) : (
          <p>Lege eine Pause für neue Downloads und Kanalabgleiche ein. Laufende Aufträge werden noch abgeschlossen. Abspielen und Umwandeln laufen weiter.</p>
        )}
      </div>
      <div className="download-pause-aktionen">
        {!pause?.aktiv ? <label>
          <span>Pausendauer</span>
          <select value={dauer} onChange={(e) => setDauer(e.target.value)} disabled={arbeitet || !pause}>
            <option value="15">15 Minuten</option>
            <option value="30">30 Minuten</option>
            <option value="60">1 Stunde</option>
            <option value="120">2 Stunden</option>
            <option value="unbegrenzt">Bis zum Fortsetzen</option>
          </select>
        </label> : null}
        <button className="knopf" data-art="stark" disabled={arbeitet || !pause} onClick={() => void umschalten()}>
          <Icon name={pause?.aktiv ? "play" : "pause"} size={20} />
          {arbeitet ? "Wird geändert …" : pause?.aktiv ? "Downloads fortsetzen" : "Pausieren"}
        </button>
      </div>
      {fehler ? <p className="download-pause-fehler" role="alert">Die Pause konnte nicht geändert werden: {fehler}</p> : null}
    </section>
  );
}
