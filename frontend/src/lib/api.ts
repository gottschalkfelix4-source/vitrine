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
  /** Fertige Adresse des Vorschaubilds, kein Dateiname - nicht durch thumbUrl schicken. */
  bild: string | null;
  /** Aufloesung der abgelegten Datei. Erst nach dem Archivieren bekannt. */
  hoehe: number | null;
  breite: number | null;
  fps: number | null;
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
  /** Zaehler je Videoart, aus der Datenbank - nicht aus der geladenen Seite. */
  zaehler: { videos: number; shorts: number; live: number };
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

export interface LaufenderAuftrag {
  id: number;
  art: string;
  ziel: string | null;
  titel: string | null;
  fortschritt: number;
  meldung: string | null;
}

/**
 * Zwangspause, weil YouTube die IP-Adresse abweist ("not a bot", HTTP 429).
 *
 * Muss sichtbar sein: Eine Pause und ein hängender Dienst sehen von außen
 * gleich aus - tausend Aufträge auf "wartet", keiner läuft.
 */
export interface Drosselung {
  /** Wahr erst, wenn KEIN Ausgang mehr frei ist - siehe `ausgaenge`. */
  pausiert: boolean;
  rest_s: number;
  /** ISO-Zeitpunkt, an dem es weitergeht. */
  bis: string | null;
  stufe: number;
  grund: string | null;
  /** Welcher Ausgang als nächster wieder darf. */
  ausgang?: string | null;
}

/**
 * Zustand der hinterlegten Cookie-Datei.
 *
 * Cookies sind der einzige Weg, YouTube gegenüber angemeldet aufzutreten -
 * yt-dlp lehnt Passwort- und OAuth-Anmeldung ausdrücklich ab. Entsprechend
 * viel hängt an einer Textdatei, die auf drei Arten kaputt sein kann, ohne
 * dass man es ihr ansieht.
 */
export interface CookieZustand {
  /** Datei vorhanden UND als Anmeldung brauchbar. */
  brauchbar: boolean;
  meldung: string;
  angemeldet: boolean;
  laeuft_ab: string | null;
  rest_s: number | null;
  bald_abgelaufen: boolean;
  /** Wie viele YouTube-Cookies drinstehen. */
  anzahl: number;
  /** Welche der Anmelde-Cookies gefunden wurden. */
  gefunden: string[];
  vorhanden: boolean;
  /** Es gilt ein per Umgebungsvariable gesetzter Pfad, nicht der Upload. */
  eigener_pfad: boolean;
}

export interface CookieProbe {
  erfolg: boolean;
  pausiert?: boolean;
  video_id?: string;
  titel?: string | null;
  angebotene_guete?: number | null;
  meldung: string;
}

/**
 * Sperrzustand eines einzelnen Ausgangs.
 *
 * Die Leiter ist je Ausgang eigen: Ein oft gesperrter Tunnel darf einen
 * frischen nicht mitbelasten.
 */
export interface AusgangSperre {
  gesperrt: boolean;
  rest_s: number;
  bis: string | null;
  stufe: number;
  grund: string | null;
}

/** Ein eingerichteter WireGuard-Tunnel samt gemessener Wirkung. */
export interface VpnTunnel {
  id: number;
  name: string;
  endpunkt: string | null;
  /** Aus heißt: bleibt im Bestand, ist aber aus der Rotation genommen. */
  aktiv: boolean;
  /** Prozess läuft und der Proxy nimmt Verbindungen an. */
  laeuft: boolean;
  /**
   * Es ist nachweislich etwas durchgekommen - nur das zählt für die Rotation.
   *
   * Ein offener Port ist kein Beweis: wireproxy bindet ihn, sobald es die
   * Datei gelesen hat, ganz gleich ob das Gegenüber je antwortet.
   */
  bereit: boolean;
  port: number | null;
  /** Die gemessene öffentliche Adresse - die einzige Auskunft, die zählt. */
  exit_ip: string | null;
  /** Wie viele Aufträge ihn gerade benutzen. */
  belegt: number;
  fehler: string | null;
  sperre: AusgangSperre | null;
}

export interface VpnZustand {
  aktiv: boolean;
  nur_tunnel: boolean;
  /** Pfad des wireproxy-Programms, null = nicht installiert. */
  wireproxy: string | null;
  tunnel: VpnTunnel[];
  bereit: number;
  /** Tunnel, die unter derselben Adresse herauskommen - kein Zugewinn. */
  doppelte_adressen: string[];
  direkt: { benutzt: boolean; sperre: AusgangSperre | null };
}

export interface VpnProbe {
  erfolg: boolean;
  ip?: string;
  dauer_s?: number;
  meldung: string;
}

/** Ein echter Probe-Encode, kein Anzeichen. */
export interface HardwareProbe {
  beschleunigung: string;
  encoder: string;
  erfolg: boolean;
  dauer_s: number | null;
  /** Videolänge geteilt durch Rechenzeit. 1 = Echtzeit. */
  tempo: number | null;
  meldung: string;
}

export interface HardwareZustand {
  /** Render-Knoten in /dev/dri. Leer = Karte nicht durchgereicht. */
  geraete: string[];
  treiber: string | null;
  treiber_vorhanden: boolean;
  eingestellt: string;
  proben: HardwareProbe[];
  meldung: string;
}

/**
 * Was „Alle laden" tatsächlich getan hat.
 *
 * Die bloße Zahl war irreführend: Wer mehrmals klickt, will wissen, warum beim
 * zweiten Mal nichts mehr dazukommt - nicht bloß „0 eingereiht".
 */
export interface AlleLadenErgebnis {
  eingereiht: number;
  wartete_schon: number;
  laeuft_gerade: number;
  bereits_archiviert: number;
  nicht_verfuegbar: number;
  /** Durch die Kanalregeln ausgeschlossen (Shorts, Livestreams, Datum). */
  regeln: number;
}

export interface UpgradeVorschau {
  ziel: number;
  videos: number;
  jetzt_bytes: number;
  geschaetzt_bytes: number;
  zusatz_bytes: number;
  freier_platz: number;
  passt: boolean | null;
  /** Wie viele Videos je bisheriger Stufe betroffen sind. */
  nach_stufe: Record<string, number>;
  stunden_mindestens: number;
}

export interface EinstellungsFeld {
  name: string;
  gruppe: string;
  titel: string;
  beschreibung: string;
  art: "int" | "float" | "bool" | "text" | "auswahl" | "liste";
  wert: unknown;
  /** Woher der geltende Wert kommt - die Datenbank gewinnt ueber die Umgebung. */
  herkunft: "datenbank" | "umgebung" | "standard";
  neustart: boolean;
  min: number | null;
  max: number | null;
  auswahl: string[];
  einheit: string | null;
  standard: unknown;
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
    recodiert: number;
    dauer_s: number;
    /** Gemessener eigener Schnitt - Grundlage der Hochrechnung. */
    bytes_je_sekunde: number;
  };
  heissspeicher: {
    anzahl: number;
    bytes: number;
    limit_bytes: number;
    in_wiedergabe: number;
  };
  freier_platz: number;
  traeger: { pfad: string; gesamt: number; belegt: number; frei: number }[];
  videos_nach_status: Record<string, number>;
  recodierungen_offen: number;
  je_kanal: { id: string; name: string; videos: number; bytes: number }[];
  groesste: { id: string; titel: string; bytes: number | null; kanal: string | null }[];
  hochrechnung: {
    offene_videos: number;
    offene_dauer_s: number;
    bytes_geschaetzt: number;
    /** false = grobe Annahme, weil noch nichts archiviert ist. */
    gemessen: boolean;
  };
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

async function auswerten<T>(antwort: Response): Promise<T> {
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

async function hole<T>(pfad: string, init?: RequestInit): Promise<T> {
  return auswerten<T>(
    await fetch(pfad, {
      ...init,
      headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    }),
  );
}

/**
 * Schickt eine Datei als multipart.
 *
 * Bewusst nicht über `hole`: Das setzt `Content-Type: application/json`, und
 * bei multipart muss der Browser den Kopf selbst setzen - er trägt die
 * Trennmarke, die er sich gerade ausgedacht hat. Von Hand gesetzt fehlt sie,
 * und der Server findet keine Datei.
 */
async function sendeDatei<T>(
  pfad: string,
  datei: File,
  felder?: Record<string, string>,
): Promise<T> {
  const koerper = new FormData();
  koerper.append("datei", datei);
  for (const [k, v] of Object.entries(felder ?? {})) koerper.append(k, v);
  return auswerten<T>(await fetch(pfad, { method: "POST", body: koerper }));
}

function frage(werte: Record<string, string | number | boolean | undefined>): string {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(werte)) {
    if (v !== undefined && v !== "") p.set(k, String(v));
  }
  const s = p.toString();
  return s ? `?${s}` : "";
}

// Bewusst ein Typalias, kein Interface: Nur Aliase bekommen die implizite
// Indexsignatur, die frage() fuer die Umwandlung in Abfrageparameter braucht.
export type VideoAbfrage = {
  kanal?: string;
  status?: string;
  suche?: string;
  nur_archiviert?: boolean;
  /** "videos" heisst: weder Short noch Livestream. */
  art?: "videos" | "shorts" | "live";
  sortierung?: "neu" | "alt" | "aufrufe" | "titel";
  limit?: number;
  offset?: number;
};

export const api = {
  kanaele: () => hole<KanalKurz[]>("/api/channels"),
  kanalLoeschen: (id: string, dateien: boolean) =>
    hole<{ videos_entfernt: number; bytes_freigegeben: number; buendel_geloescht: boolean }>(
      `/api/channels/${id}${frage({ dateien })}`,
      { method: "DELETE" },
    ),
  kanal: (id: string) => hole<KanalDetail>(`/api/channels/${id}`),
  kanalAnlegen: (daten: {
    url: string;
    sofort_archivieren: boolean;
    shorts: boolean;
    livestreams: boolean;
  }) => hole<KanalKurz>("/api/channels", { method: "POST", body: JSON.stringify(daten) }),
  kanalOffene: (id: string) =>
    hole<{ anzahl: number; dauer_s: number; bytes_geschaetzt: number }>(
      `/api/channels/${id}/downloadable`,
    ),
  kanalAlleLaden: (id: string) =>
    hole<AlleLadenErgebnis>(`/api/channels/${id}/download-all`, { method: "POST" }),
  kanalAbgleichen: (id: string, voll = false) =>
    hole<{ job_id: number }>(`/api/channels/${id}/sync${frage({ voll })}`, { method: "POST" }),

  playlist: (id: string) => hole<PlaylistDetail>(`/api/playlists/${id}`),

  videos: (opt: VideoAbfrage = {}) => hole<VideoKurz[]>(`/api/videos${frage(opt)}`),
  video: (id: string) => hole<VideoDetail>(`/api/videos/${id}`),
  videoArchivieren: (id: string) =>
    hole<{ job_id: number }>(`/api/videos/${id}/archive`, { method: "POST" }),
  /** Nimmt die Dateien aus dem Archiv; der Eintrag bleibt beim Kanal. */
  videoEntfernen: (id: string) =>
    hole<{ video_id: string; bytes_freigegeben: number; status: string }>(`/api/videos/${id}`, {
      method: "DELETE",
    }),

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
  aktiveAuftraege: () =>
    hole<{
      laufend: LaufenderAuftrag[];
      wartend: number;
      /** Wartende Aufträge je Art - ein Video erzeugt im Lauf seines Lebens mehrere. */
      nach_art: Record<string, number>;
      drosselung: Drosselung;
      /** Wie viele Wege ins Netz es gibt und wie viele davon gerade frei sind. */
      ausgaenge: { gesamt: number; frei: number };
    }>("/api/jobs/aktiv"),
  upgradeVorschau: (ziel: number, kanal?: string) =>
    hole<UpgradeVorschau>(
      `/api/upgrade/vorschau?ziel=${ziel}${kanal ? `&kanal=${encodeURIComponent(kanal)}` : ""}`,
    ),
  upgradeEinreihen: (ziel: number, kanal?: string) =>
    hole<{ eingereiht: number; ziel: number }>(
      `/api/upgrade?ziel=${ziel}${kanal ? `&kanal=${encodeURIComponent(kanal)}` : ""}`,
      { method: "POST" },
    ),
  auftragAbbrechen: (id: number) => hole<void>(`/api/jobs/${id}/cancel`, { method: "POST" }),
  auftragWiederholen: (id: number) => hole<void>(`/api/jobs/${id}/retry`, { method: "POST" }),
  alleGescheitertenWiederholen: () =>
    hole<{ auftraege: number; videos: number }>("/api/jobs/retry-failed", { method: "POST" }),

  hardware: () => hole<HardwareZustand>("/api/hardware"),
  hardwareTesten: () => hole<HardwareZustand>("/api/hardware/test", { method: "POST" }),

  vpn: () => hole<VpnZustand>("/api/vpn"),
  vpnHochladen: (datei: File, name?: string) =>
    sendeDatei<VpnZustand & { id: number }>("/api/vpn", datei, name ? { name } : undefined),
  vpnAendern: (id: number, aenderungen: { name?: string; aktiv?: boolean }) =>
    hole<VpnZustand>(`/api/vpn/${id}`, { method: "PUT", body: JSON.stringify(aenderungen) }),
  vpnEntfernen: (id: number) => hole<VpnZustand>(`/api/vpn/${id}`, { method: "DELETE" }),
  vpnTesten: (id: number) => hole<VpnProbe>(`/api/vpn/${id}/test`, { method: "POST" }),
  vpnTestenDirekt: () => hole<VpnProbe>("/api/vpn/test-direkt", { method: "POST" }),

  cookies: () => hole<CookieZustand>("/api/cookies"),
  cookiesHochladen: (datei: File) => sendeDatei<CookieZustand>("/api/cookies", datei),
  cookiesEntfernen: () => hole<CookieZustand>("/api/cookies", { method: "DELETE" }),
  cookiesTesten: () => hole<CookieProbe>("/api/cookies/test", { method: "POST" }),

  speicher: () => hole<Speicher>("/api/storage"),

  einstellungen: () =>
    hole<{ gruppen: string[]; felder: EinstellungsFeld[] }>("/api/settings"),
  einstellungenSpeichern: (aenderungen: Record<string, unknown>) =>
    hole<{ geaendert: string[]; neustart_noetig: string[] }>("/api/settings", {
      method: "PUT",
      body: JSON.stringify(aenderungen),
    }),
  einstellungenZuruecksetzen: (namen: string[]) =>
    hole<{ zurueckgesetzt: string[] }>("/api/settings/reset", {
      method: "POST",
      body: JSON.stringify(namen),
    }),

  suchen: (q: string, limit = 40) => hole<Suchergebnis>(`/api/search${frage({ q, limit })}`),
  suchindexNeuAufbauen: () =>
    hole<Record<string, number>>("/api/search/reindex", { method: "POST" }),
};

/** Adresse des Videostroms, inklusive der gemeldeten Client-Faehigkeiten. */
export function streamUrl(videoId: string): string {
  return `/api/videos/${videoId}/stream?support=${encodeURIComponent(faehigkeiten())}`;
}

/**
 * Adresse eines abgelegten Bildes: Avatar, Banner, Playlist-Vorschau.
 *
 * Fuer Videos NICHT benutzen - deren Bild kann auch von der Quelle
 * nachgeladen werden, und welcher Weg gilt, entscheidet der Server. Es
 * steht fertig in `video.bild`.
 */
export function thumbUrl(datei: string | null): string | null {
  return datei ? `/api/thumbs/${encodeURIComponent(datei)}` : null;
}

export function untertitelUrl(videoId: string, sprache: string): string {
  return `/api/videos/${videoId}/subtitles/${encodeURIComponent(sprache)}`;
}
