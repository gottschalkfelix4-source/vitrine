export interface LokalerFortschritt { sekunden: number; gesehen: boolean }

const schluessel = (id: string) => `vitrine.fortschritt.${encodeURIComponent(id)}`;

/** Gastfortschritt bleibt auf diesem Gerät und verändert keine anderen Zuschauer. */
export function lokalFortschrittLesen(id: string): LokalerFortschritt | null {
  try {
    const roh = localStorage.getItem(schluessel(id));
    if (!roh) return null;
    const wert = JSON.parse(roh);
    if (!wert || typeof wert.sekunden !== "number" || !Number.isFinite(wert.sekunden) || wert.sekunden < 0) return null;
    return { sekunden: wert.sekunden, gesehen: wert.gesehen === true };
  } catch { return null; }
}

export function lokalFortschrittMerken(id: string, sekunden: number, gesehen?: boolean): void {
  if (!Number.isFinite(sekunden) || sekunden < 0) return;
  try {
    const vorher = lokalFortschrittLesen(id);
    localStorage.setItem(schluessel(id), JSON.stringify({ sekunden, gesehen: gesehen ?? vorher?.gesehen ?? false }));
  } catch { /* Bei vollem oder gesperrtem Browserspeicher weiter abspielen. */ }
}
