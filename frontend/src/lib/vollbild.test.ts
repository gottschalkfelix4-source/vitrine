import { afterEach, describe, expect, it, vi } from "vitest";

import { istVollbild, vollbildUmschalten, wegErmitteln } from "./vollbild";

/**
 * Der Anlass für diese Tests: Auf dem Telefon passierte beim Tippen auf
 * Vollbild nichts. Der Player rief nur die Standard-API auf, die es auf dem
 * iPhone für beliebige Elemente nicht gibt - und `requestFullscreen?.()` hat
 * den Fehlschlag stumm verschluckt.
 *
 * Genau das lässt sich mit Typen nicht abfangen: Die Methoden sind optional,
 * ein fehlender Aufruf ist syntaktisch einwandfrei. Nur ein Test, der die
 * Browser nachstellt, hält fest, dass jeder der drei Wege bedient wird.
 *
 * Statt jsdom werden die paar benötigten Globalen von Hand gestellt - die
 * Datei greift ausschließlich zur Aufrufzeit darauf zu.
 */

const echtesDocument = globalThis.document;
const echterScreen = (globalThis as Record<string, unknown>).screen;

afterEach(() => {
  (globalThis as Record<string, unknown>).document = echtesDocument;
  (globalThis as Record<string, unknown>).screen = echterScreen;
  vi.restoreAllMocks();
});

function stelleDokument(felder: Record<string, unknown> = {}) {
  const d = {
    fullscreenElement: null,
    webkitFullscreenElement: null,
    exitFullscreen: vi.fn(async () => {}),
    webkitExitFullscreen: vi.fn(async () => {}),
    ...felder,
  };
  (globalThis as Record<string, unknown>).document = d;
  return d;
}

function stelleBildschirm(kannSperren = true) {
  const o = {
    lock: kannSperren
      ? vi.fn(async () => {})
      : vi.fn(async () => {
          throw new Error("NotSupportedError");
        }),
    unlock: vi.fn(),
  };
  (globalThis as Record<string, unknown>).screen = { orientation: o };
  return o;
}

/** Ein Video, wie es die jeweilige Umgebung anbietet. 16:9, sofern nicht anders. */
function video(extra: Record<string, unknown> = {}) {
  return { videoWidth: 1920, videoHeight: 1080, ...extra } as unknown as HTMLVideoElement;
}

describe("wegErmitteln", () => {
  it("nimmt die Standard-API, wo es sie gibt", () => {
    const huelle = { requestFullscreen: vi.fn() } as unknown as HTMLElement;
    expect(wegErmitteln(huelle, video())).toBe("standard");
  });

  it("weicht auf die Webkit-Fassung für Elemente aus", () => {
    const huelle = { webkitRequestFullscreen: vi.fn() } as unknown as HTMLElement;
    expect(wegErmitteln(huelle, video())).toBe("webkit-element");
  });

  it("erkennt den iPhone-Fall", () => {
    // Safari auf iOS kann ausschließlich das Videoelement ins Vollbild
    // schicken - genau daran ist es gescheitert.
    const huelle = {} as unknown as HTMLElement;
    expect(wegErmitteln(huelle, video({ webkitEnterFullscreen: vi.fn() }))).toBe("webkit-video");
  });

  it("gibt zu, wenn es keinen Weg gibt", () => {
    expect(wegErmitteln({} as HTMLElement, video())).toBe("keiner");
    expect(wegErmitteln(null, null)).toBe("keiner");
  });
});

describe("istVollbild", () => {
  it("erkennt den Standardfall", () => {
    stelleDokument({ fullscreenElement: {} });
    expect(istVollbild(null)).toBe(true);
  });

  it("erkennt die Webkit-Fassung", () => {
    stelleDokument({ webkitFullscreenElement: {} });
    expect(istVollbild(null)).toBe(true);
  });

  it("erkennt Apples Player auf dem iPhone", () => {
    // Dort meldet das Dokument gar nichts - nur das Video selbst weiß es.
    stelleDokument();
    expect(istVollbild(video({ webkitDisplayingFullscreen: true }))).toBe(true);
  });

  it("meldet sonst nichts", () => {
    stelleDokument();
    expect(istVollbild(video())).toBe(false);
  });
});

describe("vollbildUmschalten: einschalten", () => {
  it("ruft die Standard-API", async () => {
    stelleDokument();
    stelleBildschirm();
    const anfordern = vi.fn(async () => {});
    const huelle = { requestFullscreen: anfordern } as unknown as HTMLElement;

    expect(await vollbildUmschalten(huelle, video())).toBe("standard");
    expect(anfordern).toHaveBeenCalledOnce();
  });

  it("ruft auf dem iPhone das Video, nicht die Hülle", async () => {
    stelleDokument();
    stelleBildschirm();
    const betreten = vi.fn();
    const v = video({ webkitEnterFullscreen: betreten });

    expect(await vollbildUmschalten({} as HTMLElement, v)).toBe("webkit-video");
    expect(betreten).toHaveBeenCalledOnce();
  });

  it("tut nichts, wenn es keinen Weg gibt", async () => {
    stelleDokument();
    expect(await vollbildUmschalten({} as HTMLElement, video())).toBe("keiner");
  });
});

describe("vollbildUmschalten: ausschalten", () => {
  it("beendet den Standardfall", async () => {
    const d = stelleDokument({ fullscreenElement: {} });
    stelleBildschirm();
    await vollbildUmschalten({ requestFullscreen: vi.fn() } as unknown as HTMLElement, video());
    expect(d.exitFullscreen).toHaveBeenCalledOnce();
  });

  it("beendet Apples Player über das Video", async () => {
    stelleDokument();
    stelleBildschirm();
    const verlassen = vi.fn();
    const v = video({
      webkitDisplayingFullscreen: true,
      webkitEnterFullscreen: vi.fn(),
      webkitExitFullscreen: verlassen,
    });
    await vollbildUmschalten({} as HTMLElement, v);
    expect(verlassen).toHaveBeenCalledOnce();
  });
});

describe("Drehung", () => {
  it("dreht ein Querformat-Video ins Querformat", async () => {
    // Ein 16:9-Video im Hochformat-Vollbild ist ein Streifen in der Bildmitte.
    stelleDokument();
    const o = stelleBildschirm();
    await vollbildUmschalten(
      { requestFullscreen: vi.fn(async () => {}) } as unknown as HTMLElement,
      video(),
    );
    expect(o.lock).toHaveBeenCalledWith("landscape");
  });

  it("lässt ein hochkantiges Short in Ruhe", async () => {
    // Der Fall, den man beim Nachbauen von YouTube leicht übersieht.
    stelleDokument();
    const o = stelleBildschirm();
    await vollbildUmschalten(
      { requestFullscreen: vi.fn(async () => {}) } as unknown as HTMLElement,
      video({ videoWidth: 608, videoHeight: 1080 }),
    );
    expect(o.lock).not.toHaveBeenCalled();
  });

  it("verkraftet Geräte ohne Drehsperre", async () => {
    // Am Schreibtisch und unter iOS wirft der Aufruf. Das darf das Vollbild
    // nicht mitreißen.
    stelleDokument();
    stelleBildschirm(false);
    const anfordern = vi.fn(async () => {});
    await expect(
      vollbildUmschalten({ requestFullscreen: anfordern } as unknown as HTMLElement, video()),
    ).resolves.toBe("standard");
    expect(anfordern).toHaveBeenCalledOnce();
  });

  it("gibt die Drehung beim Beenden wieder frei", async () => {
    stelleDokument({ fullscreenElement: {} });
    const o = stelleBildschirm();
    await vollbildUmschalten({ requestFullscreen: vi.fn() } as unknown as HTMLElement, video());
    expect(o.unlock).toHaveBeenCalledOnce();
  });
});
