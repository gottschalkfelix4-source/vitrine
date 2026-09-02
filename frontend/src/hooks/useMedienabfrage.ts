import { useEffect, useState } from "react";

/**
 * Beobachtet eine CSS-Medienabfrage aus React heraus.
 *
 * Gebraucht wird das, weil die Seitenleiste auf schmalen Geräten nicht bloß
 * anders aussieht, sondern sich anders verhält: Am Schreibtisch schaltet der
 * Knopf zwischen breiter und schmaler Leiste um, auf dem Telefon öffnet und
 * schließt er eine Schublade, die über dem Inhalt liegt. Dasselbe Verhalten in
 * beiden Fällen wäre auf dem Telefon unbrauchbar - die Leiste belegt dort mehr
 * Platz, als das Gerät breit ist.
 *
 * Reines CSS reicht dafür nicht: Es kann die Leiste verschieben, aber nicht
 * entscheiden, was der Knopf auslöst.
 */
export function useMedienabfrage(abfrage: string): boolean {
  const [trifft, setTrifft] = useState(() =>
    typeof window === "undefined" ? false : window.matchMedia(abfrage).matches,
  );

  useEffect(() => {
    const mq = window.matchMedia(abfrage);
    const beiWechsel = (e: MediaQueryListEvent) => setTrifft(e.matches);
    // Beim ersten Lauf nachziehen: Zwischen dem Anfangswert und diesem Effekt
    // kann sich die Breite geändert haben - beim Drehen des Geräts etwa.
    setTrifft(mq.matches);
    mq.addEventListener("change", beiWechsel);
    return () => mq.removeEventListener("change", beiWechsel);
  }, [abfrage]);

  return trifft;
}

/** Die Schwelle, ab der die Oberfläche auf Handbedienung umschaltet.
 *  Muss mit der Medienabfrage in app.css übereinstimmen. */
export const SCHMAL = "(max-width: 860px)";
