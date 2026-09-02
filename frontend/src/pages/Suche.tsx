import { Link, useSearchParams } from "react-router-dom";

import { Fehler, Gitter, Leer, Skelettgitter, Videokachel } from "../components/ui";
import { useApi } from "../hooks/useApi";
import { api } from "../lib/api";
import { dauer } from "../lib/format";

export function Suchseite() {
  const [parameter] = useSearchParams();
  const anfrage = parameter.get("q") ?? "";

  const { daten, laedt, fehler, neuLaden } = useApi(
    () => (anfrage ? api.suchen(anfrage) : Promise.resolve(null)),
    [anfrage],
  );

  if (fehler) return <Fehler text={fehler} erneut={neuLaden} />;
  if (laedt && !daten) return <Skelettgitter anzahl={8} />;

  if (daten?.zu_kurz) {
    return (
      <Leer
        zeichen="⌕"
        titel="Suchbegriff zu kurz"
        text={
          "Mindestens drei Zeichen. Der Index arbeitet mit Zeichenfolgen – damit findet er " +
          "auch mitten im Wort, etwa „Konfiguration“ in „Netzwerkkonfiguration“."
        }
      />
    );
  }

  const videos = daten?.videos ?? [];
  const gesprochen = daten?.im_gesprochenen ?? [];
  // Videos, die schon oben stehen, unten nicht wiederholen - es sei denn, der
  // Untertitelfund fuegt etwas hinzu, naemlich die Fundstelle.
  const gesamt = videos.length + gesprochen.length;

  return (
    <>
      <div className="seiten-kopf">
        <h1>Suche: {anfrage}</h1>
        <span className="beiwerk">
          {videos.length} in Titel und Beschreibung
          {gesprochen.length > 0 ? ` · ${gesprochen.length} im gesprochenen Wort` : ""}
        </span>
      </div>

      {gesamt === 0 ? (
        <Leer
          zeichen="⌕"
          titel="Nichts gefunden"
          text={`Zu „${anfrage}“ gibt es im Archiv keinen Treffer. Gesucht wird in Titeln, Beschreibungen und in den Untertiteln.`}
        />
      ) : null}

      {videos.length > 0 ? (
        <Gitter>
          {videos.map((v) => (
            <Videokachel key={v.id} video={v} />
          ))}
        </Gitter>
      ) : null}

      {/*
        Der eigentliche Mehrwert gegenueber einer Titelsuche: Hier steht nicht
        nur, in welchem Video der Begriff vorkommt, sondern an welcher Stelle.
        Ein Klick springt direkt dorthin.
      */}
      {gesprochen.length > 0 ? (
        <>
          <div className="seiten-kopf" style={{ marginTop: 36 }}>
            <h1 style={{ fontSize: 17 }}>Im gesprochenen Wort</h1>
            <span className="beiwerk">Klick springt an die Fundstelle</span>
          </div>
          <div className="fundstellen">
            {gesprochen.map((f) => (
              <Link
                key={`${f.video.id}-${f.start_s}`}
                className="fundstelle"
                to={`/video/${f.video.id}?t=${Math.floor(f.start_s)}`}
              >
                <div className="fundstelle-bild">
                  {f.video.bild ? (
                    <img src={f.video.bild!} alt="" loading="lazy" />
                  ) : (
                    <div className="platzhalter">▶</div>
                  )}
                  <span className="dauer">{dauer(f.start_s)}</span>
                </div>
                <div style={{ minWidth: 0 }}>
                  <div className="fundstelle-zitat">„{f.zeile}“</div>
                  <div className="kachel-zeile">
                    <span>{f.video.titel}</span>
                    <span className="punkt">{f.video.kanal_name}</span>
                    <span className="marke-zustand" data-zustand="archived">
                      {f.sprache}
                    </span>
                  </div>
                </div>
              </Link>
            ))}
          </div>
        </>
      ) : null}
    </>
  );
}
