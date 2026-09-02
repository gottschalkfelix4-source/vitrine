import { Link } from "react-router-dom";

import { Hochstufen } from "../components/Hochstufen";
import { Fehler, Skelettgitter } from "../components/ui";
import { useApi } from "../hooks/useApi";
import { api } from "../lib/api";
import { bytes, prozent, zustandText } from "../lib/format";

/** Balken - das Grundelement der Seite.
 *
 * Bei Fuellstaenden wechselt die Farbe gestaffelt: Ein Datentraeger zu 87 %
 * in Gruen zu zeigen waere zu freundlich, denn ab da wird es eng. Reine
 * Groessenvergleiche (nach Kanal, groesste Buendel) bekommen dagegen eine
 * feste Farbe - dort ist "voll" keine Aussage.
 */
function Balken({
  anteil,
  farbe,
  fuellstand,
}: {
  anteil: number;
  farbe?: string;
  fuellstand?: boolean;
}) {
  const gestaffelt =
    anteil > 0.9 ? "var(--zu-fehler)" : anteil > 0.75 ? "var(--zu-arbeit)" : "var(--zu-archiviert)";
  return (
    <div className="sp-balken">
      <span
        style={{
          width: `${Math.min(100, Math.max(0, anteil * 100))}%`,
          background: fuellstand ? gestaffelt : (farbe ?? "var(--zu-archiviert)"),
        }}
      />
    </div>
  );
}

function stunden(sekunden: number): string {
  const std = sekunden / 3600;
  if (std < 1) return `${Math.round(sekunden / 60)} Min.`;
  if (std < 100) return `${std.toFixed(1).replace(".", ",")} Std.`;
  return `${Math.round(std)} Std.`;
}

export function Speicherseite() {
  const { daten, laedt, fehler, neuLaden } = useApi(() => api.speicher(), [], 10_000);

  if (fehler) return <Fehler text={fehler} erneut={neuLaden} />;
  if (laedt && !daten) return <Skelettgitter anzahl={4} />;
  if (!daten) return null;

  const k = daten.kaltspeicher;
  const h = daten.heissspeicher;
  const hr = daten.hochrechnung;
  const ersparnis = k.quelle_bytes > 0 ? k.gespart_bytes / k.quelle_bytes : 0;
  const heissAnteil = h.limit_bytes > 0 ? h.bytes / h.limit_bytes : 0;
  const groessteBytes = daten.groesste[0]?.bytes ?? 1;
  const maxKanal = Math.max(1, ...daten.je_kanal.map((x) => x.bytes));

  return (
    <>
      <div className="seiten-kopf">
        <h1>Speicher</h1>
        <span className="beiwerk">aktualisiert sich alle 10 Sekunden</span>
      </div>

      {/* ---- Die drei Kernzahlen */}
      <div className="karten">
        <div className="karte">
          <div className="beschriftung">Kaltspeicher</div>
          <div className="wert">{bytes(k.bytes)}</div>
          <div className="zusatz">
            {k.videos} {k.videos === 1 ? "Video" : "Videos"} als Bündel
            {k.dauer_s > 0 ? ` · ${stunden(k.dauer_s)} Laufzeit` : ""}
          </div>
        </div>

        <div className="karte">
          <div className="beschriftung">Heißspeicher</div>
          <div className="wert">{bytes(h.bytes)}</div>
          <div className="zusatz">
            {h.anzahl === 0
              ? "nichts entpackt"
              : `${h.anzahl} entpackt${h.in_wiedergabe > 0 ? `, ${h.in_wiedergabe} in Wiedergabe` : ""}`}
          </div>
          {h.limit_bytes > 0 ? (
            <>
              <Balken anteil={heissAnteil} fuellstand />
              <div className="zusatz">{prozent(heissAnteil)} von {bytes(h.limit_bytes)}</div>
            </>
          ) : null}
        </div>

        <div className="karte">
          <div className="beschriftung">
            {k.gespart_bytes >= 0 ? "Eingespart" : "Mehrbedarf"}
          </div>
          <div
            className="wert"
            style={{
              color:
                k.gespart_bytes > 0
                  ? "var(--zu-archiviert)"
                  : k.gespart_bytes < 0
                    ? "var(--text-gedaempft)"
                    : undefined,
            }}
          >
            {bytes(Math.abs(k.gespart_bytes))}
          </div>
          <div className="zusatz">
            {k.gespart_bytes >= 0
              ? `${prozent(ersparnis)} gegenüber ${bytes(k.quelle_bytes)} beim Download`
              : "noch nichts verkleinert"}
          </div>
          {k.recodiert > 0 ? (
            <div className="zusatz">{k.recodiert} nach AV1 umkodiert</div>
          ) : daten.recodierungen_offen > 0 ? (
            <div className="zusatz">{daten.recodierungen_offen} Verkleinerungen offen</div>
          ) : null}
        </div>
      </div>

      {/* ---- Datenträger. Auf Unraid sind das zwei: Array und Cache-Pool. */}
      {daten.traeger.length > 0 ? (
        <section className="sp-block">
          <h2>Datenträger</h2>
          {daten.traeger.map((t) => {
            const belegtAnteil = t.gesamt > 0 ? t.belegt / t.gesamt : 0;
            return (
              <div key={t.pfad} className="sp-traeger">
                <div className="sp-zeile-kopf">
                  <span className="sp-pfad">{t.pfad}</span>
                  <span>
                    {bytes(t.frei)} frei von {bytes(t.gesamt)}
                  </span>
                </div>
                <Balken anteil={belegtAnteil} fuellstand />
                <div className="zusatz">{prozent(belegtAnteil)} belegt</div>
              </div>
            );
          })}
        </section>
      ) : null}

      {/* ---- Die eigentlich wichtige Zahl: Was käme noch dazu? */}
      {hr.offene_videos > 0 ? (
        <section className="sp-block">
          <h2>Wenn alles geladen wird</h2>
          <div className="sp-prognose">
            <div>
              <div className="wert">{bytes(hr.bytes_geschaetzt)}</div>
              <div className="zusatz">
                für {hr.offene_videos.toLocaleString("de-DE")} noch nicht geladene Videos
                {hr.offene_dauer_s > 0 ? ` (${stunden(hr.offene_dauer_s)})` : ""}
              </div>
            </div>
            <p>
              {hr.gemessen ? (
                <>
                  Hochgerechnet aus deinem eigenen Schnitt von{" "}
                  <strong>{bytes(k.bytes_je_sekunde)} je Sekunde</strong> Videomaterial. Je
                  mehr archiviert ist, desto belastbarer wird die Zahl.
                </>
              ) : (
                <>
                  Grobe Annahme für 1080p – noch ist nichts archiviert, woran sich messen ließe.
                  Sobald die ersten Videos liegen, rechnet die Schätzung mit deinen echten Werten.
                </>
              )}
              {daten.traeger.length > 0 && hr.bytes_geschaetzt > daten.traeger[0].frei ? (
                <>
                  {" "}
                  <strong style={{ color: "var(--zu-fehler)" }}>
                    Das passt nicht auf den freien Platz.
                  </strong>{" "}
                  Eine Höchsthöhe von 1440p oder 1080p unter{" "}
                  <Link to="/einstellungen" style={{ textDecoration: "underline" }}>
                    Einstellungen
                  </Link>{" "}
                  senkt den Bedarf deutlich.
                </>
              ) : null}
            </p>
          </div>
        </section>
      ) : null}

      {/* ---- Nachtraeglich hoeher: gehoert hierher, weil man genau hier
              merkt, dass Platz da ist. */}
      <Hochstufen />

      {/* ---- Aufteilung nach Kanal */}
      {daten.je_kanal.some((x) => x.bytes > 0) ? (
        <section className="sp-block">
          <h2>Nach Kanal</h2>
          {daten.je_kanal
            .filter((x) => x.bytes > 0)
            .map((x) => (
              <Link key={x.id} to={`/kanal/${x.id}`} className="sp-reihe">
                <div className="sp-zeile-kopf">
                  <span>{x.name}</span>
                  <span className="zahl">
                    {bytes(x.bytes)} · {x.videos} {x.videos === 1 ? "Video" : "Videos"}
                  </span>
                </div>
                <Balken anteil={x.bytes / maxKanal} farbe="var(--zu-wartet)" />
              </Link>
            ))}
        </section>
      ) : null}

      {/* ---- Größte Bündel: bei knappem Platz die erste Stellschraube */}
      {daten.groesste.length > 0 ? (
        <section className="sp-block">
          <h2>Größte Bündel</h2>
          {daten.groesste.map((g) => (
            <Link key={g.id} to={`/video/${g.id}`} className="sp-reihe">
              <div className="sp-zeile-kopf">
                <span className="sp-titel">{g.titel}</span>
                <span className="zahl">{bytes(g.bytes)}</span>
              </div>
              <Balken anteil={(g.bytes ?? 0) / groessteBytes} farbe="var(--text-schwach)" />
            </Link>
          ))}
        </section>
      ) : null}

      {/* ---- Zustände */}
      <section className="sp-block">
        <h2>Videos nach Zustand</h2>
        <table className="tabelle" style={{ maxWidth: 460 }}>
          <tbody>
            {Object.entries(daten.videos_nach_status)
              .sort((a, b) => b[1] - a[1])
              .map(([status, anzahl]) => (
                <tr key={status}>
                  <td>
                    <span className="marke-zustand" data-zustand={status}>
                      {zustandText(status)}
                    </span>
                  </td>
                  <td className="zahl" style={{ textAlign: "right" }}>
                    {anzahl.toLocaleString("de-DE")}
                  </td>
                </tr>
              ))}
          </tbody>
        </table>
      </section>

      {/*
        Die Erklaerung gehoert hierher, weil die Zahl sonst enttaeuscht: Wer
        "AV1 spart 50 %" gelesen hat und hier 30 % sieht, haelt es fuer einen
        Fehler. Es ist keiner - die 50 % gelten gegenueber einem
        unkomprimierten Master, nicht gegenueber YouTubes bereits gequetschter
        Datei.
      */}
      <div className="hinweis">
        <div>
          <strong>Woher die Ersparnis kommt</strong>
          <div style={{ color: "var(--text-gedaempft)", marginTop: 6 }}>
            Nicht aus dem ZIP – auf fertig kodiertes Video wirkt Kompression praktisch nicht
            (gemessen: 0,01 %). Das Bündel hält nur Video, Metadaten, Vorschaubild und Untertitel
            als eine Datei zusammen. Der Platz wird durch die Neukodierung nach AV1 gespart, und
            zwar gegenüber der bereits komprimierten YouTube-Datei – deshalb liegt der Wert
            typischerweise bei 25–55 % und nicht bei den oft genannten 50 % gegenüber einem
            unkomprimierten Original.
          </div>
        </div>
      </div>
    </>
  );
}
