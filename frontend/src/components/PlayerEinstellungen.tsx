import { useEffect, useId, useRef, useState, type RefObject } from "react";
import { createPortal } from "react-dom";
import type { Qualitaetsangebot, WiedergabeQualitaet } from "../lib/wiedergabe";
import { Icon } from "./Icons";

const TEMPI = [0.5, 0.75, 1, 1.25, 1.5, 1.75, 2];

export function PlayerEinstellungen({ offen, aufOffen, bereich, angebote, qualitaet, bezeichnung, aufQualitaet, tempo, aufTempo }: {
  offen: boolean; aufOffen: (offen: boolean) => void; bereich: RefObject<HTMLDivElement | null>;
  angebote: Qualitaetsangebot[]; qualitaet: WiedergabeQualitaet; bezeichnung: string;
  aufQualitaet: (q: WiedergabeQualitaet) => void; tempo: number; aufTempo: (t: number) => void;
}) {
  const [seite, setSeite] = useState<"haupt" | "qualitaet" | "tempo">("haupt");
  const knopf = useRef<HTMLButtonElement>(null);
  const menue = useRef<HTMLDivElement>(null);
  const id = useId();
  function schliessen(fokus = false) {
    aufOffen(false);
    if (fokus) knopf.current?.focus();
  }
  useEffect(() => { if (!offen) setSeite("haupt"); }, [offen]);
  useEffect(() => {
    if (!offen) return;
    (menue.current?.querySelector<HTMLButtonElement>('[aria-checked="true"]')
      ?? menue.current?.querySelector<HTMLButtonElement>("button"))?.focus();
  }, [offen, seite]);
  useEffect(() => {
    if (!offen) return;
    const aussen = (e: PointerEvent) => {
      const ziel = e.target as Node;
      if (!menue.current?.contains(ziel) && !knopf.current?.contains(ziel)) aufOffen(false);
    };
    document.addEventListener("pointerdown", aussen);
    return () => document.removeEventListener("pointerdown", aussen);
  }, [offen, aufOffen]);
  return <>
    <button ref={knopf} className="steuer-knopf" aria-label="Wiedergabeeinstellungen" title="Wiedergabeeinstellungen"
      aria-haspopup="menu" aria-expanded={offen} aria-controls={offen ? id : undefined}
      onClick={() => aufOffen(!offen)}><Icon name="settings" /></button>
    {offen && bereich.current ? createPortal(<div ref={menue} id={id} className="steuer-menue player-einstellungen" role="menu"
      aria-label={seite === "qualitaet" ? "Qualität" : seite === "tempo" ? "Wiedergabegeschwindigkeit" : "Wiedergabeeinstellungen"}
      onKeyDown={(e) => {
        e.stopPropagation();
        if (e.key === "Escape") { e.preventDefault(); schliessen(true); return; }
        if (e.key === "Tab") { schliessen(true); return; }
        const knoepfe = [...(menue.current?.querySelectorAll<HTMLButtonElement>("button:not(:disabled)") ?? [])];
        const index = knoepfe.indexOf(document.activeElement as HTMLButtonElement);
        let ziel: number | null = null;
        if (e.key === "ArrowDown") ziel = (index + 1) % knoepfe.length;
        if (e.key === "ArrowUp") ziel = (index - 1 + knoepfe.length) % knoepfe.length;
        if (e.key === "Home") ziel = 0;
        if (e.key === "End") ziel = knoepfe.length - 1;
        if (e.key === "ArrowLeft" && seite !== "haupt") { e.preventDefault(); setSeite("haupt"); }
        if (ziel !== null) { e.preventDefault(); knoepfe[ziel]?.focus(); }
      }}>
      {seite === "haupt" ? <>
        <button role="menuitem" onClick={() => setSeite("qualitaet")}><span>Qualität</span><span>{bezeichnung}<Icon name="chevronRight" size={16} /></span></button>
        <button role="menuitem" onClick={() => setSeite("tempo")}><span>Geschwindigkeit</span><span>{tempo === 1 ? "Normal" : `${tempo}×`}<Icon name="chevronRight" size={16} /></span></button>
      </> : <>
        <button role="menuitem" className="player-menue-zurueck" onClick={() => setSeite("haupt")}><Icon name="arrowLeft" size={18} />{seite === "qualitaet" ? "Qualität" : "Geschwindigkeit"}</button>
        {seite === "qualitaet" ? angebote.map((q) => <button key={q.value} role="menuitemradio" aria-checked={q.value === qualitaet}
          data-aktiv={q.value === qualitaet} onClick={() => { aufQualitaet(q.value); schliessen(true); }}>{q.label}</button>)
          : TEMPI.map((t) => <button key={t} role="menuitemradio" aria-checked={t === tempo} data-aktiv={t === tempo}
            onClick={() => { aufTempo(t); schliessen(true); }}>{t === 1 ? "Normal" : `${t}×`}</button>)}
      </>}
    </div>, bereich.current) : null}
  </>;
}
