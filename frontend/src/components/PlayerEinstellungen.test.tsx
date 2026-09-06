// @vitest-environment jsdom
import { act, useRef, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { PlayerEinstellungen, PlayerUntertitel } from "./PlayerEinstellungen";

let root: Root;
let host: HTMLDivElement;

beforeEach(() => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(async () => {
  await act(() => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
});

function button(text: string) {
  const gefunden = [...host.querySelectorAll<HTMLButtonElement>("button")].find((el) =>
    el.getAttribute("aria-label") === text || el.textContent === text);
  if (!gefunden) throw new Error(`Knopf fehlt: ${text}`);
  return gefunden;
}

async function click(el: HTMLButtonElement) {
  await act(() => { el.focus(); el.click(); });
}

it("öffnet Untertitel mit Auswahlfokus, navigiert per Tastatur und gibt den Fokus zurück", async () => {
  const waehlen = vi.fn();
  function Beispiel() {
    const bereich = useRef<HTMLDivElement>(null);
    const [offen, setOffen] = useState(false);
    const [spur, setSpur] = useState(0);
    return <div ref={bereich}><PlayerUntertitel offen={offen} aufOffen={setOffen} bereich={bereich}
      untertitel={[{ sprache: "de", automatisch: false }, { sprache: "en", automatisch: true }]}
      spur={spur} aufSpur={(index) => { setSpur(index); waehlen(index); }} /></div>;
  }
  await act(() => root.render(<Beispiel />));
  await click(button("Untertitel"));
  expect(document.activeElement).toBe(button("de"));
  expect(host.querySelector('[role="menu"]')?.parentElement).toBe(host.firstElementChild);
  await act(() => button("de").dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowDown", bubbles: true })));
  expect(document.activeElement).toBe(button("en (automatisch)"));
  await click(button("en (automatisch)"));
  expect(waehlen).toHaveBeenCalledWith(1);
  expect(host.querySelector('[role="menu"]')).toBeNull();
  expect(document.activeElement).toBe(button("Untertitel"));
  await click(button("Untertitel"));
  expect(button("en (automatisch)").getAttribute("aria-checked")).toBe("true");
  await act(() => document.activeElement?.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })));
  expect(host.querySelector('[role="menu"]')).toBeNull();
  expect(document.activeElement).toBe(button("Untertitel"));
});

it("schließt Einstellungen über denselben Knopf und außerhalb, ohne sich wieder zu öffnen", async () => {
  function Beispiel() {
    const bereich = useRef<HTMLDivElement>(null);
    const [offen, setOffen] = useState(false);
    return <div ref={bereich}><PlayerEinstellungen offen={offen} aufOffen={setOffen} bereich={bereich}
      angebote={[{ value: "auto", label: "Automatisch" }]} qualitaet="auto" bezeichnung="Automatisch"
      aufQualitaet={() => {}} tempo={3} aufTempo={() => {}} /><button>Außerhalb</button></div>;
  }
  await act(() => root.render(<Beispiel />));
  await click(button("Wiedergabeeinstellungen"));
  expect(host.querySelector('[role="menu"]')).not.toBeNull();
  await click(button("Wiedergabeeinstellungen"));
  expect(host.querySelector('[role="menu"]')).toBeNull();
  await click(button("Wiedergabeeinstellungen"));
  const tempo = [...host.querySelectorAll<HTMLButtonElement>('[role="menuitem"]')].find((el) => el.textContent?.startsWith("Geschwindigkeit"))!;
  await click(tempo);
  expect(document.activeElement).toBe(button("3×"));
  expect(button("3×").getAttribute("aria-checked")).toBe("true");
  await act(() => button("Außerhalb").dispatchEvent(new Event("pointerdown", { bubbles: true })));
  expect(host.querySelector('[role="menu"]')).toBeNull();
});

it("zeigt das Touch-Menü außerhalb des kleinen Videos und behält die Auswahl beim Drehen im Vollbild", async () => {
  const waehlen = vi.fn();
  function Beispiel({ vollbild }: { vollbild: boolean }) {
    const bereich = useRef<HTMLDivElement>(null);
    const [offen, setOffen] = useState(false);
    return <div ref={bereich}><PlayerEinstellungen offen={offen} aufOffen={setOffen} bereich={bereich}
      touch vollbild={vollbild} angebote={[{ value: "auto", label: "Automatisch" }]} qualitaet="auto"
      bezeichnung="Automatisch" aufQualitaet={() => {}} tempo={1} aufTempo={waehlen} /></div>;
  }
  await act(() => root.render(<Beispiel vollbild={false} />));
  await click(button("Wiedergabeeinstellungen"));
  expect(host.querySelector('[role="menu"]')).toBeNull();
  const blatt = document.body.querySelector('.player-menue-blatt')!;
  expect(blatt.parentElement).toBe(document.body);
  const tempo = [...blatt.querySelectorAll<HTMLButtonElement>('[role="menuitem"]')].find(el => el.textContent?.startsWith("Geschwindigkeit"))!;
  await click(tempo);
  expect(document.activeElement?.textContent).toBe("Normal");
  await act(() => root.render(<Beispiel vollbild />));
  expect(document.body.querySelector('.player-menue-blatt')).toBeNull();
  expect(host.querySelector('[role="menu"]')?.getAttribute("aria-label")).toBe("Wiedergabegeschwindigkeit");
  await click(button("1.5×"));
  expect(waehlen).toHaveBeenCalledExactlyOnceWith(1.5);
  expect(host.querySelector('[role="menu"]')).toBeNull();
  expect(document.activeElement).toBe(button("Wiedergabeeinstellungen"));
});
