import { Link, useSearchParams } from "react-router-dom";

import { Fehler, Gitter, Leer, Skelettgitter, Videokachel } from "../components/ui";
import { VideoNachladen } from "../components/VideoNachladen";
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
  const [suchparameter, setSuchparameter] = useSearchParams();
  const sortWert = suchparameter.get("sort");
  const sortierung: Sortierung = sortWert === "alt" || sortWert === "aufrufe" || sortWert === "titel" ? sortWert : "neu";
  const nurOffen = admin && suchparameter.get("status") === "alle";

  function setSortierung(wert: Sortierung) {
    setSuchparameter((vorher) => {
      const neu = new URLSearchParams(vorher);
      if (wert === "neu") neu.delete("sort"); else neu.set("sort", wert);
      return neu;
    });
  }

  function alleZustaendeUmschalten() {
    setSuchparameter((vorher) => {
      const neu = new URLSearchParams(vorher);
      if (nurOffen) neu.delete("status"); else neu.set("status", "alle");
      return neu;
    });
  }

  const stapel = useVideostapel({ sortierung, nur_archiviert: !admin || !nurOffen });

  return (
    <div className="startseite">
      <h1 className="nur-screenreader">Dein Videoarchiv</h1>
      <div className="chips start-filter" role="group" aria-label="Videos sortieren und filtern">
        {SORTIERUNGEN.map((s) => (
          <button
            key={s.wert}
            type="button"
            className="chip"
            data-aktiv={sortierung === s.wert}
            aria-pressed={sortierung === s.wert}
            onClick={() => setSortierung(s.wert)}
          >
            {s.text}
          </button>
        ))}
        {admin ? <><span className="chip-trenner" aria-hidden="true" />
        <button type="button" className="chip" data-aktiv={nurOffen} aria-pressed={nurOffen} onClick={alleZustaendeUmschalten}>
          Auch nicht archivierte
        </button></> : null}
      </div>
      <div className="start-bestand">{stapel.laedt && stapel.videos.length === 0 ? "Videos werden geladen …" : `${stapel.videos.length} Videos${stapel.ende ? "" : " geladen"} · ${nurOffen ? "Alle Archivzustände" : "Zum Ansehen bereit"}`}</div>

      {stapel.fehler && stapel.videos.length === 0 ? <Fehler text={stapel.fehler} erneut={stapel.mehrLaden} /> : null}

      {stapel.laedt && stapel.videos.length === 0 ? (
        <Skelettgitter />
      ) : stapel.videos.length > 0 ? (
        <>
          <Gitter>
            {stapel.videos.map((v) => (
              <Videokachel key={v.id} video={v} />
            ))}
          </Gitter>
          <VideoNachladen stapel={stapel} />
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
