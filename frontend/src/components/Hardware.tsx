import { useState } from "react";

import { useApi } from "../hooks/useApi";
import { api } from "../lib/api";
import type { HardwareZustand } from "../lib/api";

/**
 * Sagt, ob der Hardware-Encoder wirklich arbeitet.
 *
 * Der Anlass war ein Verdacht, den man nicht selbst ausräumen konnte: „Ich
 * glaube, die Arc wird nicht verwendet." Dass diese Frage offen sein konnte,
 * war der eigentliche Mangel — der Weg zur Grafikkarte kann an drei Stellen
 * reißen, und keine davon meldete sich:
 *
 * - Die Karte ist gar nicht in den Container gereicht (`/dev/dri` fehlt).
 * - Im Image liegt kein Treiber. ffmpeg listet `av1_qsv` trotzdem auf, denn es
 *   ist *gebaut* mit dieser Unterstützung — laden kann es nichts.
 * - Der Encoder nimmt den Auftrag an und fällt still auf die CPU zurück.
 *
 * Deshalb wird hier nicht nach Anzeichen gesucht, sondern probehalber kodiert.
 */
export function HardwarePruefung() {
  const { daten, neuLaden } = useApi(() => api.hardware(), []);
  const [probe, setProbe] = useState<HardwareZustand | null>(null);
  const [laeuft, setLaeuft] = useState(false);
  const [fehler, setFehler] = useState<string | null>(null);

  async function testen() {
    setLaeuft(true);
    setFehler(null);
    try {
      setProbe(await api.hardwareTesten());
      neuLaden();
    } catch (e) {
      setFehler(e instanceof Error ? e.message : String(e));
    } finally {
      setLaeuft(false);
    }
  }

  const z = probe ?? daten;
  if (!z) return null;

  const bereit = z.geraete.length > 0 && z.treiber_vorhanden;
  const genutzt = z.eingestellt !== "none";
  const zustand = !bereit ? "schlecht" : genutzt ? "gut" : "leer";

  return (
    <section className="einst-gruppe">
      <h2>Grafikkarte</h2>

      <div className="cookie-zustand" data-zustand={zustand}>
        <strong>
          {!bereit
            ? "Keine nutzbare Grafikkarte"
            : genutzt
              ? `Hardware-Encoder aktiv: ${z.eingestellt}`
              : "Grafikkarte einsatzbereit, aber nicht eingeschaltet"}
        </strong>
        <p>{z.meldung}</p>
        {z.geraete.length > 0 ? (
          <p className="cookie-hinweis">Geräte: {z.geraete.join(", ")}</p>
        ) : null}
        {bereit && !genutzt ? (
          <p className="cookie-hinweis">
            Unten bei „Hardware-Encoder" auf <code>qsv</code> stellen. Vorher lohnt der
            Probelauf: Er zeigt, wie viel schneller es wirklich wird.
          </p>
        ) : null}
      </div>

      {fehler ? (
        <div className="cookie-zustand" data-zustand="schlecht">
          <strong>Prüfung fehlgeschlagen</strong>
          <p>{fehler}</p>
        </div>
      ) : null}

      {z.proben.length > 0 ? (
        <div className="hw-proben">
          <table className="tabelle">
            <thead>
              <tr>
                <th>Weg</th>
                <th>Encoder</th>
                <th>Ergebnis</th>
                <th style={{ width: 110 }}>Tempo</th>
              </tr>
            </thead>
            <tbody>
              {z.proben.map((p) => (
                <tr key={p.beschleunigung}>
                  <td>{p.beschleunigung === "none" ? "CPU" : p.beschleunigung}</td>
                  <td className="zahl">{p.encoder}</td>
                  <td style={{ color: p.erfolg ? undefined : "var(--zu-fehler)" }}>
                    {p.meldung}
                  </td>
                  <td className="zahl">
                    {p.tempo != null ? `${p.tempo}× Echtzeit` : "–"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="cookie-hinweis">
            Gemessen an 3 Sekunden 720p. „1× Echtzeit" heißt: eine Stunde Video braucht
            eine Stunde. Die Recodierung ersetzt das Bündel unwiderruflich — vergleiche
            deshalb nicht nur das Tempo, sondern auch die Dateigröße, bevor du umstellst.
          </p>
        </div>
      ) : null}

      <div className="cookie-knoepfe">
        <button className="knopf" data-art="stark" disabled={laeuft} onClick={() => void testen()}>
          {laeuft ? "Kodiert probehalber…" : "Probelauf starten"}
        </button>
      </div>
    </section>
  );
}
