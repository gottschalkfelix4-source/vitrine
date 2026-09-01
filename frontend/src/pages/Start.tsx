import { useState } from "react";
import { Link } from "react-router-dom";

import { Fehler, Gitter, Leer, Skelettgitter, Videokachel } from "../components/ui";
import { useApi } from "../hooks/useApi";
import { api } from "../lib/api";

type Sortierung = "neu" | "alt" | "aufrufe" | "titel";

const SORTIERUNGEN: { wert: Sortierung; text: string }[] = [
  { wert: "neu", text: "Neueste" },
  { wert: "alt", text: "Älteste" },
  { wert: "aufrufe", text: "Meistgesehen" },
  { wert: "titel", text: "A–Z" },
];

export function Startseite() {
  const [sortierung, setSortierung] = useState<Sortierung>("neu");
  const [nurOffen, setNurOffen] = useState(false);

  const { daten, laedt, fehler, neuLaden } = useApi(
    () => api.videos({ sortierung, limit: 60, nur_archiviert: !nurOffen }),
    [sortierung, nurOffen],
  );

  return (
    <>
      <div className="seiten-kopf">
        <h1>Archiv</h1>
        {daten ? <span className="beiwerk">{daten.length} Videos</span> : null}
      </div>

      <div className="chips">
        {SORTIERUNGEN.map((s) => (
          <button
            key={s.wert}
            className="chip"
            data-aktiv={sortierung === s.wert}
            onClick={() => setSortierung(s.wert)}
          >
            {s.text}
          </button>
        ))}
        <button className="chip" data-aktiv={nurOffen} onClick={() => setNurOffen(!nurOffen)}>
          Auch nicht archivierte
        </button>
      </div>

      {fehler ? <Fehler text={fehler} erneut={neuLaden} /> : null}

      {laedt && !daten ? (
        <Skelettgitter />
      ) : daten && daten.length > 0 ? (
        <Gitter>
          {daten.map((v) => (
            <Videokachel key={v.id} video={v} />
          ))}
        </Gitter>
      ) : !fehler ? (
        <Leer
          titel="Noch nichts im Archiv"
          text="Nimm einen Kanal auf, dann werden dessen Videos im Hintergrund geladen und erscheinen hier."
          kinder={
            <Link className="knopf" data-art="stark" to="/kanaele">
              Zu den Kanälen
            </Link>
          }
        />
      ) : null}
    </>
  );
}
