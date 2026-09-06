// @vitest-environment jsdom

import { act, StrictMode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { VideoNachladen } from "../components/VideoNachladen";
import { api, type VideoAbfrage, type VideoKurz } from "../lib/api";
import { useApi, useVideostapel, type Ladezustand, type Videostapel } from "./useApi";

const wurzeln = new Set<Root>();

function ausstehend<T>() {
  let resolve!: (wert: T) => void;
  let reject!: (grund: unknown) => void;
  const promise = new Promise<T>((ja, nein) => { resolve = ja; reject = nein; });
  return { promise, resolve, reject };
}

function video(id: string): VideoKurz {
  return {
    id, titel: id, kanal_id: "kanal", kanal_name: "Kanal", dauer_s: 100,
    hochgeladen: null, aufrufe: null, bild: null, hoehe: null, breite: null, fps: null,
    status: "archived", ist_short: false, war_live: false, gesehen: false,
    fortschritt_s: 0, fortschritt_anteil: null, buendel_bytes: null, recodiert: false,
  };
}

async function hookRender<P, T>(hook: (props: P) => T, props: P, strikt = false) {
  let aktuell!: T;
  const element = document.createElement("div");
  document.body.append(element);
  const root = createRoot(element);
  wurzeln.add(root);
  function Probe({ wert }: { wert: P }) { aktuell = hook(wert); return null; }
  async function render(wert: P) {
    await act(async () => { root.render(strikt ? <StrictMode><Probe wert={wert} /></StrictMode> : <Probe wert={wert} />); });
  }
  await render(props);
  return {
    get wert() { return aktuell; },
    render,
    async entfernen() { await act(async () => root.unmount()); wurzeln.delete(root); },
  };
}

beforeEach(() => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
});

afterEach(async () => {
  await act(async () => { for (const root of wurzeln) root.unmount(); });
  wurzeln.clear();
  document.body.replaceChildren();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("Videos seitenweise laden", () => {
  it("verhindert doppelte gleichzeitige Anfragen und zählt den Server-Offset trotz doppelter IDs weiter", async () => {
    const zweite = ausstehend<VideoKurz[]>();
    const laden = vi.spyOn(api, "videos").mockResolvedValueOnce([video("1"), video("2")])
      .mockReturnValueOnce(zweite.promise).mockResolvedValueOnce([video("4")]);
    const h = await hookRender(() => useVideostapel({}, 2), null);
    await act(async () => { h.wert.mehrLaden(); h.wert.mehrLaden(); h.wert.mehrLaden(); });
    expect(laden).toHaveBeenCalledTimes(2);
    expect(h.wert.laedt).toBe(true);
    await act(async () => zweite.resolve([video("2"), video("3")]));
    expect(h.wert.videos.map((v) => v.id)).toEqual(["1", "2", "3"]);
    await act(async () => h.wert.mehrLaden());
    expect(laden.mock.calls.map(([p]) => p?.offset)).toEqual([0, 2, 4]);
    expect(h.wert.videos.map((v) => v.id)).toEqual(["1", "2", "3", "4"]);
    expect(h.wert.ende).toBe(true);
    await act(async () => h.wert.mehrLaden());
    expect(laden).toHaveBeenCalledTimes(3);
  });

  it("verwirft verspätete Seiten nach Kanal- und Filterwechseln", async () => {
    const alt = ausstehend<VideoKurz[]>();
    const neu = ausstehend<VideoKurz[]>();
    vi.spyOn(api, "videos").mockReturnValueOnce(alt.promise).mockReturnValueOnce(neu.promise);
    const h = await hookRender<VideoAbfrage, Videostapel>((abfrage) => useVideostapel(abfrage, 2), { kanal: "alt" });
    await h.render({ kanal: "neu", art: "shorts" });
    expect(h.wert.videos).toEqual([]);
    await act(async () => neu.resolve([video("richtig")]));
    await act(async () => alt.resolve([video("veraltet")]));
    expect(h.wert.videos.map((v) => v.id)).toEqual(["richtig"]);
    expect(h.wert.ende).toBe(true);
  });

  it("behält bei einem Netzwerkfehler die Liste und wiederholt genau die fehlende Seite", async () => {
    const laden = vi.spyOn(api, "videos").mockResolvedValueOnce([video("1"), video("2")])
      .mockRejectedValueOnce(new Error("offline")).mockResolvedValueOnce([video("3")]);
    const h = await hookRender(() => useVideostapel({}, 2), null);
    await act(async () => h.wert.mehrLaden());
    expect(h.wert.videos).toHaveLength(2);
    expect(h.wert.fehler).toBe("offline");
    expect(h.wert.laedt).toBe(false);
    await act(async () => h.wert.mehrLaden());
    expect(laden.mock.calls.map(([p]) => p?.offset)).toEqual([0, 2, 2]);
    expect(h.wert.videos).toHaveLength(3);
    expect(h.wert.fehler).toBeNull();
  });

  it("beginnt beim Aktualisieren neu und hängt eine ältere laufende Seite nicht mehr an", async () => {
    const alt = ausstehend<VideoKurz[]>();
    vi.spyOn(api, "videos").mockResolvedValueOnce([video("1"), video("2")])
      .mockReturnValueOnce(alt.promise).mockResolvedValueOnce([video("neu")]);
    const h = await hookRender(() => useVideostapel({}, 2), null);
    await act(async () => h.wert.mehrLaden());
    await act(async () => h.wert.neuLaden());
    await act(async () => alt.resolve([video("3"), video("4")]));
    expect(h.wert.videos.map((v) => v.id)).toEqual(["neu"]);
  });

  it("lädt bei äquivalenten Filterobjekten nicht erneut und setzt eine neue Seitengröße zurück", async () => {
    const laden = vi.spyOn(api, "videos").mockResolvedValue([video("1")]);
    const h = await hookRender(({ abfrage, limit }: { abfrage: VideoAbfrage; limit: number }) => useVideostapel(abfrage, limit), {
      abfrage: { kanal: "test", sortierung: "alt" }, limit: 2,
    });
    await h.render({ abfrage: { sortierung: "alt", kanal: "test", suche: undefined }, limit: 2 });
    expect(laden).toHaveBeenCalledTimes(1);
    await h.render({ abfrage: { kanal: "test", sortierung: "alt" }, limit: 3 });
    expect(laden).toHaveBeenLastCalledWith({ kanal: "test", sortierung: "alt", limit: 3, offset: 0 });
  });

  it("ignoriert in StrictMode die erste verworfene Anfrage und startet nach Unmount nichts mehr", async () => {
    const alt = ausstehend<VideoKurz[]>();
    const laden = vi.spyOn(api, "videos").mockReturnValueOnce(alt.promise).mockResolvedValueOnce([video("neu")]);
    const h = await hookRender(() => useVideostapel({}, 2), null, true);
    await act(async () => alt.resolve([video("alt")]));
    expect(h.wert.videos.map((v) => v.id)).toEqual(["neu"]);
    const gespeichert = h.wert;
    await h.entfernen();
    gespeichert.mehrLaden();
    gespeichert.neuLaden();
    expect(laden).toHaveBeenCalledTimes(2);
  });
});

describe("Abfragen beim Navigieren und Aktualisieren", () => {
  it("zeigt beim Wechsel niemals alte Detaildaten, behält sie aber während derselben Aktualisierung", async () => {
    const zweite = ausstehend<string>();
    const dritte = ausstehend<string>();
    const laden = vi.fn().mockResolvedValueOnce("Kanal A").mockReturnValueOnce(zweite.promise).mockReturnValueOnce(dritte.promise);
    const gerendert: { kanal: string; daten: string | undefined }[] = [];
    const h = await hookRender<string, Ladezustand<string>>((kanal) => {
      const wert = useApi<string>(laden, [kanal]);
      gerendert.push({ kanal, daten: wert.daten });
      return wert;
    }, "A");
    await act(async () => { h.wert.neuLaden(); });
    expect(h.wert.daten).toBe("Kanal A");
    expect(h.wert.laedt).toBe(true);
    await h.render("B");
    expect(h.wert.daten).toBeUndefined();
    await act(async () => dritte.resolve("Kanal B"));
    await act(async () => zweite.resolve("Veraltetes A"));
    expect(h.wert.daten).toBe("Kanal B");
    expect(gerendert.filter((r) => r.kanal === "B").every((r) => r.daten !== "Kanal A")).toBe(true);
  });

  it("überlappt langsame Polls nicht und räumt den Timer beim Unmount auf", async () => {
    vi.useFakeTimers();
    const erste = ausstehend<string>();
    const laden = vi.fn().mockReturnValueOnce(erste.promise).mockResolvedValue("Aktuell");
    const h = await hookRender(() => useApi(laden, [], 1000), null);
    await act(async () => vi.advanceTimersByTime(5000));
    expect(laden).toHaveBeenCalledTimes(1);
    await act(async () => erste.resolve("Alt"));
    await act(async () => vi.advanceTimersByTime(1000));
    expect(laden).toHaveBeenCalledTimes(2);
    await h.entfernen();
    await act(async () => vi.advanceTimersByTime(5000));
    expect(laden).toHaveBeenCalledTimes(2);
  });
});

describe("Automatisches Nachladen", () => {
  type Beobachtung = { sichtbar: () => void; trennen: ReturnType<typeof vi.fn>; optionen: IntersectionObserverInit };
  let beobachtungen: Beobachtung[];

  beforeEach(() => {
    beobachtungen = [];
    vi.stubGlobal("IntersectionObserver", class {
      disconnect = vi.fn();
      observe = vi.fn();
      constructor(callback: IntersectionObserverCallback, optionen: IntersectionObserverInit) {
        beobachtungen.push({
          sichtbar: () => callback([{ isIntersecting: true } as IntersectionObserverEntry], this as unknown as IntersectionObserver),
          trennen: this.disconnect, optionen,
        });
      }
    });
  });

  async function liste() {
    const element = document.createElement("main");
    element.id = "inhalt";
    document.body.append(element);
    const root = createRoot(element);
    wurzeln.add(root);
    let stapel!: Videostapel;
    function Liste() {
      stapel = useVideostapel({}, 2);
      return <VideoNachladen stapel={stapel} />;
    }
    await act(async () => root.render(<Liste />));
    return { element, get stapel() { return stapel; } };
  }

  it("beobachtet den App-Scrollbereich, lädt automatisch vor und hält bei Fehlern bis zum Wiederholen an", async () => {
    const zweite = ausstehend<VideoKurz[]>();
    const laden = vi.spyOn(api, "videos").mockResolvedValueOnce([video("1"), video("2")])
      .mockReturnValueOnce(zweite.promise).mockRejectedValueOnce(new Error("offline"))
      .mockResolvedValueOnce([video("5")]);
    const l = await liste();
    expect(beobachtungen.at(-1)?.optionen.root).toBe(l.element);
    expect(beobachtungen.at(-1)?.optionen.rootMargin).toBe("0px 0px 800px 0px");
    const erste = beobachtungen.at(-1)!;
    await act(async () => { erste.sichtbar(); erste.sichtbar(); });
    expect(laden).toHaveBeenCalledTimes(2);
    expect(erste.trennen).toHaveBeenCalled();
    await act(async () => zweite.resolve([video("3"), video("4")]));
    expect(beobachtungen).toHaveLength(2);
    await act(async () => beobachtungen.at(-1)!.sichtbar());
    expect(l.element.querySelector('[role="alert"]')?.textContent).toContain("offline");
    expect(beobachtungen).toHaveLength(2);
    expect(l.stapel.videos).toHaveLength(4);
    const wiederholen = l.element.querySelector("button")!;
    expect(wiederholen.textContent).toBe("Erneut versuchen");
    await act(async () => wiederholen.click());
    expect(laden.mock.calls.map(([p]) => p?.offset)).toEqual([0, 2, 4, 4]);
    expect(l.stapel.videos).toHaveLength(5);
    expect(l.element.querySelector("button")).toBeNull();
    expect(l.element.textContent).toContain("Alle geladen");
  });

  it("lädt auch ohne IntersectionObserver über Scrollereignisse nach", async () => {
    vi.stubGlobal("IntersectionObserver", undefined);
    const laden = vi.spyOn(api, "videos").mockResolvedValueOnce([video("1"), video("2")]).mockResolvedValueOnce([]);
    const l = await liste();
    await act(async () => l.element.dispatchEvent(new Event("scroll")));
    expect(laden).toHaveBeenCalledTimes(2);
    expect(l.stapel.ende).toBe(true);
  });
});
