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
  useEffect(() => {
    const el = dialog.current;
    el?.showModal();
    el?.querySelector<HTMLElement>("[data-dialog-fokus]")?.focus();
    return () => el?.close();
  }, []);
  return <dialog ref={dialog} className="kanal-dialog" aria-labelledby={titelId} aria-describedby={beschreibungId}
    onKeyDown={(e) => {
      if (e.key !== "Tab") return;
      const felder = Array.from(e.currentTarget.querySelectorAll<HTMLElement>(
        "button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), a[href], [tabindex]:not([tabindex='-1'])",
      )).filter((el) => el.getClientRects().length > 0);
      const erstes = felder[0], letztes = felder.at(-1);
      if (!erstes) { e.preventDefault(); return; }
      if (e.shiftKey && document.activeElement === erstes) { e.preventDefault(); letztes?.focus(); }
      if (!e.shiftKey && document.activeElement === letztes) { e.preventDefault(); erstes.focus(); }
    }}
    onCancel={(e) => { if (schliessenGesperrt) e.preventDefault(); else aufSchliessen(); }}
    onClick={(e) => { if (e.target === e.currentTarget && !schliessenGesperrt) aufSchliessen(); }}>
    {children}
  </dialog>;
}
