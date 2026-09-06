// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { usePlayerAusrichtung } from "./usePlayerAusrichtung";

let root: Root;
let host: HTMLDivElement;
let orientierung: EventTarget & { type: string };
beforeEach(() => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  orientierung = Object.assign(new EventTarget(), { type: "portrait-primary" });
  vi.stubGlobal("screen", { orientation: orientierung, width: 390, height: 844 });
  vi.stubGlobal("matchMedia", () => ({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() }));
  host = document.createElement("div"); root = createRoot(host);
});
afterEach(async () => { await act(() => root.unmount()); vi.unstubAllGlobals(); });
function Probe() { const { touch, quer } = usePlayerAusrichtung(); return <div data-touch={touch} data-quer={quer} />; }

it("folgt beiden Drehrichtungen und verwechselt die Tastatur nicht mit Querformat", async () => {
  await act(() => root.render(<Probe />));
  expect(host.firstElementChild?.getAttribute("data-quer")).toBe("false");
  vi.stubGlobal("innerHeight", 200);
  await act(() => window.dispatchEvent(new Event("resize")));
  expect(host.firstElementChild?.getAttribute("data-quer")).toBe("false");
  orientierung.type = "landscape-secondary";
  await act(() => orientierung.dispatchEvent(new Event("change")));
  expect(host.firstElementChild?.getAttribute("data-quer")).toBe("true");
  orientierung.type = "portrait-primary";
  await act(() => orientierung.dispatchEvent(new Event("change")));
  expect(host.firstElementChild?.getAttribute("data-quer")).toBe("false");
});

it("verwendet auf älteren iPhones den Orientierungswinkel", async () => {
  vi.stubGlobal("screen", { width: 390, height: 844 });
  vi.stubGlobal("orientation", 90);
  await act(() => root.render(<Probe />));
  expect(host.firstElementChild?.getAttribute("data-quer")).toBe("true");
  vi.stubGlobal("orientation", 0);
  await act(() => window.dispatchEvent(new Event("orientationchange")));
  expect(host.firstElementChild?.getAttribute("data-quer")).toBe("false");
});
