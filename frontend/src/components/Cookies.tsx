import { useRef, useState } from "react";

import { useApi } from "../hooks/useApi";
import { api } from "../lib/api";
import type { CookieProbe } from "../lib/api";
import { datum, wartedauer } from "../lib/format";

/**
 * Der Cookie-Assistent.
 *
 * Warum das eine eigene Oberfläche verdient und nicht ein Textfeld mit einem
 * Pfad: YouTube weist Gastzugriffe ab einer gewissen Rate ab („Sign in to
 * confirm you're not a bot"). Dagegen hilft, angemeldet aufzutreten - und
 * angemeldet heißt bei yt-dlp ausschließlich Cookies. Konto und Passwort gehen
 * nicht, OAuth auch nicht; yt-dlp lehnt beides ausdrücklich ab.
 *
 * Damit hängt alles an einer Textdatei, die auf drei Arten kaputt sein kann,
 * ohne dass man es ihr ansieht: falsches Format, abgemeldet exportiert, oder
 * inzwischen rotiert. Alle drei zeigen sich sonst erst Stunden später als rote
 * Zeile in der Warteschlange. Hier zeigen sie sich sofort.
 */
export function CookieAssistent() {
  const { daten, neuLaden } = useApi(() => api.cookies(), []);
  const [fehler, setFehler] = useState<string | null>(null);
  const [probe, setProbe] = useState<CookieProbe | null>(null);
  const [laeuft, setLaeuft] = useState<"upload" | "test" | "loeschen" | null>(null);
  const dateiwahl = useRef<HTMLInputElement>(null);

  async function hochladen(datei: File) {
    setLaeuft("upload");
    setFehler(null);
    setProbe(null);
    try {
      await api.cookiesHochladen(datei);
      neuLaden();
    } catch (e) {
      setFehler(e instanceof Error ? e.message : String(e));
    } finally {
      setLaeuft(null);
      // Zurücksetzen, damit dieselbe Datei nach einer Korrektur erneut
      // gewählt werden kann - sonst feuert change nicht noch einmal.
      if (dateiwahl.current) dateiwahl.current.value = "";
    }
  }

  async function testen() {
    setLaeuft("test");
    setFehler(null);
    try {
      setProbe(await api.cookiesTesten());
    } catch (e) {
      setFehler(e instanceof Error ? e.message : String(e));
    } finally {
      setLaeuft(null);
    }
  }

  async function entfernen() {
    setLaeuft("loeschen");
    setProbe(null);
    try {
      await api.cookiesEntfernen();
      neuLaden();
    } catch (e) {
      setFehler(e instanceof Error ? e.message : String(e));
    } finally {
      setLaeuft(null);
    }
  }

  const zustand = daten?.brauchbar ? "gut" : daten?.vorhanden ? "schlecht" : "leer";

  return (
    <section className="einst-gruppe">
      <h2>YouTube-Anmeldung</h2>

      <div className="cookie-zustand" data-zustand={zustand}>
        <strong>
          {zustand === "gut"
            ? "Angemeldet"
            : zustand === "schlecht"
              ? "Datei hinterlegt, aber unbrauchbar"
              : "Nicht angemeldet"}
        </strong>
        <p>{daten?.meldung ?? "…"}</p>

        {daten?.brauchbar && daten.laeuft_ab ? (
          <p className={daten.bald_abgelaufen ? "cookie-warnung" : undefined}>
            Gültig bis {datum(daten.laeuft_ab)}
            {daten.rest_s != null ? ` – noch ${wartedauer(daten.rest_s)}` : ""}.
            {daten.bald_abgelaufen
              ? " Bald neu exportieren, sonst steht das Archiv eines Nachts still."
              : ""}
          </p>
        ) : null}

        {daten?.eigener_pfad ? (
          <p className="cookie-hinweis">
            Es gilt der Pfad aus <code>YTA_YTDLP_COOKIES_FILE</code>. Ein Upload hier bliebe
            wirkungslos, solange die Variable gesetzt ist.
          </p>
        ) : null}
      </div>

      {fehler ? (
        <div className="cookie-zustand" data-zustand="schlecht">
          <strong>Datei abgelehnt</strong>
          <p>{fehler}</p>
          <p className="cookie-hinweis">
            Die bisherige Datei ist unverändert – es wurde nichts überschrieben.
          </p>
        </div>
      ) : null}

      {probe ? (
        <div className="cookie-zustand" data-zustand={probe.erfolg ? "gut" : "schlecht"}>
          <strong>{probe.erfolg ? "Probelauf erfolgreich" : "Probelauf fehlgeschlagen"}</strong>
          <p>{probe.meldung}</p>
          {probe.titel ? <p className="cookie-hinweis">Getestet an: {probe.titel}</p> : null}
        </div>
      ) : null}

      <div className="cookie-knoepfe">
        <input
          ref={dateiwahl}
          type="file"
          accept=".txt,text/plain"
          hidden
          onChange={(e) => {
            const datei = e.target.files?.[0];
            if (datei) void hochladen(datei);
          }}
        />
        <button
          className="knopf"
          data-art="stark"
          disabled={laeuft !== null}
          onClick={() => dateiwahl.current?.click()}
        >
          {laeuft === "upload"
            ? "Wird geprüft…"
            : daten?.vorhanden
              ? "Andere Datei wählen"
              : "cookies.txt wählen"}
        </button>
        <button className="knopf" disabled={laeuft !== null} onClick={() => void testen()}>
          {laeuft === "test" ? "Wird getestet…" : "Verbindung testen"}
        </button>
        {daten?.vorhanden && !daten.eigener_pfad ? (
          <button className="knopf" disabled={laeuft !== null} onClick={() => void entfernen()}>
            Entfernen
          </button>
        ) : null}
      </div>

      <details className="cookie-anleitung">
        <summary>Wie komme ich an die Datei?</summary>
        <ol>
          <li>
            <strong>Ein Wegwerf-Konto anlegen.</strong> Nicht dein eigenes: Die Datei ist ein
            Sitzungsschlüssel, und Google kann ein Konto für automatisierte Zugriffe sperren.
          </li>
          <li>
            Im Browser eine Erweiterung installieren, die Cookies im <strong>Netscape</strong>
            -Format exportiert (in der Auswahl oft „cookies.txt"). JSON funktioniert nicht.
          </li>
          <li>
            <strong>Ein privates Fenster öffnen</strong>, dort bei YouTube anmelden, exportieren –
            und das Fenster schließen, <em>ohne</em> sich abzumelden. Das ist der entscheidende
            Schritt: Bewegt man sich im selben Fenster weiter, tauscht YouTube die Schlüssel aus
            und die exportierte Datei ist tot.
          </li>
          <li>Datei hier hochladen und auf „Verbindung testen" klicken.</li>
        </ol>
        <p className="cookie-hinweis">
          Cookies heben die Grenze nicht auf, sie vergrößern nur das Budget. Bei einem
          Erstbestand von tausenden Videos wird YouTube weiter gelegentlich abweisen – das
          Archiv legt dann von selbst eine Pause ein.
        </p>
      </details>
    </section>
  );
}
