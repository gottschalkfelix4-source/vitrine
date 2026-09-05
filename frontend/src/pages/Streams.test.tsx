import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { expect, it } from "vitest";
import { StreamListe } from "./Streams";

it("zeigt nur tatsächliche Verbindungen und einen ehrlichen Leerzustand", () => {
  const leer = renderToStaticMarkup(<MemoryRouter><StreamListe daten={{ streams: [], limits: { sessions: 64, transcodes: 2 } }} /></MemoryRouter>);
  expect(leer).toContain("Gerade schaut niemand");
  expect(leer).not.toContain("<table");
});

it("unterscheidet pausierte Direktwiedergabe und aktive Transkodierung ohne Roh-HTML", () => {
  const stream = { id: "1", video_id: "video1", video_title: "<script>kein Code</script>", channel_title: "Kanal",
    client_address: "192.168.1.2", client_name: "Firefox", position_s: 24, started_at: "2026-09-05T12:00:00Z",
    last_seen_at: "2026-09-05T12:02:00Z", segments_ready: 2 };
  const html = renderToStaticMarkup(<MemoryRouter><StreamListe daten={{ streams: [
    { ...stream, mode: "direct", state: "paused", transcoding: false },
    { ...stream, id: "2", mode: "transcode", state: "playing", transcoding: true },
  ], limits: { sessions: 64, transcodes: 2 } }} /></MemoryRouter>);
  expect(html).toContain("Pausiert");
  expect(html).toContain("Live-Transkodierung");
  expect(html).toContain("192.168.1.2");
  expect(html).toContain("1 aktive Umwandlung");
  expect(html).not.toContain("<script>");
});
