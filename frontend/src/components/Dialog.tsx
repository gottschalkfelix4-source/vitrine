import { useEffect, useRef, type ReactNode } from "react";

/** Gemeinsame native Dialoghülle: Fokusbegrenzung und Hintergrundsperre
 * übernimmt der Browser, das Aussehen entspricht den vorhandenen Dialogen. */
export function Dialog({ titelId, beschreibungId, aufSchliessen, schliessenGesperrt = false, children }: {
  titelId: string;
  beschreibungId?: string;
  aufSchliessen: () => void;
  schliessenGesperrt?: boolean;
  children: ReactNode;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const startAufHintergrund = useRef(false);
  useEffect(() => {
    const el = dialog.current;
    if (!el) return;
    const vorherigerFokus = document.activeElement;
    const vorherigerOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    if (!el.open) el.showModal();
    el.querySelector<HTMLElement>("[data-dialog-fokus]")?.focus({ preventScroll: true });
    return () => {
      el.close();
      document.body.style.overflow = vorherigerOverflow;
      if (vorherigerFokus instanceof HTMLElement && vorherigerFokus.isConnected) {
        vorherigerFokus.focus({ preventScroll: true });
      }
    };
  }, []);
  return <dialog ref={dialog} className="kanal-dialog" aria-labelledby={titelId} aria-describedby={beschreibungId}
    onKeyDown={(e) => {
      if (e.key !== "Tab") return;
      const felder = Array.from(e.currentTarget.querySelectorAll<HTMLElement>(
        "button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), a[href], [tabindex]:not([tabindex='-1'])",
      )).filter((el) => el.tabIndex >= 0 && !el.matches(":disabled") && el.getClientRects().length > 0);
      const erstes = felder[0], letztes = felder.at(-1);
      if (!erstes) { e.preventDefault(); e.currentTarget.focus(); return; }
      const fokusImDialog = felder.includes(document.activeElement as HTMLElement);
      if (e.shiftKey && (!fokusImDialog || document.activeElement === erstes)) { e.preventDefault(); letztes?.focus(); }
      if (!e.shiftKey && (!fokusImDialog || document.activeElement === letztes)) { e.preventDefault(); erstes.focus(); }
    }}
    onCancel={(e) => { e.preventDefault(); if (!schliessenGesperrt) aufSchliessen(); }}
    onPointerDown={(e) => {
      const rand = e.currentTarget.getBoundingClientRect();
      startAufHintergrund.current = e.target === e.currentTarget &&
        (e.clientX < rand.left || e.clientX > rand.right || e.clientY < rand.top || e.clientY > rand.bottom);
    }}
    onPointerCancel={() => { startAufHintergrund.current = false; }}
    onClick={(e) => {
      const rand = e.currentTarget.getBoundingClientRect();
      const ausserhalb = e.clientX < rand.left || e.clientX > rand.right || e.clientY < rand.top || e.clientY > rand.bottom;
      if (startAufHintergrund.current && e.target === e.currentTarget && ausserhalb && !schliessenGesperrt) aufSchliessen();
      startAufHintergrund.current = false;
    }}>
    {children}
  </dialog>;
}
