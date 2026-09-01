import { Fehler, Skelettgitter } from "../components/ui";
import { useApi } from "../hooks/useApi";
import { api } from "../lib/api";
import { bytes, prozent, zustandText } from "../lib/format";

export function Speicherseite() {
  const { daten, laedt, fehler, neuLaden } = useApi(() => api.speicher(), [], 10_000);

  if (fehler) return <Fehler text={fehler} erneut={neuLaden} />;
  if (laedt && !daten) return <Skelettgitter anzahl={4} />;
  if (!daten) return null;

  const k = daten.kaltspeicher;
  const h = daten.heissspeicher;
  const ersparnis = k.quelle_bytes > 0 ? k.gespart_bytes / k.quelle_bytes : 0;
  const heissAnteil = h.limit_bytes > 0 ? h.bytes / h.limit_bytes : 0;

  return (
    <>
      <div className="seiten-kopf">
        <h1>Speicher</h1>
        <span className="beiwerk">frei auf dem Datenträger: {bytes(daten.freier_platz)}</span>
      </div>

      <div className="karten">
        <div className="karte">
          <div className="beschriftung">Kaltspeicher</div>
          <div className="wert">{bytes(k.bytes)}</div>
          <div className="zusatz">{k.videos} Videos als Bündel</div>
        </div>
        {/*
          Die Zahl kann negativ werden: ohne Recodierung kostet allein das
          Umpacken in einen browsertauglichen Behaelter ein paar Kilobyte. Dann
          ist "eingespart" das falsche Wort - lieber ehrlich benennen, als eine
          negative Ersparnis anzuzeigen.
        */}
        <div className="karte">
          <div className="beschriftung">{k.gespart_bytes >= 0 ? "Eingespart" : "Mehrbedarf"}</div>
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
              : "noch nichts verkleinert – Verkleinerung läuft im Hintergrund"}
          </div>
        </div>
        <div className="karte">
          <div className="beschriftung">Heißspeicher</div>
          <div className="wert">{bytes(h.bytes)}</div>
          <div className="zusatz">
            {h.anzahl} entpackt
            {h.in_wiedergabe > 0 ? `, ${h.in_wiedergabe} in Wiedergabe` : ""}
          </div>
          {h.limit_bytes > 0 ? (
            <div className="balken" style={{ marginTop: 10 }}>
              <span
                style={{
                  width: `${Math.min(100, heissAnteil * 100)}%`,
                  background: heissAnteil > 0.9 ? "var(--zu-fehler)" : "var(--zu-archiviert)",
                }}
              />
            </div>
          ) : null}
        </div>
        <div className="karte">
          <div className="beschriftung">Verkleinerung offen</div>
          <div className="wert">{daten.recodierungen_offen}</div>
          <div className="zusatz">
            {daten.recodierungen_offen > 0
              ? "läuft im Hintergrund, blockiert nichts"
              : "alles verkleinert"}
          </div>
        </div>
      </div>

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

      <div className="seiten-kopf" style={{ marginTop: 32 }}>
        <h1 style={{ fontSize: 17 }}>Videos nach Zustand</h1>
      </div>
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
                  {anzahl}
                </td>
              </tr>
            ))}
        </tbody>
      </table>
    </>
  );
}
