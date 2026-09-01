import { useState } from "react";
import { Link } from "react-router-dom";

import { Fehler, Gitter, Leer, Skelettgitter, Videokachel } from "../components/ui";
import { useVideostapel } from "../hooks/useApi";

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

  const stapel = useVideostapel({ sortierung, nur_archiviert: !nurOffen });

  return (
    <>
      <div className="seiten-kopf">
        <h1>Archiv</h1>
        <span className="beiwerk">
          {stapel.videos.length} Videos{stapel.ende ? "" : " geladen"}
        </span>
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

      {stapel.fehler ? <Fehler text={stapel.fehler} erneut={stapel.neuLaden} /> : null}

      {stapel.laedt && stapel.videos.length === 0 ? (
        <Skelettgitter />
      ) : stapel.videos.length > 0 ? (
        <>
          <Gitter>
            {stapel.videos.map((v) => (
              <Videokachel key={v.id} video={v} />
            ))}
          </Gitter>
          {!stapel.ende ? (
            <div className="mehr-laden">
              <button className="knopf" onClick={stapel.mehrLaden} disabled={stapel.laedt}>
                {stapel.laedt ? "lädt …" : "Mehr laden"}
              </button>
            </div>
          ) : null}
        </>
      ) : !stapel.fehler ? (
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
