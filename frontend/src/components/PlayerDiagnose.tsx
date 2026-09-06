import { useEffect, useState, type RefObject } from "react";
import { createPortal } from "react-dom";
import { dokumentScrollKorrekturen } from "../lib/appViewport";
import { trefferflaecheVersetzt } from "../lib/trefferflaeche";
import { alsAppGestartet } from "../pwa";

/**
 * Anzeige zum Ablesen auf dem Gerät: Wo liegt der Player, wo wird er
 * getroffen, wie sieht der Browser gerade seinen Viewport?
 *
 * Nötig, weil ein Versatz der Trefferflächen nach dem Drehen nur auf dem
 * iPhone auftritt und sich in keiner Testumgebung nachstellen lässt. Statt zu
 * raten, zeigt der Player die Antworten des Browsers auf genau die Fragen, die
 * er sich bei einer Berührung selbst stellt. Erreichbar über fünfmaliges
 * Antippen der Zeitanzeige oder ?diagnose in der Adresse.
 */
export function PlayerDiagnose({ huelle, oberflaeche, appVollbild, nativesVollbild, quer }: {
  huelle: RefObject<HTMLDivElement | null>; oberflaeche: RefObject<HTMLDivElement | null>;
  appVollbild: boolean; nativesVollbild: boolean; quer: boolean;
}) {
  const [zeilen, setZeilen] = useState<string[]>([]);
  const [tipp, setTipp] = useState<string[]>([]);

  useEffect(() => {
    const name = (el: Element | null) => el ? `${el.tagName.toLowerCase()}${el.className && typeof el.className === "string" ? "." + el.className.split(" ").slice(0, 2).join(".") : ""}` : "–";
    const kasten = (el: Element | null) => {
      if (!el) return "–";
      const r = el.getBoundingClientRect();
      return `y ${Math.round(r.top)} h ${Math.round(r.height)} x ${Math.round(r.left)} b ${Math.round(r.width)}`;
    };
    const lesen = () => {
      const vv = window.visualViewport;
      const de = document.documentElement;
      const taste = huelle.current?.querySelector('button[aria-label="Wiedergabeeinstellungen"]') ?? null;
      const tr = taste?.getBoundingClientRect();
      const treffer = tr ? document.elementFromPoint(tr.left + tr.width / 2, tr.top + tr.height / 2) : null;
      setZeilen([
        `lage ${screen.orientation?.type ?? "?"} quer=${quer} app=${appVollbild} nativ=${nativesVollbild} pwa=${alsAppGestartet()}`,
        `innen ${window.innerWidth}×${window.innerHeight} sichtbar ${vv ? `${Math.round(vv.width)}×${Math.round(vv.height)} +${Math.round(vv.offsetTop)} skala ${vv.scale.toFixed(2)}` : "–"}`,
        `layout ${de.clientWidth}×${de.clientHeight} dokument h ${de.scrollHeight} scroll ${Math.round(de.scrollTop)}/${Math.round(document.body.scrollTop)} zurueck ${dokumentScrollKorrekturen()}×`,
        `var ${getComputedStyle(de).getPropertyValue("--app-viewport-hoehe") || "–"} scrollY ${Math.round(window.scrollY)} inhalt ${Math.round(document.querySelector(".inhalt")?.scrollTop ?? -1)}`,
        `huelle ${kasten(document.querySelector(".huelle"))}`,
        `player ${kasten(huelle.current)}`,
        `ebene ${kasten(oberflaeche.current)} versetzt=${oberflaeche.current ? trefferflaecheVersetzt(oberflaeche.current) : "–"}`,
        `zahnrad ${kasten(taste)} → efp ${name(treffer)}`,
      ]);
    };
    const beruehrung = (e: Event) => {
      const p = e instanceof TouchEvent ? e.touches[0] ?? e.changedTouches[0] : (e as PointerEvent);
      if (!p) return;
      const x = Math.round(p.clientX), y = Math.round(p.clientY);
      const efp = document.elementFromPoint(x, y);
      setTipp([
        `${e.type} client ${x},${y} seite ${Math.round(p.pageX)},${Math.round(p.pageY)}`,
        `ziel ${name(e.target as Element)} | efp ${name(efp)} | im player ${!!huelle.current?.contains(efp)}`,
      ]);
      lesen();
    };
    lesen();
    const takt = window.setInterval(lesen, 700);
    document.addEventListener("touchstart", beruehrung, { capture: true, passive: true });
    document.addEventListener("pointerdown", beruehrung, { capture: true, passive: true });
    return () => {
      window.clearInterval(takt);
      document.removeEventListener("touchstart", beruehrung, true);
      document.removeEventListener("pointerdown", beruehrung, true);
    };
  }, [huelle, oberflaeche, appVollbild, nativesVollbild, quer]);

  // Ausserhalb des Players, dessen Stapelebene sonst den Kopf der App über
  // die ersten Zeilen zeichnet.
  return createPortal(<pre className="player-diagnose" aria-hidden="true">{[...zeilen, ...tipp].join("\n")}</pre>, document.body);
}
