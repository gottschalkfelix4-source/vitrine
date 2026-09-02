import { useState } from "react";
import { Link } from "react-router-dom";

import type { VideoKurz } from "../lib/api";
import { api } from "../lib/api";
import { aufrufe, dauer, vorZeit, zustandText } from "../lib/format";

/** Zustandsmarke - im Archiv-Kontext das wichtigste Zusatzelement gegenueber
 *  YouTube: Man muss auf einen Blick sehen, was da ist und was fehlt. */
export function Zustand({ status }: { status: string }) {
  return (
    <span className="marke-zustand" data-zustand={status}>
      {zustandText(status)}
    </span>
  );
}

interface KachelProps {
  video: VideoKurz;
  /** Kanalzeile ausblenden, wenn ohnehin alles vom selben Kanal ist. */
  ohneKanal?: boolean;
  position?: number;
}

/** Zustaende, aus denen heraus ein Video (erneut) geholt werden kann. */
const HOLBAR = new Set(["new", "failed", "skipped"]);

export function Videokachel({ video, ohneKanal, position }: KachelProps) {
  // Nach dem Klick auf "Laden" zeigt die Kachel sofort "wartet", ohne dass
  // die ganze Liste neu geholt werden muss.
  const [neuerStatus, setNeuerStatus] = useState<string | null>(null);
  const [holFehler, setHolFehler] = useState<string | null>(null);
  const status = neuerStatus ?? video.status;
  const spielbar = status === "archived";
  const bild = video.bild;

  async function holen(e: React.MouseEvent) {
    e.preventDefault();
    e.stopPropagation();
    setHolFehler(null);
    try {
      await api.videoArchivieren(video.id);
      setNeuerStatus("queued");
    } catch (err) {
      setHolFehler(err instanceof Error ? err.message : String(err));
    }
  }
  const inhalt = (
    <>
      <div className="kachel-bild">
        {bild ? (
          <img src={bild} alt="" loading="lazy" />
        ) : (
          <div className="platzhalter">{video.war_live ? "◉" : "▶"}</div>
        )}
        {video.dauer_s ? <span className="dauer">{dauer(video.dauer_s)}</span> : null}
        {video.fortschritt_anteil ? (
          <div className="fortschritt">
            <span style={{ width: `${Math.min(100, video.fortschritt_anteil * 100)}%` }} />
          </div>
        ) : null}
      </div>
      <div className="kachel-text">
        <div style={{ minWidth: 0, flex: 1 }}>
          <h3 className="kachel-titel" title={video.titel}>
            {position != null ? <span style={{ color: "var(--text-schwach)" }}>{position + 1}. </span> : null}
            {/* "(ohne Titel)" sagt nicht, was los ist. Bei einem verschwundenen
                Video ist der fehlende Titel gerade die Aussage - dann lieber
                benennen, was passiert ist. */}
            {status === "unavailable" && video.titel === "(ohne Titel)"
              ? "Gelöscht oder privat gestellt"
              : video.titel}
          </h3>
          <div className="kachel-zeile">
            {!ohneKanal && video.kanal_name ? (
              <Link to={`/kanal/${video.kanal_id}`} onClick={(e) => e.stopPropagation()}>
                {video.kanal_name}
              </Link>
            ) : null}
          </div>
          <div className="kachel-zeile">
            {video.aufrufe ? <span>{aufrufe(video.aufrufe)}</span> : null}
            {video.hochgeladen ? (
              <span className={video.aufrufe ? "punkt" : undefined}>{vorZeit(video.hochgeladen)}</span>
            ) : null}
            {!spielbar ? <Zustand status={status} /> : null}
            {HOLBAR.has(status) ? (
              <button className="kachel-laden" onClick={holen} title="Dieses Video ins Archiv holen">
                ↓ Laden
              </button>
            ) : null}
            {holFehler ? <span style={{ color: "var(--zu-fehler)" }}>{holFehler}</span> : null}
            {video.recodiert ? (
              <span title="nach AV1 verkleinert" style={{ color: "var(--text-schwach)" }}>
                AV1
              </span>
            ) : null}
          </div>
        </div>
      </div>
    </>
  );

  // Nicht archivierte Positionen bleiben sichtbar, sind aber nicht anklickbar -
  // ein Link, der zu einem schwarzen Player fuehrt, waere schlimmer als keiner.
  return spielbar ? (
    <Link className="kachel" to={`/video/${video.id}`} data-verfuegbar="true">
      {inhalt}
    </Link>
  ) : (
    <div className="kachel" data-verfuegbar="false" title={zustandText(status)}>
      {inhalt}
    </div>
  );
}

export function Gitter({ children, form }: { children: React.ReactNode; form?: "liste" }) {
  return (
    <div className="gitter" data-form={form}>
      {children}
    </div>
  );
}

export function Skelettgitter({ anzahl = 12 }: { anzahl?: number }) {
  return (
    <div className="gitter">
      {Array.from({ length: anzahl }, (_, i) => (
        <div key={i} className="kachel">
          <div className="skelett" style={{ aspectRatio: "16 / 9" }} />
          <div>
            <div className="skelett skelett-zeile" style={{ width: "90%" }} />
            <div className="skelett skelett-zeile" style={{ width: "55%" }} />
          </div>
        </div>
      ))}
    </div>
  );
}

export function Leer({
  zeichen = "📼",
  titel,
  text,
  kinder,
}: {
  zeichen?: string;
  titel: string;
  text?: string;
  kinder?: React.ReactNode;
}) {
  return (
    <div className="leer">
      <div className="zeichen">{zeichen}</div>
      <h2>{titel}</h2>
      {text ? <p>{text}</p> : null}
      {kinder ? <div style={{ marginTop: 20 }}>{kinder}</div> : null}
    </div>
  );
}

export function Hinweis({
  art,
  children,
}: {
  art?: "arbeit" | "fehler";
  children: React.ReactNode;
}) {
  return (
    <div className="hinweis" data-art={art}>
      <div>{children}</div>
    </div>
  );
}

export function Fehler({ text, erneut }: { text: string; erneut?: () => void }) {
  return (
    <Hinweis art="fehler">
      <strong>Das hat nicht geklappt.</strong>
      <div style={{ color: "var(--text-gedaempft)", marginTop: 4 }}>{text}</div>
      {erneut ? (
        <button className="knopf" style={{ marginTop: 12 }} onClick={erneut}>
          Nochmal versuchen
        </button>
      ) : null}
    </Hinweis>
  );
}
