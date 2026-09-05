import { authFetch, auswerten } from "./auth";
import { faehigkeiten } from "./capabilities";

export type WiedergabeQualitaet = "auto" | "original" | "1080p" | "720p" | "480p" | "360p" | "240p";
export interface Qualitaetsangebot { value: WiedergabeQualitaet; label: string }

export interface Wiedergabesitzung {
  token: string;
  mode: "direct" | "transcode";
  url: string;
  duration_s: number | null;
  segment_seconds: number;
  reason: string;
  quality: WiedergabeQualitaet;
  quality_label: string;
  available_qualities: Qualitaetsangebot[];
}

export type StreamStatus = "playing" | "paused" | "buffering";

async function senden<T>(pfad: string, daten: object, keepalive = false): Promise<T> {
  return auswerten<T>(await authFetch(pfad, {
    method: "POST", headers: { "Content-Type": "application/json", "X-Vitrine-Request": "1" },
    body: JSON.stringify(daten), keepalive,
  }, true));
}

export function wiedergabeStarten(videoId: string, transkodieren = false, quality: WiedergabeQualitaet = "auto"): Promise<Wiedergabesitzung> {
  return senden(`/api/videos/${encodeURIComponent(videoId)}/playback`, {
    support: faehigkeiten(), force_transcode: transkodieren, quality,
  });
}

export function wiedergabeMelden(token: string, sekunden: number, status: StreamStatus, keepalive = false): Promise<void> {
  return senden(`/api/playback/${encodeURIComponent(token)}/heartbeat`, {
    position_s: Number.isFinite(sekunden) ? Math.max(0, sekunden) : 0, state: status,
  }, keepalive);
}

export function wiedergabeBeenden(token: string): Promise<void> {
  return senden(`/api/playback/${encodeURIComponent(token)}/ended`, {}, true);
}

export interface StreamStandort {
  status: "located" | "private" | "unknown" | "unavailable";
  latitude: number | null;
  longitude: number | null;
  city: string | null;
  region: string | null;
  country: string | null;
  country_code: string | null;
}

export interface GeoIpStatus { available: boolean; database_date: string | null }

export function standortText(geo?: StreamStandort): string {
  if (geo?.status === "private") return "Lokales oder privates Netzwerk";
  if (geo?.status === "unavailable") return "Standortdaten nicht verfügbar";
  if (geo?.status !== "located") return "Standort unbekannt";
  return [...new Set([geo.city, geo.region, geo.country].filter(Boolean))].join(", ") || "Ungefährer Standort";
}

export interface AktiverStream {
  id: string;
  video_id: string;
  video_title: string;
  channel_title: string | null;
  client_address: string;
  client_name: string;
  mode: "direct" | "transcode";
  state: StreamStatus;
  position_s: number;
  started_at: string;
  last_seen_at: string;
  transcoding: boolean;
  segments_ready: number;
  geo?: StreamStandort;
}

export interface StreamUebersicht {
  streams: AktiverStream[];
  limits: { sessions: number; transcodes: number };
  geoip?: GeoIpStatus;
}

export async function streamsLaden(): Promise<StreamUebersicht> {
  return auswerten<StreamUebersicht>(await authFetch("/api/streams"));
}
