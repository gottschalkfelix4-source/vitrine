import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, expect, it, vi } from "vitest";
import { Playlistseite } from "./Playlist";
import { Suchseite } from "./Suche";
import type { VideoKurz } from "../lib/api";

const zustand = vi.hoisted(() => ({ daten: undefined as unknown, laden: undefined as (() => Promise<unknown>) | undefined, admin: false, suchanfrage: "" }));
vi.mock("../hooks/useApi", () => ({ useApi: (laden: () => Promise<unknown>) => {
  zustand.laden = laden;
  return { daten: zustand.daten, laedt: false, fehler: null, neuLaden: vi.fn() };
} }));
vi.mock("../hooks/useSuchstapel", () => ({ useSuchstapel: (anfrage: string) => {
  zustand.suchanfrage = anfrage;
  return { daten: zustand.daten, laedt: false, fehler: null, ende: true, mehrLaden: vi.fn() };
} }));
vi.mock("../components/Anmeldung", () => ({ useAdmin: () => zustand.admin }));

const video: VideoKurz = {
  id: "eins", titel: "Archivvideo", kanal_id: "kanal", kanal_name: "Kanal", dauer_s: 100,
  hochgeladen: null, aufrufe: null, bild: null, hoehe: 720, breite: 1280, fps: 25,
  status: "archived", ist_short: false, war_live: false, gesehen: false, fortschritt_s: 0,
  fortschritt_anteil: null, buendel_bytes: 100, recodiert: false,
};

beforeEach(() => { zustand.daten = undefined; zustand.laden = undefined; zustand.admin = false; zustand.suchanfrage = ""; vi.restoreAllMocks(); });

it("zählt mehrfach enthaltene Archivvideos pro Playlistposition", () => {
  zustand.admin = true;
  zustand.daten = { id: "playlist", titel: "Doppelte Videos", anzahl_archiviert: 1, positionen: [
    { position: 0, video }, { position: 1, video }, { position: 2, video: { ...video, id: "zwei", status: "new" } },
  ] };
  const html = renderToStaticMarkup(<MemoryRouter><Playlistseite /></MemoryRouter>);
  expect(html).toContain("2 archiviert");
  expect(html).toContain("1 Position ist noch nicht im Archiv.");
  expect(html).not.toContain("2 Positionen sind noch nicht im Archiv.");
});

it("beschränkt öffentliche Playlistzahlen auf die sichtbaren archivierten Positionen", () => {
  zustand.daten = { id: "playlist", titel: "Playlist", anzahl_archiviert: 3, positionen: [
    { position: 0, video }, { position: 1, video: { ...video, id: "zwei", status: "new" } },
  ] };
  const html = renderToStaticMarkup(<MemoryRouter><Playlistseite /></MemoryRouter>);
  expect(html).toContain("1 archiviert");
  expect(html).not.toContain("3 archiviert");
});

it("behandelt unbekannte Suchfilter als Alle und entfernt Such-Leerzeichen", () => {
  zustand.daten = { videos: [video], im_gesprochenen: [], zu_kurz: false };
  const html = renderToStaticMarkup(<MemoryRouter initialEntries={["/suche?q=%20Archiv%20&bereich=unbekannt"]}><Suchseite /></MemoryRouter>);
  expect(html).toContain('aria-pressed="true">Alle</button>');
  expect(html).toContain("Suchergebnisse für „Archiv“");
  expect(zustand.suchanfrage).toBe("Archiv");
});
