import { useRef, type ComponentProps } from "react";
import { playerTouchAbschliessen } from "../lib/playerTouch";

/** Touch direkt abschließen; ein nachgereichter Safari-Klick darf nicht doppelt auslösen. */
export function PlayerTaste({ onClick, onPointerDown, onPointerMove, onPointerUp, onPointerCancel, ...props }: ComponentProps<"button">) {
  const druck = useRef<{ id: number; x: number; y: number } | null>(null);
  const letzterTap = useRef(-Infinity);
  return <button type="button" {...props}
    onPointerDown={(e) => {
      letzterTap.current = e.pointerType === "mouse" ? -Infinity : Date.now();
      druck.current = e.isPrimary !== false && e.button === 0 && e.pointerType !== "mouse"
        ? { id: e.pointerId, x: e.clientX, y: e.clientY } : null;
      onPointerDown?.(e);
    }}
    onPointerMove={(e) => {
      const start = druck.current;
      if (start && Math.hypot(e.clientX - start.x, e.clientY - start.y) > 12) druck.current = null;
      onPointerMove?.(e);
    }}
    onPointerCancel={(e) => { druck.current = null; onPointerCancel?.(e); }}
    onPointerUp={(e) => {
      if (e.pointerType !== "mouse") {
        letzterTap.current = Date.now();
        playerTouchAbschliessen();
      }
      const start = druck.current;
      druck.current = null;
      onPointerUp?.(e);
      if (!start || start.id !== e.pointerId || props.disabled || e.defaultPrevented) return;
      const r = e.currentTarget.getBoundingClientRect();
      if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) return;
      letzterTap.current = Date.now();
      e.preventDefault();
      onClick?.(e);
    }}
    onClick={(e) => {
      // Tastatur und assistive Technik aktivieren mit detail=0 weiterhin normal.
      if (e.detail > 0 && Date.now() - letzterTap.current < 1000) { e.preventDefault(); return; }
      onClick?.(e);
    }} />;
}
