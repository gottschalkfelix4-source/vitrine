import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import type { VideoKurz } from "../lib/api";
import { api } from "../lib/api";
import { aufrufe, dauer, istHochaufloesend, qualitaet, vorZeit, zustandText } from "../lib/format";
import { Icon } from "./Icons";
import { KanalAvatar } from "./KanalAvatar";
import { useAdmin } from "./Anmeldung";
import { lokalFortschrittLesen } from "../lib/wiedergabeFortschritt";

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
  const admin = useAdmin();
  const lokal = admin ? null : lokalFortschrittLesen(video.id);
  const anteil = admin ? video.fortschritt_anteil : lokal && video.dauer_s ? lokal.sekunden / video.dauer_s : null;
  // Nach dem Klick auf "Laden" zeigt die Kachel sofort "wartet", ohne dass
  // die ganze Liste neu geholt werden muss.
  const [neuerStatus, setNeuerStatus] = useState<string | null>(null);
  const [holFehler, setHolFehler] = useState<string | null>(null);
  const [holt, setHolt] = useState(false);
  useEffect(() => { setNeuerStatus(null); }, [video.id, video.status]);
  const status = neuerStatus ?? video.status;
  const spielbar = status === "archived";
  // Erst nach dem Archivieren bekannt - vorher nennt YouTube beim Auflisten
  // keine Aufloesung.
  const guete = qualitaet(video.breite, video.hoehe, video.fps);
  const bild = video.bild;

  async function holen(e: React.MouseEvent) {
    e.preventDefault();
    e.stopPropagation();
    if (!admin || holt) return;
    setHolt(true);
    setHolFehler(null);
    try {
      await api.videoArchivieren(video.id);
      setNeuerStatus("queued");
    } catch (err) {
      setHolFehler(err instanceof Error ? err.message : String(err));
    } finally {
      setHolt(false);
    }
  }
  const vorschaubild = (
    <>
      <div className="kachel-bild">
        {bild ? (
          <img src={bild} alt="" loading="lazy" />
        ) : (
          <div className="platzhalter"><Icon name="play" size={40} /></div>
        )}
        {/* Qualitaet oben, Dauer unten - so ueberdecken sie sich nie, auch
            nicht bei "1440p60" neben "1:02:33". */}
        {guete ? (
          <span className="guete" data-hoch={istHochaufloesend(video.breite, video.hoehe) || undefined}>
            {guete}
          </span>
        ) : null}
        {video.dauer_s ? <span className="dauer">{dauer(video.dauer_s)}</span> : null}
        {anteil ? (
          <div className="fortschritt">
            <span style={{ width: `${Math.min(100, anteil * 100)}%` }} />
          </div>
        ) : null}
        {spielbar ? <span className="kachel-abspielen"><Icon name="play" size={28} /></span> : null}
      </div>
    </>
  );
  const titel = status === "unavailable" && video.titel === "(ohne Titel)"
    ? "Gelöscht oder privat gestellt" : video.titel;

  return (
    <article className="kachel" data-verfuegbar={spielbar}>
      {spielbar ? <Link className="kachel-vorschau" to={`/video/${video.id}`} aria-label={`${titel} abspielen`} tabIndex={-1}>
        {vorschaubild}
      </Link> : <div className="kachel-vorschau">{vorschaubild}</div>}
      <div className="kachel-text">
        {!ohneKanal && video.kanal_id ? <Link className="kachel-kanalbild" to={`/kanal/${video.kanal_id}`} aria-label={video.kanal_name ?? "Zum Kanal"} tabIndex={-1}>
          <KanalAvatar kanalId={video.kanal_id} name={video.kanal_name} />
        </Link> : null}
        <div className="kachel-details" style={{ minWidth: 0, flex: 1 }}>
          <h3 className="kachel-titel" title={video.titel}>
            {position != null ? <span style={{ color: "var(--text-schwach)" }}>{position + 1}. </span> : null}
            {/* "(ohne Titel)" sagt nicht, was los ist. Bei einem verschwundenen
                Video ist der fehlende Titel gerade die Aussage - dann lieber
                benennen, was passiert ist. */}
            {spielbar ? <Link to={`/video/${video.id}`}>{titel}</Link> : titel}
          </h3>
          <div className="kachel-zeile">
            {!ohneKanal && video.kanal_name ? (
              <Link to={`/kanal/${video.kanal_id}`} onClick={(e) => e.stopPropagation()}>
                {video.kanal_name}
              </Link>
            ) : null}
          </div>
          <div className="kachel-zeile">
            {video.aufrufe != null ? <span>{aufrufe(video.aufrufe)}</span> : null}
            {video.hochgeladen ? (
              <span className={video.aufrufe != null ? "punkt" : undefined}>{vorZeit(video.hochgeladen)}</span>
            ) : null}
          </div>
          <div className="kachel-zeile kachel-archiv">
            {!spielbar ? <Zustand status={status} /> : null}
            {admin && HOLBAR.has(status) ? (
              <button className="kachel-laden" onClick={holen} disabled={holt} title="Dieses Video ins Archiv holen">
                <Icon name="download" size={16} />{holt ? "Wird eingereiht …" : "Laden"}
              </button>
            ) : null}
            {holFehler ? <span role="alert" style={{ color: "var(--zu-fehler)" }}>{holFehler}</span> : null}
            {video.recodiert ? (
              <span title="nach AV1 verkleinert" style={{ color: "var(--text-schwach)" }}>
                AV1
              </span>
            ) : null}
          </div>
        </div>
      </div>
    </article>
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
  zeichen,
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
      <div className="zeichen" aria-hidden="true">{zeichen ?? <Icon name="channels" size={48} />}</div>
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
    <div className="hinweis" data-art={art} role={art === "fehler" ? "alert" : "status"}>
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
