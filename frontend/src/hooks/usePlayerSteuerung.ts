import { useCallback, useEffect, useRef, useState, type HTMLAttributes } from "react";

const AUSBLENDEN_MS = 2600;

/** Ein Sichtbarkeitszustand für Kopf, Titel, Wiedergabetaste und Zeitleiste. */
export function usePlayerSteuerung({ laeuft, bereit, minimiert, menueOffen, vollbild }: {
  laeuft: boolean; bereit: boolean; minimiert: boolean; menueOffen: boolean; vollbild: boolean;
}) {
  const [angezeigt, setAngezeigt] = useState(true);
  const [tastaturFokus, setTastaturFokus] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const gedrueckt = useRef(false);
  const letzterTouch = useRef(-Infinity);
  const festhalten = !laeuft || !bereit || minimiert || menueOffen || tastaturFokus;
  const festhaltenRef = useRef(festhalten);
  festhaltenRef.current = festhalten;

  const ausblenden = useCallback(() => {
    clearTimeout(timer.current);
    if (!festhaltenRef.current) setAngezeigt(false);
  }, []);
  const planen = useCallback(() => {
    clearTimeout(timer.current);
    timer.current = setTimeout(() => {
      if (!gedrueckt.current && !festhaltenRef.current) setAngezeigt(false);
    }, AUSBLENDEN_MS);
  }, []);
  const zeigen = useCallback(() => { setAngezeigt(true); planen(); }, [planen]);
  useEffect(() => {
    zeigen();
    return () => clearTimeout(timer.current);
  }, [festhalten, vollbild, zeigen]);

  const ereignisse: HTMLAttributes<HTMLDivElement> = {
    onPointerDownCapture: (e) => {
      if (e.pointerType !== "mouse") letzterTouch.current = Date.now();
      gedrueckt.current = true;
      setTastaturFokus(false);
      clearTimeout(timer.current);
    },
    onPointerUpCapture: () => { gedrueckt.current = false; planen(); },
    onPointerCancelCapture: () => { gedrueckt.current = false; planen(); },
    onLostPointerCapture: () => {
      if (gedrueckt.current) { gedrueckt.current = false; planen(); }
    },
    onPointerMove: (e) => {
      // iOS kann nach einem Tap noch Mausereignisse synthetisieren.
      if (e.pointerType === "mouse" && Date.now() - letzterTouch.current > 1000) zeigen();
    },
    onPointerLeave: (e) => {
      if (e.pointerType === "mouse" && !gedrueckt.current && Date.now() - letzterTouch.current > 1000) ausblenden();
    },
    onFocusCapture: (e) => {
      if (e.target !== e.currentTarget && e.target.matches(":focus-visible") && Date.now() - letzterTouch.current > 1000) {
        setTastaturFokus(true);
        zeigen();
      }
    },
    onBlurCapture: (e) => {
      if (!e.currentTarget.contains(e.relatedTarget)) setTastaturFokus(false);
    },
  };
  return { sichtbar: festhalten || angezeigt, zeigen, ausblenden, ereignisse };
}
