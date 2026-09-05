import { useEffect, useId, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import land from "../data/world-land.json";
import { standortText, type AktiverStream, type GeoIpStatus } from "../lib/wiedergabe";
import "../styles/stream-karte.css";

interface Ausschnitt { x: number; y: number; zoom: number }
export interface Standortgruppe { x: number; y: number; streams: AktiverStream[] }
const WELT = { x: 0, y: 0, zoom: 1 };

export function koordinaten(stream: AktiverStream): { x: number; y: number } | null {
  const geo = stream.geo;
  if (geo?.status !== "located" || typeof geo.latitude !== "number" || typeof geo.longitude !== "number"
    || !Number.isFinite(geo.latitude) || !Number.isFinite(geo.longitude)
    || Math.abs(geo.latitude) > 90 || Math.abs(geo.longitude) > 180) return null;
  return { x: (geo.longitude + 180) * 1000 / 360, y: (90 - geo.latitude) * 500 / 180 };
}

/** Nahe Punkte zusammenfassen; beim Hineinzoomen werden sie wieder getrennt. */
export function standorteGruppieren(streams: AktiverStream[], zoom: number): Standortgruppe[] {
  const gruppen: Standortgruppe[] = [];
  for (const stream of [...streams].sort((a, b) => a.id.localeCompare(b.id))) {
    const punkt = koordinaten(stream);
    if (!punkt) continue;
    const gruppe = gruppen.find((g) => Math.hypot(g.x - punkt.x, g.y - punkt.y) * zoom < 38);
    if (gruppe) {
      const n = gruppe.streams.length;
      gruppe.x = (gruppe.x * n + punkt.x) / (n + 1);
      gruppe.y = (gruppe.y * n + punkt.y) / (n + 1);
      gruppe.streams.push(stream);
    } else gruppen.push({ ...punkt, streams: [stream] });
  }
  return gruppen;
}

function begrenzen(a: Ausschnitt): Ausschnitt {
  const zoom = Math.max(1, Math.min(8, a.zoom));
  return { zoom, x: Math.max(0, Math.min(1000 - 1000 / zoom, a.x)), y: Math.max(0, Math.min(500 - 500 / zoom, a.y)) };
}

export function StreamKarte({ streams, geoip }: { streams: AktiverStream[]; geoip?: GeoIpStatus }) {
  const [ausschnitt, setAusschnitt] = useState<Ausschnitt>(WELT);
  const [auswahl, setAuswahl] = useState<string | null>(null);
  const [zieht, setZieht] = useState(false);
  const svg = useRef<SVGSVGElement>(null);
  const [breite, setBreite] = useState(1000);
  const drag = useRef<{ id: number; x: number; y: number; breite: number; start: Ausschnitt } | null>(null);
  const hinweisId = useId();
  const auswahlId = useId();
  const pixelFaktor = ausschnitt.zoom * breite / 1000;
  const gruppen = useMemo(() => standorteGruppieren(streams, pixelFaktor), [streams, pixelFaktor]);
  const gewaehlt = gruppen.find((g) => g.streams.some((s) => s.id === auswahl));
  const verortet = gruppen.reduce((summe, g) => summe + g.streams.length, 0);
  const privat = streams.filter((s) => s.geo?.status === "private").length;
  const unbekannt = streams.length - verortet - privat;
  useEffect(() => {
    const el = svg.current;
    if (!el) return;
    const messen = () => setBreite(Math.max(1, el.getBoundingClientRect().width));
    messen();
    const beobachter = new ResizeObserver(messen);
    beobachter.observe(el);
    return () => beobachter.disconnect();
  }, []);
  function zoom(faktor: number) {
    setAusschnitt((a) => {
      const neu = Math.max(1, Math.min(8, a.zoom * faktor));
      return begrenzen({ zoom: neu, x: a.x + 500 / a.zoom - 500 / neu, y: a.y + 250 / a.zoom - 250 / neu });
    });
  }
  function verschieben(x: number, y: number) {
    setAusschnitt((a) => begrenzen({ ...a, x: a.x + x / a.zoom, y: a.y + y / a.zoom }));
  }
  function dragEnde() { drag.current = null; setZieht(false); }

  return <section className="stream-karte" aria-label="Standorte der Verbindungen">
    <div className="stream-karte-kopf">
      <h2>Standorte</h2>
      <div className="stream-karte-zoom" role="group" aria-label="Kartenzoom">
        <button className="knopf" aria-label="Karte verkleinern" disabled={ausschnitt.zoom <= 1} onClick={() => zoom(1 / 1.5)}>−</button>
        <output aria-label="Kartenvergrößerung">{Math.round(ausschnitt.zoom * 100)} %</output>
        <button className="knopf" aria-label="Karte vergrößern" disabled={ausschnitt.zoom >= 8} onClick={() => zoom(1.5)}>+</button>
        <button className="knopf" onClick={() => setAusschnitt(WELT)}>Weltansicht</button>
      </div>
    </div>
    <p className="stream-karte-zahlen" role="status">{verortet} auf der Karte · {privat} lokal oder privat · {unbekannt} ohne Standort</p>
    {geoip?.available === false ? <p className="stream-karte-meldung">Standortdaten sind momentan nicht verfügbar. Die Verbindungen werden weiterhin angezeigt.</p> : null}
    <div className="stream-karte-flaeche">
      <svg ref={svg} className="stream-welt" viewBox={`${ausschnitt.x} ${ausschnitt.y} ${1000 / ausschnitt.zoom} ${500 / ausschnitt.zoom}`}
        role="group" aria-label="Weltkarte der aktiven Verbindungen" aria-describedby={hinweisId} tabIndex={0} data-zieht={zieht}
        onKeyDown={(e) => {
          if (e.target !== e.currentTarget) return;
          const schritte: Record<string, [number, number]> = { ArrowLeft: [-60, 0], ArrowRight: [60, 0], ArrowUp: [0, -60], ArrowDown: [0, 60] };
          if (schritte[e.key]) { e.preventDefault(); verschieben(...schritte[e.key]); }
          else if (e.key === "+" || e.key === "=") { e.preventDefault(); zoom(1.5); }
          else if (e.key === "-") { e.preventDefault(); zoom(1 / 1.5); }
          else if (e.key === "Home") { e.preventDefault(); setAusschnitt(WELT); }
        }}
        onPointerDown={(e) => {
          if (e.button !== 0 || (e.target as Element).closest("[data-kartenpunkt]")) return;
          e.currentTarget.setPointerCapture(e.pointerId);
          drag.current = { id: e.pointerId, x: e.clientX, y: e.clientY, breite: e.currentTarget.getBoundingClientRect().width, start: ausschnitt };
          setZieht(true);
        }}
        onPointerMove={(e) => {
          const d = drag.current;
          if (!d || d.id !== e.pointerId || !d.breite) return;
          const faktor = 1000 / d.breite / d.start.zoom;
          setAusschnitt(begrenzen({ ...d.start, x: d.start.x - (e.clientX - d.x) * faktor, y: d.start.y - (e.clientY - d.y) * faktor }));
        }} onPointerUp={dragEnde} onPointerCancel={dragEnde} onLostPointerCapture={dragEnde}>
        <rect width="1000" height="500" className="stream-meer" />
        <g className="stream-land" aria-hidden="true" fillRule="evenodd">{land.map((d, i) => <path d={d} key={i} />)}</g>
        {gruppen.map((g) => {
          const aktiv = g.streams.some((s) => s.id === auswahl);
          const beschreibung = `${g.streams.length} ${g.streams.length === 1 ? "Verbindung" : "Verbindungen"}: ${standortText(g.streams[0].geo)}`;
          return <g key={g.streams[0].id} className="stream-kartenpunkt" data-kartenpunkt data-aktiv={aktiv}
            transform={`translate(${g.x} ${g.y}) scale(${1 / pixelFaktor})`} role="button" tabIndex={0}
            aria-label={beschreibung} aria-pressed={aktiv} aria-controls={auswahlId}
            onClick={() => setAuswahl(g.streams[0].id)} onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setAuswahl(g.streams[0].id); }
            }}>
            <title>{beschreibung}</title><circle className="stream-punkt-treffer" r="22" />
            <circle className="stream-punkt-rand" r="13" /><circle className="stream-punkt" r="10" />
            <text textAnchor="middle" dominantBaseline="central" aria-hidden="true">{g.streams.length}</text>
          </g>;
        })}
      </svg>
    </div>
    <div className="stream-karte-details" id={auswahlId} aria-live="polite">
      {gewaehlt ? <>
        <div className="stream-karte-auswahl"><h3>{gewaehlt.streams.length === 1 ? "Ausgewählte Verbindung" : `${gewaehlt.streams.length} Verbindungen in dieser Gegend`}</h3>
          <button className="knopf" onClick={() => setAuswahl(null)}>Auswahl schließen</button></div>
        <ul>{gewaehlt.streams.map((s) => <li key={s.id}>
          <Link to={`/video/${encodeURIComponent(s.video_id)}`}>{s.video_title}</Link>
          <span>{s.client_name || "Unbekannter Browser"} · {s.client_address || "Unbekannte Adresse"}</span>
          <span>{standortText(s.geo)}</span>
        </li>)}</ul>
      </> : <p>{auswahl ? "Die ausgewählte Verbindung ist nicht mehr auf der Karte." : verortet ? "Wähle einen Punkt, um die Verbindungen und Videos anzuzeigen." : "Zurzeit gibt es keine zuordenbaren Standorte."}</p>}
    </div>
    <p className="stream-karte-hinweis" id={hinweisId}>Standorte sind Näherungswerte; ein VPN kann einen anderen Ort anzeigen. Lokale IP-Adressen lassen sich nicht zuordnen. Karte ziehen oder mit den Pfeiltasten verschieben; + und − zoomen, Pos1 zeigt die Weltansicht.</p>
    <p className="stream-karte-quelle"><a href="https://db-ip.com" target="_blank" rel="noreferrer">IP Geolocation by DB-IP</a>
      {geoip?.database_date ? <span> · Datenstand {geoip.database_date}</span> : null}</p>
  </section>;
}
