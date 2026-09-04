import { useRef, useState } from "react";

import { useApi } from "../hooks/useApi";
import { api } from "../lib/api";
import type { VpnProbe, VpnTunnel } from "../lib/api";
import { wartedauer } from "../lib/format";

/**
 * Die WireGuard-Tunnel.
 *
 * Warum das überhaupt existiert: YouTube zählt je IP-Adresse. Als Gast liegt
 * die Grenze bei rund 300 Videos je Stunde, danach kommt „Sign in to confirm
 * you're not a bot" und das Archiv legt eine Zwangspause ein. Bei einem
 * Erstbestand von tausenden Videos ist das der bestimmende Engpass — es wartet
 * dann länger, als es lädt.
 *
 * Vier Tunnel sind vier Adressen und damit grob das vierfache Budget. Fällt
 * einer in die Sperre, wechselt das Archiv auf den nächsten, statt anzuhalten.
 *
 * Die Oberfläche zeigt bewusst die **gemessene** Adresse jedes Tunnels und
 * nicht nur „läuft". Ein Prozess kann laufen, der Port kann offen sein und der
 * Verkehr trotzdem über die Hausleitung gehen — dann steht neben zwei Tunneln
 * dieselbe Adresse, und man hat vier Prozesse für ein Budget.
 */
export function VpnTunnelListe() {
  const { daten, neuLaden } = useApi(() => api.vpn(), []);
  const [fehler, setFehler] = useState<string | null>(null);
  const [proben, setProben] = useState<Record<number, VpnProbe>>({});
  const [direkt, setDirekt] = useState<VpnProbe | null>(null);
  const [laeuft, setLaeuft] = useState<string | null>(null);
  const dateiwahl = useRef<HTMLInputElement>(null);

  async function fuehren(marke: string, tat: () => Promise<unknown>) {
    setLaeuft(marke);
    setFehler(null);
    try {
      await tat();
    } catch (e) {
      setFehler(e instanceof Error ? e.message : String(e));
    } finally {
      setLaeuft(null);
    }
  }

  async function hochladen(dateien: FileList) {
    // Mehrere auf einmal: Wer vier Standorte einrichtet, lädt beim Anbieter
    // vier Dateien herunter und will sie nicht einzeln durchklicken.
    await fuehren("upload", async () => {
      for (const datei of Array.from(dateien)) {
        await api.vpnHochladen(datei);
      }
      neuLaden();
    });
    if (dateiwahl.current) dateiwahl.current.value = "";
  }

  const tunnel = daten?.tunnel ?? [];
  const bereit = daten?.bereit ?? 0;
  const fehltProgramm = daten !== null && daten !== undefined && !daten.wireproxy;
  const zustand = !daten?.aktiv ? "leer" : bereit > 0 ? "gut" : "schlecht";

  return (
    <section className="einst-gruppe">
      <h2>VPN-Tunnel</h2>

      <div className="cookie-zustand" data-zustand={zustand}>
        <strong>
          {!daten?.aktiv
            ? tunnel.length > 0
              ? "Tunnel eingerichtet, aber abgeschaltet"
              : "Kein Tunnel eingerichtet"
            : bereit > 0
              ? `${bereit} von ${tunnel.length} Tunneln bereit`
              : "Eingeschaltet, aber kein Tunnel lässt etwas durch"}
        </strong>
        <p>
          {!daten?.aktiv
            ? tunnel.length > 0
              ? "Solange der Schalter aus ist, wird kein Tunnel gestartet – die Zeilen unten sagen deshalb „nicht gestartet“ und nicht, dass etwas kaputt wäre."
              : "Alles läuft über die eigene Leitung."
            : daten.nur_tunnel
              ? bereit > 0
                ? "Jeder Download nimmt reihum einen freien Tunnel. Läuft einer in die Sperre, übernimmt der nächste."
                : "Es wird nichts geladen: „Nur über Tunnel laden“ ist an, und kein Tunnel ist bereit."
              : "Die eigene Leitung wird mitbenutzt."}
        </p>

        {/*
          Der Schalter steht hier und nicht nur weiter unten in der Liste der
          Einstellungen. Wer gerade drei Konfigurationen hochgeladen hat, ist
          fertig in seinem Kopf - dass es dann noch einen Hauptschalter
          irgendwo anders gibt, liest niemand. Er bleibt zusätzlich unten
          stehen, wo alle anderen Einstellungen sind.
        */}
        <button
          className="knopf"
          data-art={daten?.aktiv ? undefined : "stark"}
          style={{ marginTop: 10 }}
          disabled={laeuft !== null || tunnel.length === 0}
          onClick={() =>
            fuehren("hauptschalter", async () => {
              await api.einstellungenSpeichern({ vpn_aktiv: !daten?.aktiv });
              neuLaden();
            })
          }
        >
          {laeuft === "hauptschalter"
            ? "…"
            : daten?.aktiv
              ? "Tunnel nicht mehr benutzen"
              : "Tunnel jetzt benutzen"}
        </button>
      </div>

      {fehltProgramm ? (
        <div className="cookie-zustand" data-zustand="schlecht">
          <strong>wireproxy fehlt</strong>
          <p>
            Ohne dieses Programm lassen sich keine Tunnel starten. Im mitgelieferten Container
            ist es dabei — außerhalb muss es im Pfad liegen oder <code>YTA_WIREPROXY_PATH</code>{" "}
            gesetzt sein.
          </p>
        </div>
      ) : null}

      {daten && daten.doppelte_adressen.length > 0 ? (
        <div className="cookie-zustand" data-zustand="schlecht">
          <strong>Zwei Tunnel, eine Adresse</strong>
          <p>
            {daten.doppelte_adressen.join(", ")} kommt mehrfach vor. Diese Tunnel teilen sich
            ein Budget, statt es zu verdoppeln — beim Anbieter unterschiedliche Standorte
            wählen.
          </p>
        </div>
      ) : null}

      {fehler ? (
        <div className="cookie-zustand" data-zustand="schlecht">
          <strong>Abgelehnt</strong>
          <p>{fehler}</p>
        </div>
      ) : null}

      {tunnel.length > 0 ? (
        <div className="vpn-liste">
          {tunnel.map((t) => (
            <Zeile
              key={t.id}
              tunnel={t}
              vpnAn={daten?.aktiv ?? false}
              probe={proben[t.id]}
              beschaeftigt={laeuft !== null}
              aufTesten={() =>
                fuehren(`test-${t.id}`, async () => {
                  const ergebnis = await api.vpnTesten(t.id);
                  setProben((alt) => ({ ...alt, [t.id]: ergebnis }));
                  neuLaden();
                })
              }
              aufUmschalten={() =>
                fuehren(`schalt-${t.id}`, async () => {
                  await api.vpnAendern(t.id, { aktiv: !t.aktiv });
                  neuLaden();
                })
              }
              aufEntfernen={() =>
                fuehren(`weg-${t.id}`, async () => {
                  await api.vpnEntfernen(t.id);
                  neuLaden();
                })
              }
            />
          ))}
        </div>
      ) : null}

      {direkt ? (
        <p className="cookie-hinweis">
          Ohne Tunnel: {direkt.erfolg ? direkt.ip : direkt.meldung}. Steht diese Adresse auch
          neben einem Tunnel, geht sein Verkehr nicht durch den Tunnel.
        </p>
      ) : null}

      <div className="cookie-knoepfe">
        <input
          ref={dateiwahl}
          type="file"
          accept=".conf,text/plain"
          multiple
          hidden
          onChange={(e) => {
            if (e.target.files?.length) void hochladen(e.target.files);
          }}
        />
        <button
          className="knopf"
          data-art="stark"
          disabled={laeuft !== null}
          onClick={() => dateiwahl.current?.click()}
        >
          {laeuft === "upload" ? "Wird geprüft…" : "WireGuard-Datei(en) hinzufügen"}
        </button>
        <button
          className="knopf"
          disabled={laeuft !== null}
          onClick={() =>
            fuehren("direkt", async () => setDirekt(await api.vpnTestenDirekt()))
          }
        >
          {laeuft === "direkt" ? "Wird geprüft…" : "Eigene Adresse zeigen"}
        </button>
      </div>

      <details className="cookie-anleitung">
        <summary>Woher bekomme ich die Dateien?</summary>
        <ol>
          <li>
            <strong>Ein VPN-Anbieter mit WireGuard.</strong> Im Kundenbereich lässt sich für
            jeden Standort eine <code>.conf</code> erzeugen. Genau diese Datei kommt hier
            hinein, unverändert.
          </li>
          <li>
            <strong>Verschiedene Standorte wählen.</strong> Vier Konfigurationen desselben
            Servers ergeben viermal dieselbe Adresse und bringen nichts. Berlin, Amsterdam,
            Zürich, Stockholm bringen vier Budgets.
          </li>
          <li>
            Dateien hier hinzufügen, dann oben unter <em>VPN</em> „Tunnel benutzen“
            einschalten und auf „prüfen“ klicken.
          </li>
          <li>
            <strong>Parallele Downloads nachziehen.</strong> Unter <em>Arbeiter</em> lohnt sich
            so viel, wie es Tunnel gibt — mehr teilen sich wieder eine Adresse.
          </li>
        </ol>
        <p className="cookie-hinweis">
          Anmeldung und Tunnel zusammen sind mit Bedacht zu benutzen: Ein Google-Konto, das im
          selben Zeitraum aus vier Ländern zugreift, fällt eher auf als eines, das immer vom
          selben Ort kommt. Entweder Cookies oder Tunnel ist der ruhigere Weg.
        </p>
      </details>
    </section>
  );
}

function Zeile({
  tunnel,
  vpnAn,
  probe,
  beschaeftigt,
  aufTesten,
  aufUmschalten,
  aufEntfernen,
}: {
  tunnel: VpnTunnel;
  /** Hauptschalter. Ist er aus, läuft KEIN Tunnel - dann sagt die Zeile das. */
  vpnAn: boolean;
  probe: VpnProbe | undefined;
  beschaeftigt: boolean;
  aufTesten: () => void;
  aufUmschalten: () => void;
  aufEntfernen: () => void;
}) {
  const gesperrt = tunnel.sperre?.gesperrt ?? false;
  // Die Reihenfolge ist der ganze Witz dieser Zeile, und der erste Zweig war
  // vorher nicht da: Bei ausgeschaltetem Hauptschalter wird gar kein Tunnel
  // gestartet. Er meldete dann „läuft nicht" und „nichts durchgekommen" - was
  // beides stimmt und trotzdem zusammen die falsche Geschichte erzählt. Drei
  // rote Zeilen „kommt nichts durch" sehen nach drei kaputten Konfigurationen
  // aus, während in Wahrheit nur ein Schalter fehlt.
  //
  // Danach gilt: „läuft" und „bereit" sind nicht dasselbe. Der Port ist offen,
  // sobald wireproxy die Datei gelesen hat - ob das Gegenüber antwortet, weiß
  // es da noch nicht. Erst die gemessene Adresse beweist, dass etwas durchkommt.
  const lage = !tunnel.aktiv
    ? "aus"
    : !vpnAn
      ? "wartet"
      : gesperrt
        ? "gesperrt"
        : tunnel.bereit
          ? "bereit"
          : tunnel.laeuft && !tunnel.fehler
            ? "misst"
            : "fehler";

  return (
    <div className="vpn-zeile" data-lage={lage}>
      <div className="vpn-kopf">
        <strong>{tunnel.name}</strong>
        <span className="vpn-marke" data-lage={lage}>
          {lage === "aus"
            ? "abgeschaltet"
            : lage === "wartet"
              ? "nicht gestartet"
              : lage === "gesperrt"
                ? `gesperrt, noch ${wartedauer(tunnel.sperre?.rest_s ?? 0)}`
                : lage === "misst"
                  ? "wird gemessen…"
                  : lage === "bereit"
                    ? tunnel.belegt > 0
                      ? `lädt (${tunnel.belegt})`
                      : "bereit"
                    : "kommt nichts durch"}
        </span>
      </div>

      <div className="vpn-daten">
        {tunnel.endpunkt ? <span>{tunnel.endpunkt}</span> : null}
        {tunnel.exit_ip ? <span>tritt auf als {tunnel.exit_ip}</span> : null}
      </div>

      {/* Der Grund der Sperre gehört hierher und nicht nur ins Log: Sonst
          sieht man vier gesperrte Tunnel und weiß nicht, ob YouTube abweist
          oder der Anbieter dicht ist. */}
      {gesperrt && tunnel.sperre?.grund ? (
        <p className="vpn-meldung">{tunnel.sperre.grund}</p>
      ) : null}
      {tunnel.fehler ? <p className="vpn-meldung">{tunnel.fehler}</p> : null}
      {probe ? (
        <p className="vpn-meldung" data-gut={probe.erfolg}>
          {probe.meldung}
        </p>
      ) : null}

      <div className="vpn-knoepfe">
        <button className="knopf" disabled={beschaeftigt || !tunnel.laeuft} onClick={aufTesten}>
          {/* „prüfen" misst die Adresse - und entscheidet damit zugleich, ob
              der Tunnel wieder in die Rotation darf. */}
          prüfen
        </button>
        <button className="knopf" disabled={beschaeftigt} onClick={aufUmschalten}>
          {tunnel.aktiv ? "abschalten" : "einschalten"}
        </button>
        <button className="knopf" disabled={beschaeftigt} onClick={aufEntfernen}>
          entfernen
        </button>
      </div>
    </div>
  );
}
