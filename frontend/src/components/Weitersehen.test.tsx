// @vitest-environment jsdom
import { act, useEffect } from "react";
import { createRoot, type Root } from "react-dom/client";
import { Link, MemoryRouter, useLocation, useParams } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { Weitersehen } from "./Weitersehen";
import { SeitenFehlergrenze } from "./SeitenFehlergrenze";

const montiert = vi.hoisted(() => ({ start: vi.fn(), ende: vi.fn() }));
vi.mock("../pages/Wiedergabe", () => ({ Wiedergabeseite: (p: {
  minimiert: boolean; aufMinimieren: () => void; aufVergroessern: () => void; aufSchliessen: () => void;
}) => {
  const { videoId } = useParams();
  useEffect(() => { montiert.start(videoId); return () => montiert.ende(videoId); }, [videoId]);
  return <section data-mini={p.minimiert}><video data-video={videoId} />
    <button onClick={p.aufMinimieren}>Minimieren</button>
    <button onClick={p.aufVergroessern}>Vergrößern</button>
    <button onClick={p.aufSchliessen}>Schließen</button>
  </section>;
} }));

let root: Root;
let host: HTMLDivElement;
beforeEach(() => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  montiert.start.mockClear(); montiert.ende.mockClear();
  host = document.createElement("div"); document.body.append(host);
  root = createRoot(host);
});
afterEach(async () => { await act(() => root.unmount()); host.remove(); vi.unstubAllGlobals(); });
async function starten(pfad = "/kanaele") {
  function Seiten() {
    const ort = useLocation();
    return <Weitersehen><SeitenFehlergrenze key={ort.pathname}><nav>
      <Link to="/video/eins">Erstes Video</Link><Link to="/video/zwei">Zweites Video</Link>
      <Link to="/video/eins?t=30">Zeitstempel</Link><Link to="/suche">Suche</Link>
    </nav></SeitenFehlergrenze></Weitersehen>;
  }
  await act(async () => root.render(<MemoryRouter initialEntries={[pfad]}><Seiten />
  </MemoryRouter>));
}
async function klick(text: string) {
  const knopf = [...host.querySelectorAll<HTMLElement>("a, button")].find(el => el.textContent === text)!;
  await act(async () => knopf.click());
}

it("behält beim Minimieren, Stöbern und Wiederöffnen dieselbe Wiedergabe", async () => {
  await starten(); await klick("Erstes Video");
  const video = host.querySelector("video");
  expect(video).not.toBeNull();
  await klick("Minimieren");
  expect(host.querySelector('[data-mini="true"] video')).toBe(video);
  await klick("Suche");
  expect(host.querySelector("video")).toBe(video);
  await klick("Vergrößern");
  expect(host.querySelector('[data-mini="false"] video')).toBe(video);
  expect(montiert.start).toHaveBeenCalledExactlyOnceWith("eins");
  expect(montiert.ende).not.toHaveBeenCalled();
});

it("minimiert auch beim Navigieren und beendet die Wiedergabe erst beim Schließen", async () => {
  await starten("/video/eins");
  await klick("Suche");
  expect(host.querySelector('[data-mini="true"]')).not.toBeNull();
  await klick("Schließen");
  expect(host.querySelector("video")).toBeNull();
  expect(montiert.ende).toHaveBeenCalledExactlyOnceWith("eins");
});

it("behält einen Zeitstempelwechsel bei und ersetzt beim nächsten Video den alten Player", async () => {
  await starten(); await klick("Erstes Video");
  const video = host.querySelector("video");
  await klick("Zeitstempel");
  expect(host.querySelector("video")).toBe(video);
  expect(montiert.ende).not.toHaveBeenCalled();
  await klick("Zweites Video");
  expect(host.querySelectorAll("video")).toHaveLength(1);
  expect(host.querySelector("video")).not.toBe(video);
  expect(montiert.ende).toHaveBeenCalledExactlyOnceWith("eins");
  expect(montiert.start).toHaveBeenLastCalledWith("zwei");
});
