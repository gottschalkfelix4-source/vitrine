import { useCallback, useEffect, useRef, useState } from "react";

export interface Ladezustand<T> {
  daten: T | undefined;
  laedt: boolean;
  fehler: string | null;
  neuLaden: () => void;
}

/**
 * Holt Daten und haelt Lade- und Fehlerzustand fest.
 *
 * Zwei Feinheiten, die im Alltag den Unterschied machen:
 *
 * Beim erneuten Laden bleiben die alten Daten stehen. Sonst blitzt bei jedem
 * Aktualisieren die Ladeanzeige auf und die Seite springt - besonders stoerend
 * bei der Warteschlange, die sich sekuendlich auffrischt.
 *
 * Antworten ueberholter Anfragen werden verworfen. Wer schnell zwischen Kanaelen
 * wechselt, bekaeme sonst irgendwann die Videos des vorletzten Kanals angezeigt.
 */
export function useApi<T>(
  laden: () => Promise<T>,
  abhaengigkeiten: unknown[] = [],
  intervallMs?: number,
): Ladezustand<T> {
  const [daten, setDaten] = useState<T | undefined>(undefined);
  const [laedt, setLaedt] = useState(true);
  const [fehler, setFehler] = useState<string | null>(null);
  const lauf = useRef(0);
  // Die Ladefunktion wird bei jedem Rendern neu erzeugt; ueber eine Referenz
  // bleibt der Effekt trotzdem an den ausdruecklichen Abhaengigkeiten haengen.
  const ladenRef = useRef(laden);
  ladenRef.current = laden;

  const ausfuehren = useCallback(async () => {
    const meine = ++lauf.current;
    setLaedt(true);
    try {
      const ergebnis = await ladenRef.current();
      if (meine === lauf.current) {
        setDaten(ergebnis);
        setFehler(null);
      }
    } catch (e) {
      if (meine === lauf.current) {
        setFehler(e instanceof Error ? e.message : String(e));
      }
    } finally {
      if (meine === lauf.current) setLaedt(false);
    }
  }, []);

  useEffect(() => {
    void ausfuehren();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, abhaengigkeiten);

  useEffect(() => {
    if (!intervallMs) return;
    const id = window.setInterval(() => void ausfuehren(), intervallMs);
    return () => window.clearInterval(id);
  }, [intervallMs, ausfuehren]);

  return { daten, laedt, fehler, neuLaden: ausfuehren };
}

/** Verzoegert einen Wert - fuer Suchfelder, damit nicht jeder Tastendruck fragt. */
export function useVerzoegert<T>(wert: T, ms = 300): T {
  const [verzoegert, setVerzoegert] = useState(wert);
  useEffect(() => {
    const id = window.setTimeout(() => setVerzoegert(wert), ms);
    return () => window.clearTimeout(id);
  }, [wert, ms]);
  return verzoegert;
}
