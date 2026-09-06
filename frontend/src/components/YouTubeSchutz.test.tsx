// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { Fortschrittsleiste } from "./Fortschritt";
import { Warteschlangeseite } from "../pages/Warteschlange";
import { api, type Anfragelimit } from "../lib/api";

vi.mock("./Anmeldung", () => ({ useAdmin: () => true }));

type Aktiv = Awaited<ReturnType<typeof api.aktiveAuftraege>>;
let host: HTMLDivElement;
let root: Root;
let aktiv: Aktiv;
const budget = (): Anfragelimit => ({
  pausiert: false, rest_s: 0, bis: null, grund: null,
  anfragen_stunde: 24, anfragen_tag: 210, videos_stunde: 3, videos_tag: 18,
  limit_anfragen_stunde: 100, limit_anfragen_tag: 1000,
  limit_videos_stunde: 10, limit_videos_tag: 100,
  medienanfragen_tag: 12000, reduziert: false,
});

beforeEach(() => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  aktiv = {
    laufend: [], wartend: 2, nach_art: { video_archive: 2 },
    pause: { aktiv: false, rest_s: 0, bis: null, laufend: 0 },
    drosselung: { pausiert: false, rest_s: 0, bis: null, stufe: 0, grund: null },
    anfragelimit: budget(), ausgaenge: { gesamt: 3, frei: 3 },
  };
  vi.spyOn(api, "auftraege").mockResolvedValue([]);
  vi.spyOn(api, "aktiveAuftraege").mockImplementation(async () => aktiv);
});

afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

async function anzeigen() {
  await act(async () => root.render(<MemoryRouter><Fortschrittsleiste /><Warteschlangeseite /></MemoryRouter>));
}

it("zeigt gleitende gemeinsame Budgets und Mediensegmente getrennt", async () => {
  await anzeigen();
  const tabelle = host.querySelector(".youtube-schutz-budgets")!;
  expect(tabelle.textContent).toContain("24 / 100");
  expect(tabelle.textContent).toContain("210 / 1.000");
  expect(tabelle.textContent).toContain("3 / 10");
  expect(tabelle.textContent).toContain("18 / 100");
  expect(tabelle.textContent).not.toContain("12.000");
  expect(host.textContent).toContain("Medienanfragen in den letzten 24 Stunden: 12.000");
  expect(host.textContent).toContain("gemeinsam für alle Tunnel und Worker dieser Instanz");
  expect(host.textContent).toContain("Versuche einschließlich Wiederholungen");
  expect(host.textContent).toContain("keine garantierten YouTube-Grenzen");
});

it("zeigt eine vorsorgliche Pause auch ohne Aufträge und behauptet keine Abweisung", async () => {
  aktiv.wartend = 0;
  aktiv.anfragelimit = { ...budget(), pausiert: true, rest_s: 600, bis: "2030-01-01T12:00:00Z", grund: "Stundenbudget für Downloadversuche erreicht." };
  await anzeigen();
  expect(host.querySelector("summary")!.textContent).toContain("vorsorgliche Wartezeit 10 Minuten");
  expect(host.textContent).toContain("Danach wird die Freigabe erneut geprüft");
  expect(host.textContent).not.toContain("YouTube weist");
  expect(host.textContent).not.toContain("IP-Sperre");
});

it("erhält manuelle Pause und Abweisung neben reduzierten Budgets", async () => {
  aktiv.pause = { aktiv: true, rest_s: null, bis: null, laufend: 0 };
  aktiv.drosselung = { pausiert: true, rest_s: 300, bis: null, stufe: 1, grund: "429" };
  aktiv.anfragelimit = { ...budget(), pausiert: true, rest_s: 3600, bis: "2030-01-01T12:00:00Z", reduziert: true, limit_anfragen_stunde: 50 };
  await anzeigen();
  expect(host.querySelector("summary")!.textContent).toContain("Download-Warteschlange pausiert");
  expect(host.textContent).toContain("Manuell pausiert");
  expect(host.textContent).toContain("YouTube weist gerade ab");
  expect(host.textContent).toContain("Eine weitere Pause bleibt zusätzlich wirksam");
  expect(host.textContent).toContain("Budgets sind vorübergehend reduziert");
  expect(host.querySelector(".youtube-schutz-budgets")!.textContent).toContain("24 / 50");
});

it("zeigt unbekannte Zähler als Strich und verspricht beim Speicherfehler keinen Start", async () => {
  aktiv.anfragelimit = {
    ...budget(), pausiert: true, rest_s: 60, bis: null,
    grund: "Der YouTube-Schutz kann seinen Zustand gerade nicht speichern.",
    anfragen_stunde: null, anfragen_tag: null, videos_stunde: null, videos_tag: null, medienanfragen_tag: null,
  };
  await anzeigen();
  expect(host.querySelector("summary")!.textContent).toContain("nächste Prüfung in 1 Minute");
  expect(host.querySelector(".youtube-schutz-budgets")!.textContent).toContain("— / 100");
  expect(host.textContent).toContain("Die Zähler sind gerade nicht verfügbar");
  expect(host.textContent).toContain("Medienanfragen in den letzten 24 Stunden: —");
  expect(host.textContent).not.toContain("noch 1 Minute");
});
