import { alsAppGestartet } from "../pwa";

/** Hält die PWA samt unterer Navigation innerhalb des sichtbaren Webviews. */
export function appViewportBeobachten(): () => void {
  if (!alsAppGestartet()) return () => {};

  const stil = document.documentElement.style;
  const viewport = window.visualViewport;
  let frame: number | null = null;

  function messen() {
    // Pinch-Zoom verkleinert den sichtbaren Ausschnitt, nicht das App-Layout.
    if (viewport && Math.abs(viewport.scale - 1) > 0.01) return;
    // offsetTop berücksichtigt das Verschieben beim Öffnen der Tastatur.
    // Nie über innerHeight hinausgehen, auch wenn iOS einen veralteten
    // VisualViewport meldet. Safe Areas sind bereits Teil dieser Höhe.
    const unten = viewport ? viewport.offsetTop + viewport.height : window.innerHeight;
    const hoehe = Math.floor(Math.min(window.innerHeight, unten));
    if (Number.isFinite(hoehe) && hoehe > 0) stil.setProperty("--app-viewport-hoehe", `${hoehe}px`);
  }

  function aktualisieren() {
    if (frame !== null) return;
    frame = window.requestAnimationFrame(() => { frame = null; messen(); });
  }

  messen();
  window.addEventListener("resize", aktualisieren);
  window.addEventListener("pageshow", aktualisieren);
  viewport?.addEventListener("resize", aktualisieren);
  viewport?.addEventListener("scroll", aktualisieren);
  return () => {
    window.removeEventListener("resize", aktualisieren);
    window.removeEventListener("pageshow", aktualisieren);
    viewport?.removeEventListener("resize", aktualisieren);
    viewport?.removeEventListener("scroll", aktualisieren);
    if (frame !== null) window.cancelAnimationFrame(frame);
    stil.removeProperty("--app-viewport-hoehe");
  };
}
