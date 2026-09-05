/*
 * Service Worker der Vitrine.
 *
 * Zweck ist nicht "alles offline verfügbar machen" - ein Videoarchiv lebt vom
 * Server, und die Dateien sind zu groß, um sie im Browser zu spiegeln. Der
 * Worker sorgt für zweierlei:
 *
 *   1. Die App ist installierbar und startet ohne Browserleiste.
 *   2. Die Oberfläche erscheint sofort, auch bei schlechtem Mobilfunk.
 *
 * Was hier NICHT passiert, ist genauso wichtig. Drei Regeln, und die erste
 * ist die, an der eine unbedachte Umsetzung dieses Projekt zerlegen würde:
 *
 *   - Videoströme werden nicht angefasst. Sie kommen bereichsweise (Range) und
 *     sind bis zu mehrere Gigabyte groß. Ein Worker, der sie durch den Cache
 *     schleift, sprengt den Speicher des Telefons und zerbricht das Springen
 *     in der Zeitleiste, weil eine Teilantwort (206) nicht als vollständige
 *     Antwort zwischengespeichert werden darf.
 *   - API-Antworten und Vorschaubilder werden nie zwischengespeichert.
 *     Nach dem Abmelden dürfen keine geschützten Dateien verfügbar bleiben.
 *   - Nur GET. Ein zwischengespeichertes POST gibt es nicht, und ein
 *     abgefangenes DELETE wäre ein Datenverlust.
 */

const VERSION = "v2";
const SCHALE = `vitrine-schale-${VERSION}`;

/** Was beim Installieren schon geholt wird, damit der erste Start offline
 *  gelingt. Bewusst nur die Hülle - die gehashten Bündel von Vite kommen beim
 *  ersten Besuch von allein in den Cache. */
const SCHALE_DATEIEN = ["/", "/manifest.webmanifest", "/icons/icon-192.png"];

self.addEventListener("install", (e) => {
  e.waitUntil(
    caches
      .open(SCHALE)
      // addAll bricht ab, sobald eine Datei fehlt, und die Installation
      // scheitert komplett. Deshalb einzeln und fehlertolerant: Ein fehlendes
      // Icon darf nicht dazu führen, dass die App gar nicht installierbar ist.
      .then((c) => Promise.allSettled(SCHALE_DATEIEN.map((p) => c.add(p))))
      // Sofort übernehmen. Das ist hier gefahrlos, weil das Frontend als ein
      // einziges Bündel ausgeliefert wird - es gibt keine nachgeladenen
      // Teilstücke, die zu einer bereits laufenden alten Fassung passen
      // müssten. Bei aufgeteiltem Code wäre das ein Fehler.
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches
      .keys()
      .then((namen) =>
        Promise.all(
          namen
            .filter((n) => n.startsWith("vitrine-") && n !== SCHALE)
            .map((n) => caches.delete(n)),
        ),
      )
      .then(() => self.clients.claim()),
  );
});

/** Nachricht von der Seite: sofort aktualisieren. */
self.addEventListener("message", (e) => {
  if (e.data === "uebernimm") self.skipWaiting();
});

/** Die Hülle: erst das Netz, bei Fehlschlag der Cache.
 *
 * Andersherum wäre es schneller, aber dann bekäme man nach einem Update des
 * Containers so lange die alte Oberfläche, bis der Cache irgendwann abläuft -
 * ein Fehlerbild, das man nicht versteht und nicht wegbekommt. */
async function huelle(anfrage) {
  const cache = await caches.open(SCHALE);
  try {
    const antwort = await fetch(anfrage);
    if (speicherbar(antwort) && antwort.headers.get("Content-Type")?.split(";")[0].trim() === "text/html") {
      await cache.put(anfrage, antwort.clone());
    }
    return antwort;
  } catch (fehler) {
    const treffer = (await cache.match(anfrage)) || (await cache.match("/"));
    if (treffer) return treffer;
    throw fehler;
  }
}

/** CacheStorage berücksichtigt HTTP-Cache-Control nicht selbst. Auch eine
 * kodierte oder mit einem Pfadpräfix versehene API muss no-store behalten. */
function speicherbar(antwort) {
  return antwort.ok && !(antwort.headers.get("Cache-Control") || "")
    .split(",").some((regel) => regel.trim().toLowerCase() === "no-store");
}

/** Gehashte Bündel von Vite: erst der Cache, sonst holen.
 *
 * Der Dateiname enthält den Inhaltsstempel. Ändert sich der Inhalt, ändert
 * sich der Name - eine einmal gespeicherte Datei kann deshalb nie veralten. */
async function baustein(anfrage) {
  const cache = await caches.open(SCHALE);
  const treffer = await cache.match(anfrage);
  if (treffer) return treffer;
  const antwort = await fetch(anfrage);
  if (speicherbar(antwort)) await cache.put(anfrage, antwort.clone());
  return antwort;
}

/**
 * Entscheidet, wie eine Anfrage behandelt wird.
 *
 * Bewusst als eigene, seiteneffektfreie Funktion: Das ist die Stelle, an der
 * ein Fehler am teuersten wäre - ein versehentlich zwischengespeicherter
 * Videostrom sprengt den Gerätespeicher und zerbricht die Wiedergabe. Als
 * reine Funktion lässt sich die Entscheidung prüfen, ohne einen Browser und
 * einen laufenden Worker zu brauchen.
 *
 * "durchreichen" heißt: gar nicht anfassen, der Browser macht es selbst.
 *
 * @returns {"durchreichen"|"huelle"|"baustein"}
 */
function strategie(adresse, methode, modus, eigeneHerkunft) {
  // Ein zwischengespeichertes POST gibt es nicht, und ein abgefangenes DELETE
  // wäre ein Datenverlust.
  if (methode !== "GET") return "durchreichen";

  const url = new URL(adresse);
  if (url.origin !== eigeneHerkunft) return "durchreichen";

  // ---- Die wichtigste Entscheidung der Datei.
  // Videoströme und Untertitel gehen unberührt ans Netz - samt
  // Bereichsanfragen, Weiterleitungen und dem 202 während der Vorbereitung.
  if (url.pathname.includes("/stream") || url.pathname.includes("/subtitles/")) {
    return "durchreichen";
  }

  // Übrige API: nie aus dem Cache. Lieber ein ehrlicher Fehler als ein
  // Speicherstand von gestern.
  if (url.pathname === "/api" || url.pathname.startsWith("/api/")) return "durchreichen";

  if (modus === "navigate") return "huelle";

  if (url.pathname.startsWith("/assets/") || url.pathname.startsWith("/icons/")) {
    return "baustein";
  }

  return "huelle";
}

self.addEventListener("fetch", (e) => {
  const anfrage = e.request;
  const wahl = strategie(anfrage.url, anfrage.method, anfrage.mode, self.location.origin);
  if (wahl === "durchreichen") return;
  if (wahl === "baustein") e.respondWith(baustein(anfrage));
  else e.respondWith(huelle(anfrage));
});
