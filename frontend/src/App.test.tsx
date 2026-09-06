// @vitest-environment jsdom
import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import App from "./App";
import { SeitenFehlergrenze } from "./components/SeitenFehlergrenze";

const sitzung = vi.hoisted(() => ({ admin: false }));
vi.mock("./components/Anmeldung", () => ({
  useAdmin: () => sitzung.admin,
  useAnmeldung: () => ({ art: "bereit", sitzung: { angemeldet: sitzung.admin } }),
  Sitzungsverwaltung: ({ children }: { children: ReactNode }) => children,
  AnmeldeSchranke: ({ children }: { children: ReactNode }) => children,
  Anmeldung: () => <p>Anmeldung</p>,
  Abmelden: () => <button>Abmelden</button>,
}));
vi.mock("./components/Fortschritt", () => ({ Fortschrittsleiste: () => null }));
vi.mock("./hooks/useMedienabfrage", () => ({ SCHMAL: "mobil", useMedienabfrage: (abfrage: string) => abfrage === "mobil" }));
vi.mock("./hooks/useApi", () => ({
  useApi: () => ({ daten: [], laedt: false, fehler: null, neuLaden: vi.fn() }),
  useVideostapel: () => ({ videos: [], laedt: false, ende: true, fehler: null, neuLaden: vi.fn(), mehrLaden: vi.fn() }),
}));

let root: Root;
let host: HTMLDivElement;
beforeEach(() => {
  vi.useFakeTimers();
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  vi.stubGlobal("requestAnimationFrame", (fn: FrameRequestCallback) => window.setTimeout(() => fn(0), 0));
  vi.stubGlobal("cancelAnimationFrame", (id: number) => window.clearTimeout(id));
  HTMLElement.prototype.scrollTo = vi.fn();
  sitzung.admin = false;
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});
afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});
async function render(pfad = "/") {
  await act(async () => root.render(<MemoryRouter initialEntries={[pfad]}><App /></MemoryRouter>));
  await act(async () => vi.runOnlyPendingTimers());
}
async function click(selector: string) {
  const el = host.querySelector<HTMLElement>(selector);
  expect(el).not.toBeNull();
  await act(async () => el!.click());
  await act(async () => vi.runOnlyPendingTimers());
}

it("begrenzt den mobilen Menüfokus, sperrt den Hintergrund und gibt den Fokus nach Escape zurück", async () => {
  sitzung.admin = true;
  await render();
  await click("#menue-knopf");
  const nav = host.querySelector("#hauptnavigation")!;
  const schliessen = nav.querySelector("button")!;
  expect(document.activeElement).toBe(schliessen);
  expect(host.querySelector("main")?.hasAttribute("inert")).toBe(true);
  expect(host.querySelector("header")?.hasAttribute("inert")).toBe(true);
  expect(nav.querySelector(".leiste-abmelden button")?.textContent).toBe("Abmelden");
  await act(async () => schliessen.dispatchEvent(new KeyboardEvent("keydown", { key: "Tab", shiftKey: true, bubbles: true })));
  expect(document.activeElement).toBe(nav.querySelector(".leiste-abmelden button"));
  await act(async () => document.activeElement?.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })));
  await act(async () => vi.runOnlyPendingTimers());
  expect(host.querySelector("main")?.hasAttribute("inert")).toBe(false);
  expect(document.activeElement).toBe(host.querySelector("#menue-knopf"));
});

it("öffnet die Suche erneut, wenn derselbe mobile Suchlink noch einmal gewählt wird", async () => {
  await render("/suche");
  expect(document.activeElement).toBe(host.querySelector("input[type='search']"));
  await click(".suche-zurueck");
  expect(host.querySelector("header")?.getAttribute("data-suche-offen")).toBe("false");
  await click(".mobile-navigation a[href='/suche']");
  expect(host.querySelector("header")?.getAttribute("data-suche-offen")).toBe("true");
  expect(document.activeElement).toBe(host.querySelector("input[type='search']"));
});

it("zeigt bei einem Seitenfehler einen Ausweg und setzt die Fehlergrenze beim Seitenwechsel zurück", async () => {
  vi.spyOn(console, "error").mockImplementation(() => {});
  function Defekt(): ReactNode { throw new Error("Testfehler"); }
  await act(async () => root.render(<MemoryRouter><SeitenFehlergrenze key="defekt"><Defekt /></SeitenFehlergrenze></MemoryRouter>));
  expect(host.querySelector("[role='alert']")?.textContent).toContain("Die Seite konnte nicht angezeigt werden");
  expect(host.querySelector("a[href='/']")).not.toBeNull();
  await act(async () => root.render(<MemoryRouter><SeitenFehlergrenze key="gesund"><p>Archiv</p></SeitenFehlergrenze></MemoryRouter>));
  expect(host.textContent).toBe("Archiv");
});
