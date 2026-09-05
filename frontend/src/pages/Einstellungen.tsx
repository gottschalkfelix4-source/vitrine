import { useEffect, useMemo, useState } from "react";

import { Fehler, Hinweis, Skelettgitter } from "../components/ui";
import { useApi } from "../hooks/useApi";
import type { EinstellungsFeld } from "../lib/api";
import { api } from "../lib/api";
import { AppInstallieren } from "../components/AppInstallieren";
import { CookieAssistent } from "../components/Cookies";
import { HardwarePruefung } from "../components/Hardware";
import { VpnTunnelListe } from "../components/Vpn";

const HERKUNFT_TEXT: Record<string, string> = {
  datenbank: "hier geändert",
  umgebung: "aus der Umgebung",
  standard: "Standard",
};

export function Einstellungenseite() {
  const { daten, laedt, fehler, neuLaden } = useApi(() => api.einstellungen(), []);
  // Nur die vom Nutzer angefassten Felder - so bleibt beim Speichern klar,
  // was er wirklich gesetzt hat, und unberuehrte Felder behalten ihre Herkunft.
  const [entwurf, setEntwurf] = useState<Record<string, unknown>>({});
  const [speichert, setSpeichert] = useState(false);
  const [meldung, setMeldung] = useState<string | null>(null);
  const [speicherFehler, setSpeicherFehler] = useState<string | null>(null);
  const [neustartNoetig, setNeustartNoetig] = useState<string[]>([]);
  const [bereich, setBereich] = useState("Allgemein");

  // Beim Neuladen der Daten den Entwurf verwerfen, sonst zeigt die Seite
  // Werte an, die es serverseitig gar nicht gibt.
  useEffect(() => {
    setEntwurf({});
  }, [daten]);

  const gruppen = useMemo(() => {
    if (!daten) return [];
    return daten.gruppen.map((g) => ({
      name: g,
      felder: daten.felder.filter((f) => f.gruppe === g),
    }));
  }, [daten]);

  const offen = Object.keys(entwurf).length;

  function setzen(feld: EinstellungsFeld, wert: unknown) {
    setEntwurf((alt) => {
      const neu = { ...alt };
      // Zurück auf den Ausgangswert heißt: keine Änderung mehr.
      if (String(wert) === String(feld.wert)) delete neu[feld.name];
      else neu[feld.name] = wert;
      return neu;
    });
    setMeldung(null);
    setSpeicherFehler(null);
  }

  async function speichern() {
    setSpeichert(true);
    setSpeicherFehler(null);
    setMeldung(null);
    try {
      const r = await api.einstellungenSpeichern(entwurf);
      setNeustartNoetig(r.neustart_noetig);
      setMeldung(`${r.geaendert.length} Einstellung${r.geaendert.length === 1 ? "" : "en"} gespeichert.`);
      neuLaden();
    } catch (e) {
      setSpeicherFehler(e instanceof Error ? e.message : String(e));
    } finally {
      setSpeichert(false);
    }
  }

  async function zuruecksetzen(name: string) {
    try {
      await api.einstellungenZuruecksetzen([name]);
      setMeldung(null);
      neuLaden();
    } catch (e) {
      setSpeicherFehler(e instanceof Error ? e.message : String(e));
    }
  }

  if (fehler) return <Fehler text={fehler} erneut={neuLaden} />;
  if (laedt && !daten) return <Skelettgitter anzahl={4} />;
  if (!daten) return null;

  return (
    <div className="verwaltung einstellungen-seite">
      <div className="seiten-kopf">
        <h1>Einstellungen</h1>
        <span className="beiwerk">
          Änderungen wirken sofort für neue Aufträge – laufende bleiben unberührt.
        </span>
      </div>

      <div className="einstellungen-layout">
      <nav className="einstellungen-nav" aria-label="Einstellungsbereiche">
        {["Allgemein", "Cookies", "VPN-Tunnel", "Hardware", ...gruppen.map((g) => g.name)].map((name) => (
          <button key={name} data-aktiv={bereich === name} aria-pressed={bereich === name}
            onClick={() => setBereich(name)}>{name}</button>
        ))}
      </nav>
      <div className="einstellungen-inhalt">
      {/* Ganz oben, weil es die einzige Einstellung ist, die nicht am Server
          haengt, sondern am Geraet, auf dem man gerade schaut. */}
      <div hidden={bereich !== "Allgemein"} className="einst-gruppe"><h2>App auf diesem Gerät</h2><AppInstallieren /></div>

      {neustartNoetig.length > 0 ? (
        <Hinweis art="arbeit">
          <div>
            <strong>Neustart nötig für: {neustartNoetig.join(", ")}</strong>
            <div style={{ color: "var(--text-gedaempft)", marginTop: 4 }}>
              Diese Werte werden nur beim Start gelesen. Der Rest ist bereits aktiv.
            </div>
          </div>
        </Hinweis>
      ) : null}

      {meldung ? (
        <Hinweis>
          <div>{meldung}</div>
        </Hinweis>
      ) : null}

      {speicherFehler ? (
        <Hinweis art="fehler">
          <div>
            <strong>Nicht gespeichert.</strong>
            <div style={{ color: "var(--text-gedaempft)", marginTop: 4 }}>{speicherFehler}</div>
            <div style={{ color: "var(--text-schwach)", marginTop: 4, fontSize: 12.5 }}>
              Es wurde nichts geändert – auch nicht die übrigen Felder.
            </div>
          </div>
        </Hinweis>
      ) : null}

      <div hidden={bereich !== "Cookies"}><CookieAssistent /></div>
      <div hidden={bereich !== "VPN-Tunnel"}><VpnTunnelListe /></div>
      <div hidden={bereich !== "Hardware"}><HardwarePruefung /></div>

      {gruppen.map((g) => (
        <section key={g.name} className="einst-gruppe" hidden={bereich !== g.name}>
          <h2>{g.name}</h2>
          {g.felder.map((f) => (
            <Zeile
              key={f.name}
              feld={f}
              entwurf={entwurf[f.name]}
              geaendert={f.name in entwurf}
              aufAendern={(w) => setzen(f, w)}
              aufZuruecksetzen={() => void zuruecksetzen(f.name)}
            />
          ))}
        </section>
      ))}
      </div>
      </div>

      {/* Die Leiste erscheint erst, wenn es etwas zu speichern gibt - so bleibt
          klar, dass Tippen allein noch nichts verändert. */}
      {offen > 0 ? (
        <div className="speicherleiste">
          <span>
            {offen} Änderung{offen === 1 ? "" : "en"} noch nicht gespeichert
          </span>
          <button className="knopf" onClick={() => setEntwurf({})} disabled={speichert}>
            Verwerfen
          </button>
          <button className="knopf" data-art="stark" onClick={speichern} disabled={speichert}>
            {speichert ? "wird gespeichert …" : "Speichern"}
          </button>
        </div>
      ) : null}
    </div>
  );
}

function Zeile({
  feld,
  entwurf,
  geaendert,
  aufAendern,
  aufZuruecksetzen,
}: {
  feld: EinstellungsFeld;
  entwurf: unknown;
  geaendert: boolean;
  aufAendern: (wert: unknown) => void;
  aufZuruecksetzen: () => void;
}) {
  const wert = geaendert ? entwurf : feld.wert;

  return (
    <div className="einst-zeile" data-geaendert={geaendert}>
      <div className="einst-text">
        <label htmlFor={`f-${feld.name}`}>
          {feld.titel}
          {feld.neustart ? <span className="einst-marke">Neustart</span> : null}
          <span className="einst-herkunft" data-h={feld.herkunft}>
            {HERKUNFT_TEXT[feld.herkunft] ?? feld.herkunft}
          </span>
        </label>
        {feld.beschreibung ? <p>{feld.beschreibung}</p> : null}
        {/* Nur bei "datenbank" sinnvoll: Sonst gibt es nichts zurückzunehmen,
            und der Knopf wäre ein leeres Versprechen. */}
        {feld.herkunft === "datenbank" ? (
          <button className="einst-zuruecksetzen" onClick={aufZuruecksetzen}>
            zurücksetzen
          </button>
        ) : null}
      </div>

      <div className="einst-eingabe">
        {feld.art === "bool" ? (
          <label className="schalter" style={{ padding: 0 }}>
            <input
              id={`f-${feld.name}`}
              type="checkbox"
              checked={Boolean(wert)}
              onChange={(e) => aufAendern(e.target.checked)}
            />
            <span>{wert ? "an" : "aus"}</span>
          </label>
        ) : feld.art === "auswahl" ? (
          <select
            id={`f-${feld.name}`}
            value={String(wert ?? "")}
            onChange={(e) => aufAendern(e.target.value)}
          >
            {feld.auswahl.map((o) => (
              <option key={o} value={o}>
                {o}
              </option>
            ))}
          </select>
        ) : feld.art === "int" || feld.art === "float" ? (
          <>
            <input
              id={`f-${feld.name}`}
              type="number"
              value={String(wert ?? "")}
              min={feld.min ?? undefined}
              max={feld.max ?? undefined}
              step={feld.art === "float" ? 0.5 : 1}
              onChange={(e) => aufAendern(e.target.value === "" ? "" : Number(e.target.value))}
            />
            {feld.einheit ? <span className="einst-einheit">{feld.einheit}</span> : null}
          </>
        ) : (
          <input
            id={`f-${feld.name}`}
            type="text"
            value={String(wert ?? "")}
            placeholder={feld.art === "liste" ? "de,en" : "leer = aus"}
            onChange={(e) => aufAendern(e.target.value)}
          />
        )}
      </div>
    </div>
  );
}
