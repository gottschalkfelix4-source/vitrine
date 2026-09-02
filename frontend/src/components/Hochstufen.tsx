import { useCallback, useEffect, useState } from "react";

import { api } from "../lib/api";
import type { UpgradeVorschau } from "../lib/api";
import { bytes } from "../lib/format";

/**
 * Nachträglich auf eine höhere Qualität gehen.
 *
 * Der Name „Upgrade" führt leicht in die Irre, deshalb steht es auch in der
 * Oberfläche: Qualität lässt sich einer vorhandenen Datei nicht hinzufügen.
 * Was in 1080p gespeichert ist, enthält die fehlenden Pixel nicht irgendwo
 * versteckt. Jedes betroffene Video wird vollständig neu geladen — mit
 * derselben Bandbreite, derselben Zeit und demselben Anteil an YouTubes
 * Drosselung wie beim ersten Mal.
 *
 * Deshalb steht hier eine Vorschau vor dem Knopf und nicht danach: wie viele
 * Videos betroffen sind, was sie heute belegen, was danach zu erwarten ist und
 * ob das überhaupt auf die Platte passt.
 */

const STUFEN: { wert: number; text: string }[] = [
  { wert: 1080, text: "1080p" },
  { wert: 1440, text: "1440p" },
  { wert: 2160, text: "4K" },
  { wert: 4320, text: "8K" },
];

export function Hochstufen({ kanal }: { kanal?: string }) {
  const [ziel, setZiel] = useState(2160);
  const [vorschau, setVorschau] = useState<UpgradeVorschau | null>(null);
  const [laedt, setLaedt] = useState(true);
  const [fehler, setFehler] = useState<string | null>(null);
  const [nachfrage, setNachfrage] = useState(false);
  const [meldung, setMeldung] = useState<string | null>(null);

  const holen = useCallback(() => {
    setLaedt(true);
    setNachfrage(false);
    api
      .upgradeVorschau(ziel, kanal)
      .then((v) => {
        setVorschau(v);
        setFehler(null);
      })
      .catch((e) => setFehler(e instanceof Error ? e.message : String(e)))
      .finally(() => setLaedt(false));
  }, [ziel, kanal]);

  useEffect(holen, [holen]);

  async function einreihen() {
    try {
      const r = await api.upgradeEinreihen(ziel, kanal);
      setMeldung(
        r.eingereiht === 0
          ? "Nichts einzureihen – alles liegt schon auf dieser Stufe oder darüber."
          : `${r.eingereiht} ${r.eingereiht === 1 ? "Video" : "Videos"} eingereiht. Der Fortschritt steht oben und in der Warteschlange.`,
      );
      setNachfrage(false);
      holen();
    } catch (e) {
      setFehler(e instanceof Error ? e.message : String(e));
    }
  }

  const stufen = Object.entries(vorschau?.nach_stufe ?? {});

  return (
    <section className="sp-block">
      <h2>Qualität nachträglich anheben</h2>

      <div className="hs-kopf">
        <label>
          Zielqualität{" "}
          <select
            className="knopf"
            value={ziel}
            onChange={(e) => setZiel(Number(e.target.value))}
          >
            {STUFEN.map((s) => (
              <option key={s.wert} value={s.wert}>
                {s.text}
              </option>
            ))}
          </select>
        </label>
        {laedt ? <span className="beiwerk">wird berechnet …</span> : null}
      </div>

      {fehler ? (
        <div className="hinweis" data-art="fehler">
          <div>{fehler}</div>
        </div>
      ) : null}

      {meldung ? (
        <div className="hinweis">
          <div>{meldung}</div>
        </div>
      ) : null}

      {vorschau && !laedt ? (
        vorschau.videos === 0 ? (
          <p className="beiwerk">
            Alles Archivierte liegt bereits auf dieser Stufe oder darüber. Nichts zu tun.
          </p>
        ) : (
          <>
            <div className="hs-zahlen">
              <div>
                <div className="beschriftung">Betroffen</div>
                <div className="wert">{vorschau.videos.toLocaleString("de-DE")}</div>
                <div className="zusatz">
                  {stufen.map(([stufe, anzahl], i) => (
                    <span key={stufe}>
                      {i > 0 ? " · " : ""}
                      {anzahl}× {stufe}p
                    </span>
                  ))}
                </div>
              </div>
              <div>
                <div className="beschriftung">Belegen heute</div>
                <div className="wert">{bytes(vorschau.jetzt_bytes)}</div>
              </div>
              <div>
                <div className="beschriftung">Zusätzlich nötig</div>
                <div
                  className="wert"
                  style={{ color: vorschau.passt === false ? "var(--zu-fehler)" : undefined }}
                >
                  {bytes(vorschau.zusatz_bytes)}
                </div>
                <div className="zusatz">{bytes(vorschau.freier_platz)} frei</div>
              </div>
            </div>

            <p className="beiwerk" style={{ lineHeight: 1.6 }}>
              Das ist <strong>kein Veredeln vorhandener Dateien</strong>, sondern ein
              vollständiger Neu-Download je Video. Die Schätzung rechnet mit dem Verhältnis
              der Pixelzahlen und fällt damit eher zu niedrig aus – YouTube gibt 4K in der
              Praxis rund das Vier- bis Fünffache von 1080p mit.
              {vorschau.stunden_mindestens >= 1 ? (
                <>
                  {" "}
                  Wegen YouTubes Drosselung von etwa 300 Videos je Stunde dauert das{" "}
                  <strong>mindestens {vorschau.stunden_mindestens} Stunden</strong>, unabhängig
                  von der Leitung.
                </>
              ) : null}{" "}
              Jedes Video bleibt währenddessen abspielbar; ausgetauscht wird erst, wenn die
              bessere Fassung vollständig da ist.
            </p>

            {vorschau.passt === false ? (
              <div className="hinweis" data-art="fehler">
                <div>
                  <strong>Das passt nicht auf den freien Platz.</strong>
                  <div style={{ color: "var(--text-gedaempft)", marginTop: 4 }}>
                    Nötig wären {bytes(vorschau.zusatz_bytes)} zusätzlich, frei sind{" "}
                    {bytes(vorschau.freier_platz)}. Eine niedrigere Zielstufe oder ein
                    einzelner Kanal wären der nächste Schritt.
                  </div>
                </div>
              </div>
            ) : null}

            {nachfrage ? (
              <div className="hs-nachfrage">
                <span>
                  {vorschau.videos.toLocaleString("de-DE")} Videos neu laden, geschätzt{" "}
                  {bytes(vorschau.zusatz_bytes)} zusätzlich?
                </span>
                <button className="knopf" data-art="stark" onClick={() => void einreihen()}>
                  Ja, einreihen
                </button>
                <button className="knopf" onClick={() => setNachfrage(false)}>
                  Abbrechen
                </button>
              </div>
            ) : (
              <button
                className="knopf"
                data-art={vorschau.passt === false ? "gefahr" : "stark"}
                onClick={() => setNachfrage(true)}
              >
                {vorschau.videos.toLocaleString("de-DE")}{" "}
                {vorschau.videos === 1 ? "Video" : "Videos"} hochstufen
              </button>
            )}
          </>
        )
      ) : null}
    </section>
  );
}
