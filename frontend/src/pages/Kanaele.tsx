import { useState } from "react";
import { Link } from "react-router-dom";

import { Fehler, Leer, Skelettgitter } from "../components/ui";
import { Icon } from "../components/Icons";
import { useApi } from "../hooks/useApi";
import { api, thumbUrl } from "../lib/api";
import { bytes, prozent, vorZeit } from "../lib/format";
import "../styles/browse.css";

export function Kanaeleseite({ aufAnlegen }: { aufAnlegen: () => void }) {
  const { daten, laedt, fehler, neuLaden } = useApi(() => api.kanaele(), []);
  const [filter, setFilter] = useState("");
  const [sortierung, setSortierung] = useState("name");

  if (fehler) return <Fehler text={fehler} erneut={neuLaden} />;
  if (laedt && !daten) return <Skelettgitter anzahl={4} />;

  if (!daten || daten.length === 0) {
    return (
      <Leer
        zeichen="◎"
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

  const sichtbar = daten
    .filter((k) => `${k.name} ${k.handle ?? ""}`.toLocaleLowerCase("de").includes(filter.toLocaleLowerCase("de")))
    .sort((a, b) => sortierung === "archiviert"
      ? b.videos_archiviert - a.videos_archiviert
      : a.name.localeCompare(b.name, "de"));

  return (
    <section className="kanaele-seite">
      <div className="seiten-kopf">
        <div>
          <h1>Deine Kanäle</h1>
          <p className="browse-meta">{daten.length} {daten.length === 1 ? "Kanal" : "Kanäle"} im Archiv</p>
        </div>
        <button className="knopf" data-art="stark" onClick={aufAnlegen}>
          <Icon name="plus" size={20} /> Kanal aufnehmen
        </button>
      </div>

      <div className="kanal-filter">
        <input className="eingabe" aria-label="Kanäle filtern" placeholder="Kanäle filtern" type="search" value={filter} onChange={(e) => setFilter(e.target.value)} />
        <select className="auswahl" aria-label="Kanäle sortieren" value={sortierung} onChange={(e) => setSortierung(e.target.value)}>
          <option value="name">Name A–Z</option>
          <option value="archiviert">Meiste archivierte Videos</option>
        </select>
      </div>

      <div className="kanal-liste">
          {sichtbar.map((k) => {
            const anteil = k.videos_gesamt ? k.videos_archiviert / k.videos_gesamt : 0;
            return (
              <Link className="kanal-abo" key={k.id} to={`/kanal/${k.id}`}>
                    {thumbUrl(k.avatar) ? (
                      <img className="kanal-abo-avatar" src={thumbUrl(k.avatar)!} alt="" loading="lazy" />
                    ) : (
                      <span className="kanal-abo-avatar" aria-hidden="true">{k.name.charAt(0).toLocaleUpperCase("de")}</span>
                    )}
                    <div className="kanal-abo-info">
                      <h2>{k.name}</h2>
                      {k.handle ? (
                        <div className="browse-meta">{k.handle}</div>
                      ) : null}
                      <p className="kanal-abo-zahlen">
                        {k.videos_archiviert} von {k.videos_gesamt} Videos archiviert
                        {k.videos_gesamt ? ` · ${prozent(anteil)}` : ""}
                        {" · "}{bytes(k.belegung_bytes)}
                      </p>
                      <p className="browse-meta">Abgleich {k.abgleich_aktiv ? vorZeit(k.zuletzt_abgeglichen) || "steht aus" : "abgeschaltet"}</p>
                    </div>
                    <span className="kanal-abo-oeffnen">Kanal ansehen <Icon name="chevronRight" size={18} /></span>
              </Link>
            );
          })}
      </div>
      {sichtbar.length === 0 ? <Leer zeichen="⌕" titel="Kein Kanal gefunden" text={`Keiner deiner Kanäle enthält „${filter}“.`} /> : null}
    </section>
  );
}
