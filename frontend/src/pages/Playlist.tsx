import { Link, useParams } from "react-router-dom";

import { Fehler, Leer, Skelettgitter, Videokachel } from "../components/ui";
import { Icon } from "../components/Icons";
import { useApi } from "../hooks/useApi";
import { api } from "../lib/api";
import { prozent } from "../lib/format";
import "../styles/browse.css";

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
  const erstesVideo = daten.positionen.find((p) => p.video.status === "archived")?.video;
  const vorschaubild = daten.positionen.find((p) => p.video.bild)?.video.bild;
  const kanalName = daten.positionen.find((p) => p.video.kanal_name)?.video.kanal_name;

  return (
    <section className="playlist-seite">
      <aside className="playlist-uebersicht" aria-label="Über diese Playlist">
        <div className="playlist-cover">
          {vorschaubild ? <img src={vorschaubild} alt="" /> : <div className="platzhalter"><Icon name="playlist" size={48} /></div>}
          <span className="dauer playlist-anzahl"><Icon name="playlist" size={16} />{daten.positionen.length} Videos</span>
        </div>
        <div className="playlist-details">
          <h1>{daten.titel}</h1>
          {daten.kanal_id ? <Link className="playlist-kanallink" to={`/kanal/${daten.kanal_id}`}>{kanalName ?? "Zum Kanal"}<Icon name="chevronRight" size={16} /></Link> : null}
          <p className="browse-meta">{daten.positionen.length} Videos · {daten.anzahl_archiviert} archiviert ({prozent(anteil)})</p>
          {erstesVideo ? <Link className="knopf playlist-start" data-art="stark" to={`/video/${erstesVideo.id}`}><Icon name="play" size={20} />Wiedergeben</Link> : null}
          {daten.beschreibung ? <p className="playlist-beschreibung">{daten.beschreibung}</p> : null}
        </div>
      </aside>

      <div className="playlist-inhalt">

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

      <div className="playlist-videoliste">
        {daten.positionen.map((p) => (
          <div className="playlist-position" key={`${p.position}-${p.video.id}`}>
            <span className="playlist-nummer" aria-label={`Position ${p.position + 1}`}>{p.position + 1}</span>
            <Videokachel video={p.video} />
          </div>
        ))}
      </div>
      {daten.positionen.length === 0 ? <Leer zeichen="☰" titel="Diese Playlist ist noch leer" text="Sobald der Kanal abgeglichen wurde, erscheinen die Videos hier in ihrer ursprünglichen Reihenfolge." /> : null}
      </div>
    </section>
  );
}
