import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { expect, it } from "vitest";
import { StreamListe } from "./Streams";
import { koordinaten, standorteGruppieren } from "../components/StreamKarte";
import type { AktiverStream, StreamStandort } from "../lib/wiedergabe";

const ort: StreamStandort = { status: "located", latitude: 52.52, longitude: 13.405, city: "Berlin", region: "Berlin", country: "Deutschland", country_code: "DE" };
function verbindung(id: string, geo?: StreamStandort): AktiverStream {
  return { id, video_id: id, video_title: `Video ${id}`, channel_title: "Kanal", client_address: "8.8.8.8", client_name: "Firefox",
    mode: "direct", state: "playing", position_s: 10, started_at: "2026-09-05T12:00:00Z", last_seen_at: "2026-09-05T12:02:00Z",
    transcoding: false, segments_ready: 0, geo };
}

it("zeigt nur tatsächliche Verbindungen und einen ehrlichen Leerzustand", () => {
  const leer = renderToStaticMarkup(<MemoryRouter><StreamListe daten={{ streams: [], limits: { sessions: 64, transcodes: 2 } }} /></MemoryRouter>);
  expect(leer).toContain("Gerade schaut niemand");
  expect(leer).not.toContain("<table");
});

it("unterscheidet pausierte Direktwiedergabe und aktive Transkodierung ohne Roh-HTML", () => {
  const stream = { id: "1", video_id: "video1", video_title: "<script>kein Code</script>", channel_title: "Kanal",
    client_address: "192.168.1.2", client_name: "Firefox", position_s: 24, started_at: "2026-09-05T12:00:00Z",
    last_seen_at: "2026-09-05T12:02:00Z", segments_ready: 2 };
  const html = renderToStaticMarkup(<MemoryRouter><StreamListe daten={{ streams: [
    { ...stream, mode: "direct", state: "paused", transcoding: false },
    { ...stream, id: "2", mode: "transcode", state: "playing", transcoding: true },
  ], limits: { sessions: 64, transcodes: 2 } }} /></MemoryRouter>);
  expect(html).toContain("Pausiert");
  expect(html).toContain("Live-Transkodierung");
  expect(html).toContain("192.168.1.2");
  expect(html).toContain("1 aktive Umwandlung");
  expect(html).not.toContain("<script>");
});

function auslieferung(stream: Partial<AktiverStream>) {
  return renderToStaticMarkup(<MemoryRouter><StreamListe daten={{ streams: [{ ...verbindung("encoder"), ...stream }],
    limits: { sessions: 64, transcodes: 2 } }} /></MemoryRouter>);
}

it.each([
  ["qsv", "h264_qsv", "GPU (Intel Quick Sync)"],
  ["vaapi", "h264_vaapi", "GPU (VA-API)"],
  ["nvenc", "h264_nvenc", "GPU (NVIDIA)"],
] as const)("benennt erfolgreiche %s-Transkodierung und übernimmt die Qualität des Servers", (hardware_accel, encoder, label) => {
  const html = auslieferung({ mode: "transcode", hardware_accel, encoder, encoder_state: "running", segments_ready: 1,
    transcoding: true, quality_label: "480p" });
  expect(html).toContain(`${label} · aktiv`);
  expect(html).toContain("480p · Abschnitt wird umgewandelt");
});

it.each([
  ["pending", 0, "GPU (Intel Quick Sync) vorgesehen"],
  ["running", 0, "GPU (Intel Quick Sync) vorgesehen"],
  ["ready", 1, "GPU (Intel Quick Sync) · verwendet"],
  ["failed", 0, "GPU (Intel Quick Sync) · fehlgeschlagen"],
] as const)("behauptet beim Encoderzustand %s keine unbelegte GPU-Nutzung", (encoder_state, segments_ready, text) => {
  const html = auslieferung({ mode: "transcode", hardware_accel: "qsv", encoder: "h264_qsv", encoder_state, segments_ready });
  expect(html).toContain(text);
  expect(html).not.toContain("GPU (Intel Quick Sync) · aktiv");
});

it("zeigt CPU-Fallback samt unveränderter, als Text behandelter Serverbegründung", () => {
  const html = auslieferung({ mode: "transcode", hardware_accel: "none", encoder: "libx264", encoder_state: "running",
    segments_ready: 1, fallback_reason: "GPU nicht verfügbar <Test>" });
  expect(html).toContain("CPU · aktiv");
  expect(html).toContain("CPU-Fallback: GPU nicht verfügbar &lt;Test&gt;");
  expect(html).not.toContain("<Test>");
});

it("zeigt bei Direktwiedergabe die Serverqualität ohne Encoder oder Fallback", () => {
  const html = auslieferung({ quality_label: "Original (720p)", encoder: null, hardware_accel: null, encoder_state: "direct",
    fallback_reason: "Nicht für Direktwiedergabe" });
  expect(html).toContain("Original (720p) · Original aus dem Archiv");
  expect(html).not.toContain("CPU");
  expect(html).not.toContain("GPU");
  expect(html).not.toContain("Nicht für Direktwiedergabe");
});

it("gruppiert nahe Verbindungen stabil und trennt sie beim Hineinzoomen", () => {
  const streams = [verbindung("a", ort), verbindung("b", ort), verbindung("c", { ...ort, longitude: 23.4 })];
  expect(standorteGruppieren(streams, 1)).toHaveLength(1);
  const nah = standorteGruppieren(streams, 4);
  expect(nah).toHaveLength(2);
  expect(nah[0].streams.map((s) => s.id)).toEqual(["a", "b"]);
  expect(standorteGruppieren([...streams].reverse(), 4)).toEqual(nah);
});

it("verwendet die Projektion der Basiskarte einschließlich gültiger Nullkoordinaten", () => {
  expect(koordinaten(verbindung("a", { ...ort, latitude: 0, longitude: 0 }))).toEqual({ x: 500, y: 250 });
  expect(koordinaten(verbindung("b", { ...ort, latitude: 90, longitude: -180 }))).toEqual({ x: 0, y: 0 });
  expect(koordinaten(verbindung("c", { ...ort, latitude: -90, longitude: 180 }))).toEqual({ x: 1000, y: 500 });
});

it.each([
  { ...ort, latitude: null }, { ...ort, longitude: null }, { ...ort, latitude: NaN },
  { ...ort, longitude: Infinity }, { ...ort, latitude: 91 }, { ...ort, longitude: -181 },
  { ...ort, status: "private" as const }, { ...ort, status: "unknown" as const }, { ...ort, status: "unavailable" as const },
])("zeichnet keinen Ersatzpunkt für ungültige oder nicht zuordenbare Koordinaten (%j)", (geo) => {
  expect(koordinaten(verbindung("a", geo))).toBeNull();
  expect(standorteGruppieren([verbindung("a", geo)], 1)).toEqual([]);
});

it("zeigt markierte Verbindungen, private und unbekannte Adressen getrennt und nennt DB-IP sichtbar", () => {
  const html = renderToStaticMarkup(<MemoryRouter><StreamListe daten={{ streams: [
    verbindung("a", ort), verbindung("b", ort), verbindung("c", { ...ort, status: "private", latitude: null, longitude: null }), verbindung("d"),
  ], limits: { sessions: 64, transcodes: 2 }, geoip: { available: true, database_date: "2026-09-01" } }} /></MemoryRouter>);
  expect(html).toContain("2 auf der Karte · 1 lokal oder privat · 1 ohne Standort");
  expect(html).toContain('aria-label="2 Verbindungen: Berlin, Deutschland"');
  expect(html.match(/data-kartenpunkt="true"/g)).toHaveLength(1);
  expect(html).toContain('href="https://db-ip.com"');
  expect(html).toContain("IP Geolocation by DB-IP");
  expect(html).toContain("Datenstand 2026-09-01");
  expect(html).toContain("Standorte sind Näherungswerte");
  expect(html).toContain("Lokales oder privates Netzwerk");
  expect(html).not.toContain("<iframe");
  expect(html).not.toContain("<img");
});

it("zeigt bei fehlender Datenbank weiter Verbindungen und behauptet keinen Standort", () => {
  const html = renderToStaticMarkup(<MemoryRouter><StreamListe daten={{ streams: [verbindung("a", { ...ort, status: "unavailable", latitude: null, longitude: null })],
    limits: { sessions: 64, transcodes: 2 }, geoip: { available: false, database_date: null } }} /></MemoryRouter>);
  expect(html).toContain("Standortdaten sind momentan nicht verfügbar");
  expect(html).toContain("Video a");
  expect(html).toContain("0 auf der Karte · 0 lokal oder privat · 1 ohne Standort");
  expect(html).not.toContain("data-kartenpunkt");
});
