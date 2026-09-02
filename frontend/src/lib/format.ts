/** Zahlen- und Zeitformate, durchgehend auf Deutsch. */

/** Sekunden als 4:07 bzw. 1:02:33 - so, wie es auf dem Vorschaubild steht. */
export function dauer(sekunden: number | null | undefined): string {
  if (sekunden == null || !Number.isFinite(sekunden) || sekunden < 0) return "";
  const s = Math.floor(sekunden % 60);
  const m = Math.floor((sekunden / 60) % 60);
  const h = Math.floor(sekunden / 3600);
  const zz = (n: number) => String(n).padStart(2, "0");
  return h > 0 ? `${h}:${zz(m)}:${zz(s)}` : `${m}:${zz(s)}`;
}

const zahl = new Intl.NumberFormat("de-DE");

/** Aufrufe kompakt: 1234 -> "1234", 12345 -> "12.345", 1200000 -> "1,2 Mio." */
export function aufrufe(n: number | null | undefined): string {
  if (n == null) return "";
  if (n >= 1_000_000) {
    const mio = n / 1_000_000;
    return `${mio.toFixed(mio < 10 ? 1 : 0).replace(".", ",")} Mio. Aufrufe`;
  }
  if (n >= 10_000) return `${Math.round(n / 1000)} Tsd. Aufrufe`;
  return `${zahl.format(n)} Aufrufe`;
}

const EINHEITEN: [number, Intl.RelativeTimeFormatUnit][] = [
  [60, "second"],
  [60, "minute"],
  [24, "hour"],
  [7, "day"],
  [4.34524, "week"],
  [12, "month"],
  [Number.POSITIVE_INFINITY, "year"],
];

const relativ = new Intl.RelativeTimeFormat("de-DE", { numeric: "auto" });

/** "vor 3 Tagen", "vor 2 Jahren" - wie in der Videozeile unter dem Titel. */
export function vorZeit(iso: string | null | undefined): string {
  if (!iso) return "";
  const dann = new Date(iso).getTime();
  if (Number.isNaN(dann)) return "";
  let wert = (dann - Date.now()) / 1000;
  for (const [teiler, einheit] of EINHEITEN) {
    if (Math.abs(wert) < teiler) return relativ.format(Math.round(wert), einheit);
    wert /= teiler;
  }
  return "";
}

export function datum(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleDateString("de-DE", { day: "2-digit", month: "long", year: "numeric" });
}

/**
 * Bytes in lesbarer Form. Bewusst mit den Praefixen KB/MB/GB in
 * 1024er-Schritten - so rechnen Dateimanager und NAS-Oberflaechen, und genau
 * damit vergleicht der Nutzer.
 */
export function bytes(n: number | null | undefined, nachkomma = 1): string {
  if (n == null || !Number.isFinite(n)) return "–";
  if (n === 0) return "0 B";
  const einheiten = ["B", "KB", "MB", "GB", "TB", "PB"];
  const i = Math.min(einheiten.length - 1, Math.floor(Math.log(Math.abs(n)) / Math.log(1024)));
  const wert = n / 1024 ** i;
  return `${wert.toFixed(i === 0 ? 0 : nachkomma).replace(".", ",")} ${einheiten[i]}`;
}

/**
 * Kurzes Qualitätsetikett: "4K", "1080p", "1080p60".
 *
 * Gemessen wird an der **Höhe**, nicht an der kürzeren Seite. Das ist bei
 * hochkantigen Videos ein Unterschied: Ein Short mit 608×1080 hätte nach der
 * kurzen Seite "608p", nach der Höhe "1080p". Die Höhe ist hier richtig, weil
 * das ganze Archiv auf dieser Achse rechnet — die Einstellung heißt
 * `archive_min_height`, der Formatwähler fordert `height>=1080` an, und die
 * Prüfung auf stille 360p-Rückfälle vergleicht ebenfalls die Höhe. Ein Video,
 * das die Untergrenze von 1080p erfüllt hat, darf hier nicht als "608p"
 * erscheinen; das würde der eigenen Einstellung widersprechen.
 *
 * Liefert `null`, solange nichts bekannt ist — vor dem Herunterladen nennt
 * YouTube beim Auflisten keine Auflösung.
 */
export function qualitaet(
  hoehe: number | null | undefined,
  fps?: number | null,
): string | null {
  if (!hoehe || !Number.isFinite(hoehe) || hoehe <= 0) return null;
  const stufe = hoehe >= 4320 ? "8K" : hoehe >= 2160 ? "4K" : `${Math.round(hoehe)}p`;
  // Nur echte Hochbildraten anhängen. 29,97 ist der Normalfall und würde als
  // "1080p30" nur Platz kosten, ohne etwas zu sagen.
  return fps && fps >= 50 ? `${stufe}${Math.round(fps)}` : stufe;
}

/** Ist das eine Auflösung, die man als hochauflösend hervorhebt? */
export function istHochaufloesend(hoehe: number | null | undefined): boolean {
  return !!hoehe && hoehe >= 1440;
}

export function prozent(anteil: number | null | undefined, nachkomma = 0): string {
  if (anteil == null || !Number.isFinite(anteil)) return "–";
  return `${(anteil * 100).toFixed(nachkomma).replace(".", ",")} %`;
}

/** Beschriftungen der Archivzustaende. */
export const ZUSTAND_TEXT: Record<string, string> = {
  new: "neu",
  queued: "wartet",
  downloading: "wird geladen",
  remuxing: "wird umgepackt",
  encoding: "wird verkleinert",
  bundling: "wird gebündelt",
  archived: "archiviert",
  failed: "fehlgeschlagen",
  unavailable: "nicht mehr bei der Quelle",
  skipped: "übersprungen",
};

export const AUFTRAG_TEXT: Record<string, string> = {
  channel_sync: "Kanalabgleich",
  playlist_sync: "Playlist-Abgleich",
  video_archive: "Archivierung",
  video_recode: "Verkleinerung",
  video_prepare: "Vorbereitung",
};

export function zustandText(status: string): string {
  return ZUSTAND_TEXT[status] ?? status;
}
