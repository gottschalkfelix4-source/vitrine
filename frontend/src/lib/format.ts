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

/**
 * Eine Wartezeit in Worten: "40 Sekunden", "5 Minuten", "1 Stunde 5 Minuten",
 * "45 Tage".
 *
 * Bewusst nicht `dauer()`: Ein Countdown als "1:05:00" liest sich wie eine
 * Videolänge. Wer wissen will, wann es weitergeht, will keine Uhr entziffern.
 *
 * Die Einheit wächst mit der Spanne, und das ist keine Kosmetik. Dieselbe
 * Funktion beziffert zwei sehr verschiedene Dinge: eine Drosselpause von
 * Minuten und die Restlaufzeit einer Cookie-Datei von Wochen. Ohne die
 * Tagesstufe stand da "noch 1080 Stunden" – eine Zahl, die niemand in ein
 * Datum umrechnet.
 */
export function wartedauer(sekunden: number | null | undefined): string {
  if (sekunden == null || !Number.isFinite(sekunden) || sekunden <= 0) return "gleich";
  const gerundet = Math.round(sekunden);
  if (gerundet < 60) return `${gerundet} Sekunden`;
  const minuten = Math.round(gerundet / 60);
  if (minuten < 60) return `${minuten} ${minuten === 1 ? "Minute" : "Minuten"}`;
  // Ab zwei Tagen sagen Stunden nichts mehr. Darunter bleiben sie die
  // genauere Auskunft - "noch 1 Tag" wäre bei 30 Stunden irreführend rund.
  if (minuten >= 48 * 60) {
    const tage = Math.round(minuten / (24 * 60));
    return `${tage} ${tage === 1 ? "Tag" : "Tage"}`;
  }
  const stunden = Math.floor(minuten / 60);
  const rest = minuten % 60;
  const std = `${stunden} ${stunden === 1 ? "Stunde" : "Stunden"}`;
  return rest ? `${std} ${rest} ${rest === 1 ? "Minute" : "Minuten"}` : std;
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
 * Gemessen wird die **kürzere Seite** — so, wie YouTube selbst zählt. Für ein
 * hochkantiges Video liefert es das Format 1080×1920 mit der Angabe "1080p";
 * die Höhe ist dort 1920 und wäre als Etikett schlicht falsch.
 *
 * Das war hier zuerst anders gelöst, mit der Höhe. Die Begründung lautete,
 * das ganze Archiv rechne auf dieser Achse — was stimmte, aber nichts wert
 * war: Die Achse selbst war der Fehler. Sie hat im Betrieb reihenweise
 * einwandfreie Downloads senkrechter Videos verworfen, mit Meldungen wie
 * "nur 1280p erhalten, obwohl die Quelle 1920p anbietet". Beides sind Höhen
 * von 720p- und 1080p-Videos im Hochformat.
 *
 * Liefert `null`, solange nichts bekannt ist — vor dem Herunterladen nennt
 * YouTube beim Auflisten keine Auflösung.
 */
export function qualitaet(
  breite: number | null | undefined,
  hoehe: number | null | undefined,
  fps?: number | null,
): string | null {
  const seiten = [breite, hoehe].filter(
    (n): n is number => typeof n === "number" && Number.isFinite(n) && n > 0,
  );
  if (seiten.length === 0) return null;
  const kurz = Math.round(Math.min(...seiten));
  const stufe = kurz >= 4320 ? "8K" : kurz >= 2160 ? "4K" : `${kurz}p`;
  // Nur echte Hochbildraten anhängen. 29,97 ist der Normalfall und würde als
  // "1080p30" nur Platz kosten, ohne etwas zu sagen.
  return fps && fps >= 50 ? `${stufe}${Math.round(fps)}` : stufe;
}

/** Ist das eine Auflösung, die man als hochauflösend hervorhebt? */
export function istHochaufloesend(
  breite: number | null | undefined,
  hoehe: number | null | undefined,
): boolean {
  const seiten = [breite, hoehe].filter(
    (n): n is number => typeof n === "number" && Number.isFinite(n) && n > 0,
  );
  return seiten.length > 0 && Math.min(...seiten) >= 1440;
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
  video_upgrade: "Qualität anheben",
};

export function zustandText(status: string): string {
  return ZUSTAND_TEXT[status] ?? status;
}
