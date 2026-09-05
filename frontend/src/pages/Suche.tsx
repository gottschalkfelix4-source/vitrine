import { Link, useSearchParams } from "react-router-dom";

import { Fehler, Gitter, Leer, Skelettgitter, Videokachel } from "../components/ui";
import { Icon } from "../components/Icons";
import { useAdmin } from "../components/Anmeldung";
import { useApi } from "../hooks/useApi";
import { api } from "../lib/api";
import { dauer } from "../lib/format";
import "../styles/browse.css";

export function Suchseite() {
  const admin = useAdmin();
  const [parameter, setParameter] = useSearchParams();
  const anfrage = parameter.get("q") ?? "";
  const filter = parameter.get("bereich") ?? "alle";

  const { daten, laedt, fehler, neuLaden } = useApi(
    () => (anfrage ? api.suchen(anfrage) : Promise.resolve(null)),
    [anfrage],
  );

  if (fehler) return <Fehler text={fehler} erneut={neuLaden} />;
  if (laedt && !daten) return <Skelettgitter anzahl={8} />;

  if (!anfrage) return <Leer zeichen="⌕" titel="Im Archiv suchen" text="Gib oben einen Suchbegriff ein. Du findest Videos, Beschreibungen und Stellen aus den Untertiteln." />;

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

  const videos = (daten?.videos ?? []).filter((v) => admin || v.status === "archived");
  const gesprochen = (daten?.im_gesprochenen ?? []).filter((f) => admin || f.video.status === "archived");
  // Videos, die schon oben stehen, unten nicht wiederholen - es sei denn, der
  // Untertitelfund fuegt etwas hinzu, naemlich die Fundstelle.
  const gesamt = videos.length + gesprochen.length;

  return (
    <section className="such-seite">
      <div className="such-filter chips" aria-label="Suchbereich">
        {([{ wert: "alle", text: "Alle" }, { wert: "videos", text: "Titel & Beschreibung" }, { wert: "gesprochen", text: "Im gesprochenen Wort" }] as const).map((f) => (
          <button key={f.wert} className="chip" data-aktiv={filter === f.wert} aria-pressed={filter === f.wert} onClick={() => setParameter((vorher) => {
            const neu = new URLSearchParams(vorher);
            if (f.wert === "alle") neu.delete("bereich"); else neu.set("bereich", f.wert);
            return neu;
          })}>{f.text}</button>
        ))}
      </div>
      <div className="seiten-kopf such-kopf">
        <h1>Suchergebnisse für „{anfrage}“</h1>
        <span className="browse-meta">
          {videos.length} in Titel und Beschreibung
          {gesprochen.length > 0 ? ` · ${gesprochen.length} im gesprochenen Wort` : ""}
        </span>
      </div>

      {gesamt === 0 || (filter === "videos" && videos.length === 0) || (filter === "gesprochen" && gesprochen.length === 0) ? (
        <Leer
          zeichen="⌕"
          titel="Nichts gefunden"
          text={gesamt === 0 ? `Zu „${anfrage}“ gibt es im Archiv keinen Treffer. Gesucht wird in Titeln, Beschreibungen und in den Untertiteln.` : "In diesem Suchbereich gibt es keine Treffer. Wähle „Alle“, um die übrigen Ergebnisse zu sehen."}
        />
      ) : null}

      {videos.length > 0 && filter !== "gesprochen" ? (
        <Gitter form="liste">
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
      {gesprochen.length > 0 && filter !== "videos" ? (
        <>
          <div className="seiten-kopf such-untertitel-kopf">
            <h2>Im gesprochenen Wort</h2>
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
                    <div className="platzhalter"><Icon name="play" size={32} /></div>
                  )}
                  <span className="dauer">{dauer(f.start_s)}</span>
                </div>
                <div style={{ minWidth: 0 }}>
                  <h3 className="fundstelle-titel">{f.video.titel}</h3>
                  <div className="kachel-zeile">
                    <span>{f.video.kanal_name}</span>
                    <span className="punkt">Untertitel · {f.sprache}</span>
                  </div>
                  <div className="fundstelle-zitat">„{f.zeile}“</div>
                  <span className="fundstelle-zeit">Ab {dauer(f.start_s)} ansehen <Icon name="chevronRight" size={16} /></span>
                </div>
              </Link>
            ))}
          </div>
        </>
      ) : null}
    </section>
  );
}
