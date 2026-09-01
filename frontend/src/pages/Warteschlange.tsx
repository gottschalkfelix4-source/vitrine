import { Link } from "react-router-dom";

import { Fehler, Leer } from "../components/ui";
import { useApi } from "../hooks/useApi";
import { api } from "../lib/api";
import { AUFTRAG_TEXT, prozent, vorZeit } from "../lib/format";

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

  async function abbrechen(id: number) {
    await api.auftragAbbrechen(id);
    neuLaden();
  }

  async function wiederholen(id: number) {
    await api.auftragWiederholen(id);
    neuLaden();
  }

  if (fehler) return <Fehler text={fehler} erneut={neuLaden} />;

  const laufend = daten?.filter((a) => a.status === "running") ?? [];
  const wartend = daten?.filter((a) => a.status === "pending") ?? [];
  const gescheitert = daten?.filter((a) => a.status === "failed") ?? [];

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
