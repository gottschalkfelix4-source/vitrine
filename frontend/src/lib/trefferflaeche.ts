/**
 * Wird eine Fläche noch dort getroffen, wo sie gezeichnet wird?
 *
 * WebKit kann die Trefferfläche eines Bereichs nach einer Drehung an der alten
 * Stelle stehen lassen. Gezeichnet wird dann richtig, getroffen wird woanders -
 * auf dem iPhone lagen die Bedienelemente des Players danach um mehrere
 * Knopfhöhen neben ihren Symbolen. Von aussen ist dem Zustand nicht anzusehen,
 * dass etwas nicht stimmt; messen kann man es aber: Der Browser beantwortet mit
 * elementFromPoint dieselbe Frage, die er auch bei einer Berührung beantwortet.
 * Weicht seine Antwort von der gezeichneten Lage ab, ist die Fläche versetzt.
 *
 * Geprüft wird knapp innerhalb der Ober- und Unterkante. Die Mitte taugt nicht:
 * Ein Versatz, der kleiner ist als die halbe Höhe, träfe dort immer noch die
 * Fläche selbst und bliebe unbemerkt.
 */
export function trefferflaecheVersetzt(flaeche: HTMLElement): boolean {
  const rand = 4;
  const r = flaeche.getBoundingClientRect();
  // Zu klein oder nicht vollständig sichtbar: Dann sagt die Messung nichts.
  if (r.width < 3 * rand || r.height < 3 * rand) return false;
  if (r.top < 0 || r.left < 0 || r.bottom > window.innerHeight || r.right > window.innerWidth) return false;
  const mitte = r.left + r.width / 2;
  return [r.top + rand, r.bottom - rand].some((y) => {
    const ziel = document.elementFromPoint(mitte, y);
    return !ziel || (ziel !== flaeche && !flaeche.contains(ziel));
  });
}

/**
 * Zwingt den Browser, die Darstellung einer Fläche von Grund auf neu
 * aufzubauen - und damit auch ihre Trefferfläche. Ein Bild lang ohne Anzeige
 * ist nicht zu sehen; das Videoelement selbst bleibt unangetastet, damit die
 * Wiedergabe nicht stockt.
 */
export function flaecheNeuAufbauen(flaeche: HTMLElement) {
  const vorher = flaeche.style.display;
  flaeche.style.display = "none";
  void flaeche.offsetHeight;
  flaeche.style.display = vorher;
}
