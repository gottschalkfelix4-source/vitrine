import { useState } from "react";
import { Link, useParams } from "react-router-dom";

import { Fehler, Leer, Skelettgitter, Videokachel } from "../components/ui";
import { Icon } from "../components/Icons";
import { Bild } from "../components/Bild";
import { useAdmin } from "../components/Anmeldung";
import { useApi } from "../hooks/useApi";
import { api } from "../lib/api";
import { prozent } from "../lib/format";
import "../styles/browse.css";

export function Playlistseite() {
  const admin = useAdmin();
  const { playlistId = "" } = useParams();
  const [beschreibungFuer, setBeschreibungFuer] = useState<string | null>(null);
  const beschreibungOffen = beschreibungFuer === playlistId;
  const { daten, laedt, fehler, neuLaden } = useApi(() => api.playlist(playlistId), [playlistId]);

  if (fehler) return <Fehler text={fehler} erneut={neuLaden} />;
  if (laedt && !daten) return <Skelettgitter anzahl={8} />;
  if (!daten) return null;

  const positionen = admin ? daten.positionen : daten.positionen.filter((p) => p.video.status === "archived");
  // Positionen zählen: Ein Video kann mehrfach in derselben Playlist stehen.
  const archiviert = positionen.filter((p) => p.video.status === "archived").length;
  const verschwunden = positionen.filter((p) => p.video.status === "unavailable").length;
  // Nicht archiviert heisst hier: noch holbar. Verschwundene zaehlen getrennt,
  // sonst klingt es nach Arbeit, die man noch erledigen koennte.
  const fehlend = positionen.length - archiviert - verschwunden;
  const anteil = positionen.length ? archiviert / positionen.length : 0;
  const erstesVideo = positionen.find((p) => p.video.status === "archived")?.video;
  const vorschaubild = positionen.find((p) => p.video.bild)?.video.bild;
  const kanalName = positionen.find((p) => p.video.kanal_name)?.video.kanal_name;
  const langeBeschreibung = Boolean(daten.beschreibung && (daten.beschreibung.length > 160 || daten.beschreibung.split("\n").length > 3));

  return (
    <section className="playlist-seite">
      <aside className="playlist-uebersicht" aria-label="Über diese Playlist">
        <div className="playlist-cover">
          <Bild src={vorschaubild} alt=""><div className="platzhalter"><Icon name="playlist" size={48} /></div></Bild>
          <span className="dauer playlist-anzahl"><Icon name="playlist" size={16} />{positionen.length} {positionen.length === 1 ? "Video" : "Videos"}</span>
        </div>
        <div className="playlist-details">
          <h1>{daten.titel}</h1>
          {daten.kanal_id ? <Link className="playlist-kanallink" to={`/kanal/${daten.kanal_id}`}><span>{kanalName ?? "Zum Kanal"}</span><Icon name="chevronRight" size={16} /></Link> : null}
          <p className="browse-meta">{positionen.length} {positionen.length === 1 ? "Video" : "Videos"} · {archiviert} archiviert ({prozent(anteil)})</p>
          {erstesVideo ? <Link className="knopf playlist-start" data-art="stark" to={`/video/${erstesVideo.id}`}><Icon name="play" size={20} />Wiedergeben</Link> : null}
          {daten.beschreibung ? <div className="playlist-beschreibung-block">
            <p className="playlist-beschreibung" id="playlist-beschreibung" data-offen={beschreibungOffen || !langeBeschreibung}>{daten.beschreibung}</p>
            {langeBeschreibung ? <button type="button" className="beschreibung-umschalten"
              aria-expanded={beschreibungOffen} aria-controls="playlist-beschreibung"
              onClick={() => setBeschreibungFuer(beschreibungOffen ? null : playlistId)}>{beschreibungOffen ? "Weniger anzeigen" : "Mehr anzeigen"}</button> : null}
          </div> : null}
        </div>
      </aside>

      <div className="playlist-inhalt">

      {/*
        Die Playlist zeigt bewusst ALLE Positionen, auch die nicht archivierten.
        Eine Liste, die stillschweigend nur das Vorhandene zeigt, verschweigt
        genau die Information, die man in einem Archiv braucht: was fehlt.
      */}
      {admin && (fehlend > 0 || verschwunden > 0) ? (
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

      <ol className="playlist-videoliste" aria-label="Videos in dieser Playlist">
        {positionen.map((p) => (
          <li className="playlist-position" key={`${p.position}-${p.video.id}`} value={p.position + 1}>
            <span className="playlist-nummer" aria-hidden="true">{p.position + 1}</span>
            <Videokachel video={p.video} />
          </li>
        ))}
      </ol>
      {positionen.length === 0 ? <Leer zeichen="☰" titel="Diese Playlist ist noch leer" text="Archivierte Videos erscheinen hier in ihrer ursprünglichen Reihenfolge." /> : null}
      </div>
    </section>
  );
}
