import { useEffect, useState } from "react";

/** Physische Ausrichtung: Eine geöffnete Tastatur ist kein Querformat. */
export function usePlayerAusrichtung() {
  const lesen = () => {
    if (typeof window === "undefined") return { touch: false, quer: false };
    const touch = window.matchMedia?.("(pointer: coarse)").matches ?? false;
    const typ = window.screen?.orientation?.type;
    const winkel = (window as Window & { orientation?: number }).orientation;
    const quer = typ ? typ.startsWith("landscape") : typeof winkel === "number"
      ? Math.abs(winkel) === 90 : window.screen.width > window.screen.height;
    return { touch, quer };
  };
  const [ausrichtung, setAusrichtung] = useState(lesen);
  useEffect(() => {
    const aktualisieren = () => setAusrichtung(lesen());
    const abfrage = window.matchMedia?.("(pointer: coarse)");
    aktualisieren();
    window.screen.orientation?.addEventListener("change", aktualisieren);
    window.addEventListener("orientationchange", aktualisieren);
    window.addEventListener("resize", aktualisieren);
    abfrage?.addEventListener("change", aktualisieren);
    return () => {
      window.screen.orientation?.removeEventListener("change", aktualisieren);
      window.removeEventListener("orientationchange", aktualisieren);
      window.removeEventListener("resize", aktualisieren);
      abfrage?.removeEventListener("change", aktualisieren);
    };
  }, []);
  return ausrichtung;
}
