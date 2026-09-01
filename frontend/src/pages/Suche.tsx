import { useSearchParams } from "react-router-dom";

import { Fehler, Gitter, Leer, Skelettgitter, Videokachel } from "../components/ui";
import { useApi } from "../hooks/useApi";
import { api } from "../lib/api";

export function Suchseite() {
  const [parameter] = useSearchParams();
  const anfrage = parameter.get("q") ?? "";

  const { daten, laedt, fehler, neuLaden } = useApi(
    () => (anfrage ? api.videos({ suche: anfrage, limit: 60, nur_archiviert: false }) : Promise.resolve([])),
    [anfrage],
  );

  if (fehler) return <Fehler text={fehler} erneut={neuLaden} />;

  return (
    <>
      <div className="seiten-kopf">
        <h1>Suche: {anfrage}</h1>
        {daten ? <span className="beiwerk">{daten.length} Treffer</span> : null}
      </div>

      {laedt && !daten ? (
        <Skelettgitter anzahl={8} />
      ) : daten && daten.length > 0 ? (
        <Gitter>
          {daten.map((v) => (
            <Videokachel key={v.id} video={v} />
          ))}
        </Gitter>
      ) : (
        <Leer
          zeichen="⌕"
          titel="Nichts gefunden"
          text={`Zu "${anfrage}" gibt es im Archiv keinen Treffer. Gesucht wird in Titeln und Beschreibungen.`}
        />
      )}
    </>
  );
}
