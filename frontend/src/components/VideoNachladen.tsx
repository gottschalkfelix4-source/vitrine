import { useEffect, useRef } from "react";

import type { Videostapel } from "../hooks/useApi";

/** Lädt vor dem Listenende nach und lässt die Aktion auch per Tastatur erreichbar. */
export function VideoNachladen({ stapel, gesamt, anzahl, einheit = "Videos" }: {
  stapel: Pick<Videostapel, "laedt" | "fehler" | "ende" | "mehrLaden"> & { videos?: unknown[] };
  gesamt?: number;
  anzahl?: number;
  einheit?: "Videos" | "Treffer";
}) {
  const anker = useRef<HTMLDivElement>(null);
  const { videos, laedt, fehler, ende, mehrLaden } = stapel;
  const geladen = anzahl ?? videos?.length ?? 0;
  const singular = einheit === "Videos" ? "Video" : "Treffer";

  useEffect(() => {
    const element = anker.current;
    if (!element || laedt || fehler || ende) return;
    const wurzel = element.closest("#inhalt");

    if (typeof IntersectionObserver !== "undefined") {
      const beobachter = new IntersectionObserver((eintraege) => {
        if (eintraege.some((eintrag) => eintrag.isIntersecting)) mehrLaden();
      }, { root: wurzel, rootMargin: "0px 0px 800px 0px", threshold: 0 });
      beobachter.observe(element);
      return () => beobachter.disconnect();
    }

    // Ältere WebViews: gleicher Vorlauf im tatsächlichen Scrollbereich.
    const scrollbereich = wurzel ?? window;
    const pruefen = () => {
      const rand = element.getBoundingClientRect();
      const sichtbereich = wurzel?.getBoundingClientRect();
      if (rand.top <= (sichtbereich?.bottom ?? window.innerHeight) + 800
        && rand.bottom >= (sichtbereich?.top ?? 0)) mehrLaden();
    };
    scrollbereich.addEventListener("scroll", pruefen, { passive: true });
    window.addEventListener("resize", pruefen, { passive: true });
    const frame = window.requestAnimationFrame(pruefen);
    return () => {
      scrollbereich.removeEventListener("scroll", pruefen);
      window.removeEventListener("resize", pruefen);
      window.cancelAnimationFrame(frame);
    };
    // Neu beobachten, wenn eine Seite noch nicht den ganzen Bildschirm füllt.
  }, [geladen, laedt, fehler, ende, mehrLaden]);

  return (
    <div ref={anker} className="mehr-laden" aria-busy={laedt} data-ende={ende}>
      <p className="nachladen-status" role="status" aria-live="polite">
        {laedt ? <><span className="nachladen-spinner" aria-hidden="true" />Weitere {einheit} werden geladen …</>
          : ende ? `${geladen} ${geladen === 1 ? singular : einheit} · Alle geladen`
            : gesamt !== undefined ? `${geladen} von ${gesamt} ${einheit}`
              : `${geladen} ${einheit} geladen`}
      </p>
      {fehler ? <p className="nachladen-fehler" role="alert">Weitere {einheit} konnten nicht geladen werden. {fehler}</p> : null}
      {!ende ? (
        <button type="button" className="knopf" onClick={mehrLaden} disabled={laedt}>
          {fehler ? "Erneut versuchen" : laedt ? "Wird geladen …" : `Weitere ${einheit} laden`}
        </button>
      ) : null}
    </div>
  );
}
