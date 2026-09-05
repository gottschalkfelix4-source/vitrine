import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";

import { Icon } from "../components/Icons";
import { KanalAvatar } from "../components/KanalAvatar";
import { Player } from "../components/Player";
import { useAdmin } from "../components/Anmeldung";
import { Fehler, Gitter, Skelettgitter, Videokachel, Zustand } from "../components/ui";
import { useApi } from "../hooks/useApi";
import { api } from "../lib/api";
import { lokalFortschrittLesen } from "../lib/wiedergabeFortschritt";
import { aufrufe, bytes, dauer, datum, istHochaufloesend, prozent, qualitaet } from "../lib/format";
import "../styles/watch.css";

export function Wiedergabeseite() {
  const admin = useAdmin();
  const { videoId = "" } = useParams();
  const [parameter] = useSearchParams();
  const sprungparameter = parameter.get("t");
  const sprungziel = sprungparameter === null ? null : Number(sprungparameter);
  const [beschreibungOffen, setBeschreibungOffen] = useState(false);
  const [aktivesKapitel, setAktivesKapitel] = useState<number | null>(null);
  const [technikOffen, setTechnikOffen] = useState(false);
  const [theater, setTheater] = useState(false);
  const [entfernenNachfrage, setEntfernenNachfrage] = useState(false);
  const [entfernenLaeuft, setEntfernenLaeuft] = useState(false);
  const [entfernenFehler, setEntfernenFehler] = useState<string | null>(null);
  const playerBereich = useRef<HTMLDivElement>(null);
  const navigate = useNavigate();

  const detail = useApi(() => api.video(videoId), [videoId]);
  const kanalId = detail.daten?.video.id === videoId ? detail.daten.video.kanal_id : null;
  const weitere = useApi(
    () => kanalId ? api.videos({ kanal: kanalId, nur_archiviert: true, limit: 16 }) : Promise.resolve([]),
    [kanalId],
  );

  useEffect(() => {
    setBeschreibungOffen(false);
    setTechnikOffen(false);
    setAktivesKapitel(null);
    setEntfernenNachfrage(false);
    setEntfernenFehler(null);
  }, [videoId]);

  const beiKapitel = useCallback((i: number | null) => setAktivesKapitel(i), []);

  if (detail.fehler) return <Fehler text={detail.fehler} erneut={detail.neuLaden} />;
  // useApi behält Daten während des Nachladens. Beim Video-Wechsel darf der
  // neue Player nicht mit Titel und Fortschritt des vorherigen Videos starten.
  if (!detail.daten || detail.daten.video.id !== videoId) {
    return <div className="watch-laden" aria-label="Video wird geladen"><div className="skelett watch-laden-bild" /><Skelettgitter anzahl={3} /></div>;
  }

  const { video: v, technik, kapitel, untertitel, beschreibung, in_playlists, statusmeldung } = detail.daten;
  const lokal = admin ? null : lokalFortschrittLesen(videoId);
  const guete = qualitaet(technik.breite, technik.hoehe, technik.fps);
  const ersparnis = technik.quelle_bytes && technik.buendel_bytes
    ? 1 - technik.buendel_bytes / technik.quelle_bytes : null;
  const andere = (weitere.daten ?? []).filter((x) => x.id !== videoId && x.kanal_id === kanalId).slice(0, 10);
  const sprungSekunde = sprungziel !== null && Number.isFinite(sprungziel) && sprungziel >= 0
    ? sprungziel : undefined;

  function springe(sekunde: number) {
    const el = playerBereich.current?.querySelector("video");
    if (el) {
      el.currentTime = sekunde;
      void el.play().catch(() => { /* Manuelles Abspielen bleibt möglich. */ });
    }
  }

  async function entfernen() {
    if (!admin) return;
    setEntfernenLaeuft(true);
    setEntfernenFehler(null);
    try {
      await api.videoEntfernen(videoId);
      navigate(v.kanal_id ? `/kanal/${v.kanal_id}` : "/");
    } catch (e) {
      setEntfernenFehler(e instanceof Error ? e.message : String(e));
    } finally {
      setEntfernenLaeuft(false);
    }
  }

  return (
    <div className="watch watch-seite" data-theater={theater}>
      <div className="watch-player" ref={playerBereich}>
        <Player
          key={videoId}
          videoId={videoId}
          startSekunde={admin ? v.fortschritt_s : lokal?.sekunden ?? 0}
          sprungSekunde={sprungSekunde}
          dauerS={v.dauer_s}
          poster={v.bild ?? undefined}
          titel={v.titel}
          kapitel={kapitel}
          untertitel={untertitel}
          aufKapitel={beiKapitel}
          theater={theater}
          aufTheater={setTheater}
        />
      </div>

      <div className="watch-information">
        <h1>{v.titel}</h1>
        <div className="watch-kanalzeile">
          {v.kanal_id ? (
            <Link to={`/kanal/${v.kanal_id}`} className="watch-kanal">
              <KanalAvatar kanalId={v.kanal_id} name={v.kanal_name} className="watch-avatar" />
              <span className="watch-kanaltext"><strong>{v.kanal_name ?? "Zum Kanal"}</strong><span>Zum Kanal</span></span>
            </Link>
          ) : null}
          <div className="watch-aktionen">
            {(admin ? v.gesehen : lokal?.gesehen) ? <span className="watch-gesehen"><Icon name="check" size={20} />Gesehen</span> : null}
            <button className="knopf" onClick={() => setTechnikOffen(!technikOffen)} aria-expanded={technikOffen} aria-controls="watch-technik">
              <Icon name="info" size={20} />Technik
            </button>
            {admin && v.status === "archived" ? (
              <button className="knopf" onClick={() => setEntfernenNachfrage(!entfernenNachfrage)} aria-label="Aus dem Archiv entfernen" aria-expanded={entfernenNachfrage} aria-controls="watch-entfernen">
                <Icon name="trash" size={20} />Entfernen
              </button>
            ) : null}
          </div>
        </div>

        {admin && entfernenNachfrage ? (
          <div className="watch-entfernen" id="watch-entfernen">
            <p><strong>Video aus dem Archiv entfernen?</strong><br />Die Dateien werden gelöscht. Der Eintrag bleibt beim Kanal.</p>
            {entfernenFehler ? <div role="alert" className="watch-fehler">{entfernenFehler}</div> : null}
            <div className="watch-entfernen-aktionen">
              <button className="knopf" disabled={entfernenLaeuft} onClick={() => setEntfernenNachfrage(false)}>Abbrechen</button>
              <button className="knopf" data-art="gefahr-stark" disabled={entfernenLaeuft} onClick={() => void entfernen()}>
                {entfernenLaeuft ? "Wird entfernt …" : "Ja, entfernen"}
              </button>
            </div>
          </div>
        ) : null}

        <section className="watch-beschreibung" aria-label="Videobeschreibung">
          <div className="watch-beschreibung-meta">
            {v.aufrufe !== null ? <strong>{aufrufe(v.aufrufe)}</strong> : null}
            {v.hochgeladen ? <strong>{datum(v.hochgeladen)}</strong> : null}
            {v.status === "archived" ? <span className="watch-archiviert"><Icon name="check" size={16} />Archiviert</span> : <Zustand status={v.status} />}
          </div>
          {guete ? (
            <div className="watch-qualitaet">
              <span className="guete-marke" data-hoch={istHochaufloesend(technik.breite, technik.hoehe) || undefined}>{guete}</span>
              <span>{technik.breite}×{technik.hoehe}{technik.fps ? ` · ${Math.round(technik.fps)} Bilder/s` : ""}{technik.videocodec ? ` · ${technik.videocodec.toUpperCase()}` : ""}{technik.recodiert ? " · verkleinert" : ""}</span>
            </div>
          ) : null}
          <div id="watch-beschreibung-text" className="watch-beschreibung-text" data-offen={beschreibungOffen}>
            {beschreibung || "Keine Beschreibung vorhanden."}
          </div>
          {beschreibung ? <button className="watch-mehr" onClick={() => setBeschreibungOffen(!beschreibungOffen)} aria-expanded={beschreibungOffen} aria-controls="watch-beschreibung-text">{beschreibungOffen ? "Weniger anzeigen" : "Mehr anzeigen"}</button> : null}
          {admin && statusmeldung ? <p className="watch-statusmeldung">{statusmeldung}</p> : null}
        </section>

        {technikOffen ? (
          <section className="watch-technik" id="watch-technik" aria-label="Technische Angaben">
            <h2>Wie dieses Video gespeichert ist</h2>
            <dl>
              <div><dt>Codec</dt><dd>{technik.videocodec ?? "Unbekannt"} / {technik.audiocodec ?? "Unbekannt"}{technik.recodiert ? " (verkleinert)" : " (Original)"}</dd></div>
              <div><dt>Auflösung</dt><dd>{technik.breite && technik.hoehe ? `${technik.breite}×${technik.hoehe}` : "Unbekannt"}{technik.fps ? ` @ ${Math.round(technik.fps)} fps` : ""}</dd></div>
              <div><dt>Bündel</dt><dd>{bytes(technik.buendel_bytes)}</dd></div>
              {technik.quelle_bytes ? <div><dt>Quelle beim Download</dt><dd>{bytes(technik.quelle_bytes)}{ersparnis && ersparnis > 0.01 ? <span> · {prozent(ersparnis)} gespart</span> : null}</dd></div> : null}
            </dl>
          </section>
        ) : null}
      </div>

      <aside className="watch-neben" aria-label="Kapitel und weitere Videos">
        {kapitel.length > 0 ? (
          <section className="watch-kapitel">
            <h2>Kapitel <span>{kapitel.length}</span></h2>
            <div className="kapitel">
              {kapitel.map((k, i) => (
                <button key={i} data-aktiv={aktivesKapitel === i} aria-current={aktivesKapitel === i ? "true" : undefined} onClick={() => springe(k.start_s)}>
                  <span className="watch-kapitel-nummer">{aktivesKapitel === i ? <Icon name="play" size={14} /> : i + 1}</span>
                  <span className="watch-kapitel-text"><span>{k.titel}</span><span className="zeit">{dauer(k.start_s)}</span></span>
                </button>
              ))}
            </div>
          </section>
        ) : null}

        {in_playlists.length > 0 ? (
          <section className="watch-playlists">
            <h2>In diesen Playlists</h2>
            {in_playlists.map((p) => (
              <Link key={p.id} to={`/playlist/${p.id}`}><Icon name="playlist" size={22} /><span>{p.titel}</span><Icon name="chevronRight" size={18} /></Link>
            ))}
          </section>
        ) : null}

        <section className="watch-empfehlungen">
          <h2>Mehr von {v.kanal_name ?? "diesem Kanal"}</h2>
          {weitere.fehler ? <Fehler text={weitere.fehler} erneut={weitere.neuLaden} /> : weitere.laedt && andere.length === 0 ? <Skelettgitter anzahl={3} /> : andere.length === 0 ? (
            <p className="watch-leer">Noch keine weiteren archivierten Videos dieses Kanals.{" "}{v.kanal_id ? <Link to={`/kanal/${v.kanal_id}`}>Zum Kanal</Link> : null}</p>
          ) : <Gitter form="liste">{andere.map((x) => <Videokachel key={x.id} video={x} />)}</Gitter>}
        </section>
      </aside>
    </div>
  );
}
