/** Dieselben Unicode-Regeln wie bei der serverseitigen Passwortvergabe. */
export function neuesPasswortFehler(passwort: string): string | null {
  const laenge = [...passwort].length;
  if (laenge < 8 || laenge > 256) return "Das Passwort muss zwischen 8 und 256 Zeichen lang sein.";
  if (!/\p{Lu}/u.test(passwort)) return "Das Passwort muss mindestens einen Großbuchstaben enthalten.";
  if (!/[\p{P}\p{S}]/u.test(passwort)) return "Das Passwort muss mindestens ein Sonderzeichen enthalten.";
  return null;
}
