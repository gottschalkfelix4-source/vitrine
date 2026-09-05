import { Link } from "react-router-dom";
import { Fehler } from "../components/ui";
import { StreamKarte } from "../components/StreamKarte";
import { useApi } from "../hooks/useApi";
import { dauer } from "../lib/format";
import { standortText, streamsLaden, type AktiverStream, type StreamStatus, type StreamUebersicht } from "../lib/wiedergabe";
import "../styles/streams.css";

const statusText: Record<StreamStatus, string> = { playing: "Läuft", paused: "Pausiert", buffering: "Lädt" };

function uhrzeit(zeit: string): string {
  const datum = new Date(zeit);
  return Number.isFinite(datum.getTime()) ? datum.toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "–";
}

function transkodierungText(stream: AktiverStream): string | null {
  if (stream.mode !== "transcode") return null;
  const geraet = stream.hardware_accel === "qsv" ? "GPU (Intel Quick Sync)"
    : stream.hardware_accel === "vaapi" ? "GPU (VA-API)"
      : stream.hardware_accel === "nvenc" ? "GPU (NVIDIA)"
        : stream.hardware_accel === "none" ? "CPU" : null;
  if (!geraet) return null;
  if (stream.encoder_state === "failed") return `${geraet} · fehlgeschlagen`;
  if (stream.encoder_state === "ready") return `${geraet} · verwendet`;
  if (stream.encoder_state === "running" && stream.segments_ready > 0) return `${geraet} · aktiv`;
  return `${geraet} vorgesehen`;
}

export function StreamListe({ daten }: { daten: StreamUebersicht }) {
  const laufend = daten.streams.filter((s) => s.state === "playing").length;
  const kodierungen = daten.streams.filter((s) => s.transcoding).length;
  return <>
    <p className="streams-ueberblick" role="status">
      {daten.streams.length} {daten.streams.length === 1 ? "Verbindung" : "Verbindungen"} · {laufend} {laufend === 1 ? "Wiedergabe läuft" : "Wiedergaben laufen"} · {kodierungen} {kodierungen === 1 ? "aktive Umwandlung" : "aktive Umwandlungen"}
    </p>
    <StreamKarte streams={daten.streams} geoip={daten.geoip} />
    {daten.streams.length === 0 ? <div className="streams-leer"><h2>Gerade schaut niemand</h2>
      <p>Sobald ein Video geöffnet wird, erscheint die Verbindung hier. Geschlossene Verbindungen verschwinden automatisch.</p></div>
      : <div className="streams-tabelle"><table>
        <thead><tr><th scope="col">Video</th><th scope="col">Verbindung</th><th scope="col">Standort</th><th scope="col">Wiedergabe</th><th scope="col">Auslieferung</th><th scope="col">Zeit</th></tr></thead>
        <tbody>{daten.streams.map((s) => <tr key={s.id}>
          <td data-label="Video"><Link to={`/video/${encodeURIComponent(s.video_id)}`}>{s.video_title}</Link><span>{s.channel_title || "Unbekannter Kanal"}</span></td>
          <td data-label="Verbindung"><strong>{s.client_name || "Unbekannter Browser"}</strong><span>{s.client_address || "Unbekannte Adresse"}</span></td>
          <td data-label="Standort">{standortText(s.geo)}</td>
          <td data-label="Wiedergabe"><strong>{statusText[s.state] ?? "Verbunden"}</strong><span>Position {dauer(s.position_s)}</span></td>
          <td data-label="Auslieferung"><strong>{s.mode === "transcode" ? "Live-Transkodierung" : "Direktwiedergabe"}</strong>
            <span>{s.quality_label ? `${s.quality_label} · ` : ""}{s.mode === "transcode" ? s.transcoding ? "Abschnitt wird umgewandelt" : `${s.segments_ready} Abschnitte vorbereitet` : "Original aus dem Archiv"}</span>
            {transkodierungText(s) ? <span>{transkodierungText(s)}</span> : null}
            {s.mode === "transcode" && s.fallback_reason ? <span>CPU-Fallback: {s.fallback_reason}</span> : null}</td>
          <td data-label="Zeit"><strong>Seit {uhrzeit(s.started_at)}</strong><span>Zuletzt {uhrzeit(s.last_seen_at)}</span></td>
        </tr>)}</tbody>
      </table></div>}
    <p className="streams-hinweis">Maximal {daten.limits.sessions} gleichzeitige Verbindungen und {daten.limits.transcodes} parallele Umwandlungen. Aktualisierung alle fünf Sekunden.</p>
  </>;
}

export function Streamsseite() {
  const { daten, laedt, fehler, neuLaden } = useApi(streamsLaden, [], 5000);
  return <section className="streams-seite">
    <div className="streams-kopf"><div><h1>Streams</h1><p>Laufende Wiedergaben und Verbindungen</p></div>
      <button className="knopf" onClick={neuLaden} disabled={laedt}>Aktualisieren</button></div>
    {fehler ? <Fehler text={fehler} erneut={neuLaden} /> : null}
    {daten ? <StreamListe daten={daten} /> : !fehler ? <p role="status">Verbindungen werden geladen …</p> : null}
  </section>;
}
