import { alsAppGestartet } from "../pwa";

/** Wie lange nach einem Größenwechsel weiter nachgemessen wird. iOS meldet
 *  direkt nach dem Drehen noch die Maße der alten Lage - teils mehrere hundert
 *  Millisekunden lang. Wer nur einmal misst, schreibt genau diesen falschen
 *  Wert fest, und niemand korrigiert ihn je wieder: Ein weiteres Ereignis
 *  kommt ja nicht. Deshalb wird über die ganze Umbauphase jedes Bild gemessen
 *  und der jeweils neueste Wert übernommen. */
const NACHLAUF_BILDER = 60;

/** Pinch-Zoom verkleinert den sichtbaren Ausschnitt, nicht das App-Layout;
 *  solange er anhält, bleibt die zuletzt gemessene Höhe stehen.
 *
 *  Erkannt wird er an den Breiten, nicht an viewport.scale. Das iPhone meldet
 *  in der PWA nach dem Drehen ins Querformat dauerhaft eine Skala von Breite
 *  geteilt durch Höhe (956/440 = 2,17), obwohl nichts vergrößert ist: Fenster,
 *  sichtbarer Ausschnitt und Layout sind alle 956 breit. Mit der Skala als
 *  Maßstab galt quer jede Messung als Zoom, die Höhe blieb bei den 894 des
 *  Hochformats, und die Hülle ragte 454 Pixel unter den Bildschirmrand - die
 *  Seite wurde scrollbar, obwohl sie das nie sein soll. Ein echter Zoom zeigt
 *  sich daran, dass der sichtbare Ausschnitt schmaler ist als das Layout;
 *  während des Drehens kann der Ausschnitt ein paar Bilder lang noch die alte
 *  Breite haben, das übergeht die Schleife von selbst. */
function gezoomt(viewport: VisualViewport): boolean {
  const layout = document.documentElement.clientWidth;
  if (layout > 0) return viewport.width < layout * 0.99;
  return Math.abs(viewport.scale - 1) > 0.01;
}

let scrollKorrekturen = 0;

/** Wie oft ein unmöglicher Scrollstand des Dokuments zurückgesetzt wurde;
 *  zum Ablesen in der Diagnoseanzeige des Players. */
export function dokumentScrollKorrekturen(): number {
  return scrollKorrekturen;
}

/** Das Dokument scrollt in der App nie, das übernimmt der Inhaltsbereich.
 *  Nach dem Zurückdrehen ins Hochformat steht window.scrollY auf dem iPhone
 *  trotzdem auf 62 - der Höhe der Statusleiste -, obwohl das Dokument keinen
 *  Überlauf hat: Die Hülle liegt rechnerisch 62 Pixel über dem Bildschirm,
 *  gezeichnet wird sie oben bündig. Jede Berührung trifft damit, was 62 Pixel
 *  tiefer gezeichnet ist, in der ganzen App und dauerhaft; von selbst räumt
 *  WebKit den Stand nie auf. Ein Scrollstand jenseits des Spielraums ist in
 *  keinem gesunden Zustand möglich und wird deshalb zurückgesetzt. */
function dokumentZurueckscrollen() {
  const de = document.documentElement;
  const spielraum = Math.max(0, de.scrollHeight - de.clientHeight);
  if (!(window.scrollY > spielraum)) return;
  scrollKorrekturen++;
  window.scrollTo(0, spielraum);
}

/** Hält die PWA samt unterer Navigation innerhalb des sichtbaren Webviews. */
export function appViewportBeobachten(): () => void {
  if (!alsAppGestartet()) return () => {};

  const stil = document.documentElement.style;
  const viewport = window.visualViewport;
  let frame: number | null = null;
  let offen = 0;
  let beendet = false;

  function messen() {
    dokumentZurueckscrollen();
    if (viewport && gezoomt(viewport)) return;
    // offsetTop berücksichtigt das Verschieben beim Öffnen der Tastatur.
    // Nie über innerHeight hinausgehen, auch wenn iOS einen veralteten
    // VisualViewport meldet. Safe Areas sind bereits Teil dieser Höhe.
    const unten = viewport ? viewport.offsetTop + viewport.height : window.innerHeight;
    const hoehe = Math.floor(Math.min(window.innerHeight, unten));
    if (Number.isFinite(hoehe) && hoehe > 0) stil.setProperty("--app-viewport-hoehe", `${hoehe}px`);
  }

  function schleife() {
    frame = null;
    if (beendet) return;
    messen();
    if (--offen > 0) frame = window.requestAnimationFrame(schleife);
  }

  function aktualisieren() {
    offen = NACHLAUF_BILDER;
    if (frame === null && !beendet) frame = window.requestAnimationFrame(schleife);
  }

  messen();
  window.addEventListener("resize", aktualisieren);
  window.addEventListener("orientationchange", aktualisieren);
  window.addEventListener("pageshow", aktualisieren);
  window.addEventListener("scroll", aktualisieren, { passive: true });
  window.screen?.orientation?.addEventListener("change", aktualisieren);
  viewport?.addEventListener("resize", aktualisieren);
  viewport?.addEventListener("scroll", aktualisieren);
  return () => {
    beendet = true;
    window.removeEventListener("resize", aktualisieren);
    window.removeEventListener("orientationchange", aktualisieren);
    window.removeEventListener("pageshow", aktualisieren);
    window.removeEventListener("scroll", aktualisieren);
    window.screen?.orientation?.removeEventListener("change", aktualisieren);
    viewport?.removeEventListener("resize", aktualisieren);
    viewport?.removeEventListener("scroll", aktualisieren);
    if (frame !== null) window.cancelAnimationFrame(frame);
    stil.removeProperty("--app-viewport-hoehe");
  };
}
