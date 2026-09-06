import { expect, test, type Page } from "@playwright/test";
import { clip } from "./clip";

async function archiv(page: Page, ohneKlick = false, maus = false, ohnePointer = false) {
  const sitzungen = { gestartet: 0, beendet: 0 };
  await page.addInitScript(({ ohneKlick, ohnePointer }) => {
    Object.defineProperty(navigator, "standalone", { value: true });
    const ausrichtung = Object.assign(new EventTarget(), { type: "portrait-primary" });
    Object.defineProperty(screen, "orientation", { get: () => ausrichtung });
    Object.assign(window, { testDrehen: (quer: boolean) => {
      ausrichtung.type = quer ? "landscape-primary" : "portrait-primary";
      ausrichtung.dispatchEvent(new Event("change"));
    } });
    // Touch muss auch ohne das von Safari synthetisierte Maus-/Klickereignis gehen.
    if (ohneKlick) document.addEventListener("click", (e) => {
      if (e.detail > 0 && (e.target as Element).closest(".player, .player-menue-blatt")) e.stopImmediatePropagation();
    }, true);
    // iOS-Touch muss unabhängig von Pointer-Events und deren Metadaten funktionieren.
    if (ohnePointer) for (const name of ["pointerdown", "pointerup", "pointermove", "pointercancel"]) {
      document.addEventListener(name, (e) => {
        if ((e.target as Element).closest(".player, .player-menue-blatt")) e.stopImmediatePropagation();
      }, true);
    }
  }, { ohneKlick, ohnePointer });
  const angebote = [{ value: "auto", label: "Automatisch" }, { value: "original", label: "Original" }, { value: "720p", label: "720p" }];
  const video = (id: string) => ({ id, titel: `Testvideo ${id}`, kanal_id: "kanal", kanal_name: "Testkanal", dauer_s: 60,
    status: "archived", hoehe: 72, breite: 128, fps: 1, bild: null, ist_short: false, war_live: false, gesehen: false, fortschritt_s: 0 });
  await page.route("**/testclip.mp4*", async route => {
    const range = /bytes=(\d+)-(\d*)/.exec(route.request().headers().range ?? "");
    if (!range) return route.fulfill({ contentType: "video/mp4", body: clip });
    const start = Number(range[1]), end = range[2] ? Number(range[2]) : clip.length - 1;
    await route.fulfill({ status: 206, contentType: "video/mp4", headers: {
      "accept-ranges": "bytes", "content-range": `bytes ${start}-${end}/${clip.length}`,
    }, body: clip.subarray(start, end + 1) });
  });
  await page.route("**/api/**", async route => {
    const pfad = new URL(route.request().url()).pathname;
    let daten: unknown = [];
    if (pfad.endsWith("/auth/session")) daten = { eingerichtet: true, angemeldet: false, benutzer: null, csrf_token: null };
    else if (/\/videos\/[^/]+\/playback$/.test(pfad)) {
      const quality = route.request().postDataJSON().quality;
      daten = { token: `sitzung-${++sitzungen.gestartet}`, mode: "direct", url: `/testclip.mp4?s=${sitzungen.gestartet}`,
        duration_s: 60, segment_seconds: 4, quality, quality_label: angebote.find(a => a.value === quality)?.label,
        available_qualities: angebote };
    } else if (pfad.endsWith("/ended")) { sitzungen.beendet++; daten = {}; }
    else if (pfad.endsWith("/heartbeat")) daten = {};
    else if (pfad.includes("/subtitles/")) return route.fulfill({ contentType: "text/vtt", body: "WEBVTT\n\n00:00.000 --> 01:00.000\nTestuntertitel\n" });
    else if (pfad === "/api/videos") daten = Array.from({ length: 8 }, (_, i) => video(`v${i}`));
    else if (/\/videos\/[^/]+$/.test(pfad)) daten = { video: video(pfad.split("/").at(-1)!), technik: { breite: 128, hoehe: 72, fps: 1 },
      kapitel: [], untertitel: [{ sprache: "de", automatisch: false }], beschreibung: "Testbeschreibung", in_playlists: [], statusmeldung: null };
    await route.fulfill({ json: daten });
  });
  await page.goto("/");
  const kachel = page.locator('.kachel a[href="/video/v0"]').first();
  if (maus) await kachel.click(); else await kachel.tap();
  const el = page.locator("video");
  await expect(page.locator('.player')).toHaveAttribute("data-status", "bereit");
  // Der stille Testclip benötigt keine Audio-Freigabe durch den Testhost.
  await el.evaluate(v => { v.muted = true; void v.play(); });
  await expect.poll(() => el.evaluate(v => v.currentTime)).toBeGreaterThan(0);
  await el.evaluate(v => { Object.assign(window, { originalVideo: v }); v.currentTime = 12; });
  await expect.poll(() => el.evaluate(v => v.currentTime)).toBeGreaterThanOrEqual(12);
  return sitzungen;
}

async function drehen(page: Page, quer: boolean) {
  await page.setViewportSize(quer ? { width: 844, height: 390 } : { width: 390, height: 844 });
  await page.evaluate(quer => (window as unknown as { testDrehen: (q: boolean) => void }).testDrehen(quer), quer);
}
async function bildAntippen(page: Page) {
  // Freies Bild neben der Wiedergabetaste: Ein Bedienelement fängt den Tap ab.
  await page.locator('.player-oberflaeche').tap({ position: { x: 60, y: 90 } });
}
async function gleicheWiedergabe(page: Page) {
  await expect.poll(() => page.locator('video').evaluate(v => v === (window as unknown as { originalVideo: HTMLVideoElement }).originalVideo)).toBe(true);
  await expect.poll(() => page.locator('video').evaluate(v => v.currentTime)).toBeGreaterThanOrEqual(12);
}

for (const modus of ["normal", "ohne Klickereignis", "ohne Pointer-Events"]) {
  test(`Alle Bedienelemente mit Touch ${modus}`, async ({ page }, info) => {
    const sitzungen = await archiv(page, modus === "ohne Klickereignis", false, modus === "ohne Pointer-Events");
    const taste = (name: string) => page.getByRole("button", { name, exact: true });
    await bildAntippen(page);
    await expect(page.locator('.player')).toHaveAttribute("data-steuerung", "false");
    await bildAntippen(page);
    await expect(page.locator('.player')).toHaveAttribute("data-steuerung", "true");
    // Prüft die tatsächliche Trefferfläche, nicht nur Sichtbarkeit oder Handler.
    const verdeckt = await page.locator('.player button').evaluateAll(tasten => tasten.filter(taste => {
      const r = taste.getBoundingClientRect();
      if (!r.width || !r.height || getComputedStyle(taste).visibility === "hidden") return false;
      const ziel = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
      return !ziel || !taste.contains(ziel);
    }).map(taste => taste.getAttribute("aria-label")));
    expect(verdeckt).toEqual([]);
    await page.locator('.player-gross').tap();
    await expect.poll(() => page.locator('video').evaluate(v => v.paused)).toBe(true);
    await page.locator('.steuer-zeile').getByRole("button", { name: "Abspielen", exact: true }).tap();
    await expect.poll(() => page.locator('video').evaluate(v => v.paused)).toBe(false);
    await page.locator('.steuer-zeile').getByRole("button", { name: "Pause", exact: true }).tap();
    await expect.poll(() => page.locator('video').evaluate(v => v.paused)).toBe(true);
    await taste("Ton an").tap();
    await expect.poll(() => page.locator('video').evaluate(v => v.muted)).toBe(false);
    await taste("Stumm").tap();
    await expect.poll(() => page.locator('video').evaluate(v => v.muted)).toBe(true);
    const zeitleiste = page.getByRole("slider", { name: "Position" });
    const breite = await zeitleiste.evaluate(el => el.getBoundingClientRect().width);
    await zeitleiste.tap({ position: { x: breite / 3, y: 12 } });
    await expect.poll(() => page.locator('video').evaluate(v => Math.round(v.currentTime))).toBe(20);
    if (modus === "normal") await page.screenshot({ path: info.outputPath("portrait.png") });
    await page.locator('.player-gross').tap();
    await expect.poll(() => page.locator('video').evaluate(v => v.paused)).toBe(false);
    await taste("Wiedergabeeinstellungen").tap();
    await page.getByRole("menuitem", { name: /Geschwindigkeit/ }).tap();
    await page.getByRole("menuitemradio", { name: "2.5×", exact: true }).tap();
    await expect.poll(() => page.locator('video').evaluate(v => v.playbackRate)).toBe(2.5);
    await taste("Wiedergabeeinstellungen").tap();
    await page.getByRole("menuitem", { name: /Qualität/ }).tap();
    await page.getByRole("menuitemradio", { name: "720p", exact: true }).tap();
    await gleicheWiedergabe(page);
    await expect.poll(() => page.locator('video').evaluate(v => v.paused)).toBe(false);
    await taste("Untertitel").tap();
    await page.getByRole("menuitemradio", { name: "de", exact: true }).tap();
    await expect.poll(() => page.locator('video').evaluate(v => v.textTracks[0].mode)).toBe("showing");
    await drehen(page, true);
    await expect(page.locator('.player')).toHaveAttribute("data-app-vollbild", "true");
    if (modus === "normal") await page.screenshot({ path: info.outputPath("quer.png") });
    const vorher = sitzungen.gestartet;
    await taste("Video minimieren").tap();
    await expect(page.locator('.player')).toHaveAttribute("data-mini", "true");
    await expect(page.locator('.player')).toHaveAttribute("data-app-vollbild", "false");
    await drehen(page, false);
    await page.locator('.mobile-navigation a[href="/kanaele"]').tap();
    if (modus === "normal") await page.screenshot({ path: info.outputPath("mini.png") });
    await gleicheWiedergabe(page);
    await taste("Pause").tap();
    await expect.poll(() => page.locator('video').evaluate(v => v.paused)).toBe(true);
    await taste("Abspielen").tap();
    await taste("Video vergrößern").tap();
    await expect(page.locator('.player')).toHaveAttribute("data-mini", "false");
    await gleicheWiedergabe(page);
    expect(sitzungen.gestartet).toBe(vorher);
    await expect.poll(() => page.locator('video').evaluate(v => v.playbackRate)).toBe(2.5);
    await taste("Vollbild").tap();
    await expect(page.locator('.player')).toHaveAttribute("data-app-vollbild", "true");
    await taste("Vollbild beenden").tap();
    await expect(page.locator('.player')).toHaveAttribute("data-app-vollbild", "false");
    await taste("Video minimieren").tap();
    const beendet = sitzungen.beendet;
    await taste("Video schließen").tap();
    await expect(page.locator('video')).toHaveCount(0);
    await expect.poll(() => sitzungen.beendet).toBeGreaterThan(beendet);
  });
}

test("Titel und Steuerung blenden nach Drehung aus und lassen sich erneut bedienen", async ({ page }) => {
  await archiv(page);
  await drehen(page, true);
  await expect(page.locator('.player')).toHaveAttribute("data-app-vollbild", "true");
  // Früher hielt :focus-within den Titel selbst nach Ablauf des Timers sichtbar.
  await page.locator('.player').focus();
  await expect(page.locator('.player-kopf')).toHaveCSS("opacity", "0");
  await expect(page.locator('.player-steuerung')).toHaveCSS("opacity", "0");
  await bildAntippen(page);
  await expect(page.locator('.player-kopf')).toHaveCSS("opacity", "1");
  await page.getByRole("button", { name: "Wiedergabeeinstellungen", exact: true }).tap();
  await page.getByRole("menuitem", { name: /Geschwindigkeit/ }).tap();
  // Fokuswechsel beim Berühren eines nicht fokussierbaren Bereichs schließt kein Menü.
  await page.locator('.player').focus();
  await page.waitForTimeout(2800);
  await expect(page.getByRole("menu")).toBeVisible();
  await page.getByRole("menuitemradio", { name: "1.5×", exact: true }).tap();
  await expect(page.locator('.player-kopf')).toHaveCSS("opacity", "0");
  await bildAntippen(page);
  await page.getByRole("button", { name: "Video minimieren", exact: true }).tap();
  await expect(page.locator('.player')).toHaveAttribute("data-mini", "true");
  await page.getByRole("button", { name: "Video schließen", exact: true }).tap();
  await expect(page.locator('video')).toHaveCount(0);
});

test.describe("Maus und Tastatur", () => {
  test.use({ isMobile: false, hasTouch: false, viewport: { width: 1280, height: 800 } });
  test("bedient Wiedergabe, Menüs, Vollbild und Miniplayer auch am Schreibtisch", async ({ page }) => {
    const sitzungen = await archiv(page, false, true);
    await page.locator('.player').focus();
    await page.keyboard.press("k");
    await expect.poll(() => page.locator('video').evaluate(v => v.paused)).toBe(true);
    await page.keyboard.press("k");
    await expect.poll(() => page.locator('video').evaluate(v => v.paused)).toBe(false);
    await page.getByRole("button", { name: "Wiedergabeeinstellungen", exact: true }).click();
    await expect(page.getByRole("menuitem", { name: /Qualität/ })).toBeFocused();
    await page.keyboard.press("ArrowDown");
    await page.keyboard.press("Enter");
    await expect(page.getByRole("menuitemradio", { name: /Normal/ })).toBeFocused();
    await page.keyboard.press("Escape");
    await expect(page.getByRole("menu")).toHaveCount(0);
    await page.getByRole("button", { name: "Vollbild", exact: true }).click();
    await expect(page.locator('.player')).toHaveAttribute("data-vollbild", "true");
    await page.getByRole("button", { name: "Vollbild beenden", exact: true }).click();
    await expect(page.locator('.player')).toHaveAttribute("data-vollbild", "false");
    // Nach dem nativen Vollbild liegt der Mauszeiger außerhalb des kleineren Players.
    await page.locator('.player').hover({ position: { x: 60, y: 90 } });
    await page.getByRole("button", { name: "Video minimieren", exact: true }).click();
    await expect(page.locator('.player')).toHaveAttribute("data-mini", "true");
    await gleicheWiedergabe(page);
    expect(sitzungen.gestartet).toBe(1);
    await page.getByRole("button", { name: "Video schließen", exact: true }).click();
    await expect(page.locator('video')).toHaveCount(0);
  });
});

test("bedient Kopf- und Vollbildtasten auch während laufender Wiedergabe", async ({ page }) => {
  await archiv(page);
  await expect.poll(() => page.locator('video').evaluate(v => v.paused)).toBe(false);
  const wiederZeigen = async () => {
    // Während der Wiedergabe blendet die Steuerung von selbst aus.
    await expect(page.locator('.player')).toHaveAttribute("data-steuerung", "false", { timeout: 8000 });
    await bildAntippen(page);
    await expect(page.locator('.player')).toHaveAttribute("data-steuerung", "true");
  };
  await wiederZeigen();
  await page.getByRole("button", { name: "Wiedergabeeinstellungen", exact: true }).tap();
  await expect(page.getByRole("menu")).toBeVisible();
  await page.getByRole("button", { name: "Menü schließen", exact: true }).tap();
  await expect(page.getByRole("menu")).toHaveCount(0);
  // Nach einem Menü als Blatt darf kein vermeintlicher Tastaturfokus hängen bleiben.
  await wiederZeigen();
  await page.getByRole("button", { name: "Vollbild", exact: true }).tap();
  await expect(page.locator('.player')).toHaveAttribute("data-app-vollbild", "true");
  await wiederZeigen();
  await page.getByRole("button", { name: "Video minimieren", exact: true }).tap();
  await expect(page.locator('.player')).toHaveAttribute("data-mini", "true");
  await expect.poll(() => page.locator('video').evaluate(v => v.paused)).toBe(false);
});

test("bleibt im Quer-Vollbild am Bildschirm, auch bei veralteter Viewport-Messung", async ({ page }) => {
  await archiv(page);
  await drehen(page, true);
  await expect(page.locator('.player')).toHaveAttribute("data-app-vollbild", "true");
  // Genau das meldet iOS nach dem Drehen für einen Moment: die alte Lage.
  // Der Player darf sich davon nicht aus dem Bildschirm schieben lassen.
  await page.evaluate(() => document.documentElement.style.setProperty("--app-viewport-hoehe", "844px"));
  const schirm = page.viewportSize()!;
  const kasten = (await page.locator('.player').boundingBox())!;
  expect(Math.round(kasten.y)).toBe(0);
  expect(Math.round(kasten.height)).toBe(schirm.height);
  // Und jede sichtbare Taste muss an ihrer eigenen Mitte getroffen werden.
  const danebenn = await page.locator('.player button').evaluateAll(tasten => tasten.filter(taste => {
    const r = taste.getBoundingClientRect();
    if (!r.width || !r.height || getComputedStyle(taste).visibility === "hidden") return false;
    const ziel = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
    return !ziel || !taste.contains(ziel);
  }).map(taste => taste.getAttribute("aria-label")));
  expect(danebenn).toEqual([]);
});
