/** Zugriff auf das Backend. */

import { faehigkeiten } from "./capabilities";

export interface VideoKurz {
  id: string;
  titel: string;
  kanal_id: string | null;
  kanal_name: string | null;
  dauer_s: number | null;
  hochgeladen: string | null;
  aufrufe: number | null;
  thumb: string | null;
  status: string;
  ist_short: boolean;
  war_live: boolean;
  gesehen: boolean;
  fortschritt_s: number;
  fortschritt_anteil: number | null;
  buendel_bytes: number | null;
  recodiert: boolean;
}

export interface KanalKurz {
  id: string;
  name: string;
  handle: string | null;
  avatar: string | null;
  abonniert: boolean;
  abgleich_aktiv: boolean;
  zuletzt_abgeglichen: string | null;
  videos_gesamt: number;
  videos_archiviert: number;
  belegung_bytes: number;
}

export interface Sammlung {
  id: string;
  titel: string;
  art: "uploads" | "shorts" | "live" | "playlist";
  anzahl: number;
  thumb: string | null;
}

export interface KanalDetail {
  kanal: KanalKurz;
  beschreibung: string | null;
  banner: string | null;
  sammlungen: Sammlung[];
  regeln: {
    auto_archivieren: boolean;
    shorts: boolean;
    livestreams: boolean;
    codec: string;
    abgleich_stunden: number;
  };
}

export interface PlaylistDetail {
  id: string;
  titel: string;
  art: string;
  kanal_id: string | null;
  beschreibung: string | null;
  anzahl_quelle: number;
  anzahl_archiviert: number;
  positionen: { position: number; video: VideoKurz }[];
}

export interface Kapitel {
  titel: string;
  start_s: number;
  ende_s: number | null;
}

export interface VideoDetail {
  video: VideoKurz;
  beschreibung: string | null;
  kapitel: Kapitel[];
  untertitel: { sprache: string; automatisch: boolean }[];
  technik: {
    videocodec: string | null;
    audiocodec: string | null;
    breite: number | null;
    hoehe: number | null;
    fps: number | null;
    recodiert: boolean;
    buendel_bytes: number | null;
    quelle_bytes: number | null;
    gespart_bytes: number | null;
  };
  in_playlists: { id: string; titel: string }[];
  statusmeldung: string | null;
}

export interface Untertitelfund {
  video: VideoKurz;
  start_s: number;
  sprache: string;
  zeile: string;
}

export interface Suchergebnis {
  anfrage: string;
  videos: VideoKurz[];
  im_gesprochenen: Untertitelfund[];
  zu_kurz: boolean;
}

export interface Auftrag {
  id: number;
  art: string;
  ziel: string | null;
  titel: string | null;
  status: string;
  fortschritt: number;
  meldung: string | null;
  fehler: string | null;
  erstellt: string | null;
}

export interface Speicher {
  kaltspeicher: {
    bytes: number;
    videos: number;
    quelle_bytes: number;
    gespart_bytes: number;
  };
  heissspeicher: {
    anzahl: number;
    bytes: number;
    limit_bytes: number;
    in_wiedergabe: number;
  };
  freier_platz: number;
  videos_nach_status: Record<string, number>;
  recodierungen_offen: number;
}

export interface Wiedergabezustand {
  video_id: string;
  archiv_status: string;
  heisskopien: {
    variante: string;
    status: string;
    groesse: number | null;
    laeuft: boolean;
    verfaellt: string | null;
    fehler: string | null;
  }[];
  fortschritt_s: number;
}

export class ApiFehler extends Error {
  constructor(
    public readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

async function hole<T>(pfad: string, init?: RequestInit): Promise<T> {
  const antwort = await fetch(pfad, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!antwort.ok) {
    let text = antwort.statusText;
    try {
      const koerper = await antwort.json();
      text = koerper.detail ?? text;
    } catch {
      /* keine JSON-Antwort */
    }
    throw new ApiFehler(antwort.status, text);
  }
  if (antwort.status === 204) return undefined as T;
  return (await antwort.json()) as T;
}

function frage(werte: Record<string, string | number | boolean | undefined>): string {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(werte)) {
    if (v !== undefined && v !== "") p.set(k, String(v));
  }
  const s = p.toString();
  return s ? `?${s}` : "";
}

export const api = {
  kanaele: () => hole<KanalKurz[]>("/api/channels"),
  kanal: (id: string) => hole<KanalDetail>(`/api/channels/${id}`),
  kanalAnlegen: (daten: {
    url: string;
    sofort_archivieren: boolean;
    shorts: boolean;
    livestreams: boolean;
  }) => hole<KanalKurz>("/api/channels", { method: "POST", body: JSON.stringify(daten) }),
  kanalAbgleichen: (id: string, voll = false) =>
    hole<{ job_id: number }>(`/api/channels/${id}/sync${frage({ voll })}`, { method: "POST" }),

  playlist: (id: string) => hole<PlaylistDetail>(`/api/playlists/${id}`),

  videos: (opt: {
    kanal?: string;
    status?: string;
    suche?: string;
    nur_archiviert?: boolean;
    sortierung?: "neu" | "alt" | "aufrufe" | "titel";
    limit?: number;
    offset?: number;
  } = {}) => hole<VideoKurz[]>(`/api/videos${frage(opt)}`),
  video: (id: string) => hole<VideoDetail>(`/api/videos/${id}`),
  videoArchivieren: (id: string) =>
    hole<{ job_id: number }>(`/api/videos/${id}/archive`, { method: "POST" }),

  fortschrittMerken: (id: string, sekunden: number, gesehen?: boolean) =>
    hole<void>(`/api/videos/${id}/progress`, {
      method: "PUT",
      body: JSON.stringify({ sekunden, gesehen }),
    }),

  wiedergabezustand: (id: string) => hole<Wiedergabezustand>(`/api/videos/${id}/playback-state`),
  herzschlag: (id: string) =>
    fetch(`/api/videos/${id}/heartbeat`, { method: "POST", keepalive: true }),
  wiedergabeBeendet: (id: string) =>
    // keepalive, damit die Meldung auch dann noch rausgeht, wenn der Tab
    // gerade geschlossen wird - sonst bliebe die Heisskopie bis zum Ablauf
    // der langen Frist liegen.
    fetch(`/api/videos/${id}/playback-ended`, { method: "POST", keepalive: true }),

  auftraege: (status?: string) => hole<Auftrag[]>(`/api/jobs${frage({ status })}`),
  auftragAbbrechen: (id: number) => hole<void>(`/api/jobs/${id}/cancel`, { method: "POST" }),
  auftragWiederholen: (id: number) => hole<void>(`/api/jobs/${id}/retry`, { method: "POST" }),

  speicher: () => hole<Speicher>("/api/storage"),

  suchen: (q: string, limit = 40) => hole<Suchergebnis>(`/api/search${frage({ q, limit })}`),
  suchindexNeuAufbauen: () =>
    hole<Record<string, number>>("/api/search/reindex", { method: "POST" }),
};

/** Adresse des Videostroms, inklusive der gemeldeten Client-Faehigkeiten. */
export function streamUrl(videoId: string): string {
  return `/api/videos/${videoId}/stream?support=${encodeURIComponent(faehigkeiten())}`;
}

export function thumbUrl(datei: string | null): string | null {
  return datei ? `/api/thumbs/${encodeURIComponent(datei)}` : null;
}

export function untertitelUrl(videoId: string, sprache: string): string {
  return `/api/videos/${videoId}/subtitles/${encodeURIComponent(sprache)}`;
}
