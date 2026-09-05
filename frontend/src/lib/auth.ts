export interface Sitzung {
  eingerichtet: boolean;
  angemeldet: boolean;
  benutzer: string | null;
  csrf_token: string | null;
}

export interface Anmeldezustand {
  art: "pruefen" | "bereit" | "fehler";
  sitzung: Sitzung | null;
  meldung: string | null;
  wechsel: number;
}

export class ApiFehler extends Error {
  constructor(public readonly status: number, message: string) { super(message); }
}

let zustand: Anmeldezustand = { art: "pruefen", sitzung: null, meldung: null, wechsel: 0 };
let generation = 0;
const horcher = new Set<() => void>();
const ABMELDE_EREIGNIS = "vitrine-abmeldung";

function setzen(neu: Anmeldezustand) {
  zustand = neu;
  horcher.forEach((h) => h());
}

function bekanntgeben() {
  // Nur ein Ereignis, niemals Sitzungsdaten oder Zugangsdaten speichern.
  try { localStorage.setItem(ABMELDE_EREIGNIS, `${Date.now()}-${Math.random()}`); } catch { /* Privater Browsermodus */ }
  try {
    const kanal = new BroadcastChannel(ABMELDE_EREIGNIS);
    kanal.postMessage("abgemeldet");
    kanal.close();
  } catch { /* Ältere Browser nutzen das storage-Ereignis. */ }
}

function verwerfen(meldung: string | null, verbreiten = false) {
  generation++;
  setzen({ art: "bereit", sitzung: { eingerichtet: true, angemeldet: false, benutzer: null, csrf_token: null },
    meldung, wechsel: zustand.wechsel + 1 });
  if (verbreiten) bekanntgeben();
}

/** Alle API-Aufrufe, auch multipart, Range und keepalive, gehen hier durch. */
export async function authFetch(pfad: string, init: RequestInit = {}, oeffentlich = false): Promise<Response> {
  const lauf = generation;
  const token = zustand.sitzung?.csrf_token;
  if (!oeffentlich && !token) throw new ApiFehler(401, "Bitte melde dich erneut an.");
  const headers = new Headers(init.headers);
  if (!oeffentlich && !["GET", "HEAD", "OPTIONS"].includes((init.method ?? "GET").toUpperCase())) {
    headers.set("X-CSRF-Token", token!);
  }
  const antwort = await fetch(pfad, { ...init, headers, credentials: "same-origin", cache: "no-store" });
  if (!oeffentlich && lauf !== generation) throw new ApiFehler(401, "Die Sitzung wurde beendet.");
  if (!oeffentlich && antwort.status === 401) {
    verwerfen("Deine Sitzung ist abgelaufen. Bitte melde dich erneut an.", true);
    throw new ApiFehler(401, "Bitte melde dich erneut an.");
  }
  return antwort;
}

export async function auswerten<T>(antwort: Response): Promise<T> {
  if (!antwort.ok) {
    let text = antwort.statusText || "Die Anfrage ist fehlgeschlagen.";
    try {
      const koerper = await antwort.json();
      if (typeof koerper.detail === "string") text = koerper.detail;
    } catch { /* Keine JSON-Antwort. */ }
    throw new ApiFehler(antwort.status, text);
  }
  if (antwort.status === 204) return undefined as T;
  return await antwort.json() as T;
}

function uebernehmen(sitzung: Sitzung) {
  if (sitzung.angemeldet && !sitzung.csrf_token) throw new Error("Die Anmeldung konnte nicht bestätigt werden.");
  if (!sitzung.angemeldet) {
    generation++;
    setzen({ art: "bereit", sitzung: { ...sitzung, benutzer: null, csrf_token: null },
      meldung: zustand.sitzung?.angemeldet ? "Deine Sitzung ist abgelaufen. Bitte melde dich erneut an." : zustand.meldung,
      wechsel: zustand.wechsel + 1 });
  } else {
    setzen({ art: "bereit", sitzung, meldung: null, wechsel: zustand.wechsel });
  }
}

let pruefung: { generation: number; versprechen: Promise<void> } | null = null;
async function pruefen(): Promise<void> {
  if (pruefung?.generation === generation) return pruefung.versprechen;
  const lauf = generation;
  const versprechen = (async () => {
    try {
      const sitzung = await auswerten<Sitzung>(await authFetch("/api/auth/session", {}, true));
      if (lauf === generation) uebernehmen(sitzung);
    } catch {
      if (lauf !== generation) return;
      generation++;
      setzen({ art: "fehler", sitzung: null, meldung: "Die Verbindung zum Server ist fehlgeschlagen.", wechsel: zustand.wechsel + 1 });
    } finally {
      if (pruefung?.generation === lauf) pruefung = null;
    }
  })();
  pruefung = { generation: lauf, versprechen };
  return versprechen;
}

export const auth = {
  zustand: () => zustand,
  abonnieren(h: () => void) { horcher.add(h); return () => { horcher.delete(h); }; },
  pruefen,
  sperren() {
    generation++;
    setzen({ art: "pruefen", sitzung: null, meldung: null, wechsel: zustand.wechsel + 1 });
  },
  async anmelden(benutzer: string, passwort: string) {
    const lauf = ++generation;
    const sitzung = await auswerten<Sitzung>(await authFetch("/api/auth/login", {
      method: "POST", headers: { "Content-Type": "application/json", "X-Vitrine-Request": "1" },
      body: JSON.stringify({ benutzer, passwort }),
    }, true));
    if (lauf === generation) {
      // Eine während des Logins gestartete Statusanfrage kennt dessen
      // neues Cookie noch nicht und darf die Anmeldung nicht zurücknehmen.
      generation++;
      uebernehmen(sitzung);
    }
  },
  async abmelden() {
    await auswerten<void>(await authFetch("/api/auth/logout", { method: "POST" }));
    verwerfen("Du bist abgemeldet.", true);
  },
  async passwortAendern(aktuelles_passwort: string, neues_passwort: string) {
    await auswerten<void>(await authFetch("/api/auth/password", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ aktuelles_passwort, neues_passwort }),
    }));
    verwerfen("Passwort geändert. Bitte melde dich mit dem neuen Passwort an.", true);
  },
  tabAbmeldungenBeachten(): () => void {
    const abmelden = () => {
      // BroadcastChannel und storage können dasselbe Ereignis liefern.
      // Eine schon bestätigte Passwortänderung behält ihren eigenen Hinweis.
      if (zustand.art === "bereit" && zustand.sitzung?.angemeldet === false) return;
      verwerfen("Die Sitzung wurde beendet. Bitte melde dich erneut an.");
    };
    const beiSpeicher = (e: StorageEvent) => { if (e.key === ABMELDE_EREIGNIS && e.newValue) abmelden(); };
    window.addEventListener("storage", beiSpeicher);
    let kanal: BroadcastChannel | undefined;
    try {
      kanal = new BroadcastChannel(ABMELDE_EREIGNIS);
      kanal.onmessage = (e) => { if (e.data === "abgemeldet") abmelden(); };
    } catch { /* storage bleibt verfügbar. */ }
    return () => { window.removeEventListener("storage", beiSpeicher); kanal?.close(); };
  },
};
