import { describe, expect, it } from "vitest";

import { bytes, dauer, istHochaufloesend, qualitaet, wartedauer } from "./format";

describe("qualitaet", () => {
  // 16:9-Masse zu einer Qualitaetsstufe.
  const quer = (kurz: number): [number, number] => [Math.round((kurz * 16) / 9), kurz];

  it("benennt die gängigen Stufen", () => {
    expect(qualitaet(...quer(2160))).toBe("4K");
    expect(qualitaet(...quer(4320))).toBe("8K");
    expect(qualitaet(...quer(1440))).toBe("1440p");
    expect(qualitaet(...quer(1080))).toBe("1080p");
    expect(qualitaet(...quer(720))).toBe("720p");
    expect(qualitaet(...quer(360))).toBe("360p");
  });

  it("misst die kürzere Seite, nicht die Höhe", () => {
    // Der Kern der Sache. YouTube nennt das Format 1080x1920 eines senkrechten
    // Videos selbst "1080p". Nach der Höhe wäre es "1920p" - und genau diese
    // Zählweise hat im Betrieb einwandfreie Downloads verworfen.
    expect(qualitaet(1920, 1080)).toBe("1080p"); // quer
    expect(qualitaet(1080, 1920)).toBe("1080p"); // hochkant, dieselbe Stufe
    expect(qualitaet(3840, 2160)).toBe("4K");
    expect(qualitaet(2160, 3840)).toBe("4K");
    expect(qualitaet(720, 1280)).toBe("720p");
  });

  it("hängt nur echte Hochbildraten an", () => {
    // 29,97 ist der Normalfall und sagt nichts.
    expect(qualitaet(1920, 1080, 29.97)).toBe("1080p");
    expect(qualitaet(1920, 1080, 30)).toBe("1080p");
    expect(qualitaet(1920, 1080, 59.94)).toBe("1080p60");
    expect(qualitaet(3840, 2160, 50)).toBe("4K50");
  });

  it("schweigt, solange nichts bekannt ist", () => {
    // Vor dem Herunterladen nennt YouTube beim Auflisten keine Auflösung.
    // Dann ist kein Etikett richtig - auch kein "?".
    expect(qualitaet(null, null)).toBeNull();
    expect(qualitaet(undefined, undefined)).toBeNull();
    expect(qualitaet(0, 0)).toBeNull();
    expect(qualitaet(Number.NaN, Number.NaN)).toBeNull();
  });

  it("kommt mit einer fehlenden Angabe aus", () => {
    expect(qualitaet(null, 1080)).toBe("1080p");
    expect(qualitaet(1920, null)).toBe("1920p");
  });

  it("erfindet keine Zwischenstufen", () => {
    // Ungewöhnliche Masse werden genannt, wie sie sind, statt auf eine
    // Marketingstufe gerundet zu werden.
    expect(qualitaet(2133, 1200)).toBe("1200p");
    expect(qualitaet(608, 1080)).toBe("608p");
  });
});

describe("istHochaufloesend", () => {
  it("hebt erst ab 1440p hervor", () => {
    expect(istHochaufloesend(3840, 2160)).toBe(true);
    expect(istHochaufloesend(2560, 1440)).toBe(true);
    expect(istHochaufloesend(1440, 2560)).toBe(true); // hochkant zählt gleich
    expect(istHochaufloesend(1920, 1080)).toBe(false);
    expect(istHochaufloesend(null, null)).toBe(false);
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

describe("wartedauer", () => {
  it("rundet auf die Einheit, die man ablesen will", () => {
    expect(wartedauer(40)).toBe("40 Sekunden");
    expect(wartedauer(300)).toBe("5 Minuten");
    expect(wartedauer(900)).toBe("15 Minuten");
    expect(wartedauer(3600)).toBe("1 Stunde");
    expect(wartedauer(3900)).toBe("1 Stunde 5 Minuten");
  });

  it("setzt Einzahl und Mehrzahl richtig", () => {
    expect(wartedauer(60)).toBe("1 Minute");
    expect(wartedauer(120)).toBe("2 Minuten");
    expect(wartedauer(7200)).toBe("2 Stunden");
  });

  it("wechselt bei langen Spannen auf Tage", () => {
    // Dieselbe Funktion beziffert eine Drosselpause von Minuten UND die
    // Restlaufzeit einer Cookie-Datei von Wochen. Ohne die Tagesstufe stand
    // in der Oberfläche "noch 1080 Stunden".
    expect(wartedauer(45 * 86400)).toBe("45 Tage");
    expect(wartedauer(86400 * 3)).toBe("3 Tage");
    // Knapp darunter bleiben Stunden die genauere Auskunft.
    expect(wartedauer(30 * 3600)).toBe("30 Stunden");
    expect(wartedauer(47 * 3600)).toBe("47 Stunden");
    expect(wartedauer(48 * 3600)).toBe("2 Tage");
  });

  it("sagt bei abgelaufener Frist nicht \"0 Sekunden\"", () => {
    // Die Anzeige läuft im Sekundentakt und erreicht die Null zwangsläufig.
    // "gleich" ist dort die einzige Aussage, die nicht falsch wirkt.
    expect(wartedauer(0)).toBe("gleich");
    expect(wartedauer(-5)).toBe("gleich");
    expect(wartedauer(null)).toBe("gleich");
  });
});
