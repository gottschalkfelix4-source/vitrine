// @vitest-environment jsdom
import { act, StrictMode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api, type Suchergebnis, type Untertitelfund, type VideoKurz } from "../lib/api";
import { useSuchstapel, type Suchbereich } from "./useSuchstapel";
import { Suchseite } from "../pages/Suche";

let root: Root;
let host: HTMLDivElement;
let wert: ReturnType<typeof useSuchstapel>;
const video = (id: string): VideoKurz => ({
  id, titel: id, kanal_id: null, kanal_name: null, dauer_s: 100, hochgeladen: null, aufrufe: null, bild: null,
  hoehe: null, breite: null, fps: null, status: "archived", ist_short: false, war_live: false,
  gesehen: false, fortschritt_s: 0, fortschritt_anteil: null, buendel_bytes: null, recodiert: false,
});
const fund = (id: string, sprache = "de"): Untertitelfund => ({ video: video(id), start_s: 10, sprache, zeile: "Gesprochener Text" });
const seite = (videos: string[], untertitel: Untertitelfund[] = [], weitereVideos = false, weitereUntertitel = false): Suchergebnis => ({
  anfrage: "test", videos: videos.map(video), im_gesprochenen: untertitel, zu_kurz: false,
  has_more: { videos: weitereVideos, untertitel: weitereUntertitel },
});
function spaeter<T>() {
  let fertig!: (wert: T) => void;
  const promise = new Promise<T>((resolve) => { fertig = resolve; });
  return { promise, fertig };
}
function Probe({ q, bereich }: { q: string; bereich: Suchbereich }) {
  wert = useSuchstapel(q, bereich, 2);
  return null;
}
async function render(q = "test", bereich: Suchbereich = "alle", strikt = false) {
  await act(async () => root.render(strikt ? <StrictMode><Probe q={q} bereich={bereich} /></StrictMode> : <Probe q={q} bereich={bereich} />));
}
beforeEach(() => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});
afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

it("lädt beide Trefferarten ohne Duplikate und zählt den Server-Offset unabhängig von der Anzeige", async () => {
  const zweite = spaeter<Suchergebnis>();
  const suchen = vi.spyOn(api, "suchen").mockResolvedValueOnce(seite(["a", "b"], [fund("a")], true, true))
    .mockReturnValueOnce(zweite.promise).mockResolvedValueOnce(seite(["d", "e"], [fund("d")], false, false));
  await render();
  await act(async () => { wert.mehrLaden(); wert.mehrLaden(); wert.mehrLaden(); });
  expect(suchen).toHaveBeenCalledTimes(2);
  await act(async () => zweite.fertig(seite(["b", "c"], [fund("a"), fund("a", "en")], true, true)));
  expect(wert.daten?.videos.map((v) => v.id)).toEqual(["a", "b", "c"]);
  expect(wert.daten?.im_gesprochenen).toHaveLength(2);
  await act(async () => wert.mehrLaden());
  expect(suchen.mock.calls.map(([, , offset]) => offset)).toEqual([0, 2, 4]);
  expect(wert.ende).toBe(true);
  await act(async () => wert.mehrLaden());
  expect(suchen).toHaveBeenCalledTimes(3);
});

it("beendet eine ausgeschöpfte Ansicht und setzt Untertiteltreffer nach dem Bereichswechsel fort", async () => {
  const suchen = vi.spyOn(api, "suchen").mockResolvedValueOnce(seite(["a"], [fund("a"), fund("b")], false, true))
    .mockResolvedValueOnce(seite([], [fund("c")], false, false));
  await render("test", "videos");
  expect(wert.ende).toBe(true);
  await act(async () => wert.mehrLaden());
  expect(suchen).toHaveBeenCalledTimes(1);
  await render("test", "gesprochen");
  expect(wert.ende).toBe(false);
  expect(wert.daten?.videos).toHaveLength(1);
  await act(async () => wert.mehrLaden());
  expect(suchen).toHaveBeenLastCalledWith("test", 2, 2);
  expect(wert.daten?.im_gesprochenen).toHaveLength(3);
  expect(wert.ende).toBe(true);
  await render("test", "alle");
  expect(wert.ende).toBe(true);
  expect(suchen).toHaveBeenCalledTimes(2);
});

it("behält bereits gefundene Treffer bei Fehlern und wiederholt dieselbe Seite", async () => {
  const suchen = vi.spyOn(api, "suchen").mockResolvedValueOnce(seite(["a"], [fund("a")], true, true))
    .mockRejectedValueOnce(new Error("offline")).mockResolvedValueOnce(seite(["b"], [fund("b")]));
  await render();
  await act(async () => wert.mehrLaden());
  expect(wert.fehler).toBe("offline");
  expect(wert.daten?.videos.map((v) => v.id)).toEqual(["a"]);
  expect(wert.daten?.im_gesprochenen).toHaveLength(1);
  await act(async () => wert.mehrLaden());
  expect(suchen.mock.calls.map(([, , offset]) => offset)).toEqual([0, 2, 2]);
  expect(wert.fehler).toBeNull();
  expect(wert.daten?.videos.map((v) => v.id)).toEqual(["a", "b"]);
});

it("ignoriert verspätete Suchantworten nach einem neuen Suchbegriff", async () => {
  const alt = spaeter<Suchergebnis>();
  const neu = spaeter<Suchergebnis>();
  vi.spyOn(api, "suchen").mockReturnValueOnce(alt.promise).mockReturnValueOnce(neu.promise);
  await render("alt");
  await render("neu");
  expect(wert.daten).toBeUndefined();
  await act(async () => neu.fertig(seite(["aktuell"])));
  await act(async () => alt.fertig(seite(["veraltet"])));
  expect(wert.daten?.videos.map((v) => v.id)).toEqual(["aktuell"]);
});

it("verwirft die erste StrictMode-Anfrage und startet nach Unmount keine neue Anfrage", async () => {
  const alt = spaeter<Suchergebnis>();
  const suchen = vi.spyOn(api, "suchen").mockReturnValueOnce(alt.promise).mockResolvedValueOnce(seite(["aktuell"], [], true));
  await render("test", "alle", true);
  await act(async () => alt.fertig(seite(["veraltet"])));
  expect(wert.daten?.videos.map((v) => v.id)).toEqual(["aktuell"]);
  const vorher = wert;
  await act(async () => root.render(null));
  vorher.mehrLaden();
  expect(suchen).toHaveBeenCalledTimes(2);
});

it("fragt leere Suchbegriffe nicht an und behandelt eine zu kurze Suche als abgeschlossen", async () => {
  const suchen = vi.spyOn(api, "suchen").mockResolvedValue({ ...seite([]), zu_kurz: true });
  await render("   ");
  expect(suchen).not.toHaveBeenCalled();
  expect(wert.laedt).toBe(false);
  expect(wert.ende).toBe(true);
  await render(" ab ");
  expect(suchen).toHaveBeenCalledWith("ab", 2, 0);
  expect(wert.daten?.zu_kurz).toBe(true);
  expect(wert.ende).toBe(true);
});

it("lädt beim Scrollen Suchtreffer nach und lässt vorhandene Treffer bei einem Fehler bedienbar", async () => {
  let sichtbar!: () => void;
  vi.stubGlobal("IntersectionObserver", class {
    observe() {}
    disconnect() {}
    constructor(callback: IntersectionObserverCallback) {
      sichtbar = () => callback([{ isIntersecting: true } as IntersectionObserverEntry], this as unknown as IntersectionObserver);
    }
  });
  const suchen = vi.spyOn(api, "suchen").mockResolvedValueOnce(seite(["erstes-video"], [fund("untertitel-video")], true, true))
    .mockRejectedValueOnce(new Error("Verbindung unterbrochen")).mockResolvedValueOnce(seite(["zweites-video"], []));
  await act(async () => root.render(<MemoryRouter initialEntries={["/suche?q=test"]}><Suchseite /></MemoryRouter>));
  await act(async () => sichtbar());
  expect(suchen).toHaveBeenLastCalledWith("test", 40, 40);
  expect(host.querySelector('a[href="/video/erstes-video"]')).not.toBeNull();
  expect(host.querySelector('a[href="/video/untertitel-video?t=10"]')).not.toBeNull();
  expect(host.textContent).toContain("Weitere Treffer konnten nicht geladen werden.");
  const erneut = [...host.querySelectorAll("button")].find((button) => button.textContent === "Erneut versuchen")!;
  await act(async () => erneut.click());
  expect(suchen.mock.calls.map(([, , offset]) => offset)).toEqual([0, 40, 40]);
  expect(host.textContent).toContain("3 Treffer · Alle geladen");
  expect(host.querySelector('a[href="/video/zweites-video"]')).not.toBeNull();
});
