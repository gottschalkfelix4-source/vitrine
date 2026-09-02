import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

/**
 * Prüft die Weichenstellung des Service Workers.
 *
 * Geprüft wird die ausgelieferte Datei selbst, nicht eine Nachbildung: Sie
 * wird gelesen und ihre Entscheidungsfunktion in einer minimalen Umgebung
 * ausgewertet. Eine Kopie der Regeln in den Test zu schreiben würde nur
 * bestätigen, dass zwei Texte übereinstimmen, die ich beide selbst verfasst
 * habe.
 *
 * Der Anlass ist der teuerste denkbare Fehler dieses Projekts: Ein
 * versehentlich zwischengespeicherter Videostrom füllt den Gerätespeicher mit
 * Gigabytes und zerbricht das Springen in der Zeitleiste, weil eine
 * Teilantwort (206) nicht als vollständige Antwort abgelegt werden darf.
 */
const quelle = readFileSync(
  fileURLToPath(new URL("../../public/sw.js", import.meta.url)),
  "utf-8",
);

// Nur die Funktion herauslösen - der Rest der Datei ruft self.addEventListener
// auf, das es hier nicht gibt.
const anfang = quelle.indexOf("function strategie(");
const ende = quelle.indexOf("\nself.addEventListener(\"fetch\"", anfang);
if (anfang < 0 || ende < 0) throw new Error("strategie() nicht in public/sw.js gefunden");

const strategie = new Function(
  `${quelle.slice(anfang, ende)}; return strategie;`,
)() as (adresse: string, methode: string, modus: string, herkunft: string) => string;

const HERKUNFT = "http://archiv.local:8000";
const waehle = (pfad: string, methode = "GET", modus = "no-cors") =>
  strategie(`${HERKUNFT}${pfad}`, methode, modus, HERKUNFT);

describe("Service Worker: was angefasst wird", () => {
  it("lässt Videoströme unberührt", () => {
    // Die Zusage, an der alles hängt.
    expect(waehle("/api/videos/dQw4w9WgXcQ/stream")).toBe("durchreichen");
    expect(waehle("/api/videos/dQw4w9WgXcQ/stream?support=av1")).toBe("durchreichen");
  });

  it("lässt Untertitel unberührt", () => {
    expect(waehle("/api/videos/dQw4w9WgXcQ/subtitles/de")).toBe("durchreichen");
  });

  it("speichert Vorschaubilder", () => {
    // Der eigentliche Gewinn auf dem Telefon: 60 Kacheln je Kanalseite.
    expect(waehle("/api/thumbs/quelle/dQw4w9WgXcQ")).toBe("bild");
    expect(waehle("/api/thumbs/abc.webp")).toBe("bild");
  });

  it("speichert keine anderen API-Antworten", () => {
    // Eine Warteschlange von gestern wäre schlimmer als gar keine Anzeige.
    expect(waehle("/api/videos?kanal=UC1")).toBe("durchreichen");
    expect(waehle("/api/jobs/aktiv")).toBe("durchreichen");
    expect(waehle("/api/storage")).toBe("durchreichen");
    expect(waehle("/api/settings")).toBe("durchreichen");
  });

  it("fasst nur GET an", () => {
    // Ein abgefangenes DELETE wäre ein Datenverlust.
    expect(waehle("/api/videos/abc", "DELETE")).toBe("durchreichen");
    expect(waehle("/api/channels", "POST")).toBe("durchreichen");
    expect(waehle("/api/videos/abc/progress", "PUT")).toBe("durchreichen");
  });

  it("lässt fremde Herkunft in Ruhe", () => {
    expect(strategie("https://i.ytimg.com/vi/abc/hqdefault.jpg", "GET", "no-cors", HERKUNFT))
      .toBe("durchreichen");
  });

  it("behandelt gehashte Bündel als unveränderlich", () => {
    expect(waehle("/assets/index-BNJ0BaE7.js")).toBe("baustein");
    expect(waehle("/assets/index-BNJ0BaE7.css")).toBe("baustein");
    expect(waehle("/icons/icon-192.png")).toBe("baustein");
  });

  it("holt Seitenaufrufe erst aus dem Netz", () => {
    // Sonst bekäme man nach einem Update des Containers die alte Oberfläche
    // und würde nicht verstehen, warum.
    expect(waehle("/", "GET", "navigate")).toBe("huelle");
    expect(waehle("/video/dQw4w9WgXcQ", "GET", "navigate")).toBe("huelle");
    expect(waehle("/speicher", "GET", "navigate")).toBe("huelle");
  });

  it("verwechselt einen Videopfad nicht mit einer Seite", () => {
    // /video/<id> ist eine Seite, /api/videos/<id>/stream ist der Strom.
    expect(waehle("/video/dQw4w9WgXcQ", "GET", "navigate")).toBe("huelle");
    expect(waehle("/api/videos/dQw4w9WgXcQ/stream", "GET", "no-cors")).toBe("durchreichen");
  });
});
