import { Link, useParams } from "react-router-dom";

import { Fehler, Gitter, Skelettgitter, Videokachel } from "../components/ui";
import { useApi } from "../hooks/useApi";
import { api } from "../lib/api";
import { prozent } from "../lib/format";

export function Playlistseite() {
  const { playlistId = "" } = useParams();
  const { daten, laedt, fehler, neuLaden } = useApi(() => api.playlist(playlistId), [playlistId]);

  if (fehler) return <Fehler text={fehler} erneut={neuLaden} />;
  if (laedt && !daten) return <Skelettgitter anzahl={8} />;
  if (!daten) return null;

  const fehlend = daten.positionen.length - daten.anzahl_archiviert;
  const anteil = daten.positionen.length ? daten.anzahl_archiviert / daten.positionen.length : 0;

  return (
    <>
      <div className="seiten-kopf">
        <div>
          <h1>{daten.titel}</h1>
          <div className="beiwerk" style={{ marginTop: 6 }}>
            {daten.anzahl_archiviert} von {daten.positionen.length} archiviert ({prozent(anteil)})
            {daten.kanal_id ? (
              <>
                {" · "}
                <Link to={`/kanal/${daten.kanal_id}`} style={{ textDecoration: "underline" }}>
                  zum Kanal
                </Link>
              </>
            ) : null}
          </div>
        </div>
      </div>

      {/*
        Die Playlist zeigt bewusst ALLE Positionen, auch die nicht archivierten.
        Eine Liste, die stillschweigend nur das Vorhandene zeigt, verschweigt
        genau die Information, die man in einem Archiv braucht: was fehlt.
      */}
      {fehlend > 0 ? (
        <div className="hinweis" data-art="arbeit">
          <div>
            <strong>
              {fehlend} {fehlend === 1 ? "Position ist" : "Positionen sind"} noch nicht im Archiv.
            </strong>
            <div style={{ color: "var(--text-gedaempft)", marginTop: 4 }}>
              Sie stehen trotzdem an ihrer Stelle in der Liste, damit die Reihenfolge des Kanals
              erhalten bleibt und sichtbar ist, was fehlt.
            </div>
          </div>
        </div>
      ) : null}

      <Gitter>
        {daten.positionen.map((p) => (
          <Videokachel key={p.video.id} video={p.video} position={p.position} ohneKanal />
        ))}
      </Gitter>
    </>
  );
}
