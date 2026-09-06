// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { Dialog } from "./Dialog";
import { Bild } from "./Bild";

let root: Root;
let host: HTMLDivElement;

beforeEach(() => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  Object.defineProperties(HTMLDialogElement.prototype, {
    showModal: { configurable: true, value: function (this: HTMLDialogElement) { this.setAttribute("open", ""); } },
    close: { configurable: true, value: function (this: HTMLDialogElement) { this.removeAttribute("open"); } },
  });
});

afterEach(async () => {
  await act(async () => root.unmount());
  document.body.replaceChildren();
  document.body.style.overflow = "";
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

it("zeigt bei defekten Bildern den Platzhalter und versucht eine neue Quelle erneut", async () => {
  await act(async () => root.render(<Bild src="/defekt.jpg" alt=""><span>Kein Bild</span></Bild>));
  await act(async () => host.querySelector("img")!.dispatchEvent(new Event("error")));
  expect(host.querySelector("img")).toBeNull();
  expect(host.textContent).toBe("Kein Bild");
  await act(async () => root.render(<Bild src="/neues.jpg" alt=""><span>Kein Bild</span></Bild>));
  expect(host.querySelector("img")?.getAttribute("src")).toBe("/neues.jpg");
});

it("setzt den Formularfokus und stellt Fokus und Scrollzustand nach dem Schließen wieder her", async () => {
  const ausloeser = document.createElement("button");
  document.body.append(ausloeser);
  ausloeser.focus();
  document.body.style.overflow = "auto";
  await act(async () => root.render(<Dialog titelId="titel" aufSchliessen={vi.fn()}>
    <h2 id="titel">Kanal aufnehmen</h2><input data-dialog-fokus />
  </Dialog>));
  expect(document.activeElement).toBe(host.querySelector("input"));
  expect(document.body.style.overflow).toBe("hidden");
  await act(async () => root.render(null));
  expect(document.activeElement).toBe(ausloeser);
  expect(document.body.style.overflow).toBe("auto");
});

it("schließt nur bei einem vollständigen Klick außerhalb des Dialogs", async () => {
  const schliessen = vi.fn();
  await act(async () => root.render(<Dialog titelId="titel" aufSchliessen={schliessen}><h2 id="titel">Dialog</h2></Dialog>));
  const dialog = host.querySelector("dialog")!;
  vi.spyOn(dialog, "getBoundingClientRect").mockReturnValue({ left: 100, top: 100, right: 400, bottom: 400 } as DOMRect);
  const zeiger = (typ: string, x: number) => dialog.dispatchEvent(new MouseEvent(typ, { bubbles: true, clientX: x, clientY: x }));
  await act(async () => { zeiger("pointerdown", 200); zeiger("click", 200); });
  await act(async () => { zeiger("pointerdown", 200); zeiger("click", 20); });
  expect(schliessen).not.toHaveBeenCalled();
  await act(async () => { zeiger("pointerdown", 20); zeiger("click", 20); });
  expect(schliessen).toHaveBeenCalledOnce();
});

it("behält einen gesperrten Dialog bei Escape offen", async () => {
  const schliessen = vi.fn();
  await act(async () => root.render(<Dialog titelId="titel" aufSchliessen={schliessen} schliessenGesperrt><h2 id="titel">Dialog</h2></Dialog>));
  const abbrechen = new Event("cancel", { cancelable: true });
  await act(async () => host.querySelector("dialog")!.dispatchEvent(abbrechen));
  expect(abbrechen.defaultPrevented).toBe(true);
  expect(schliessen).not.toHaveBeenCalled();
  expect(host.querySelector("dialog")!.open).toBe(true);
});
