import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { auth } from "./auth";
import { standortText, streamsLaden, wiedergabeBeenden, wiedergabeMelden, wiedergabeStarten } from "./wiedergabe";
import { lokalFortschrittLesen, lokalFortschrittMerken } from "./wiedergabeFortschritt";

vi.mock("./capabilities", () => ({ faehigkeiten: () => "mp4,h264,aac" }));
const netz = vi.fn<typeof fetch>();
let speicher: Map<string, string>;
beforeEach(() => {
  auth.sperren(); netz.mockReset(); speicher = new Map();
  vi.stubGlobal("fetch", netz);
  vi.stubGlobal("localStorage", {
    getItem: (key: string) => speicher.get(key) ?? null,
    setItem: (key: string, value: string) => speicher.set(key, value),
  });
});
afterEach(() => vi.unstubAllGlobals());

it("liest Standort- und Encoderdaten mit der Streamübersicht ohne Zusatzaufruf", async () => {
  const daten = { streams: [{ geo: { status: "located", city: "Berlin", region: "Berlin", country: "Deutschland" },
    quality_label: "480p", encoder: "libx264", hardware_accel: "none", encoder_state: "running", fallback_reason: "GPU nicht verfügbar" }],
    geoip: { available: true, database_date: "2026-09-01" } };
  netz.mockResolvedValueOnce(Response.json(daten));
  await expect(streamsLaden()).resolves.toEqual(daten);
  expect(netz).toHaveBeenCalledTimes(1);
  expect(netz.mock.calls[0][0]).toBe("/api/streams");
  expect(standortText({ ...daten.streams[0].geo, status: "located", latitude: 52, longitude: 13, country_code: "DE" })).toBe("Berlin, Deutschland");
  expect(standortText()).toBe("Standort unbekannt");
});

it("startet Gastwiedergabe ohne Admin-CSRF und beendet genau die eigene Sitzung", async () => {
  netz.mockResolvedValueOnce(Response.json({ token: "test-token", mode: "direct", url: "/media" }));
  const sitzung = await wiedergabeStarten("video/id", true);
  const [url, init] = netz.mock.calls[0];
  expect(url).toBe("/api/videos/video%2Fid/playback");
  expect(new Headers(init?.headers).get("X-Vitrine-Request")).toBe("1");
  expect(new Headers(init?.headers).has("X-CSRF-Token")).toBe(false);
  expect(JSON.parse(init!.body as string)).toEqual({ support: "mp4,h264,aac", force_transcode: true, quality: "auto" });
  netz.mockResolvedValueOnce(new Response(null, { status: 204 }));
  await wiedergabeMelden(sitzung.token, 23, "paused");
  expect(JSON.parse(netz.mock.calls[1][1]!.body as string)).toEqual({ position_s: 23, state: "paused" });
  netz.mockResolvedValueOnce(new Response(null, { status: 204 }));
  await wiedergabeBeenden(sitzung.token);
  expect(netz.mock.calls[2][0]).toBe("/api/playback/test-token/ended");
  expect(netz.mock.calls[2][1]?.keepalive).toBe(true);
});

it.each(["auto", "original", "1080p", "720p", "480p", "360p", "240p"] as const)("sendet die gewählte Qualität %s unverändert und übernimmt ausschließlich die Serverangebote", async (quality) => {
  const antwort = { token: "neue-sitzung", mode: quality === "original" ? "direct" : "transcode", quality,
    quality_label: "480p", available_qualities: [{ value: "auto", label: "Automatisch" }, { value: "480p", label: "480p" }] };
  netz.mockResolvedValueOnce(Response.json(antwort));
  await expect(wiedergabeStarten("video", false, quality)).resolves.toEqual(antwort);
  expect(JSON.parse(netz.mock.calls[0][1]!.body as string)).toMatchObject({ quality, force_transcode: false });
  expect(new Headers(netz.mock.calls[0][1]?.headers).get("X-Vitrine-Request")).toBe("1");
});

it("wiederholt eine ausgelastete Qualitätsanforderung nicht automatisch", async () => {
  netz.mockResolvedValueOnce(Response.json({ detail: "Bitte kurz warten" }, { status: 429 }));
  await expect(wiedergabeStarten("video", false, "360p")).rejects.toMatchObject({ status: 429 });
  expect(netz).toHaveBeenCalledTimes(1);
});

it("Gastfortschritt ist pro Video getrennt und erhält die Gesehen-Markierung", () => {
  lokalFortschrittMerken("eins", 12, true);
  lokalFortschrittMerken("zwei", 30);
  lokalFortschrittMerken("eins", 18);
  expect(lokalFortschrittLesen("eins")).toEqual({ sekunden: 18, gesehen: true });
  expect(lokalFortschrittLesen("zwei")).toEqual({ sekunden: 30, gesehen: false });
  expect(lokalFortschrittLesen("unbekannt")).toBeNull();
  expect(netz).not.toHaveBeenCalled();
});

it("ignoriert ungültigen und gesperrten Browserspeicher", () => {
  lokalFortschrittMerken("eins", 10);
  lokalFortschrittMerken("eins", NaN);
  lokalFortschrittMerken("eins", -1);
  expect(lokalFortschrittLesen("eins")?.sekunden).toBe(10);
  speicher.set("vitrine.fortschritt.eins", '{"sekunden":"private"}');
  expect(lokalFortschrittLesen("eins")).toBeNull();
  vi.stubGlobal("localStorage", { getItem() { throw Error(); }, setItem() { throw Error(); } });
  expect(lokalFortschrittLesen("eins")).toBeNull();
  expect(() => lokalFortschrittMerken("eins", 20)).not.toThrow();
});
