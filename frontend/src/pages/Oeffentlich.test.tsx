import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import App from "../App";
import { Videokachel } from "../components/ui";
import { auth } from "../lib/auth";
import { api, type VideoKurz } from "../lib/api";
import { Startseite } from "./Start";
import { Kanalseite } from "./Kanal";
import { Kanaeleseite } from "./Kanaele";
import { Warteschlangeseite } from "./Warteschlange";
import { Speicherseite } from "./Speicher";
import { Wiedergabeseite } from "./Wiedergabe";

const daten = vi.hoisted(() => ({ antworten: [] as unknown[], laden: [] as (() => Promise<unknown>)[] }));
vi.mock("../hooks/useApi", () => ({
  useApi: (laden: () => Promise<unknown>) => {
    daten.laden.push(laden);
    return { daten: daten.antworten.shift(), laedt: false, fehler: null, neuLaden: vi.fn() };
  },
  useVideostapel: () => ({ videos: [], laedt: false, fehler: null, ende: true, neuLaden: vi.fn() }),
}));
vi.mock("../components/Player", () => ({ Player: ({ startSekunde }: { startSekunde: number }) => <video data-start={startSekunde} /> }));

const video: VideoKurz = {
  id: "testvideo", titel: "Archivvideo", kanal_id: "kanal", kanal_name: "Testkanal", dauer_s: 100,
  hochgeladen: null, aufrufe: null, bild: null, hoehe: 720, breite: 1280, fps: 25,
  status: "archived", ist_short: false, war_live: false, gesehen: true, fortschritt_s: 77,
  fortschritt_anteil: .77, buendel_bytes: 100, recodiert: false,
};
const kanal = { id: "kanal", name: "Testkanal", videos_archiviert: 1, videos_gesamt: 1, belegung_bytes: 100 };
const netz = vi.fn<typeof fetch>();

beforeEach(() => {
  auth.sperren();
  daten.antworten = []; daten.laden = [];
  netz.mockReset();
  vi.stubGlobal("fetch", netz);
  vi.stubGlobal("localStorage", { getItem: () => null });
});
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

async function anmelden() {
  netz.mockResolvedValueOnce(Response.json({ eingerichtet: true, angemeldet: true, benutzer: "admin", csrf_token: "test-csrf" }));
  await auth.anmelden("admin", "Testpasswort!");
}
function render(element: React.ReactNode) { return renderToStaticMarkup(<MemoryRouter>{element}</MemoryRouter>); }

it("öffnet das öffentliche Archiv mit Loginlink und ohne Verwaltungsnavigation", () => {
  daten.antworten = [[], []];
  const html = render(<App />);
  expect(html).toContain("Dein Videoarchiv");
  expect(html).toContain('href="/anmelden"');
  expect(html).not.toContain('href="/warteschlange"');
  expect(html).not.toContain('href="/speicher"');
  expect(html).not.toContain("Dein Archiv");
  expect(html).not.toContain('href="/einstellungen"');
  expect(html).not.toContain('href="/streams"');
  expect(html).not.toContain("Kanal aufnehmen");
  expect(html).not.toContain("Anmeldung wird geprüft");
});

it.each(["/einstellungen", "/streams", "/warteschlange", "/speicher"])("montiert bei direktem Gastzugriff auf %s keine Verwaltungsseite", async (pfad) => {
  daten.antworten = [[], []];
  const html = renderToStaticMarkup(<MemoryRouter initialEntries={[pfad]}><App /></MemoryRouter>);
  expect(html).toContain("Bei Vitrine anmelden");
  // Nur öffentliche Kanalliste und inaktiver Navigationszähler; keine Seitenabfrage.
  expect(daten.laden).toHaveLength(2);
  const auftraege = vi.spyOn(api, "auftraege");
  await expect(daten.laden[1]()).resolves.toEqual([]);
  expect(auftraege).not.toHaveBeenCalled();
  expect(html).not.toContain('href="/warteschlange"');
  expect(html).not.toContain('href="/speicher"');
});

it.each(["/warteschlange", "/speicher"])("öffnet %s für den Admin und sperrt es nach dem Abmelden wieder", async (pfad) => {
  await anmelden();
  daten.antworten = [[], []];
  const html = renderToStaticMarkup(<MemoryRouter initialEntries={[pfad]}><App /></MemoryRouter>);
  expect(html).toContain('href="/warteschlange"');
  expect(html).toContain('href="/speicher"');
  expect(html).not.toContain("Bei Vitrine anmelden");
  expect(daten.laden.length).toBeGreaterThan(3);

  auth.sperren();
  daten.antworten = [[], []]; daten.laden = [];
  const gast = renderToStaticMarkup(<MemoryRouter initialEntries={[pfad]}><App /></MemoryRouter>);
  expect(gast).toContain("Bei Vitrine anmelden");
  expect(gast).not.toContain('href="/warteschlange"');
  expect(gast).not.toContain('href="/speicher"');
  expect(daten.laden).toHaveLength(2);
});

it("behält die öffentliche Startseite bei fehlgeschlagener Auth-Verbindung", async () => {
  netz.mockRejectedValueOnce(new TypeError("offline"));
  await auth.pruefen();
  daten.antworten = [[], []];
  const html = render(<App />);
  expect(html).toContain("Dein Videoarchiv");
  expect(html).not.toContain("Bei Vitrine anmelden");
});

it("zeigt den Filter für nicht archivierte Videos und Laden nur Administratoren", async () => {
  expect(render(<Startseite />)).not.toContain("Auch nicht archivierte");
  expect(render(<Videokachel video={{ ...video, status: "new" }} />)).not.toContain("Dieses Video ins Archiv holen");
  await anmelden();
  expect(render(<Startseite />)).toContain("Auch nicht archivierte");
  expect(render(<Videokachel video={{ ...video, status: "new" }} />)).toContain("Dieses Video ins Archiv holen");
});

it("bietet Gästen in leeren und gefüllten Kanallisten kein Aufnehmen an", () => {
  daten.antworten = [[], [kanal]];
  expect(render(<Kanaeleseite aufAnlegen={vi.fn()} />)).not.toContain("Kanal aufnehmen");
  expect(render(<Kanaeleseite aufAnlegen={vi.fn()} />)).not.toContain("Kanal aufnehmen");
});

it("zeigt einen Kanal ohne Verwaltungsaktionen und fragt keine offenen Downloads ab", async () => {
  daten.antworten = [{ kanal, zaehler: { videos: 1, shorts: 0, live: 0 }, sammlungen: [] }, null];
  const offene = vi.spyOn(api, "kanalOffene");
  const html = render(<Kanalseite />);
  expect(html).toContain("Testkanal");
  expect(html).not.toContain("Jetzt abgleichen");
  expect(html).not.toContain("Entfernen");
  await expect(daten.laden[1]()).resolves.toBeNull();
  expect(offene).not.toHaveBeenCalled();
});

it("blendet auch bei isoliertem Gast-Rendering der Warteschlange Aktionen und Fehlerdetails aus", () => {
  daten.antworten = [[
    { id: 1, art: "video_download", titel: "Download", status: "running", fortschritt: .2, fehler: "geheimer Pfad", erstellt: null },
    { id: 2, art: "video_download", titel: "Fehlversuch", status: "failed", fortschritt: 0, meldung: "internes Detail", erstellt: null },
  ], { pause: { aktiv: true }, laufend: [], wartend: 0, nach_art: {} }];
  const html = render(<Warteschlangeseite />);
  expect(html).toContain("Download");
  expect(html).toContain("Downloads sind pausiert.");
  expect(html).not.toContain("Downloads fortsetzen");
  expect(html).not.toContain("Abbrechen</button>");
  expect(html).not.toContain("Nochmal</button>");
  expect(html).not.toContain("geheimer Pfad");
  expect(html).not.toContain("internes Detail");
});

it("blendet auch bei isoliertem Gast-Rendering der Speicheransicht Serverpfad und Hochstufenformular aus", () => {
  daten.antworten = [{
    kaltspeicher: { bytes: 100, quelle_bytes: 100, gespart_bytes: 0, videos: 1, dauer_s: 100 },
    heissspeicher: { bytes: 0, limit_bytes: 0, anzahl: 0 }, hochrechnung: { offene_videos: 0 },
    groesste: [], je_kanal: [], videos_nach_status: { archived: 1 },
    traeger: [{ pfad: "/privater/server/pfad", gesamt: 1000, belegt: 100, frei: 900 }],
  }];
  const html = render(<Speicherseite />);
  expect(html).toContain("Datenträger 1");
  expect(html).not.toContain("/privater/server/pfad");
  expect(daten.laden).toHaveLength(1);
});

it("verwendet bei Gastwiedergabe und Kacheln ausschließlich lokalen Fortschritt", () => {
  vi.stubGlobal("localStorage", { getItem: () => JSON.stringify({ sekunden: 12, gesehen: false }) });
  daten.antworten = [{ video, technik: {}, kapitel: [], untertitel: [], in_playlists: [], statusmeldung: "internes Detail" }, []];
  const html = renderToStaticMarkup(<MemoryRouter initialEntries={["/video/testvideo"]}><Routes>
    <Route path="/video/:videoId" element={<Wiedergabeseite />} />
  </Routes></MemoryRouter>);
  expect(html).toContain('data-start="12"');
  expect(html).not.toContain("Aus dem Archiv entfernen");
  expect(html).not.toContain(">Gesehen");
  expect(html).not.toContain("internes Detail");
  const kachel = render(<Videokachel video={video} />);
  expect(kachel).toContain("width:12%");
  expect(kachel).not.toContain("width:77%");
});
