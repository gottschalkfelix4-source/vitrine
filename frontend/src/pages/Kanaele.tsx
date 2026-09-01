import { Link } from "react-router-dom";

import { Fehler, Leer, Skelettgitter } from "../components/ui";
import { useApi } from "../hooks/useApi";
import { api, thumbUrl } from "../lib/api";
import { bytes, prozent, vorZeit } from "../lib/format";

export function Kanaeleseite({ aufAnlegen }: { aufAnlegen: () => void }) {
  const { daten, laedt, fehler, neuLaden } = useApi(() => api.kanaele(), []);

  if (fehler) return <Fehler text={fehler} erneut={neuLaden} />;
  if (laedt && !daten) return <Skelettgitter anzahl={4} />;

  if (!daten || daten.length === 0) {
    return (
      <Leer
        zeichen="📡"
        titel="Noch keine Kanäle"
        text="Nimm einen Kanal auf. Vitrine liest dann dessen Videos und Playlists und lädt sie im Hintergrund."
        kinder={
          <button className="knopf" data-art="stark" onClick={aufAnlegen}>
            Kanal aufnehmen
          </button>
        }
      />
    );
  }

  return (
    <>
      <div className="seiten-kopf">
        <h1>Kanäle</h1>
        <button className="knopf" data-art="stark" onClick={aufAnlegen}>
          + Kanal aufnehmen
        </button>
      </div>

      <table className="tabelle">
        <thead>
          <tr>
            <th>Kanal</th>
            <th style={{ width: 200 }}>Archiviert</th>
            <th style={{ width: 120 }}>Belegung</th>
            <th style={{ width: 150 }}>Abgleich</th>
          </tr>
        </thead>
        <tbody>
          {daten.map((k) => {
            const anteil = k.videos_gesamt ? k.videos_archiviert / k.videos_gesamt : 0;
            return (
              <tr key={k.id}>
                <td>
                  <Link to={`/kanal/${k.id}`} style={{ display: "flex", alignItems: "center", gap: 12 }}>
                    {thumbUrl(k.avatar) ? (
                      <img className="avatar" src={thumbUrl(k.avatar)!} alt="" style={{ width: 32, height: 32, borderRadius: "50%" }} />
                    ) : (
                      <span className="avatar" style={{ width: 32, height: 32, borderRadius: "50%", background: "var(--flaeche-hoch)" }} />
                    )}
                    <div>
                      <div style={{ fontWeight: 500 }}>{k.name}</div>
                      {k.handle ? (
                        <div style={{ color: "var(--text-schwach)", fontSize: 12 }}>{k.handle}</div>
                      ) : null}
                    </div>
                  </Link>
                </td>
                <td>
                  <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                    <div className="balken">
                      <span style={{ width: `${anteil * 100}%`, background: "var(--zu-archiviert)" }} />
                    </div>
                    <span className="zahl" style={{ fontSize: 12.5, color: "var(--text-gedaempft)", whiteSpace: "nowrap" }}>
                      {k.videos_archiviert}/{k.videos_gesamt}
                      {k.videos_gesamt ? ` (${prozent(anteil)})` : ""}
                    </span>
                  </div>
                </td>
                <td className="zahl">{bytes(k.belegung_bytes)}</td>
                <td style={{ color: "var(--text-gedaempft)", fontSize: 12.5 }}>
                  {k.abgleich_aktiv ? vorZeit(k.zuletzt_abgeglichen) || "steht aus" : "abgeschaltet"}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </>
  );
}
