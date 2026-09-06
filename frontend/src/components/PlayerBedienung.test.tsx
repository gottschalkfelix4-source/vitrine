// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { Player } from "./Player";

// Die Tests bedienen den Player bereits während die Quelle noch lädt.
vi.mock("../lib/wiedergabe", () => ({ wiedergabeStarten: () => new Promise(() => {}) }));

let root: Root;
let host: HTMLDivElement;
let orientierung: EventTarget & { type: string };
let nativesVollbild: ReturnType<typeof vi.fn>;
beforeEach(() => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  orientierung = Object.assign(new EventTarget(), { type: "portrait-primary" });
  vi.stubGlobal("screen", { orientation: orientierung });
  vi.stubGlobal("matchMedia", () => ({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() }));
  nativesVollbild = vi.fn().mockRejectedValue(new Error("Keine native Vollbild-API"));
  Object.defineProperty(HTMLElement.prototype, "requestFullscreen", { configurable: true, value: nativesVollbild });
  host = document.createElement("div"); document.body.append(host);
  root = createRoot(host);
});
afterEach(async () => { await act(() => root.unmount()); host.remove(); Reflect.deleteProperty(HTMLElement.prototype, "requestFullscreen"); vi.unstubAllGlobals(); });
function player() { return host.querySelector('.player')!; }
async function drehen(type: string) {
  orientierung.type = type;
  await act(() => orientierung.dispatchEvent(new Event("change")));
}
async function klick(name: string) {
  await act(() => host.querySelector<HTMLButtonElement>(`button[aria-label="${name}"]`)!.click());
}

it("wechselt beim Drehen in beide Ansichten, ohne das Videoelement zu ersetzen", async () => {
  await act(() => root.render(<Player videoId="eins" />));
  const video = host.querySelector('video');
  expect(player().getAttribute("data-app-vollbild")).toBe("false");
  await drehen("landscape-primary");
  expect(player().getAttribute("data-app-vollbild")).toBe("true");
  await drehen("portrait-primary");
  expect(player().getAttribute("data-app-vollbild")).toBe("false");
  expect(host.querySelector('video')).toBe(video);
  expect(nativesVollbild).not.toHaveBeenCalled();
});

it("lässt sich auf dem iPhone manuell öffnen und schließen und respektiert den Miniplayer", async () => {
  await act(() => root.render(<Player videoId="eins" />));
  await klick("Vollbild");
  expect(player().getAttribute("data-app-vollbild")).toBe("true");
  await klick("Vollbild beenden");
  expect(player().getAttribute("data-app-vollbild")).toBe("false");
  await drehen("landscape-primary");
  await act(() => root.render(<Player videoId="eins" minimiert />));
  expect(player().getAttribute("data-app-vollbild")).toBe("false");
  await drehen("portrait-primary"); await drehen("landscape-secondary");
  expect(player().getAttribute("data-app-vollbild")).toBe("false");
  await act(() => root.render(<Player videoId="eins" />));
  expect(player().getAttribute("data-app-vollbild")).toBe("true");
  expect(nativesVollbild).not.toHaveBeenCalled();
});
