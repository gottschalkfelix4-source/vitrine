let aufraeumen: (() => void) | undefined;

/** Nach pointerup kann Safari den Klick auf ein gerade eingeblendetes Element richten. */
export function playerTouchAbschliessen() {
  aufraeumen?.();
  const klick = (e: MouseEvent) => {
    if (e.detail === 0) return; // Tastatur und assistive Technik.
    e.preventDefault();
    e.stopImmediatePropagation();
    entfernen();
  };
  const entfernen = () => {
    document.removeEventListener("click", klick, true);
    document.removeEventListener("pointerdown", entfernen, true);
    document.removeEventListener("touchstart", entfernen, true);
    clearTimeout(timer);
    aufraeumen = undefined;
  };
  const timer = setTimeout(entfernen, 1000);
  document.addEventListener("click", klick, true);
  // Ein neuer Druck ist eine neue Absicht und darf sofort wieder auslösen.
  document.addEventListener("pointerdown", entfernen, true);
  document.addEventListener("touchstart", entfernen, { capture: true, passive: true });
  aufraeumen = entfernen;
}
