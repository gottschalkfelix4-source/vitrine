import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, expect, it } from "vitest";
import { Anmeldung, AnmeldeSchranke } from "./Anmeldung";
import { auth } from "../lib/auth";

beforeEach(() => auth.sperren());

it("rendert vor der Sitzungsprüfung keinerlei geschützte Komponenten", () => {
  function Archiv() { throw new Error("Geschütztes Archiv wurde gerendert"); return null; }
  const html = renderToStaticMarkup(<AnmeldeSchranke><Archiv /></AnmeldeSchranke>);
  expect(html).toContain("Anmeldung wird geprüft");
});

it("zeigt bei fehlendem Administrator keinen Login und keine Einrichtung im Browser", () => {
  const html = renderToStaticMarkup(<Anmeldung zustand={{ art: "bereit", meldung: null, wechsel: 0,
    sitzung: { eingerichtet: false, angemeldet: false, benutzer: null, csrf_token: null } }} />);
  expect(html).toContain("Administrator noch nicht eingerichtet. Richte den Zugang am Server ein.");
  expect(html).not.toContain("<form");
});

it("bietet beschriftete Anmeldefelder mit Passwortmanager-Unterstützung", () => {
  const html = renderToStaticMarkup(<Anmeldung zustand={{ art: "bereit", meldung: null, wechsel: 0,
    sitzung: { eingerichtet: true, angemeldet: false, benutzer: null, csrf_token: null } }} />);
  expect(html).toContain('autoComplete="username"');
  expect(html).toContain('autoComplete="current-password"');
  expect(html).toContain('type="password"');
  expect(html).toContain('value="admin"');
});
