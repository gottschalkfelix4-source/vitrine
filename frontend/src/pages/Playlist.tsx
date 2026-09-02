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

  const verschwunden = daten.positionen.filter((p) => p.video.status === "unavailable").length;
  // Nicht archiviert heisst hier: noch holbar. Verschwundene zaehlen getrennt,
  // sonst klingt es nach Arbeit, die man noch erledigen koennte.
  const fehlend = daten.positionen.length - daten.anzahl_archiviert - verschwunden;
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
      {fehlend > 0 || verschwunden > 0 ? (
        <div className="hinweis" data-art="arbeit">
          <div>
            <strong>
              {fehlend > 0
                ? `${fehlend} ${fehlend === 1 ? "Position ist" : "Positionen sind"} noch nicht im Archiv.`
                : "Alles Verfügbare ist archiviert."}
            </strong>
            <div style={{ color: "var(--text-gedaempft)", marginTop: 4 }}>
              {verschwunden > 0 ? (
                <>
                  {verschwunden}{" "}
                  {verschwunden === 1 ? "Video wurde" : "Videos wurden"} bei YouTube gelöscht oder
                  privat gestellt und {verschwunden === 1 ? "ist" : "sind"} nicht mehr zu holen.{" "}
                </>
              ) : null}
              Alle Positionen bleiben an ihrer Stelle, damit die Reihenfolge des Kanals erhalten
              bleibt und sichtbar ist, was fehlt.
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
