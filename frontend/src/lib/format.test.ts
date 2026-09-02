import { describe, expect, it } from "vitest";

import { bytes, dauer, istHochaufloesend, qualitaet } from "./format";

describe("qualitaet", () => {
  it("benennt die gängigen Stufen", () => {
    expect(qualitaet(2160)).toBe("4K");
    expect(qualitaet(4320)).toBe("8K");
    expect(qualitaet(1440)).toBe("1440p");
    expect(qualitaet(1080)).toBe("1080p");
    expect(qualitaet(720)).toBe("720p");
    expect(qualitaet(360)).toBe("360p");
  });

  it("misst an der Höhe, nicht an der kürzeren Seite", () => {
    // Ein Short mit 608x1080. Nach der kurzen Seite wäre das "608p" - das
    // widerspräche der eigenen Einstellung: archive_min_height steht auf 1080,
    // der Formatwähler hat height>=1080 angefordert und genau das bekommen.
    // Ein Video, das die Untergrenze erfüllt hat, darf hier nicht darunter
    // erscheinen.
    expect(qualitaet(1080)).toBe("1080p");
  });

  it("hängt nur echte Hochbildraten an", () => {
    // 29,97 ist der Normalfall und sagt nichts.
    expect(qualitaet(1080, 29.97)).toBe("1080p");
    expect(qualitaet(1080, 30)).toBe("1080p");
    expect(qualitaet(1080, 59.94)).toBe("1080p60");
    expect(qualitaet(2160, 50)).toBe("4K50");
  });

  it("schweigt, solange nichts bekannt ist", () => {
    // Vor dem Herunterladen nennt YouTube beim Auflisten keine Auflösung.
    // Dann ist kein Etikett richtig - auch kein "?".
    expect(qualitaet(null)).toBeNull();
    expect(qualitaet(undefined)).toBeNull();
    expect(qualitaet(0)).toBeNull();
    expect(qualitaet(Number.NaN)).toBeNull();
  });

  it("erfindet keine Zwischenstufen", () => {
    // Ungewöhnliche Höhen werden genannt, wie sie sind, statt auf eine
    // Marketingstufe gerundet zu werden.
    expect(qualitaet(1200)).toBe("1200p");
    expect(qualitaet(546)).toBe("546p");
  });
});

describe("istHochaufloesend", () => {
  it("hebt erst ab 1440p hervor", () => {
    expect(istHochaufloesend(2160)).toBe(true);
    expect(istHochaufloesend(1440)).toBe(true);
    expect(istHochaufloesend(1080)).toBe(false);
    expect(istHochaufloesend(null)).toBe(false);
  });
});

describe("bytes", () => {
  it("rechnet in 1024er-Schritten, wie Dateimanager und NAS", () => {
    expect(bytes(0)).toBe("0 B");
    expect(bytes(1024)).toBe("1,0 KB");
    expect(bytes(1024 ** 3)).toBe("1,0 GB");
    expect(bytes(null)).toBe("–");
  });

  it("verkraftet negative Werte", () => {
    // Kommt vor: Ein Bündel kann größer sein als die Quelldatei, solange
    // nichts verkleinert wurde.
    expect(bytes(-201728)).toBe("-197,0 KB");
  });
});

describe("dauer", () => {
  it("zeigt Stunden nur, wenn es welche gibt", () => {
    expect(dauer(41)).toBe("0:41");
    expect(dauer(247)).toBe("4:07");
    expect(dauer(3753)).toBe("1:02:33");
  });

  it("liefert nichts bei unbrauchbaren Werten", () => {
    expect(dauer(null)).toBe("");
    expect(dauer(-5)).toBe("");
  });
});
