/**
 * Was kann dieser Browser wirklich abspielen?
 *
 * Der Server koennte das aus dem User-Agent zu erraten versuchen. Das bricht
 * aber bei jedem Browser-Update und geht bei Smart-TVs und Set-Top-Boxen fast
 * immer daneben. Also fragen wir den Browser selbst - der weiss es genau.
 *
 * Das Ergebnis geht als `support`-Parameter an den Stream-Endpunkt. Kann der
 * Client den Archivcodec, kommen die Bytes direkt aus dem Buendel; sonst wird
 * eine Heisskopie vorbereitet.
 *
 * Groessenordnung: Rund 91 % der Sitzungen koennen AV1. Der Umweg ueber die
 * Heisskopie ist also die Ausnahme - im Wesentlichen aeltere Apple-Geraete und
 * alte Fernseher.
 */

/** Pruefstrings: Merkmal -> die MIME-Typen, mit denen wir es abklopfen. */
const PRUEFUNGEN: Record<string, string[]> = {
  // Behaelter
  mp4: ['video/mp4; codecs="avc1.42E01E"', "video/mp4"],
  webm: ['video/webm; codecs="vp8"', "video/webm"],
  // Videocodecs
  h264: ['video/mp4; codecs="avc1.42E01E"', 'video/mp4; codecs="avc1.640028"'],
  av01: ['video/mp4; codecs="av01.0.05M.08"', 'video/webm; codecs="av01.0.05M.08"'],
  vp09: ['video/webm; codecs="vp09.00.10.08"', 'video/webm; codecs="vp9"'],
  hevc: ['video/mp4; codecs="hvc1.1.6.L93.B0"', 'video/mp4; codecs="hev1.1.6.L93.B0"'],
  // Toncodecs
  aac: ['audio/mp4; codecs="mp4a.40.2"'],
  opus: ['audio/webm; codecs="opus"', 'audio/ogg; codecs="opus"'],
};

let zwischengespeichert: string | null = null;

function kannAbspielen(typ: string): boolean {
  // canPlayType liefert "probably", "maybe" oder "". "maybe" heisst, dass der
  // Browser den Behaelter kennt, aber ueber die Codecs nichts sagen will -
  // das ist zu unsicher, um darauf ein Archivformat auszuliefern.
  try {
    const el = document.createElement("video");
    if (el.canPlayType(typ) === "probably") return true;
  } catch {
    /* in ungewoehnlichen Umgebungen ohne DOM-Video */
  }
  // MediaSource ist die genauere Quelle - sie antwortet nur mit ja oder nein.
  try {
    const ms = (window as unknown as { MediaSource?: { isTypeSupported(t: string): boolean } })
      .MediaSource;
    if (ms?.isTypeSupported(typ)) return true;
  } catch {
    /* ignorieren */
  }
  return false;
}

/**
 * Ermittelt die Faehigkeiten als Kommaliste, z. B. "mp4,webm,h264,av01,opus,aac".
 *
 * Das Ergebnis wird gemerkt: Es aendert sich waehrend einer Sitzung nicht, und
 * die Pruefung legt fuer jeden Aufruf ein Video-Element an.
 */
export function faehigkeiten(): string {
  if (zwischengespeichert !== null) return zwischengespeichert;

  const koennen: string[] = [];
  for (const [merkmal, typen] of Object.entries(PRUEFUNGEN)) {
    if (typen.some(kannAbspielen)) koennen.push(merkmal);
  }

  // Sicherheitsnetz: Meldet ein Browser gar nichts - etwa weil er in einer
  // Umgebung ohne echtes Video-Element laeuft -, lieber die konservative
  // Annahme schicken als eine leere Liste. Leer bedeutet serverseitig
  // ohnehin "nimm H.264", aber so steht es ausdruecklich da.
  if (koennen.length === 0) return (zwischengespeichert = "mp4,h264,aac");

  zwischengespeichert = koennen.join(",");
  return zwischengespeichert;
}

/** Nur fuer die Anzeige unter "Technik" auf der Wiedergabeseite. */
export function faehigkeitenLesbar(): string[] {
  return faehigkeiten().split(",");
}
