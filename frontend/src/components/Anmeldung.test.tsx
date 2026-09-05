import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, expect, it } from "vitest";
import { Anmeldung, AnmeldeSchranke, einrichtungsFehler } from "./Anmeldung";
import { ApiFehler, auth } from "../lib/auth";

beforeEach(() => auth.sperren());

it("rendert vor der Sitzungsprüfung keinerlei geschützte Komponenten", () => {
  function Archiv() { throw new Error("Geschütztes Archiv wurde gerendert"); return null; }
  const html = renderToStaticMarkup(<AnmeldeSchranke><Archiv /></AnmeldeSchranke>);
  expect(html).toContain("Anmeldung wird geprüft");
});

it("öffnet bei fehlendem Administrator den zugänglichen Einrichtungsdialog", () => {
  const html = renderToStaticMarkup(<Anmeldung zustand={{ art: "bereit", meldung: null, wechsel: 0,
    sitzung: { eingerichtet: false, angemeldet: false, benutzer: null, csrf_token: null } }} />);
  expect(html).toContain("Administrator noch nicht eingerichtet.");
  expect(html).toContain('<dialog class="kanal-dialog" aria-labelledby="einrichtung-titel" aria-describedby="einrichtung-hinweis"');
  expect(html).toContain("Einmaliger Einrichtungscode");
  expect(html).toContain("Nach einem Neustart gilt der neue Code.");
  expect(html).toContain('minLength="8"');
  expect(html).toContain("Mindestens 8 Zeichen, ein Großbuchstabe und ein Sonderzeichen.");
  expect(html).not.toContain('id="login-passwort"');
  expect(html).not.toContain("python");
});

it("bietet beschriftete Anmeldefelder mit Passwortmanager-Unterstützung", () => {
  const html = renderToStaticMarkup(<Anmeldung zustand={{ art: "bereit", meldung: null, wechsel: 0,
    sitzung: { eingerichtet: true, angemeldet: false, benutzer: null, csrf_token: null } }} />);
  expect(html).toContain('autoComplete="username"');
  expect(html).toContain('autoComplete="current-password"');
  expect(html).toContain('type="password"');
  expect(html).toContain('value="admin"');
  expect(html).not.toContain("<dialog");
  expect(html).not.toContain("Einrichtungscode");
});

it("übernimmt nach der Einrichtung den eingegebenen Benutzernamen in den Login", () => {
  const html = renderToStaticMarkup(<Anmeldung zustand={{ art: "bereit", meldung: "Administrator eingerichtet.", wechsel: 1,
    benutzerVorschlag: "test-admin", sitzung: { eingerichtet: true, angemeldet: false, benutzer: null, csrf_token: null } }} />);
  expect(html).toContain('value="test-admin"');
  expect(html).toContain("Administrator eingerichtet.");
  expect(html).not.toContain("<dialog");
});

it("unterscheidet einen falschen Einrichtungscode von blockierter Herkunft oder HTTP", () => {
  const code = einrichtungsFehler(new ApiFehler(403, "Der Einrichtungscode ist falsch oder abgelaufen."));
  expect(code).toContain("aktuellen Code aus dem Containerprotokoll");
  const herkunft = einrichtungsFehler(new ApiFehler(403, "Anfragen von einer fremden Herkunft sind nicht erlaubt."));
  expect(herkunft).toContain("konfigurierte HTTPS-Adresse");
  expect(herkunft).toContain("Reverse Proxy");
  expect(herkunft).not.toContain("Einrichtungscode ist falsch");
});

it("behält andere Serverfehler bei und behauptet bei Netzfehlern keine Einrichtung", () => {
  expect(einrichtungsFehler(new ApiFehler(403, "Andere Berechtigungsgrenze"))).toBe("Andere Berechtigungsgrenze");
  expect(einrichtungsFehler(new ApiFehler(400, "Passwort zu kurz"))).toBe("Passwort zu kurz");
  expect(einrichtungsFehler(new TypeError("offline"))).toContain("konnte nicht bestätigt werden");
});
