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

  // Die Elementhandler unten sehen nur, was im Baum liegen bleibt. Schließt
  // ein Druck das Menü, in dem er begonnen hat, kommt das Loslassen nirgends
  // mehr an: Der Merker "gerade gedrückt" bliebe stehen und die Steuerung
  // würde nie wieder ausblenden. Am Dokument kommt jedes Ende an. Eine
  // Berührung ist außerdem überall im Dokument ein Beleg gegen Tastaturfokus,
  // auch auf einem Menüblatt neben dem Player.
  useEffect(() => {
    const beginn = (e: Event) => {
      if (e.type === "pointerdown" && (e as PointerEvent).pointerType === "mouse") return;
      letzterTouch.current = Date.now();
      setTastaturFokus(false);
    };
    const ende = () => {
      if (!gedrueckt.current) return;
      gedrueckt.current = false;
      planen();
    };
    const optionen = { capture: true, passive: true } as const;
    const beginnNamen = ["touchstart", "pointerdown"];
    const endeNamen = ["touchend", "touchcancel", "pointerup", "pointercancel"];
    for (const name of beginnNamen) document.addEventListener(name, beginn, optionen);
    for (const name of endeNamen) document.addEventListener(name, ende, optionen);
    return () => {
      for (const name of beginnNamen) document.removeEventListener(name, beginn, true);
      for (const name of endeNamen) document.removeEventListener(name, ende, true);
    };
  }, [planen]);

  const ereignisse: HTMLAttributes<HTMLDivElement> = {
    onTouchStartCapture: () => {
      letzterTouch.current = Date.now(); gedrueckt.current = true;
      setTastaturFokus(false); clearTimeout(timer.current);
    },
    onTouchEndCapture: () => { gedrueckt.current = false; planen(); },
    onTouchCancelCapture: () => { gedrueckt.current = false; planen(); },
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
