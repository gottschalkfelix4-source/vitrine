import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type { VideoAbfrage, VideoKurz } from "../lib/api";
import { api } from "../lib/api";

export interface Ladezustand<T> {
  daten: T | undefined;
  laedt: boolean;
  fehler: string | null;
  neuLaden: () => Promise<void>;
}

/** Aktualisierungen behalten ihre Daten; eine neue Abfrage zeigt keine alten Ergebnisse. */
export function useApi<T>(
  laden: () => Promise<T>,
  abhaengigkeiten: unknown[] = [],
  intervallMs?: number,
): Ladezustand<T> {
  const schluessel = useMemo(() => ({}), abhaengigkeiten);
  const [zustand, setZustand] = useState<{
    schluessel: object; daten: T | undefined; laedt: boolean; fehler: string | null;
  }>({ schluessel, daten: undefined, laedt: true, fehler: null });
  const lauf = useRef(0);
  const aktiv = useRef<object | null>(null);
  const beschaeftigt = useRef(false);
  const ladenRef = useRef(laden);
  ladenRef.current = laden;

  const ausfuehren = useCallback(async () => {
    if (aktiv.current !== schluessel) return;
    const meine = ++lauf.current;
    beschaeftigt.current = true;
    setZustand((alt) => ({
      schluessel,
      daten: alt.schluessel === schluessel ? alt.daten : undefined,
      laedt: true,
      fehler: null,
    }));
    try {
      const daten = await ladenRef.current();
      if (meine === lauf.current && aktiv.current === schluessel) {
        setZustand({ schluessel, daten, laedt: false, fehler: null });
      }
    } catch (e) {
      if (meine === lauf.current && aktiv.current === schluessel) {
        setZustand((alt) => ({ ...alt, laedt: false, fehler: e instanceof Error ? e.message : String(e) }));
      }
    } finally {
      if (meine === lauf.current) beschaeftigt.current = false;
    }
  }, [schluessel]);

  useEffect(() => {
    aktiv.current = schluessel;
    void ausfuehren();
    return () => {
      aktiv.current = null;
      ++lauf.current;
      beschaeftigt.current = false;
    };
  }, [schluessel, ausfuehren]);

  useEffect(() => {
    if (!intervallMs) return;
    // Langsame Verbindungen dürfen nicht immer neue, überlappende Polls auslösen.
    const id = window.setInterval(() => {
      if (!beschaeftigt.current) void ausfuehren();
    }, intervallMs);
    return () => window.clearInterval(id);
  }, [intervallMs, ausfuehren]);

  const aktuell = zustand.schluessel === schluessel;
  return {
    daten: aktuell ? zustand.daten : undefined,
    laedt: !aktuell || zustand.laedt,
    fehler: aktuell ? zustand.fehler : null,
    neuLaden: ausfuehren,
  };
}

/** Verzögert einen Wert – für Suchfelder, damit nicht jeder Tastendruck fragt. */
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
  /** Wiederholt bei einem Fehler dieselbe Seite, ohne geladene Videos zu verlieren. */
  mehrLaden: () => void;
  neuLaden: () => void;
}

type Stapelzustand = Pick<Videostapel, "videos" | "laedt" | "fehler" | "ende"> & { schluessel: string };
type Stapelanfrage = { schluessel: string; offset: number; laedt: boolean; ende: boolean; aktiv: boolean };

/** Seiten werden seriell geladen; Filterwechsel und verspätete Antworten bleiben getrennt. */
export function useVideostapel(abfrage: VideoAbfrage, seitengroesse = 60): Videostapel {
  const limit = Math.min(200, Math.max(1, Math.floor(seitengroesse) || 60));
  // Objekt-Reihenfolge und leere Parameter ändern die tatsächliche Abfrage nicht.
  const parameter = JSON.stringify(Object.fromEntries(Object.entries(abfrage)
    .filter(([name, wert]) => name !== "offset" && name !== "limit" && wert !== undefined && wert !== "")
    .sort(([a], [b]) => a.localeCompare(b))));
  const schluessel = `${limit}:${parameter}`;
  const [zustand, setZustand] = useState<Stapelzustand>({ schluessel, videos: [], laedt: true, fehler: null, ende: false });
  const aktuell = useRef<Stapelanfrage | null>(null);
  const montiert = useRef(false);

  const laden = useCallback(async (anfrage: Stapelanfrage) => {
    // Die Sperre ist synchron. Mehrere Observer-Meldungen laden nur einmal.
    if (!anfrage.aktiv || anfrage.laedt || anfrage.ende) return;
    anfrage.laedt = true;
    const offset = anfrage.offset;
    setZustand((alt) => ({ ...alt, laedt: true, fehler: null }));
    try {
      const neue = await api.videos({ ...JSON.parse(parameter), limit, offset });
      if (!anfrage.aktiv || aktuell.current !== anfrage) return;
      // Der Server-Offset zählt die Antwort, nicht die nach IDs bereinigte Anzeige.
      // Neue Archivierungen können Videos über Seitengrenzen verschieben.
      anfrage.offset = offset + neue.length;
      anfrage.ende = neue.length < limit;
      setZustand((alt) => {
        const videos = new Map((offset === 0 ? [] : alt.videos).map((video) => [video.id, video]));
        for (const video of neue) videos.set(video.id, video);
        return { schluessel, videos: [...videos.values()], laedt: false, fehler: null, ende: anfrage.ende };
      });
    } catch (e) {
      if (anfrage.aktiv && aktuell.current === anfrage) {
        setZustand((alt) => ({ ...alt, laedt: false, fehler: e instanceof Error ? e.message : String(e) }));
      }
    } finally {
      anfrage.laedt = false;
    }
  }, [schluessel, parameter, limit]);

  const neuLaden = useCallback(() => {
    if (!montiert.current || (aktuell.current && aktuell.current.schluessel !== schluessel)) return;
    if (aktuell.current) aktuell.current.aktiv = false;
    const anfrage: Stapelanfrage = { schluessel, offset: 0, laedt: false, ende: false, aktiv: true };
    aktuell.current = anfrage;
    setZustand({ schluessel, videos: [], laedt: true, fehler: null, ende: false });
    void laden(anfrage);
  }, [schluessel, laden]);

  useEffect(() => {
    montiert.current = true;
    aktuell.current = null;
    neuLaden();
    return () => {
      montiert.current = false;
      if (aktuell.current) aktuell.current.aktiv = false;
    };
  }, [neuLaden]);

  const mehrLaden = useCallback(() => {
    const anfrage = aktuell.current;
    if (anfrage?.schluessel === schluessel) void laden(anfrage);
  }, [schluessel, laden]);

  return {
    videos: zustand.schluessel === schluessel ? zustand.videos : [],
    laedt: zustand.schluessel !== schluessel || zustand.laedt,
    fehler: zustand.schluessel === schluessel ? zustand.fehler : null,
    ende: zustand.schluessel === schluessel && zustand.ende,
    mehrLaden,
    neuLaden,
  };
}
