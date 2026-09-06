import { useRef, type ComponentProps } from "react";
import { playerTouchAbschliessen } from "../lib/playerTouch";

/**
 * Touch direkt abschließen; ein nachgereichter Safari-Klick darf nicht doppelt
 * auslösen. Der eigene Touch-Weg ist dabei immer nur die Abkürzung: Sperrt er
 * den nachgereichten Klick, muss er die Aktion auch wirklich ausgeführt haben.
 */
export function PlayerTaste({ onClick, onPointerDown, onPointerMove, onPointerUp, onPointerCancel,
  onTouchStart, onTouchMove, onTouchEnd, onTouchCancel, ...props }: ComponentProps<"button">) {
  const druck = useRef<{ id: number; x: number; y: number } | null>(null);
  const beruehrung = useRef<{ id: number; x: number; y: number } | null>(null);
  const mitTouch = useRef(false);
  const letzterTap = useRef(-Infinity);
  function ausloesen(taste: HTMLButtonElement) {
    letzterTap.current = Date.now();
    playerTouchAbschliessen();
    // Ein normaler Button-Klick: Tastatur, Touch und Maus benutzen dieselbe Aktion.
    taste.click();
  }
  return <button type="button" {...props}
    onPointerDown={(e) => {
      letzterTap.current = -Infinity;
      mitTouch.current = false;
      druck.current = e.isPrimary !== false && e.pointerType !== "mouse"
        ? { id: e.pointerId, x: e.clientX, y: e.clientY } : null;
      onPointerDown?.(e);
    }}
    onPointerMove={(e) => {
      const start = druck.current;
      if (start && Math.hypot(e.clientX - start.x, e.clientY - start.y) > 12) {
        druck.current = null; letzterTap.current = Date.now();
      }
      onPointerMove?.(e);
    }}
    onPointerCancel={(e) => { druck.current = null; letzterTap.current = Date.now(); onPointerCancel?.(e); }}
    onPointerUp={(e) => {
      const start = druck.current;
      druck.current = null;
      onPointerUp?.(e);
      if (mitTouch.current || !start || start.id !== e.pointerId || props.disabled || e.defaultPrevented) return;
      const r = e.currentTarget.getBoundingClientRect();
      if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) {
        letzterTap.current = Date.now(); return;
      }
      e.preventDefault();
      ausloesen(e.currentTarget);
    }}
    onTouchStart={(e) => {
      mitTouch.current = true;
      letzterTap.current = -Infinity;
      const t = e.changedTouches[0];
      beruehrung.current = e.touches.length === 1 && t ? { id: t.identifier, x: t.clientX, y: t.clientY } : null;
      onTouchStart?.(e);
    }}
    onTouchMove={(e) => {
      const start = beruehrung.current;
      const t = Array.from(e.changedTouches).find(t => t.identifier === start?.id);
      if (start && t && Math.hypot(t.clientX - start.x, t.clientY - start.y) > 12) {
        beruehrung.current = null; letzterTap.current = Date.now();
      }
      onTouchMove?.(e);
    }}
    onTouchCancel={(e) => { beruehrung.current = null; letzterTap.current = Date.now(); onTouchCancel?.(e); }}
    onTouchEnd={(e) => {
      const start = beruehrung.current;
      beruehrung.current = null;
      onTouchEnd?.(e);
      mitTouch.current = false;
      druck.current = null;
      if (props.disabled || e.defaultPrevented) return;
      const t = start ? Array.from(e.changedTouches).find(b => b.identifier === start.id) : undefined;
      // Der Klick wird nur dort gesperrt, wo die Geste erkennbar abgebrochen
      // wurde (Bewegung, Abbruch, Loslassen daneben) oder die Aktion bereits
      // lief. Liefert ein Browser unerwartete Touch-Daten - eine zweite
      // Berührung, eine andere Kennung, gar kein Start -, bleibt der ganz
      // normale Klick der Weg zur Aktion. Sonst wäre die Taste vollkommen tot.
      if (!start || !t) return;
      if (Math.hypot(t.clientX - start.x, t.clientY - start.y) > 12) { letzterTap.current = Date.now(); return; }
      ausloesen(e.currentTarget);
    }}
    onClick={(e) => {
      // Tastatur und assistive Technik aktivieren mit detail=0 weiterhin normal.
      if (e.detail > 0 && Date.now() - letzterTap.current < 1000) { e.preventDefault(); return; }
      onClick?.(e);
    }} />;
}
