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
