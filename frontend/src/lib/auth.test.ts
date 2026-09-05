import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "./api";
import { auth, authFetch } from "./auth";

const sitzung = { eingerichtet: true, angemeldet: true, benutzer: "admin", csrf_token: "csrf-nur-im-speicher" };
const netz = vi.fn<typeof fetch>();
const speichern = vi.fn();
const senden = vi.fn();

beforeEach(() => {
  auth.sperren();
  netz.mockReset(); speichern.mockReset(); senden.mockReset();
  vi.stubGlobal("fetch", netz);
  vi.stubGlobal("localStorage", { setItem: speichern });
  vi.stubGlobal("BroadcastChannel", class {
    postMessage = senden;
    close() {}
  });
});
afterEach(() => vi.unstubAllGlobals());

async function anmelden() {
  netz.mockResolvedValueOnce(Response.json(sitzung));
  await auth.anmelden("admin", "ein-langes-testpasswort");
  netz.mockClear();
}

function anfrage() {
  const [pfad, init] = netz.mock.calls.at(-1)!;
  return { pfad, init: init!, headers: new Headers(init?.headers) };
}

describe("Geschützte API-Anfragen", () => {
  it("fragt vor bestätigter Anmeldung keine Archivdaten ab", async () => {
    await expect(api.kanaele()).rejects.toMatchObject({ status: 401 });
    expect(netz).not.toHaveBeenCalled();
  });

  it("sendet Login mit Browser-Origin-Kennzeichnung und ohne gespeicherte Zugangsdaten", async () => {
    netz.mockResolvedValueOnce(Response.json(sitzung));
    await auth.anmelden("admin", "ein-langes-testpasswort");
    expect(anfrage().headers.get("X-Vitrine-Request")).toBe("1");
    expect(anfrage().headers.has("X-CSRF-Token")).toBe(false);
    expect(anfrage().init).toMatchObject({ credentials: "same-origin", cache: "no-store" });
    expect(speichern).not.toHaveBeenCalled();
    expect(auth.zustand().sitzung?.angemeldet).toBe(true);
  });

  it.each([
    ["JSON-Änderung", () => api.einstellungenSpeichern({ download_concurrency: 2 })],
    ["DELETE", () => api.videoEntfernen("testvideo")],
    ["Heartbeat", () => api.herzschlag("testvideo")],
    ["Wiedergabeende", () => api.wiedergabeBeendet("testvideo")],
    ["Fortschritt beim Verlassen", () => api.fortschrittMerken("testvideo", 12, undefined, true)],
    ["Cookie-Datei", () => api.cookiesHochladen(new File(["Beispiel"], "cookies.txt"))],
    ["VPN-Datei", () => api.vpnHochladen(new File(["Beispiel"], "vpn.conf"), "Test")],
  ])("schützt %s mit CSRF und verhindert HTTP-Caching", async (_name, aufrufen) => {
    await anmelden();
    netz.mockResolvedValueOnce(new Response(null, { status: 204 }));
    await aufrufen();
    expect(anfrage().headers.get("X-CSRF-Token")).toBe(sitzung.csrf_token);
    expect(anfrage().init).toMatchObject({ credentials: "same-origin", cache: "no-store" });
    if (anfrage().init.body instanceof FormData) expect(anfrage().headers.has("Content-Type")).toBe(false);
    if (["Heartbeat", "Wiedergabeende", "Fortschritt beim Verlassen"].includes(_name)) expect(anfrage().init.keepalive).toBe(true);
  });

  it("erhält Range-Kopf und AbortSignal beim Antasten des Videos", async () => {
    await anmelden();
    const controller = new AbortController();
    netz.mockResolvedValueOnce(new Response("x", { status: 206 }));
    await authFetch("/api/videos/test/stream", { headers: new Headers({ Range: "bytes=0-0" }), signal: controller.signal });
    expect(anfrage().headers.get("Range")).toBe("bytes=0-0");
    expect(anfrage().init.signal).toBe(controller.signal);
    expect(anfrage().headers.has("X-CSRF-Token")).toBe(false);
  });
});

describe("Sitzungsende und überholte Antworten", () => {
  it("verwirft bei 401 die Sitzung und benachrichtigt andere Tabs ohne Geheimnisse", async () => {
    await anmelden();
    netz.mockResolvedValueOnce(new Response(null, { status: 401 }));
    await expect(api.kanaele()).rejects.toMatchObject({ status: 401 });
    expect(auth.zustand().sitzung?.angemeldet).toBe(false);
    expect(auth.zustand().sitzung?.csrf_token).toBeNull();
    expect(senden).toHaveBeenCalledWith("abgemeldet");
    expect(JSON.stringify(speichern.mock.calls)).not.toContain(sitzung.csrf_token);
  });

  it("behält bei fehlgeschlagenem Abmelden die Sitzung", async () => {
    await anmelden();
    netz.mockRejectedValueOnce(new TypeError("offline"));
    await expect(auth.abmelden()).rejects.toThrow();
    expect(auth.zustand().sitzung?.angemeldet).toBe(true);
    expect(senden).not.toHaveBeenCalled();
  });

  it.each(["abmelden", "passwort"])("verwirft nach bestätigtem %s alle privaten Sitzungsdaten", async (aktion) => {
    await anmelden();
    netz.mockResolvedValueOnce(new Response(null, { status: 204 }));
    if (aktion === "abmelden") await auth.abmelden();
    else await auth.passwortAendern("altes-passwort", "neues-langes-passwort");
    expect(auth.zustand().sitzung).toMatchObject({ angemeldet: false, benutzer: null, csrf_token: null });
    expect(senden).toHaveBeenCalledWith("abgemeldet");
  });

  it("verwirft laufende Archivantworten nach dem Abmelden", async () => {
    await anmelden();
    let antwort!: (r: Response) => void;
    netz.mockReturnValueOnce(new Promise((resolve) => { antwort = resolve; }));
    const privat = api.kanaele();
    netz.mockResolvedValueOnce(new Response(null, { status: 204 }));
    await auth.abmelden();
    antwort(Response.json([{ name: "Privater Kanal" }]));
    await expect(privat).rejects.toMatchObject({ status: 401 });
  });

  it("lässt eine alte Sitzungsprüfung eine Abmeldung nicht rückgängig machen", async () => {
    await anmelden();
    let antwort!: (r: Response) => void;
    netz.mockReturnValueOnce(new Promise((resolve) => { antwort = resolve; }));
    const pruefung = auth.pruefen();
    netz.mockResolvedValueOnce(new Response(null, { status: 204 }));
    await auth.abmelden();
    antwort(Response.json(sitzung));
    await pruefung;
    expect(auth.zustand().sitzung?.angemeldet).toBe(false);
  });

  it("lässt alte 401-Antworten eine neue Anmeldung nicht beenden", async () => {
    await anmelden();
    let antwort!: (r: Response) => void;
    netz.mockReturnValueOnce(new Promise((resolve) => { antwort = resolve; }));
    const alt = api.kanaele();
    auth.sperren();
    await anmelden();
    antwort(new Response(null, { status: 401 }));
    await expect(alt).rejects.toMatchObject({ status: 401 });
    expect(auth.zustand().sitzung?.angemeldet).toBe(true);
  });

  it("ignoriert eine noch vor dem Login-Cookie gestartete Statusantwort", async () => {
    let loginAntwort!: (r: Response) => void;
    let statusAntwort!: (r: Response) => void;
    netz.mockReturnValueOnce(new Promise((resolve) => { loginAntwort = resolve; }));
    const login = auth.anmelden("admin", "ein-langes-testpasswort");
    netz.mockReturnValueOnce(new Promise((resolve) => { statusAntwort = resolve; }));
    const status = auth.pruefen();
    loginAntwort(Response.json(sitzung));
    await login;
    statusAntwort(Response.json({ ...sitzung, angemeldet: false, csrf_token: null }));
    await status;
    expect(auth.zustand().sitzung?.angemeldet).toBe(true);
  });

  it("zeigt bei fehlgeschlagener Sitzungsprüfung keine alte Anmeldung weiter", async () => {
    await anmelden();
    netz.mockRejectedValueOnce(new TypeError("offline"));
    await auth.pruefen();
    expect(auth.zustand()).toMatchObject({ art: "fehler", sitzung: null });
  });

  it("sperrt beim Verlassen bis zu einer frischen Sitzungsprüfung", async () => {
    await anmelden();
    auth.sperren();
    expect(auth.zustand()).toMatchObject({ art: "pruefen", sitzung: null });
    netz.mockResolvedValueOnce(Response.json({ ...sitzung, angemeldet: false, csrf_token: null }));
    await auth.pruefen();
    expect(auth.zustand().sitzung?.angemeldet).toBe(false);
  });

  it("entfernt die Sitzung bei einer Abmeldung in einem anderen Tab", async () => {
    await anmelden();
    const fenster = new EventTarget();
    vi.stubGlobal("window", fenster);
    const entfernen = auth.tabAbmeldungenBeachten();
    const ereignis = Object.assign(new Event("storage"), { key: "vitrine-abmeldung", newValue: "ereignis" });
    fenster.dispatchEvent(ereignis);
    expect(auth.zustand().sitzung?.angemeldet).toBe(false);
    entfernen();
  });

  it("behält die Bestätigung einer Passwortänderung bei doppelten Tab-Ereignissen", async () => {
    await anmelden();
    const fenster = new EventTarget();
    vi.stubGlobal("window", fenster);
    const entfernen = auth.tabAbmeldungenBeachten();
    netz.mockResolvedValueOnce(new Response(null, { status: 204 }));
    await auth.passwortAendern("altes-passwort", "neues-langes-passwort");
    fenster.dispatchEvent(Object.assign(new Event("storage"), { key: "vitrine-abmeldung", newValue: "ereignis" }));
    expect(auth.zustand().meldung).toContain("Passwort geändert");
    entfernen();
  });
});

describe("Einmalige Administratoreinrichtung", () => {
  const leer = { eingerichtet: false, angemeldet: false, benutzer: null, csrf_token: null };
  const bereit = { ...leer, eingerichtet: true };
  async function unkonfiguriert() {
    netz.mockResolvedValueOnce(Response.json(leer));
    await auth.pruefen();
    netz.mockClear();
  }

  it("sendet den Code als geschützten JSON-Aufruf und fordert danach einen normalen Login", async () => {
    await unkonfiguriert();
    netz.mockResolvedValueOnce(new Response(null, { status: 204 }));
    netz.mockResolvedValueOnce(Response.json(bereit));
    await auth.einrichten("einmaliger-test-code", "test-admin", "langes-test-passwort");
    const [pfad, init] = netz.mock.calls[0];
    expect(pfad).toBe("/api/auth/setup");
    expect(init).toMatchObject({ method: "POST", credentials: "same-origin", cache: "no-store" });
    expect(new Headers(init?.headers).get("X-Vitrine-Request")).toBe("1");
    expect(new Headers(init?.headers).get("Content-Type")).toBe("application/json");
    expect(new Headers(init?.headers).has("X-CSRF-Token")).toBe(false);
    expect(JSON.parse(init?.body as string)).toEqual({ einrichtungscode: "einmaliger-test-code", benutzer: "test-admin", passwort: "langes-test-passwort" });
    expect(netz.mock.calls.map((r) => r[0])).toEqual(["/api/auth/setup", "/api/auth/session"]);
    expect(auth.zustand()).toMatchObject({ benutzerVorschlag: "test-admin", sitzung: bereit });
    expect(auth.zustand().meldung).toContain("Administrator eingerichtet");
    expect(speichern).not.toHaveBeenCalled();
    await expect(api.kanaele()).rejects.toMatchObject({ status: 401 });
  });

  it("bestätigt weder falsche Codes noch fehlgeschlagene Verbindungen als Einrichtung", async () => {
    await unkonfiguriert();
    netz.mockResolvedValueOnce(Response.json({ detail: "Code ungültig" }, { status: 403 }));
    await expect(auth.einrichten("falsch", "admin", "langes-test-passwort")).rejects.toMatchObject({ status: 403 });
    expect(auth.zustand().sitzung).toEqual(leer);
    netz.mockRejectedValueOnce(new TypeError("offline"));
    await expect(auth.einrichten("code", "admin", "langes-test-passwort")).rejects.toThrow("offline");
    expect(auth.zustand().sitzung).toEqual(leer);
    expect(auth.zustand().meldung).toBeNull();
  });

  it("lädt nach konkurrierender Einrichtung den Status neu, ohne ein Konto zurückzusetzen", async () => {
    await unkonfiguriert();
    netz.mockResolvedValueOnce(new Response(null, { status: 409 }));
    netz.mockResolvedValueOnce(Response.json(bereit));
    await auth.einrichten("code", "admin", "langes-test-passwort");
    expect(auth.zustand().sitzung).toEqual(bereit);
    expect(auth.zustand().meldung).toContain("bereits eingerichtet");
    expect(netz.mock.calls.map((r) => r[0])).toEqual(["/api/auth/setup", "/api/auth/session"]);
  });

  it("behält beim Fokuswechsel eine unveränderte Einrichtung und deren Formulardaten", async () => {
    await unkonfiguriert();
    const wechsel = auth.zustand().wechsel;
    netz.mockResolvedValueOnce(Response.json(leer));
    await auth.pruefen();
    expect(auth.zustand().wechsel).toBe(wechsel);
  });

  it("lässt eine vor der Einrichtung gestartete Statusantwort die Einrichtung nicht zurücknehmen", async () => {
    await unkonfiguriert();
    let antwort!: (r: Response) => void;
    netz.mockReturnValueOnce(new Promise((resolve) => { antwort = resolve; }));
    const alt = auth.pruefen();
    netz.mockResolvedValueOnce(new Response(null, { status: 204 }));
    netz.mockResolvedValueOnce(Response.json(bereit));
    await auth.einrichten("code", "test-admin", "langes-test-passwort");
    antwort(Response.json(leer));
    await alt;
    expect(auth.zustand()).toMatchObject({ benutzerVorschlag: "test-admin", sitzung: bereit });
  });

  it("startet während einer laufenden Einrichtung keine konkurrierende Fokusprüfung", async () => {
    await unkonfiguriert();
    let antwort!: (r: Response) => void;
    netz.mockReturnValueOnce(new Promise((resolve) => { antwort = resolve; }));
    const setup = auth.einrichten("code", "test-admin", "langes-test-passwort");
    await auth.pruefen();
    expect(netz).toHaveBeenCalledTimes(1);
    netz.mockResolvedValueOnce(Response.json(bereit));
    antwort(new Response(null, { status: 204 }));
    await setup;
    expect(auth.zustand().sitzung).toEqual(bereit);
  });
});
