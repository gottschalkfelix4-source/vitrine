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
  benutzerVorschlag?: string;
}

export class ApiFehler extends Error {
  constructor(public readonly status: number, message: string) { super(message); }
}

let zustand: Anmeldezustand = { art: "pruefen", sitzung: null, meldung: null, wechsel: 0 };
let generation = 0;
let rollenGeneration = 0;
let einrichtungLaeuft = false;
const horcher = new Set<() => void>();
const ABMELDE_EREIGNIS = "vitrine-abmeldung";

function setzen(neu: Anmeldezustand) {
  if ((zustand.sitzung?.csrf_token ?? null) !== (neu.sitzung?.csrf_token ?? null)) rollenGeneration++;
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
  const rolle = rollenGeneration;
  const token = zustand.sitzung?.csrf_token;
  const veraendert = !["GET", "HEAD", "OPTIONS"].includes((init.method ?? "GET").toUpperCase());
  if (!oeffentlich && veraendert && !token) throw new ApiFehler(401, "Bitte melde dich erneut an.");
  const headers = new Headers(init.headers);
  if (veraendert) {
    if (oeffentlich) headers.set("X-Vitrine-Request", "1");
    else headers.set("X-CSRF-Token", token!);
  }
  const antwort = await fetch(pfad, { ...init, headers, credentials: "same-origin", cache: "no-store" });
  if (!oeffentlich && rolle !== rollenGeneration) throw new ApiFehler(401, "Die Sitzung wurde geändert.");
  if (!oeffentlich && antwort.status === 401) {
    if (token) verwerfen("Deine Sitzung ist abgelaufen. Bitte melde dich erneut an.", true);
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
    const wechsel = zustand.sitzung === null || zustand.sitzung.angemeldet
      || zustand.sitzung.eingerichtet !== sitzung.eingerichtet;
    if (wechsel) generation++;
    setzen({ art: "bereit", sitzung: { ...sitzung, benutzer: null, csrf_token: null },
      meldung: zustand.sitzung?.angemeldet ? "Deine Sitzung ist abgelaufen. Bitte melde dich erneut an." : zustand.meldung,
      wechsel: zustand.wechsel + (wechsel ? 1 : 0), benutzerVorschlag: zustand.benutzerVorschlag });
  } else {
    const wechsel = zustand.sitzung?.csrf_token !== sitzung.csrf_token;
    setzen({ art: "bereit", sitzung, meldung: null, wechsel: zustand.wechsel + (wechsel ? 1 : 0) });
  }
}

let pruefung: { generation: number; versprechen: Promise<void> } | null = null;
async function pruefen(): Promise<void> {
  // Beim Kopieren des Codes aus dem Containerprotokoll kommt der Fokus
  // zurück. Eine parallele Prüfung darf die Einrichtung nicht überholen.
  if (einrichtungLaeuft) return;
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
  async einrichten(einrichtungscode: string, benutzer: string, passwort: string) {
    if (einrichtungLaeuft) throw new ApiFehler(429, "Die Einrichtung läuft bereits. Bitte warte kurz.");
    const lauf = ++generation;
    einrichtungLaeuft = true;
    try {
      await auswerten<void>(await authFetch("/api/auth/setup", {
        method: "POST", headers: { "Content-Type": "application/json", "X-Vitrine-Request": "1" },
        body: JSON.stringify({ einrichtungscode, benutzer, passwort }),
      }, true));
      if (lauf !== generation) return;
      generation++;
      setzen({ art: "bereit", sitzung: { eingerichtet: true, angemeldet: false, benutzer: null, csrf_token: null },
        meldung: "Administrator eingerichtet. Bitte melde dich mit deinen Zugangsdaten an.",
        benutzerVorschlag: benutzer, wechsel: zustand.wechsel + 1 });
    } catch (e) {
      if (e instanceof ApiFehler && e.status === 409 && lauf === generation) {
        generation++;
        setzen({ ...zustand, meldung: "Der Administrator wurde bereits eingerichtet. Bitte melde dich an." });
      } else {
        throw e;
      }
    } finally {
      einrichtungLaeuft = false;
      if (zustand.art === "pruefen") void pruefen();
    }
    await pruefen();
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
