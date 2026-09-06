/**
 * Browser-Vollbild über Standard-API und WebKit-Ergänzungen.
 * Der Player verwendet auf Touch-Geräten stattdessen App-Vollbild, damit
 * automatische Drehung und eigene Bedienelemente auch ohne native API gehen.
 */

/** Die Hersteller-Ergänzungen stehen in keiner Typdefinition. */
interface WebkitElement extends HTMLElement {
  webkitRequestFullscreen?: () => Promise<void> | void;
}

interface WebkitVideo extends HTMLVideoElement {
  webkitEnterFullscreen?: () => void;
  webkitExitFullscreen?: () => void;
  /** Nur iOS: true, solange Apples Player läuft. */
  webkitDisplayingFullscreen?: boolean;
}

interface WebkitDocument extends Document {
  webkitFullscreenElement?: Element | null;
  webkitExitFullscreen?: () => Promise<void> | void;
}

export type VollbildWeg = "standard" | "webkit-element" | "webkit-video" | "keiner";

export function wegErmitteln(huelle: HTMLElement | null, video: HTMLVideoElement | null): VollbildWeg {
  if (huelle && typeof huelle.requestFullscreen === "function") return "standard";
  if (huelle && typeof (huelle as WebkitElement).webkitRequestFullscreen === "function") {
    return "webkit-element";
  }
  if (video && typeof (video as WebkitVideo).webkitEnterFullscreen === "function") {
    return "webkit-video";
  }
  return "keiner";
}

/** Läuft gerade irgendeine Form von Vollbild? */
export function istVollbild(video: HTMLVideoElement | null): boolean {
  const d = document as WebkitDocument;
  if (d.fullscreenElement || d.webkitFullscreenElement) return true;
  return (video as WebkitVideo | null)?.webkitDisplayingFullscreen === true;
}

/**
 * Dreht das Gerät ins Querformat, wenn das Video breiter als hoch ist.
 *
 * Ein 16:9-Video im Hochformat-Vollbild ist ein Streifen in der Bildmitte -
 * technisch Vollbild, praktisch nutzlos. Ein hochkantiges Short bleibt dagegen
 * ausdrücklich unberührt, dort wäre Querformat genau falsch.
 *
 * Best effort: Auf dem Schreibtisch und unter iOS gibt es die Sperre nicht,
 * dort wirft der Aufruf. Das ist kein Fehler und wird verschluckt.
 */
async function insQuerformat(video: HTMLVideoElement | null): Promise<void> {
  if (!video || !video.videoWidth || video.videoWidth <= video.videoHeight) return;
  try {
    const o = screen.orientation as ScreenOrientation & {
      lock?: (r: string) => Promise<void>;
    };
    await o.lock?.("landscape");
  } catch {
    /* Gerät oder Browser kann das nicht - dann eben nicht. */
  }
}

function drehungFreigeben(): void {
  try {
    screen.orientation?.unlock?.();
  } catch {
    /* siehe oben */
  }
}

/** Schaltet Vollbild ein oder aus. Liefert den benutzten Weg. */
export async function vollbildUmschalten(
  huelle: HTMLElement | null,
  video: HTMLVideoElement | null,
): Promise<VollbildWeg> {
  const weg = wegErmitteln(huelle, video);
  const d = document as WebkitDocument;

  if (istVollbild(video)) {
    drehungFreigeben();
    if (d.fullscreenElement) await d.exitFullscreen();
    else if (d.webkitFullscreenElement) await d.webkitExitFullscreen?.();
    else (video as WebkitVideo | null)?.webkitExitFullscreen?.();
    return weg;
  }

  switch (weg) {
    case "standard":
      await huelle!.requestFullscreen();
      break;
    case "webkit-element":
      await (huelle as WebkitElement).webkitRequestFullscreen!();
      break;
    case "webkit-video":
      // Kein await: Auf iOS liefert das nichts zurück, und ein Warten darauf
      // würde den Klick-Kontext verlassen, an dem Safari die Erlaubnis
      // festmacht.
      (video as WebkitVideo).webkitEnterFullscreen!();
      return weg;
    case "keiner":
      return weg;
  }

  void insQuerformat(video);
  return weg;
}
