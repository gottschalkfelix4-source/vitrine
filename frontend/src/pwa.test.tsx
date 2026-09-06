import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

beforeEach(() => {
  vi.resetModules();
  vi.stubEnv("DEV", false);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
});

function browser(herkunft: string, sicher: boolean, mitServiceWorker = true) {
  const registrieren = vi.fn<() => Promise<unknown>>().mockResolvedValue({});
  const navigator = mitServiceWorker ? {
    serviceWorker: { controller: null, addEventListener: vi.fn(), register: registrieren },
  } : {};
  vi.stubGlobal("navigator", navigator);
  vi.stubGlobal("window", {
    navigator,
    isSecureContext: sicher,
    location: { origin: herkunft, reload: vi.fn() },
    addEventListener: vi.fn(),
    matchMedia: () => ({ matches: false }),
  });
  return registrieren;
}

async function installationsHinweis() {
  const [{ createElement }, { renderToStaticMarkup }, { AppInstallieren }] = await Promise.all([
    import("react"),
    import("react-dom/server"),
    import("./components/AppInstallieren"),
  ]);
  return renderToStaticMarkup(createElement(AppInstallieren));
}

describe("PWA: sichere Verbindung und Browserunterstützung", () => {
  it.each([false, true])("erkennt HTTP im Heimnetz als unsicher, auch bei Service-Worker-API: %s", async (mitServiceWorker) => {
    const herkunft = "http://192.168.1.50:8000";
    const registrieren = browser(herkunft, false, mitServiceWorker);
    const pwa = await import("./pwa");

    pwa.serviceWorkerAnmelden();

    expect(pwa.pwaZustand()).toEqual({ art: "unsicher", herkunft });
    expect(registrieren).not.toHaveBeenCalled();
    const html = await installationsHinweis();
    expect(html).toContain(herkunft);
    expect(html).toContain("HTTPS");
    expect(html).not.toContain("<button");
  });

  it("meldet fehlende Browserunterstützung bei einer sicheren Verbindung", async () => {
    browser("https://archiv.example", true, false);
    const pwa = await import("./pwa");

    pwa.serviceWorkerAnmelden();

    expect(pwa.pwaZustand()).toEqual({ art: "nicht_unterstuetzt" });
  });

  it.each(["https://archiv.example", "http://localhost:8000"])("registriert den Worker im sicheren Kontext %s", async (herkunft) => {
    const registrieren = browser(herkunft, true);
    const pwa = await import("./pwa");

    pwa.serviceWorkerAnmelden();

    expect(registrieren).toHaveBeenCalledExactlyOnceWith("/sw.js", { scope: "/", updateViaCache: "none" });
    await vi.waitFor(() => expect(pwa.pwaZustand()).toEqual({ art: "aktiv" }));
  });

  it("behält einen Fehler beim Registrieren als eigenen Zustand", async () => {
    const registrieren = browser("https://archiv.example", true);
    registrieren.mockRejectedValueOnce(new Error("Registrierung fehlgeschlagen"));
    const pwa = await import("./pwa");

    pwa.serviceWorkerAnmelden();

    await vi.waitFor(() => expect(pwa.pwaZustand()).toEqual({ art: "fehler", meldung: "Registrierung fehlgeschlagen" }));
  });

  it("registriert im Entwicklungsbetrieb keinen Service Worker", async () => {
    vi.stubEnv("DEV", true);
    const registrieren = browser("http://localhost:5173", true);
    const pwa = await import("./pwa");

    pwa.serviceWorkerAnmelden();

    expect(registrieren).not.toHaveBeenCalled();
    expect(pwa.pwaZustand()).toEqual({ art: "aus" });
  });
});
