// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { flaecheNeuAufbauen, trefferflaecheVersetzt } from "./trefferflaeche";

let ebene: HTMLDivElement;
let kind: HTMLButtonElement;

/** Legt eine Fläche mit fester gezeichneter Lage an. */
function lage(el: HTMLElement, x: number, y: number, breite: number, hoehe: number) {
  el.getBoundingClientRect = () => new DOMRect(x, y, breite, hoehe);
}

beforeEach(() => {
  vi.stubGlobal("innerWidth", 400);
  vi.stubGlobal("innerHeight", 800);
  ebene = document.createElement("div");
  kind = document.createElement("button");
  ebene.append(kind);
  document.body.append(ebene);
  lage(ebene, 0, 100, 400, 220);
});

afterEach(() => { ebene.remove(); vi.unstubAllGlobals(); });

it("meldet keinen Versatz, wenn der Browser die Fläche selbst zurückgibt", () => {
  document.elementFromPoint = () => ebene;
  expect(trefferflaecheVersetzt(ebene)).toBe(false);
});

it("meldet keinen Versatz, wenn ein Bedienelement der Fläche getroffen wird", () => {
  document.elementFromPoint = () => kind;
  expect(trefferflaecheVersetzt(ebene)).toBe(false);
});

it("erkennt eine Fläche, die neben ihrer gezeichneten Lage getroffen wird", () => {
  // So verhält sich WebKit nach der Drehung: Der Treffer landet daneben.
  document.elementFromPoint = () => document.body;
  expect(trefferflaecheVersetzt(ebene)).toBe(true);
});

it("erkennt auch einen Versatz, der nur eine der beiden Kanten verfehlt", () => {
  // Ein Versatz kleiner als die halbe Höhe träfe in der Mitte noch die Fläche.
  document.elementFromPoint = (_x: number, y: number) => (y < 200 ? document.body : ebene);
  expect(trefferflaecheVersetzt(ebene)).toBe(true);
});

it("urteilt nicht über Flächen, die zu klein oder nicht ganz sichtbar sind", () => {
  document.elementFromPoint = () => document.body;
  lage(ebene, 0, 100, 400, 8);
  expect(trefferflaecheVersetzt(ebene)).toBe(false);
  lage(ebene, 0, -40, 400, 220);
  expect(trefferflaecheVersetzt(ebene)).toBe(false);
  lage(ebene, 0, 700, 400, 220);
  expect(trefferflaecheVersetzt(ebene)).toBe(false);
});

it("baut die Fläche neu auf und lässt ihre eigene Anzeigeart stehen", () => {
  ebene.style.display = "flex";
  flaecheNeuAufbauen(ebene);
  expect(ebene.style.display).toBe("flex");
  ebene.style.display = "";
  flaecheNeuAufbauen(ebene);
  expect(ebene.getAttribute("style")).not.toContain("display: none");
});
