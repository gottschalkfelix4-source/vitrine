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

it("verwirft einen normalen Klick nicht, wenn Safari den vorherigen Pointer-Start ausgelassen hat", async () => {
  const aktion = await starten();
  await zeiger("pointerup");
  await act(() => host.querySelector('button')!.dispatchEvent(new MouseEvent("click", { bubbles: true, detail: 1 })));
  expect(aktion).toHaveBeenCalledTimes(1);
});

async function touch(typ: string, x = 20) {
  const punkt = { identifier: 7, clientX: x, clientY: 20 };
  const e = Object.assign(new Event(typ, { bubbles: true, cancelable: true }), {
    changedTouches: [punkt], touches: typ === "touchend" || typ === "touchcancel" ? [] : [punkt],
  });
  await act(() => host.querySelector('button')!.dispatchEvent(e));
}

it("lässt den Klick auch bei einem Touch-Ende ohne Start als Rückfallweg zu", async () => {
  const aktion = await starten();
  await touch("touchend");
  await act(() => host.querySelector('button')!.dispatchEvent(new MouseEvent("click", { bubbles: true, detail: 1 })));
  expect(aktion).toHaveBeenCalledTimes(1);
});

it("bedient die Taste auch ausschließlich über Touch-Events", async () => {
  const aktion = await starten();
  await touch("touchstart"); await touch("touchend");
  expect(aktion).toHaveBeenCalledTimes(1);
  await act(() => host.querySelector('button')!.dispatchEvent(new MouseEvent("click", { bubbles: true, detail: 1 })));
  expect(aktion).toHaveBeenCalledTimes(1);
  await touch("touchstart"); await touch("touchend");
  expect(aktion).toHaveBeenCalledTimes(2);
});

it("führt bei Pointer- und Touch-Events genau eine Aktion aus und lässt Scrollen zu", async () => {
  const aktion = await starten();
  await zeiger("pointerdown"); await touch("touchstart");
  await zeiger("pointerup"); await touch("touchend");
  expect(aktion).toHaveBeenCalledTimes(1);
  await touch("touchstart"); await touch("touchmove", 50); await touch("touchend", 50);
  await act(() => host.querySelector('button')!.dispatchEvent(new MouseEvent("click", { bubbles: true, detail: 1 })));
  expect(aktion).toHaveBeenCalledTimes(1);
});

async function mehrfinger(typ: string, kennung = 7) {
  const punkt = { identifier: kennung, clientX: 20, clientY: 20 };
  const zweiter = { identifier: 8, clientX: 60, clientY: 60 };
  const e = Object.assign(new Event(typ, { bubbles: true, cancelable: true }), {
    changedTouches: [punkt], touches: typ === "touchstart" ? [punkt, zweiter] : [],
  });
  await act(() => host.querySelector('button')!.dispatchEvent(e));
}

it("überlässt die Aktion dem Klick, wenn der Touch-Weg keine Berührung merken konnte", async () => {
  const aktion = await starten();
  // Eine zweite Berührung auf dem Schirm - der eigene Touch-Weg steigt aus.
  await mehrfinger("touchstart"); await mehrfinger("touchend");
  expect(aktion).not.toHaveBeenCalled();
  await act(() => host.querySelector('button')!.dispatchEvent(new MouseEvent("click", { bubbles: true, detail: 1 })));
  expect(aktion).toHaveBeenCalledTimes(1);
});

it("überlässt die Aktion dem Klick, wenn das Touch-Ende eine andere Kennung meldet", async () => {
  const aktion = await starten();
  await touch("touchstart");
  const e = Object.assign(new Event("touchend", { bubbles: true, cancelable: true }), {
    changedTouches: [{ identifier: 99, clientX: 20, clientY: 20 }], touches: [],
  });
  await act(() => host.querySelector('button')!.dispatchEvent(e));
  expect(aktion).not.toHaveBeenCalled();
  await act(() => host.querySelector('button')!.dispatchEvent(new MouseEvent("click", { bubbles: true, detail: 1 })));
  expect(aktion).toHaveBeenCalledTimes(1);
});
