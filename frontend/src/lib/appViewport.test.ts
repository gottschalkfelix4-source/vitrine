// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { appViewportBeobachten, dokumentScrollKorrekturen } from "./appViewport";

let viewport: EventTarget & { width: number; height: number; offsetTop: number; scale: number };
let beenden: (() => void) | undefined;
const hoehe = () => document.documentElement.style.getPropertyValue("--app-viewport-hoehe");
/** jsdom kennt keine Layoutbreite; sie wird für die Zoom-Erkennung vorgegeben. */
const layoutBreite = (px: number) => Object.defineProperty(document.documentElement, "clientWidth", { configurable: true, get: () => px });
/** Ebenso die Dokumenthöhen, an denen der Scrollspielraum hängt. */
const dokument = (inhalt: number, sichtbar: number) => {
  Object.defineProperty(document.documentElement, "scrollHeight", { configurable: true, get: () => inhalt });
  Object.defineProperty(document.documentElement, "clientHeight", { configurable: true, get: () => sichtbar });
};

beforeEach(() => {
  vi.useFakeTimers();
  viewport = Object.assign(new EventTarget(), { width: 440, height: 894, offsetTop: 0, scale: 1 });
  vi.stubGlobal("visualViewport", viewport);
  vi.stubGlobal("innerHeight", 894);
  vi.stubGlobal("scrollY", 0);
  vi.stubGlobal("scrollTo", vi.fn());
  vi.stubGlobal("matchMedia", () => ({ matches: true }));
  vi.stubGlobal("requestAnimationFrame", (fn: FrameRequestCallback) => window.setTimeout(() => fn(0), 16));
  vi.stubGlobal("cancelAnimationFrame", (id: number) => window.clearTimeout(id));
});

afterEach(() => {
  beenden?.();
  beenden = undefined;
  vi.useRealTimers();
  vi.unstubAllGlobals();
  for (const name of ["clientWidth", "scrollHeight", "clientHeight"]) Reflect.deleteProperty(document.documentElement, name);
});

it("begrenzt die App auf das Webview, auch wenn der Bildschirm höher ist", () => {
  vi.stubGlobal("screen", { height: 956, width: 440 });
  beenden = appViewportBeobachten();
  expect(hoehe()).toBe("894px");
});

it("überschreitet weder Fensterhöhe noch sichtbare Unterkante", () => {
  viewport.height = 956;
  beenden = appViewportBeobachten();
  expect(hoehe()).toBe("894px");
  viewport.height = 820;
  viewport.dispatchEvent(new Event("resize"));
  vi.runAllTimers();
  expect(hoehe()).toBe("820px");
});

it("zieht nach Drehen und Wiederöffnen der PWA die neue Höhe nach", () => {
  beenden = appViewportBeobachten();
  vi.stubGlobal("innerHeight", 440);
  viewport.height = 440;
  window.dispatchEvent(new Event("resize"));
  vi.runAllTimers();
  expect(hoehe()).toBe("440px");
  vi.stubGlobal("innerHeight", 956);
  viewport.height = 956;
  window.dispatchEvent(new Event("pageshow"));
  vi.runAllTimers();
  expect(hoehe()).toBe("956px");
});

it("hält die Navigation bei Tastatur und verschobenem Viewport sichtbar", () => {
  beenden = appViewportBeobachten();
  viewport.height = 490;
  viewport.offsetTop = 30;
  viewport.dispatchEvent(new Event("resize"));
  vi.runAllTimers();
  expect(hoehe()).toBe("520px");
  viewport.offsetTop = 45;
  viewport.dispatchEvent(new Event("scroll"));
  vi.runAllTimers();
  expect(hoehe()).toBe("535px");
});

it("lässt Pinch-Zoom zu, ohne das Layout zu verkleinern", () => {
  layoutBreite(440);
  beenden = appViewportBeobachten();
  Object.assign(viewport, { scale: 2, width: 220, height: 447 });
  viewport.dispatchEvent(new Event("resize"));
  vi.runAllTimers();
  expect(hoehe()).toBe("894px");
});

it("misst nach dem Drehen auch dann, wenn iOS eine falsche Skala meldet", () => {
  // Das iPhone meldet in der PWA quer dauerhaft scale = Breite/Höhe, obwohl
  // nichts vergrößert ist: Ausschnitt und Layout sind gleich breit.
  layoutBreite(440);
  beenden = appViewportBeobachten();
  layoutBreite(956);
  vi.stubGlobal("innerHeight", 440);
  Object.assign(viewport, { scale: 956 / 440, width: 956, height: 440 });
  window.dispatchEvent(new Event("orientationchange"));
  vi.runAllTimers();
  expect(hoehe()).toBe("440px");
});

it("übergeht beim Drehen die Bilder, in denen der Ausschnitt noch die alte Breite hat", () => {
  layoutBreite(440);
  beenden = appViewportBeobachten();
  layoutBreite(956);
  vi.stubGlobal("innerHeight", 440);
  viewport.height = 440;
  window.dispatchEvent(new Event("orientationchange"));
  vi.advanceTimersByTime(100);
  expect(hoehe()).toBe("894px");
  viewport.width = 956;
  vi.runAllTimers();
  expect(hoehe()).toBe("440px");
});

it("setzt einen Scrollstand ohne Spielraum zurück, wie ihn das iPhone nach dem Zurückdrehen hinterlässt", () => {
  dokument(894, 894);
  beenden = appViewportBeobachten();
  const vorher = dokumentScrollKorrekturen();
  vi.mocked(window.scrollTo).mockImplementation(() => vi.stubGlobal("scrollY", 0));
  vi.stubGlobal("scrollY", 62);
  window.dispatchEvent(new Event("scroll"));
  vi.runAllTimers();
  expect(window.scrollTo).toHaveBeenCalledWith(0, 0);
  expect(dokumentScrollKorrekturen()).toBe(vorher + 1);
});

it("lässt einen Scrollstand innerhalb des Spielraums in Ruhe", () => {
  dokument(1200, 894);
  vi.stubGlobal("scrollY", 100);
  beenden = appViewportBeobachten();
  window.dispatchEvent(new Event("scroll"));
  vi.runAllTimers();
  expect(window.scrollTo).not.toHaveBeenCalled();
});

it("behält bei ungültigen Start- oder Wechselwerten die letzte gültige Höhe", () => {
  beenden = appViewportBeobachten();
  viewport.height = 0;
  viewport.dispatchEvent(new Event("resize"));
  vi.runAllTimers();
  expect(hoehe()).toBe("894px");
});

it("nutzt ohne VisualViewport die Fensterhöhe", () => {
  vi.stubGlobal("visualViewport", null);
  beenden = appViewportBeobachten();
  expect(hoehe()).toBe("894px");
});

it("erkennt den iOS-Standalone-Fallback", () => {
  vi.stubGlobal("matchMedia", () => ({ matches: false }));
  vi.stubGlobal("navigator", { standalone: true });
  beenden = appViewportBeobachten();
  expect(hoehe()).toBe("894px");
});

it("lässt normale Browserfenster unverändert", () => {
  vi.stubGlobal("matchMedia", () => ({ matches: false }));
  beenden = appViewportBeobachten();
  expect(hoehe()).toBe("");
  window.dispatchEvent(new Event("resize"));
  vi.runAllTimers();
  expect(hoehe()).toBe("");
});

it("entfernt Listener und ausstehende Messungen beim Beenden", () => {
  beenden = appViewportBeobachten();
  window.dispatchEvent(new Event("resize"));
  beenden();
  vi.runAllTimers();
  viewport.dispatchEvent(new Event("resize"));
  window.dispatchEvent(new Event("pageshow"));
  vi.runAllTimers();
  expect(hoehe()).toBe("");
});

it("misst nach dem Drehen weiter, bis iOS die neue Lage meldet", () => {
  beenden = appViewportBeobachten();
  expect(hoehe()).toBe("894px");
  // iOS meldet direkt nach dem Ereignis noch die Maße der alten Lage.
  window.dispatchEvent(new Event("orientationchange"));
  vi.advanceTimersByTime(100);
  expect(hoehe()).toBe("894px");
  // Erst einige Bilder später steht der Umbau.
  vi.stubGlobal("innerHeight", 440);
  viewport.height = 440;
  vi.runAllTimers();
  expect(hoehe()).toBe("440px");
});

it("nimmt eine späte Korrektur auch ohne weiteres Ereignis noch mit", () => {
  beenden = appViewportBeobachten();
  viewport.dispatchEvent(new Event("resize"));
  vi.advanceTimersByTime(320);
  vi.stubGlobal("innerHeight", 500);
  viewport.height = 500;
  vi.runAllTimers();
  expect(hoehe()).toBe("500px");
});
