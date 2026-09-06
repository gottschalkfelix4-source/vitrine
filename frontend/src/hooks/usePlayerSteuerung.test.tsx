// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { usePlayerSteuerung } from "./usePlayerSteuerung";

let root: Root;
let host: HTMLDivElement;
beforeEach(() => {
  vi.useFakeTimers(); vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  host = document.createElement("div"); document.body.append(host); root = createRoot(host);
});
afterEach(async () => { await act(() => root.unmount()); host.remove(); vi.useRealTimers(); vi.unstubAllGlobals(); });
function Probe({ vollbild = false, menueOffen = false, laeuft = true, minimiert = false }) {
  const { sichtbar, ereignisse } = usePlayerSteuerung({ vollbild, menueOffen, laeuft, minimiert, bereit: true });
  return <div {...ereignisse} data-sichtbar={sichtbar} tabIndex={-1}><button>Aktion</button></div>;
}
const sichtbar = () => host.firstElementChild?.getAttribute("data-sichtbar");
async function warten() { await act(() => vi.advanceTimersByTime(3000)); }

it("blendet nach Wiedergabestart und nach dem Drehen trotz Fokus auf der Hülle aus", async () => {
  await act(() => root.render(<Probe />)); await warten();
  expect(sichtbar()).toBe("false");
  await act(() => root.render(<Probe vollbild />));
  await act(() => (host.firstElementChild as HTMLElement).focus());
  expect(sichtbar()).toBe("true"); await warten();
  expect(sichtbar()).toBe("false");
});

it("hält offene Menüs, Pause und Miniplayer sichtbar und blendet danach wieder aus", async () => {
  await act(() => root.render(<Probe menueOffen />)); await warten(); expect(sichtbar()).toBe("true");
  await act(() => root.render(<Probe />)); await warten(); expect(sichtbar()).toBe("false");
  await act(() => root.render(<Probe laeuft={false} />)); await warten(); expect(sichtbar()).toBe("true");
  await act(() => root.render(<Probe minimiert />)); await warten(); expect(sichtbar()).toBe("true");
});

it("blendet während eines langen Tastendrucks nicht unter dem Finger aus", async () => {
  await act(() => root.render(<Probe />));
  const druck = (typ: string) => Object.assign(new MouseEvent(typ, { bubbles: true }), { pointerType: "touch" });
  await act(() => host.querySelector('button')!.dispatchEvent(druck("pointerdown")));
  await warten(); expect(sichtbar()).toBe("true");
  await act(() => host.querySelector('button')!.dispatchEvent(druck("pointerup")));
  await warten(); expect(sichtbar()).toBe("false");
});
