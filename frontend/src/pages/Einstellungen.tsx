import { useEffect, useMemo, useRef, useState } from "react";

import { Fehler, Hinweis, Skelettgitter } from "../components/ui";
import { useApi } from "../hooks/useApi";
import type { EinstellungsFeld } from "../lib/api";
import { api } from "../lib/api";
import { AppInstallieren } from "../components/AppInstallieren";
import { CookieAssistent } from "../components/Cookies";
import { HardwarePruefung } from "../components/Hardware";
import { VpnTunnelListe } from "../components/Vpn";
import { PasswortAendern } from "../components/Anmeldung";

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
  const [aktion, setAktion] = useState<"speichern" | "zuruecksetzen" | null>(null);
  const transaktion = useRef(false);
  const speichert = aktion !== null;
  const [meldung, setMeldung] = useState<string | null>(null);
  const [speicherFehler, setSpeicherFehler] = useState<string | null>(null);
  const [neustartNoetig, setNeustartNoetig] = useState<string[]>([]);
  const [bereich, setBereich] = useState("Allgemein");

  // Nur bestätigte Werte entfernen. Das Zurücksetzen eines einzelnen Felds
  // darf ungespeicherte Änderungen an anderen Feldern nicht verwerfen.
  useEffect(() => {
    if (!daten) return;
    setEntwurf((alt) => Object.fromEntries(Object.entries(alt).filter(([name, wert]) => {
      const feld = daten.felder.find((f) => f.name === name);
      return feld && String(wert) !== String(feld.wert);
    })));
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
    if (transaktion.current) return;
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
    if (transaktion.current || offen === 0) return;
    transaktion.current = true;
    setAktion("speichern");
    setSpeicherFehler(null);
    setMeldung(null);
    try {
      const r = await api.einstellungenSpeichern(entwurf);
      setNeustartNoetig(r.neustart_noetig);
      setMeldung(`${r.geaendert.length} Einstellung${r.geaendert.length === 1 ? "" : "en"} gespeichert.`);
      await neuLaden();
      // Der Server normalisiert etwa „de, en“ zu ["de", "en"]. Während
      // dieser Transaktion sind Eingaben gesperrt, daher ist alles bestätigt.
      setEntwurf({});
    } catch (e) {
      setSpeicherFehler(e instanceof Error ? e.message : String(e));
    } finally {
      transaktion.current = false;
      setAktion(null);
    }
  }

  async function zuruecksetzen(name: string) {
    if (transaktion.current) return;
    transaktion.current = true;
    setAktion("zuruecksetzen");
    setSpeicherFehler(null);
    try {
      await api.einstellungenZuruecksetzen([name]);
      setEntwurf((alt) => {
        const neu = { ...alt };
        delete neu[name];
        return neu;
      });
      setMeldung(null);
      await neuLaden();
    } catch (e) {
      setSpeicherFehler(e instanceof Error ? e.message : String(e));
    } finally {
      transaktion.current = false;
      setAktion(null);
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
          Änderungen gelten nach dem Speichern. YouTube-Budgets werden auch vor weiteren Anfragen laufender Aufträge geprüft.
        </span>
      </div>

      <div className="einstellungen-layout">
      <nav className="einstellungen-nav" aria-label="Einstellungsbereiche">
        {["Allgemein", "Zugang", "Cookies", "VPN-Tunnel", "Hardware", ...gruppen.map((g) => g.name)].map((name) => (
          <button key={name} data-aktiv={bereich === name} aria-pressed={bereich === name}
            onClick={() => setBereich(name)}>{name}</button>
        ))}
      </nav>
      <div className="einstellungen-inhalt">
      {/* Ganz oben, weil es die einzige Einstellung ist, die nicht am Server
          haengt, sondern am Geraet, auf dem man gerade schaut. */}
      <div hidden={bereich !== "Allgemein"} className="einst-gruppe"><h2>App auf diesem Gerät</h2><AppInstallieren /></div>
      {bereich === "Zugang" ? <PasswortAendern /> : null}

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
              gesperrt={speichert}
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
            {aktion === "speichern" ? "Wird gespeichert …" : aktion === "zuruecksetzen" ? "Wird zurückgesetzt …" : "Speichern"}
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
  gesperrt,
  aufAendern,
  aufZuruecksetzen,
}: {
  feld: EinstellungsFeld;
  entwurf: unknown;
  geaendert: boolean;
  gesperrt: boolean;
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
          <button className="einst-zuruecksetzen" onClick={aufZuruecksetzen} disabled={gesperrt}>
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
              disabled={gesperrt}
              checked={Boolean(wert)}
              onChange={(e) => aufAendern(e.target.checked)}
            />
            <span>{wert ? "an" : "aus"}</span>
          </label>
        ) : feld.art === "auswahl" ? (
          <select
            id={`f-${feld.name}`}
            disabled={gesperrt}
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
              disabled={gesperrt}
              value={String(wert ?? "")}
              min={feld.min ?? undefined}
              max={feld.max ?? undefined}
              step={feld.art === "float" ? "any" : 1}
              onChange={(e) => aufAendern(e.target.value === "" ? "" : Number(e.target.value))}
            />
            {feld.einheit ? <span className="einst-einheit">{feld.einheit}</span> : null}
          </>
        ) : (
          <input
            id={`f-${feld.name}`}
            type="text"
            disabled={gesperrt}
            value={String(wert ?? "")}
            placeholder={feld.art === "liste" ? "de,en" : "leer = aus"}
            onChange={(e) => aufAendern(e.target.value)}
          />
        )}
      </div>
    </div>
  );
}
