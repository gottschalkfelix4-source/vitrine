// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { PlayerTaste } from "./PlayerTaste";

let root: Root;
let host: HTMLDivElement;
beforeEach(() => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  host = document.createElement("div"); document.body.append(host); root = createRoot(host);
});
afterEach(async () => { await act(() => root.unmount()); host.remove(); vi.unstubAllGlobals(); });
async function zeiger(typ: string, x = 20, pointerType = "touch") {
  const e = Object.assign(new MouseEvent(typ, { bubbles: true, cancelable: true, clientX: x, clientY: 20, button: 0 }), { pointerId: 1, pointerType, isPrimary: true });
  await act(() => host.querySelector('button')!.dispatchEvent(e));
}
async function starten() {
  const aktion = vi.fn();
  await act(() => root.render(<PlayerTaste onClick={aktion}>Aktion</PlayerTaste>));
  host.querySelector('button')!.getBoundingClientRect = () => new DOMRect(0, 0, 100, 44);
  return aktion;
}

it("führt einen Touch ohne synthetischen Klick aus und ignoriert dessen spätere Wiederholung", async () => {
  const aktion = await starten();
  await zeiger("pointerdown"); await zeiger("pointerup");
  expect(aktion).toHaveBeenCalledTimes(1);
  await act(() => host.querySelector('button')!.dispatchEvent(new MouseEvent("click", { bubbles: true, detail: 1 })));
  expect(aktion).toHaveBeenCalledTimes(1);
  await act(() => host.querySelector('button')!.click());
  expect(aktion).toHaveBeenCalledTimes(2); // Tastatur/assistive Aktivierung bleibt möglich.
  await zeiger("pointerdown", 20, "mouse"); await zeiger("pointerup", 20, "mouse");
  await act(() => host.querySelector('button')!.dispatchEvent(new MouseEvent("click", { bubbles: true, detail: 1 })));
  expect(aktion).toHaveBeenCalledTimes(3);
});

it("löst beim Scrollen, Abbrechen oder Loslassen außerhalb der Taste keine Aktion aus", async () => {
  const aktion = await starten();
  await zeiger("pointerdown"); await zeiger("pointermove", 50); await zeiger("pointerup", 50);
  await zeiger("pointerdown"); await zeiger("pointercancel"); await zeiger("pointerup");
  await zeiger("pointerdown"); await zeiger("pointerup", 150);
  await act(() => host.querySelector('button')!.dispatchEvent(new MouseEvent("click", { bubbles: true, detail: 1 })));
  expect(aktion).not.toHaveBeenCalled();
});

it("führt Mausaktionen weiterhin nur über den Klick aus", async () => {
  const aktion = await starten();
  await zeiger("pointerdown", 20, "mouse"); await zeiger("pointerup", 20, "mouse");
  expect(aktion).not.toHaveBeenCalled();
  await act(() => host.querySelector('button')!.dispatchEvent(new MouseEvent("click", { bubbles: true, detail: 1 })));
  expect(aktion).toHaveBeenCalledTimes(1);
});

it("lässt den nachgereichten Klick nicht auf einen neuen Menühintergrund durchfallen", async () => {
  await starten(); await zeiger("pointerdown"); await zeiger("pointerup");
  const hintergrund = document.createElement("button");
  const schliessen = vi.fn();
  hintergrund.addEventListener("click", schliessen); document.body.append(hintergrund);
  hintergrund.dispatchEvent(new MouseEvent("click", { bubbles: true, detail: 1 }));
  expect(schliessen).not.toHaveBeenCalled();
  hintergrund.dispatchEvent(new MouseEvent("pointerdown", { bubbles: true }));
  hintergrund.dispatchEvent(new MouseEvent("click", { bubbles: true, detail: 1 }));
  expect(schliessen).toHaveBeenCalledTimes(1);
  hintergrund.remove();
});
