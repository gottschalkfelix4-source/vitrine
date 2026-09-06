import { useCallback, useEffect, useRef, useState } from "react";

import { api, type Suchergebnis } from "../lib/api";

export type Suchbereich = "alle" | "videos" | "gesprochen";
type Fortsetzung = { videos: boolean; untertitel: boolean };
type Suchlauf = { schluessel: string; offset: number; laedt: boolean; aktiv: boolean; mehr: Fortsetzung };
type Suchzustand = { schluessel: string; daten?: Suchergebnis; laedt: boolean; fehler: string | null };

function hatWeitere(mehr: Fortsetzung, bereich: Suchbereich) {
  return bereich === "videos" ? mehr.videos : bereich === "gesprochen" ? mehr.untertitel : mehr.videos || mehr.untertitel;
}

/** Beide Suchbereiche teilen einen Server-Offset; die gewählte Ansicht bestimmt, wann Schluss ist. */
export function useSuchstapel(suchbegriff: string, bereich: Suchbereich, seitengroesse = 40) {
  const anfrage = suchbegriff.trim();
  const limit = Math.min(100, Math.max(1, Math.floor(seitengroesse) || 40));
  const schluessel = JSON.stringify([anfrage, limit]);
  const [zustand, setZustand] = useState<Suchzustand>({ schluessel, laedt: Boolean(anfrage), fehler: null });
  const lauf = useRef<Suchlauf | null>(null);
  const bereichRef = useRef(bereich);
  bereichRef.current = bereich;

  const laden = useCallback(async (aktuell: Suchlauf) => {
    if (!aktuell.aktiv || aktuell.laedt || !hatWeitere(aktuell.mehr, bereichRef.current)) return;
    aktuell.laedt = true;
    const offset = aktuell.offset;
    setZustand((alt) => ({ ...alt, laedt: true, fehler: null }));
    try {
      const seite = await api.suchen(anfrage, limit, offset);
      if (!aktuell.aktiv || lauf.current !== aktuell) return;
      // Kein Offset aus bereinigten Trefferzahlen: Fundstellen und Videos können
      // unterschiedlich schnell enden und über Seitengrenzen doppelt auftreten.
      aktuell.offset += limit;
      aktuell.mehr = seite.zu_kurz ? { videos: false, untertitel: false } : seite.has_more ?? {
        videos: seite.videos.length >= limit,
        untertitel: seite.im_gesprochenen.length >= limit,
      };
      setZustand((alt) => {
        const vorher = offset === 0 ? undefined : alt.daten;
        const videos = new Map((vorher?.videos ?? []).map((video) => [video.id, video]));
        const fundstellen = new Map((vorher?.im_gesprochenen ?? []).map((fund) => [JSON.stringify([fund.video.id, fund.start_s, fund.sprache, fund.zeile]), fund]));
        for (const video of seite.videos) videos.set(video.id, video);
        for (const fund of seite.im_gesprochenen) fundstellen.set(JSON.stringify([fund.video.id, fund.start_s, fund.sprache, fund.zeile]), fund);
        return { schluessel, laedt: false, fehler: null, daten: {
          ...seite, videos: [...videos.values()], im_gesprochenen: [...fundstellen.values()], has_more: aktuell.mehr,
        } };
      });
    } catch (e) {
      if (aktuell.aktiv && lauf.current === aktuell) {
        setZustand((alt) => ({ ...alt, laedt: false, fehler: e instanceof Error ? e.message : String(e) }));
      }
    } finally {
      aktuell.laedt = false;
    }
  }, [anfrage, limit, schluessel]);

  useEffect(() => {
    const aktuell: Suchlauf = { schluessel, offset: 0, laedt: false, aktiv: true, mehr: { videos: true, untertitel: true } };
    lauf.current = aktuell;
    setZustand({ schluessel, laedt: Boolean(anfrage), fehler: null });
    if (anfrage) void laden(aktuell);
    else aktuell.mehr = { videos: false, untertitel: false };
    return () => { aktuell.aktiv = false; };
  }, [anfrage, schluessel, laden]);

  const mehrLaden = useCallback(() => {
    const aktuell = lauf.current;
    if (aktuell?.schluessel === schluessel && anfrage) void laden(aktuell);
  }, [anfrage, schluessel, laden]);

  const passt = zustand.schluessel === schluessel;
  const daten = passt ? zustand.daten : undefined;
  const mehr = daten?.has_more ?? { videos: Boolean(anfrage), untertitel: Boolean(anfrage) };
  const ende = !hatWeitere(mehr, bereich);
  return {
    daten,
    laedt: !passt || (zustand.laedt && !ende),
    fehler: passt && !ende ? zustand.fehler : null,
    ende,
    mehrLaden,
  };
}
