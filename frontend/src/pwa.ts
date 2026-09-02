/**
 * Anmeldung des Service Workers.
 *
 * Der Haken, an dem das im Heimnetz üblicherweise scheitert: Ein Service
 * Worker läuft nur in einem "sicheren Kontext". Das heißt HTTPS - oder
 * `localhost`, das der Browser als Ausnahme durchgehen lässt. Ein Aufruf über
 * `http://192.168.1.50:8000` erfüllt das **nicht**. Dort gibt es dann keine
 * Installation auf den Startbildschirm und keinen Bilder-Cache; die Seite
 * funktioniert im Browser ganz normal weiter.
 *
 * Deshalb wird hier nicht stumm nichts getan, sondern der Grund festgehalten
 * und abfragbar gemacht. Eine Installationsmöglichkeit, die einfach fehlt,
 * ohne dass irgendwo steht warum, kostet sonst einen Abend Suche.
 */

export type PwaZustand =
  | { art: "aktiv" }
  | { art: "unsicher"; herkunft: string }
  | { art: "nicht_unterstuetzt" }
  | { art: "aus" }
  | { art: "fehler"; meldung: string };

let zustand: PwaZustand = { art: "aus" };
const horcher = new Set<(z: PwaZustand) => void>();

function setzen(neu: PwaZustand) {
  zustand = neu;
  horcher.forEach((h) => h(neu));
}

export function pwaZustand(): PwaZustand {
  return zustand;
}

export function beiPwaZustand(h: (z: PwaZustand) => void): () => void {
  horcher.add(h);
  return () => horcher.delete(h);
}

/* -------------------------------------------------- Installieren anbieten */

/** Das Ereignis, mit dem Chrome die Installation anbietet. Es steht nicht in
 *  den Standard-Typdefinitionen, weil es keine Empfehlung des W3C ist. */
interface InstallEreignis extends Event {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: "accepted" | "dismissed" }>;
}

let gemerktesAngebot: InstallEreignis | null = null;
const angebotHorcher = new Set<(moeglich: boolean) => void>();

if (typeof window !== "undefined") {
  window.addEventListener("beforeinstallprompt", (e) => {
    // Ohne das zeigt Chrome seinen eigenen Streifen am unteren Rand. Wir
    // fangen es ab, um die Installation dort anzubieten, wo sie hingehoert.
    e.preventDefault();
    gemerktesAngebot = e as InstallEreignis;
    angebotHorcher.forEach((h) => h(true));
  });
  window.addEventListener("appinstalled", () => {
    gemerktesAngebot = null;
    angebotHorcher.forEach((h) => h(false));
  });
}

export function installierenMoeglich(): boolean {
  return gemerktesAngebot !== null;
}

export function beiInstallierbar(h: (moeglich: boolean) => void): () => void {
  angebotHorcher.add(h);
  return () => angebotHorcher.delete(h);
}

/** Liefert true, wenn der Nutzer zugestimmt hat. */
export async function installieren(): Promise<boolean> {
  if (!gemerktesAngebot) return false;
  await gemerktesAngebot.prompt();
  const { outcome } = await gemerktesAngebot.userChoice;
  // Das Angebot laesst sich nur einmal verwenden.
  gemerktesAngebot = null;
  angebotHorcher.forEach((h) => h(false));
  return outcome === "accepted";
}

/** Laeuft die App bereits ohne Browserleiste? */
export function alsAppGestartet(): boolean {
  if (typeof window === "undefined") return false;
  return (
    window.matchMedia("(display-mode: standalone)").matches ||
    // Safari auf iOS kennt display-mode nicht und meldet es hierueber.
    (window.navigator as { standalone?: boolean }).standalone === true
  );
}

export function serviceWorkerAnmelden(): void {
  // Im Entwicklungsbetrieb stört der Worker nur: Vite liefert Module einzeln
  // aus und tauscht sie im laufenden Betrieb, ein Cache dazwischen führt zu
  // Fehlern, die es im Betrieb gar nicht gibt.
  if (import.meta.env.DEV) return;

  if (!("serviceWorker" in navigator)) {
    setzen({ art: "nicht_unterstuetzt" });
    return;
  }
  if (!window.isSecureContext) {
    setzen({ art: "unsicher", herkunft: window.location.origin });
    return;
  }

  window.addEventListener("load", () => {
    navigator.serviceWorker
      .register("/sw.js", { scope: "/" })
      .then(() => setzen({ art: "aktiv" }))
      .catch((e) => setzen({ art: "fehler", meldung: e instanceof Error ? e.message : String(e) }));
  });
}
