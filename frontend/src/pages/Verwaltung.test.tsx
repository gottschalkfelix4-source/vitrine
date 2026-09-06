// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { Einstellungenseite } from "./Einstellungen";
import { Hochstufen } from "../components/Hochstufen";
import { api, type EinstellungsFeld, type UpgradeVorschau } from "../lib/api";

vi.mock("../components/AppInstallieren", () => ({ AppInstallieren: () => null }));
vi.mock("../components/Cookies", () => ({ CookieAssistent: () => null }));
vi.mock("../components/Hardware", () => ({ HardwarePruefung: () => null }));
vi.mock("../components/Vpn", () => ({ VpnTunnelListe: () => null }));
vi.mock("../components/Anmeldung", () => ({ PasswortAendern: () => null }));

let root: Root;
let host: HTMLDivElement;
const feld = (name: string, wert: string, herkunft: EinstellungsFeld["herkunft"] = "standard"): EinstellungsFeld => ({
  name, wert, herkunft, standard: "standard", gruppe: "Archiv", titel: name, beschreibung: "", art: "text", neustart: false,
  min: null, max: null, auswahl: [], einheit: null,
});
const einstellungen = (a = "alt", b = "gesetzt") => ({ gruppen: ["Archiv"], felder: [feld("a", a), feld("b", b, "datenbank")] });
const vorschau = (ziel: number, videos: number): UpgradeVorschau => ({
  ziel, videos, jetzt_bytes: 100, geschaetzt_bytes: 200, zusatz_bytes: 100,
  freier_platz: 1000, passt: true, nach_stufe: { "720": videos }, stunden_mindestens: 0,
});
function spaeter<T>() {
  let fertig!: (wert: T) => void;
  const promise = new Promise<T>((resolve) => { fertig = resolve; });
  return { promise, fertig };
}
function knopf(text: string) {
  const gefunden = [...host.querySelectorAll("button")].find((el) => el.textContent?.trim() === text);
  if (!gefunden) throw new Error(`Knopf fehlt: ${text}`);
  return gefunden;
}
async function eingeben(id: string, text: string) {
  await act(async () => {
    const input = host.querySelector<HTMLInputElement>(`#${id}`)!;
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, text);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

beforeEach(() => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});
afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

it("behält den Entwurf eines anderen Feldes beim Zurücksetzen", async () => {
  vi.spyOn(api, "einstellungen").mockResolvedValueOnce(einstellungen()).mockResolvedValueOnce(einstellungen("alt", "standard"));
  const reset = vi.spyOn(api, "einstellungenZuruecksetzen").mockResolvedValue({ zurueckgesetzt: ["b"] });
  await act(async () => root.render(<Einstellungenseite />));
  await act(async () => knopf("Archiv").click());
  await eingeben("f-a", "mein Entwurf");
  await act(async () => knopf("zurücksetzen").click());
  expect(reset).toHaveBeenCalledWith(["b"]);
  expect(host.querySelector<HTMLInputElement>("#f-a")!.value).toBe("mein Entwurf");
  expect(host.querySelector<HTMLInputElement>("#f-b")!.value).toBe("standard");
  expect(host.textContent).toContain("1 Änderung noch nicht gespeichert");
});

it("akzeptiert den Schutzabstand5.0 und0.1 ohne falschen Schrittfehler", async () => {
  const schutzfeld: EinstellungsFeld = {
    ...feld("youtube_anfrage_abstand", "5"), art: "float", gruppe: "YouTube-Schutz",
    min: 0.1, max: 120, wert: 5,
  };
  vi.spyOn(api, "einstellungen").mockResolvedValue({ gruppen: ["YouTube-Schutz"], felder: [schutzfeld] });
  await act(async () => root.render(<Einstellungenseite />));
  await act(async () => knopf("YouTube-Schutz").click());
  const input = host.querySelector<HTMLInputElement>("#f-youtube_anfrage_abstand")!;
  expect(input.checkValidity()).toBe(true);
  await eingeben(input.id, "0.1");
  expect(input.checkValidity()).toBe(true);
  await eingeben(input.id, "0");
  expect(input.validity.rangeUnderflow).toBe(true);
});

it("sperrt Einstellungen während Speichern und Bestätigungsabfrage und sendet nur einmal", async () => {
  const speichern = spaeter<{ geaendert: string[]; neustart_noetig: string[] }>();
  const bestaetigung = spaeter<ReturnType<typeof einstellungen>>();
  vi.spyOn(api, "einstellungen").mockResolvedValueOnce(einstellungen()).mockReturnValueOnce(bestaetigung.promise);
  const senden = vi.spyOn(api, "einstellungenSpeichern").mockReturnValue(speichern.promise);
  await act(async () => root.render(<Einstellungenseite />));
  await act(async () => knopf("Archiv").click());
  await eingeben("f-a", "neu");
  await act(async () => { const button = knopf("Speichern"); button.click(); button.click(); });
  expect(senden).toHaveBeenCalledOnce();
  expect(senden).toHaveBeenCalledWith({ a: "neu" });
  expect(host.querySelector<HTMLInputElement>("#f-a")!.disabled).toBe(true);
  expect(knopf("zurücksetzen").disabled).toBe(true);
  await act(async () => speichern.fertig({ geaendert: ["a"], neustart_noetig: [] }));
  expect(host.querySelector<HTMLInputElement>("#f-a")!.disabled).toBe(true);
  await act(async () => bestaetigung.fertig(einstellungen("neu")));
  expect(host.querySelector<HTMLInputElement>("#f-a")!.disabled).toBe(false);
  expect(host.querySelector<HTMLInputElement>("#f-a")!.value).toBe("neu");
  expect(host.textContent).not.toContain("noch nicht gespeichert");
});

it("behält Entwürfe und entsperrt Felder nach fehlgeschlagenem Speichern", async () => {
  vi.spyOn(api, "einstellungen").mockResolvedValue(einstellungen());
  vi.spyOn(api, "einstellungenSpeichern").mockRejectedValue(new Error("Verbindung unterbrochen"));
  await act(async () => root.render(<Einstellungenseite />));
  await act(async () => knopf("Archiv").click());
  await eingeben("f-a", "neu");
  await act(async () => knopf("Speichern").click());
  expect(host.querySelector<HTMLInputElement>("#f-a")!.value).toBe("neu");
  expect(host.querySelector<HTMLInputElement>("#f-a")!.disabled).toBe(false);
  expect(host.textContent).toContain("Verbindung unterbrochen");
});

it("übernimmt normalisierte Listen nach erfolgreichem Speichern ohne einen falschen offenen Entwurf", async () => {
  const vorher = { gruppen: ["Archiv"], felder: [{ ...feld("sprachen", "de"), art: "liste" as const, wert: ["de"] }] };
  const nachher = { gruppen: ["Archiv"], felder: [{ ...vorher.felder[0], wert: ["de", "en"] }] };
  vi.spyOn(api, "einstellungen").mockResolvedValueOnce(vorher).mockResolvedValueOnce(nachher);
  vi.spyOn(api, "einstellungenSpeichern").mockResolvedValue({ geaendert: ["sprachen"], neustart_noetig: [] });
  await act(async () => root.render(<Einstellungenseite />));
  await act(async () => knopf("Archiv").click());
  await eingeben("f-sprachen", "de, en");
  await act(async () => knopf("Speichern").click());
  expect(host.querySelector<HTMLInputElement>("#f-sprachen")!.value).toBe("de,en");
  expect(host.textContent).not.toContain("noch nicht gespeichert");
});

it("zeigt nach schnellem Qualitätswechsel nur die neueste Vorschau und reiht sie einmal ein", async () => {
  const alt = spaeter<UpgradeVorschau>();
  const neu = spaeter<UpgradeVorschau>();
  const einreihen = spaeter<Awaited<ReturnType<typeof api.upgradeEinreihen>>>();
  vi.spyOn(api, "upgradeVorschau").mockReturnValueOnce(alt.promise).mockReturnValueOnce(neu.promise).mockResolvedValue(vorschau(4320, 0));
  const senden = vi.spyOn(api, "upgradeEinreihen").mockReturnValue(einreihen.promise);
  await act(async () => root.render(<Hochstufen kanal="kanal" />));
  await act(async () => {
    host.querySelector("select")!.value = "4320";
    host.querySelector("select")!.dispatchEvent(new Event("change", { bubbles: true }));
  });
  await act(async () => neu.fertig(vorschau(4320, 3)));
  await act(async () => alt.fertig(vorschau(2160, 99)));
  expect(host.textContent).toContain("3 Videos hochstufen");
  expect(host.textContent).not.toContain("99");
  await act(async () => knopf("3 Videos hochstufen").click());
  await act(async () => { const button = knopf("Ja, einreihen"); button.click(); button.click(); });
  expect(senden).toHaveBeenCalledOnce();
  expect(senden).toHaveBeenCalledWith(4320, "kanal");
  expect(host.querySelector("select")!.disabled).toBe(true);
  await act(async () => einreihen.fertig({ eingereiht: 3, ziel: 4320 }));
  expect(host.querySelector("select")!.disabled).toBe(false);
  expect(host.textContent).toContain("3 Videos eingereiht");
});
