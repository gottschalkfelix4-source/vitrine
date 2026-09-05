import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";

import { DownloadPause } from "./DownloadPause";
import { Fortschrittsleiste } from "./Fortschritt";
import type { api } from "../lib/api";

const mock = vi.hoisted(() => ({ daten: undefined as unknown }));
vi.mock("../hooks/useApi", () => ({ useApi: () => ({ daten: mock.daten }) }));

type Aktiv = Awaited<ReturnType<typeof api.aktiveAuftraege>>;
let aktiv: Aktiv;

beforeEach(() => {
  aktiv = {
    laufend: [], wartend: 0, nach_art: {},
    pause: { aktiv: false, bis: null, rest_s: 0, laufend: 0 },
    drosselung: { pausiert: false, rest_s: 0, bis: null, stufe: 0, grund: null },
    ausgaenge: { gesamt: 1, frei: 1 },
  };
  mock.daten = aktiv;
});

function leiste() {
  return renderToStaticMarkup(<MemoryRouter><Fortschrittsleiste /></MemoryRouter>);
}

describe("Manuelle Downloadpause", () => {
  it("bleibt auch bei leerer Warteschlange sichtbar und erreichbar", () => {
    aktiv.pause = { aktiv: true, bis: null, rest_s: null, laufend: 0 };
    const html = leiste();
    expect(html).toContain("Download-Warteschlange pausiert");
    expect(html).toContain('href="/warteschlange"');
    expect(html).toContain("bis du die Pause beendest");
  });

  it("verspricht bei manueller Pause kein Fortsetzen nach Ablauf einer IP-Sperre", () => {
    aktiv.pause = { aktiv: true, bis: null, rest_s: null, laufend: 0 };
    aktiv.drosselung = { pausiert: true, rest_s: 300, bis: null, stufe: 1, grund: "429" };
    const html = leiste();
    expect(html).toContain("Manuell pausiert");
    expect(html).toContain("Sperrpause endet in");
    expect(html).not.toContain("weiter in");
  });

  it("zeigt laufende Downloads als ausstehend und bietet das Fortsetzen an", () => {
    aktiv.pause = { aktiv: true, bis: null, rest_s: null, laufend: 2 };
    const html = renderToStaticMarkup(<DownloadPause pause={aktiv.pause} aufAenderung={() => {}} />);
    expect(html).toContain("2 laufende Downloads oder Kanalabgleiche werden noch abgeschlossen");
    expect(html).toContain("Downloads fortsetzen");
    expect(html).toContain("Pausiert, bis du die Downloads wieder fortsetzt");
    expect(html).not.toContain("Die manuelle Pause endet in");
  });

  it("gibt für zeitlich begrenzte Pausen die verbleibende Zeit an", () => {
    aktiv.pause = { aktiv: true, bis: "2030-01-01T12:00:00Z", rest_s: 1800, laufend: 0 };
    const html = renderToStaticMarkup(<DownloadPause pause={aktiv.pause} aufAenderung={() => {}} />);
    expect(html).toContain("Die manuelle Pause endet in 30 Minuten");
    expect(html).toContain("Eine bestehende IP-Sperre wird durch Fortsetzen nicht aufgehoben");
  });

  it("blendet die leere Fortschrittsleiste nach dem Fortsetzen wieder aus", () => {
    expect(leiste()).toBe("");
  });
});
