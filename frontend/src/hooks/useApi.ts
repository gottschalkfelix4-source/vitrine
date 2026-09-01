import { useCallback, useEffect, useRef, useState } from "react";

import type { VideoAbfrage, VideoKurz } from "../lib/api";
import { api } from "../lib/api";

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

export interface Videostapel {
  videos: VideoKurz[];
  laedt: boolean;
  fehler: string | null;
  /** Es gibt nichts mehr nachzuladen. */
  ende: boolean;
  mehrLaden: () => void;
  neuLaden: () => void;
}

/**
 * Videos seitenweise laden, mit "Mehr laden" statt fester Obergrenze.
 *
 * Der Grund fuer diesen Hook: Ein Kanal kann tausende Videos haben. Alles auf
 * einmal zu laden waere zaeh, und ein festes Limit ("die ersten 90") laesst
 * den Rest schlicht verschwinden - genau das war der Fehler in der ersten
 * Fassung der Kanalseite.
 *
 * Aendern sich die Abfrageparameter (anderer Kanal, anderer Tab, andere
 * Sortierung), beginnt der Stapel von vorn.
 */
export function useVideostapel(abfrage: VideoAbfrage, seitengroesse = 60): Videostapel {
  const [videos, setVideos] = useState<VideoKurz[]>([]);
  const [laedt, setLaedt] = useState(true);
  const [fehler, setFehler] = useState<string | null>(null);
  const [ende, setEnde] = useState(false);
  const lauf = useRef(0);
  const abfrageRef = useRef(abfrage);
  abfrageRef.current = abfrage;
  // Ein stabiler Schluessel, damit sich der Effekt nur bei echten Aenderungen
  // meldet und nicht bei jedem neu erzeugten Objekt.
  const schluessel = JSON.stringify(abfrage);

  const laden = useCallback(
    async (ab: number) => {
      const meine = ++lauf.current;
      setLaedt(true);
      try {
        const neue = await api.videos({ ...abfrageRef.current, limit: seitengroesse, offset: ab });
        if (meine !== lauf.current) return;
        setVideos((alte) => (ab === 0 ? neue : [...alte, ...neue]));
        setEnde(neue.length < seitengroesse);
        setFehler(null);
      } catch (e) {
        if (meine === lauf.current) setFehler(e instanceof Error ? e.message : String(e));
      } finally {
        if (meine === lauf.current) setLaedt(false);
      }
    },
    [seitengroesse],
  );

  useEffect(() => {
    setVideos([]);
    setEnde(false);
    void laden(0);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [schluessel, laden]);

  return {
    videos,
    laedt,
    fehler,
    ende,
    mehrLaden: () => {
      if (!laedt && !ende) void laden(videos.length);
    },
    neuLaden: () => void laden(0),
  };
}
