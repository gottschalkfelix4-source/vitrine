import { Link } from "react-router-dom";

import { Fehler, Hinweis, Leer } from "../components/ui";
import { useApi } from "../hooks/useApi";
import { api } from "../lib/api";
import { AUFTRAG_TEXT, prozent, vorZeit, wartedauer } from "../lib/format";

/** Laufende Auftraege aendern sich staendig - hier lohnt haeufiges Auffrischen. */
const INTERVALL = 3000;

const STATUS_TEXT: Record<string, string> = {
  pending: "wartet",
  running: "läuft",
  failed: "fehlgeschlagen",
  done: "erledigt",
  cancelled: "abgebrochen",
};

export function Warteschlangeseite() {
  const { daten, laedt, fehler, neuLaden } = useApi(() => api.auftraege(), [], INTERVALL);
  const { daten: aktiv } = useApi(() => api.aktiveAuftraege(), [], INTERVALL);

  async function abbrechen(id: number) {
    await api.auftragAbbrechen(id);
    neuLaden();
  }

  async function wiederholen(id: number) {
    await api.auftragWiederholen(id);
    neuLaden();
  }

  async function alleWiederholen() {
    await api.alleGescheitertenWiederholen();
    neuLaden();
  }

  if (fehler) return <Fehler text={fehler} erneut={neuLaden} />;

  const laufend = daten?.filter((a) => a.status === "running") ?? [];
  const wartend = daten?.filter((a) => a.status === "pending") ?? [];
  const gescheitert = daten?.filter((a) => a.status === "failed") ?? [];
  const drosselung = aktiv?.drosselung;

  return (
    <>
      <div className="seiten-kopf">
        <h1>Warteschlange</h1>
        <span className="beiwerk">
          {laufend.length} laufend · {wartend.length} wartend
          {gescheitert.length ? ` · ${gescheitert.length} fehlgeschlagen` : ""}
        </span>
      </div>

      {/*
        Erklärung statt roter Liste. "Sign in to confirm you're not a bot" ist
        keine Auskunft über das Video, sondern über unsere IP-Adresse - und die
        naheliegende Reaktion (alles sofort nochmal) ist genau die falsche: Sie
        verlängert die Sperre. Deshalb steht hier, was passiert und wann es von
        selbst weitergeht.
      */}
      {drosselung?.pausiert ? (
        <Hinweis art="arbeit">
          <strong>YouTube weist gerade ab – Downloads pausieren.</strong>
          <div style={{ color: "var(--text-gedaempft)", marginTop: 4 }}>
            Es geht in {wartedauer(drosselung.rest_s)} von selbst weiter; nichts geht verloren.
            Die Sperre gilt der IP-Adresse, nicht den Videos. Tritt sie oft auf, helfen weniger
            parallele Downloads, eine Pause zwischen den Anfragen oder eine Cookie-Datei aus
            einem Wegwerf-Konto.
          </div>
        </Hinweis>
      ) : null}

      {gescheitert.length > 1 ? (
        <Hinweis art="fehler">
          <strong>
            {gescheitert.length} Aufträge sind fehlgeschlagen.
          </strong>
          <div style={{ color: "var(--text-gedaempft)", marginTop: 4 }}>
            Tragen sie alle denselben Fehler, lag es meist nicht an den Videos.
          </div>
          <button className="knopf" style={{ marginTop: 12 }} onClick={() => void alleWiederholen()}>
            Alle {gescheitert.length} wiederholen
          </button>
        </Hinweis>
      ) : null}

      {/*
        Die Trennung nach Art ist hier keine Kosmetik: Archivierung und
        Verkleinerung laufen in getrennten Straengen. Eine tagelange
        Verkleinerung blockiert also weder einen Download noch eine Wiedergabe -
        das soll man an der Anzeige auch ablesen koennen.
      */}
      {daten && daten.length === 0 && !laedt ? (
        <Leer
          zeichen="✓"
          titel="Nichts zu tun"
          text="Alle Aufträge sind abgearbeitet. Neue Videos werden beim nächsten Kanalabgleich gefunden."
          kinder={
            <Link className="knopf" to="/kanaele">
              Zu den Kanälen
            </Link>
          }
        />
      ) : (
        <table className="tabelle">
          <thead>
            <tr>
              <th>Art</th>
              <th>Gegenstand</th>
              <th>Zustand</th>
              <th style={{ width: 180 }}>Fortschritt</th>
              <th style={{ width: 120 }}>Angelegt</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {(daten ?? []).map((a) => (
              <tr key={a.id}>
                <td>{AUFTRAG_TEXT[a.art] ?? a.art}</td>
                <td style={{ maxWidth: 320, overflow: "hidden", textOverflow: "ellipsis" }}>
                  {a.ziel && a.art.startsWith("video") ? (
                    <Link to={`/video/${a.ziel}`}>{a.titel ?? a.ziel}</Link>
                  ) : a.ziel && a.art === "channel_sync" ? (
                    <Link to={`/kanal/${a.ziel}`}>{a.titel ?? a.ziel}</Link>
                  ) : (
                    (a.titel ?? a.ziel ?? "–")
                  )}
                  {a.fehler ? (
                    <div style={{ color: "var(--zu-fehler)", fontSize: 12, marginTop: 3 }}>
                      {a.fehler}
                    </div>
                  ) : a.meldung ? (
                    <div style={{ color: "var(--text-schwach)", fontSize: 12, marginTop: 3 }}>
                      {a.meldung}
                    </div>
                  ) : null}
                </td>
                <td>
                  <span className="marke-zustand" data-zustand={a.status === "failed" ? "failed" : a.status === "running" ? "encoding" : "queued"}>
                    {STATUS_TEXT[a.status] ?? a.status}
                  </span>
                </td>
                <td>
                  {a.status === "running" ? (
                    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                      <div className="balken">
                        <span style={{ width: `${Math.max(3, a.fortschritt * 100)}%` }} />
                      </div>
                      <span className="zahl" style={{ fontSize: 12, color: "var(--text-gedaempft)" }}>
                        {prozent(a.fortschritt)}
                      </span>
                    </div>
                  ) : (
                    <span style={{ color: "var(--text-schwach)" }}>–</span>
                  )}
                </td>
                <td className="zahl" style={{ color: "var(--text-gedaempft)", fontSize: 12.5 }}>
                  {vorZeit(a.erstellt)}
                </td>
                <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                  {a.status === "failed" ? (
                    <button className="knopf" onClick={() => void wiederholen(a.id)}>
                      Nochmal
                    </button>
                  ) : a.status === "pending" || a.status === "running" ? (
                    <button className="knopf" onClick={() => void abbrechen(a.id)}>
                      Abbrechen
                    </button>
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}
