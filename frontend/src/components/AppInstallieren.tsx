import { useEffect, useState } from "react";

import {
  alsAppGestartet,
  beiInstallierbar,
  beiPwaZustand,
  installieren,
  installierenMoeglich,
  pwaZustand,
  type PwaZustand,
} from "../pwa";

/**
 * Zeigt, ob sich das Archiv als App aufs Gerät legen lässt - und wenn nicht,
 * warum.
 *
 * Das ist der Grund, warum es diesen Abschnitt überhaupt gibt: Ein Service
 * Worker läuft nur in einem sicheren Kontext, also über HTTPS oder auf
 * `localhost`. Genau der übliche Zugriff im Heimnetz - `http://192.168.…` -
 * erfüllt das nicht. Ohne Hinweis fehlt dann einfach der Menüpunkt "Zum
 * Startbildschirm hinzufügen", ohne jede Meldung, und man sucht den Fehler bei
 * sich.
 */
export function AppInstallieren() {
  const [zustand, setZustand] = useState<PwaZustand>(pwaZustand);
  const [moeglich, setMoeglich] = useState(installierenMoeglich);
  const [laeuftAlsApp] = useState(alsAppGestartet);

  useEffect(() => beiPwaZustand(setZustand), []);
  useEffect(() => beiInstallierbar(setMoeglich), []);

  if (laeuftAlsApp) {
    return (
      <div className="hinweis">
        <div>
          <strong>Läuft als App.</strong>
          <div style={{ color: "var(--text-gedaempft)", marginTop: 4 }}>
            Vorschaubilder werden auf dem Gerät behalten, die Oberfläche startet
            ohne Browserleiste.
          </div>
        </div>
      </div>
    );
  }

  if (zustand.art === "unsicher") {
    return (
      <div className="hinweis" data-art="arbeit">
        <div>
          <strong>Als App installieren geht hier nicht.</strong>
          <div style={{ color: "var(--text-gedaempft)", marginTop: 6 }}>
            Das Archiv ist über <code>{zustand.herkunft}</code> geöffnet. Browser
            erlauben die Installation und den Bildspeicher nur über HTTPS – oder
            über <code>localhost</code>, was auf dem Telefon nicht hilft. Im
            Browser funktioniert weiterhin alles, nur eben ohne eigenes Symbol
            auf dem Startbildschirm.
          </div>
          <div style={{ color: "var(--text-gedaempft)", marginTop: 10 }}>
            Drei übliche Wege zu HTTPS im eigenen Netz:
            <ul style={{ margin: "6px 0 0", paddingLeft: 20, lineHeight: 1.7 }}>
              <li>
                <strong>Tailscale</strong> – vergibt selbst ein gültiges
                Zertifikat, funktioniert auch von unterwegs.
              </li>
              <li>
                <strong>Reverse Proxy</strong> mit eigener Domain, etwa Nginx
                Proxy Manager oder Caddy mit Let’s Encrypt.
              </li>
              <li>
                <strong>Cloudflare Tunnel</strong> – ohne offenen Port am
                Router.
              </li>
            </ul>
          </div>
        </div>
      </div>
    );
  }

  if (moeglich) {
    return (
      <div className="hinweis">
        <div style={{ display: "flex", alignItems: "center", gap: 16, flexWrap: "wrap" }}>
          <div style={{ flex: 1, minWidth: 220 }}>
            <strong>Als App auf dem Gerät ablegen</strong>
            <div style={{ color: "var(--text-gedaempft)", marginTop: 4 }}>
              Eigenes Symbol, Start ohne Browserleiste, Vorschaubilder bleiben
              gespeichert.
            </div>
          </div>
          <button className="knopf" data-art="stark" onClick={() => void installieren()}>
            Installieren
          </button>
        </div>
      </div>
    );
  }

  if (zustand.art === "nicht_unterstuetzt") {
    return (
      <div className="hinweis">
        <div>
          <strong>Dieser Browser kann keine Apps ablegen.</strong>
          <div style={{ color: "var(--text-gedaempft)", marginTop: 4 }}>
            Das Archiv funktioniert trotzdem vollständig.
          </div>
        </div>
      </div>
    );
  }

  if (zustand.art === "fehler") {
    return (
      <div className="hinweis" data-art="fehler">
        <div>
          <strong>Der Hintergrunddienst ließ sich nicht einrichten.</strong>
          <div style={{ color: "var(--text-gedaempft)", marginTop: 4 }}>{zustand.meldung}</div>
        </div>
      </div>
    );
  }

  // Aktiv, aber Chrome hat noch kein Angebot geschickt. Das ist der Normalfall
  // auf iOS - Safari bietet die Installation gar nicht selbst an, dort geht es
  // nur von Hand.
  return (
    <div className="hinweis">
      <div>
        <strong>Auf dem Startbildschirm ablegen</strong>
        <div style={{ color: "var(--text-gedaempft)", marginTop: 4 }}>
          Auf dem iPhone über <em>Teilen</em> → <em>Zum Home-Bildschirm</em>. Auf
          Android bietet Chrome es im Menü unter <em>App installieren</em> an –
          manchmal erst nach dem zweiten Besuch.
        </div>
      </div>
    </div>
  );
}
