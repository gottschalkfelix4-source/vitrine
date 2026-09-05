import { useState } from "react";
import { Link } from "react-router-dom";

import { Fehler, Gitter, Leer, Skelettgitter, Videokachel } from "../components/ui";
import { useVideostapel } from "../hooks/useApi";
import { useAdmin } from "../components/Anmeldung";

type Sortierung = "neu" | "alt" | "aufrufe" | "titel";

const SORTIERUNGEN: { wert: Sortierung; text: string }[] = [
  { wert: "neu", text: "Neueste" },
  { wert: "alt", text: "Älteste" },
  { wert: "aufrufe", text: "Meistgesehen" },
  { wert: "titel", text: "A–Z" },
];

export function Startseite() {
  const admin = useAdmin();
  const [sortierung, setSortierung] = useState<Sortierung>("neu");
  const [nurOffen, setNurOffen] = useState(false);

  const stapel = useVideostapel({ sortierung, nur_archiviert: !admin || !nurOffen });

  return (
    <div className="startseite">
      <h1 className="nur-screenreader">Dein Videoarchiv</h1>
      <div className="chips start-filter" aria-label="Videos sortieren und filtern">
        {SORTIERUNGEN.map((s) => (
          <button
            key={s.wert}
            className="chip"
            data-aktiv={sortierung === s.wert}
            aria-pressed={sortierung === s.wert}
            onClick={() => setSortierung(s.wert)}
          >
            {s.text}
          </button>
        ))}
        {admin ? <><span className="chip-trenner" aria-hidden="true" />
        <button className="chip" data-aktiv={nurOffen} aria-pressed={nurOffen} onClick={() => setNurOffen(!nurOffen)}>
          Auch nicht archivierte
        </button></> : null}
      </div>
      <div className="start-bestand">{stapel.videos.length} Videos{stapel.ende ? "" : " geladen"} · {nurOffen ? "Alle Archivzustände" : "Zum Ansehen bereit"}</div>

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
          text={admin ? "Nimm einen Kanal auf, dann werden dessen Videos im Hintergrund geladen und erscheinen hier." : "Hier erscheinen die archivierten Videos, sobald sie zum Ansehen bereit sind."}
          kinder={
            <Link className="knopf" data-art="stark" to="/kanaele">
              Zu den Kanälen
            </Link>
          }
        />
      ) : null}
    </div>
  );
}
