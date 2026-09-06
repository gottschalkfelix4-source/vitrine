import { useEffect, useId, useRef, useState, type ReactNode, type RefObject } from "react";
import { createPortal } from "react-dom";
import type { Qualitaetsangebot, WiedergabeQualitaet } from "../lib/wiedergabe";
import { Icon } from "./Icons";

const TEMPI = [0.25, 0.5, 0.75, 1, 1.25, 1.5, 1.75, 2, 2.25, 2.5, 2.75, 3];

interface MenueProps {
  offen: boolean;
  aufOffen: (offen: boolean) => void;
  bereich: RefObject<HTMLDivElement | null>;
}

/** Beide Menüs bleiben im Vollbildelement und teilen ihre Tastaturbedienung. */
function PlayerMenue({ offen, aufOffen, bereich, name, titel = name, inhaltName = name, symbol, aktiv, seite, zurueck, children }: MenueProps & {
  name: string; titel?: string; inhaltName?: string; symbol: ReactNode; aktiv?: boolean;
  seite?: string; zurueck?: () => void; children: (schliessen: () => void) => ReactNode;
}) {
  const knopf = useRef<HTMLButtonElement>(null);
  const menue = useRef<HTMLDivElement>(null);
  const id = useId();
  function schliessen(fokus = true) {
    aufOffen(false);
    if (fokus) knopf.current?.focus();
  }
  useEffect(() => {
    if (!offen) return;
    const auswahl = menue.current?.querySelector<HTMLButtonElement>('[aria-checked="true"]')
      ?? menue.current?.querySelector<HTMLButtonElement>("button");
    auswahl?.focus({ preventScroll: true });
    if (auswahl && menue.current) {
      const unten = auswahl.offsetTop + auswahl.offsetHeight;
      if (unten > menue.current.clientHeight) menue.current.scrollTop = unten - menue.current.clientHeight;
    }
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
    <button ref={knopf} className={`steuer-knopf${typeof symbol === "string" ? " steuer-text" : ""}`}
      aria-label={name} title={titel} data-aktiv={aktiv} aria-haspopup="menu" aria-expanded={offen}
      aria-controls={offen ? id : undefined} onClick={() => aufOffen(!offen)}>{symbol}</button>
    {offen && bereich.current ? createPortal(<div ref={menue} id={id} className="steuer-menue player-einstellungen"
      role="menu" aria-label={inhaltName} onBlur={(e) => {
        if (e.relatedTarget && !e.currentTarget.contains(e.relatedTarget) && !knopf.current?.contains(e.relatedTarget)) aufOffen(false);
      }} onKeyDown={(e) => {
        e.stopPropagation();
        if (e.key === "Escape") { e.preventDefault(); schliessen(); return; }
        if (e.key === "Tab") { schliessen(); return; }
        const knoepfe = [...(menue.current?.querySelectorAll<HTMLButtonElement>("button:not(:disabled)") ?? [])];
        if (!knoepfe.length) return;
        const index = knoepfe.indexOf(document.activeElement as HTMLButtonElement);
        let ziel: number | null = null;
        if (e.key === "ArrowDown") ziel = (index + 1) % knoepfe.length;
        if (e.key === "ArrowUp") ziel = (index - 1 + knoepfe.length) % knoepfe.length;
        if (e.key === "Home") ziel = 0;
        if (e.key === "End") ziel = knoepfe.length - 1;
        if (e.key === "ArrowLeft" && zurueck) { e.preventDefault(); zurueck(); }
        if (ziel !== null) { e.preventDefault(); knoepfe[ziel]?.focus(); }
      }}>{children(() => schliessen())}</div>, bereich.current) : null}
  </>;
}

export function PlayerEinstellungen({ offen, aufOffen, bereich, angebote, qualitaet, bezeichnung, aufQualitaet, tempo, aufTempo }: MenueProps & {
  angebote: Qualitaetsangebot[]; qualitaet: WiedergabeQualitaet; bezeichnung: string;
  aufQualitaet: (q: WiedergabeQualitaet) => void; tempo: number; aufTempo: (t: number) => void;
}) {
  const [seite, setSeite] = useState<"haupt" | "qualitaet" | "tempo">("haupt");
  useEffect(() => { if (!offen) setSeite("haupt"); }, [offen]);
  const tempi = [...new Set([...TEMPI, tempo])].sort((a, b) => a - b);
  return <PlayerMenue offen={offen} aufOffen={aufOffen} bereich={bereich} name="Wiedergabeeinstellungen"
    inhaltName={seite === "qualitaet" ? "Qualität" : seite === "tempo" ? "Wiedergabegeschwindigkeit" : "Wiedergabeeinstellungen"}
    symbol={<Icon name="settings" />} seite={seite} zurueck={seite === "haupt" ? undefined : () => setSeite("haupt")}>
    {(schliessen) => seite === "haupt" ? <>
      <button role="menuitem" onClick={() => setSeite("qualitaet")}><span>Qualität</span><span>{bezeichnung}<Icon name="chevronRight" size={16} /></span></button>
      <button role="menuitem" onClick={() => setSeite("tempo")}><span>Geschwindigkeit</span><span>{tempo === 1 ? "Normal" : `${tempo}×`}<Icon name="chevronRight" size={16} /></span></button>
    </> : <>
      <button role="menuitem" className="player-menue-zurueck" onClick={() => setSeite("haupt")}><Icon name="arrowLeft" size={18} />{seite === "qualitaet" ? "Qualität" : "Geschwindigkeit"}</button>
      {seite === "qualitaet" ? angebote.map((q) => <button key={q.value} role="menuitemradio" aria-checked={q.value === qualitaet}
        data-aktiv={q.value === qualitaet} onClick={() => { aufQualitaet(q.value); schliessen(); }}>{q.label}</button>)
        : tempi.map((t) => <button key={t} role="menuitemradio" aria-checked={t === tempo} data-aktiv={t === tempo}
          onClick={() => { aufTempo(t); schliessen(); }}>{t === 1 ? "Normal" : `${t}×`}</button>)}
    </>}
  </PlayerMenue>;
}

export function PlayerUntertitel({ offen, aufOffen, bereich, untertitel, spur, aufSpur }: MenueProps & {
  untertitel: { sprache: string; automatisch: boolean }[]; spur: number; aufSpur: (spur: number) => void;
}) {
  return <PlayerMenue offen={offen} aufOffen={aufOffen} bereich={bereich} name="Untertitel" titel="Untertitel (c)" symbol="CC" aktiv={spur >= 0}>
    {(schliessen) => <>
      <button role="menuitemradio" aria-checked={spur === -1} data-aktiv={spur === -1} onClick={() => { aufSpur(-1); schliessen(); }}>Aus</button>
      {untertitel.map((u, i) => <button key={`${u.sprache}-${u.automatisch}`} role="menuitemradio" aria-checked={spur === i}
        data-aktiv={spur === i} onClick={() => { aufSpur(i); schliessen(); }}>{u.sprache}{u.automatisch ? " (automatisch)" : ""}</button>)}
    </>}
  </PlayerMenue>;
}
