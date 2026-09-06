import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { Player } from "./Player";

it("behält Video und zugängliche Einstellungen schon während des ersten Ladens in derselben Hülle", () => {
  const html = renderToStaticMarkup(<Player videoId="video" titel="Beispielvideo" dauerS={30} />);
  expect(html).toContain('<video');
  expect(html).toContain('aria-label="Beispielvideo"');
  expect(html).toContain("Video wird geöffnet");
  expect(html).toContain('aria-label="Wiedergabeeinstellungen"');
  expect(html).toContain('aria-haspopup="menu"');
  expect(html).not.toContain("autoPlay");
  expect(html).not.toContain('>1080p<');
});

it("verhindert Spulen und Abspielen vor dem Öffnen der Quelle", () => {
  const html = renderToStaticMarkup(<Player videoId="video" dauerS={7200} />);
  expect(html).toMatch(/role="slider" tabindex="-1" aria-label="Position" aria-disabled="true"/);
  expect(html).toMatch(/<button[^>]*class="steuer-knopf"[^>]*disabled=""[^>]*aria-label="Abspielen"/);
  expect(html).toContain('aria-valuemax="7200"');
});

it("macht Untertitel als eigenes geschlossenes Auswahlmenü zugänglich", () => {
  const html = renderToStaticMarkup(<Player videoId="video" untertitel={[{ sprache: "de", automatisch: true }]} />);
  expect(html).toMatch(/aria-label="Untertitel"[^>]+aria-haspopup="menu" aria-expanded="false"/);
  expect(html).toContain('kind="subtitles" srcLang="de" label="de (automatisch)"');
  expect(html).not.toContain('role="menuitemradio"');
});
