import { describe, expect, it } from "vitest";
import { neuesPasswortFehler } from "./passwort";

describe("Neue Passwörter", () => {
  it.each(["Abcdefg!", "ABCDEFG!", "Äbcdefg!", "Δabcdef?", "Aabcdef—", "A123456😀", "𝐀bcdefg!"])(
    "erlaubt acht Zeichen mit Unicode-Großbuchstabe und Sonderzeichen: %s", (passwort) => {
      expect([...passwort]).toHaveLength(8);
      expect(neuesPasswortFehler(passwort)).toBeNull();
    },
  );

  it.each(["Abcdef!", "A12345😀"])("lehnt sieben Codepoints auch mit mehr UTF-16-Einheiten ab: %s", (passwort) => {
    expect([...passwort]).toHaveLength(7);
    expect(neuesPasswortFehler(passwort)).toContain("8 und 256");
  });

  it.each(["abcdefg!", "ǅbcdefg!"])("verlangt wirklich Unicode Lu: %s", (passwort) => {
    expect(neuesPasswortFehler(passwort)).toContain("Großbuchstaben");
  });

  it.each(["ABCDEFGH", "ABCDEF  ", "ABCDEF\t\n"])("zählt Buchstaben oder Leerraum nicht als Sonderzeichen: %s", (passwort) => {
    expect(neuesPasswortFehler(passwort)).toContain("Sonderzeichen");
  });

  it("begrenzt auf 256 Unicodecodepoints", () => {
    expect(neuesPasswortFehler("A" + "😀".repeat(255))).toBeNull();
    expect(neuesPasswortFehler("A" + "😀".repeat(256))).toContain("8 und 256");
  });
});
