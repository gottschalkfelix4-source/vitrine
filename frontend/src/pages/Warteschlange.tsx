import { Link } from "react-router-dom";
import { useEffect, useState } from "react";

import { Fehler, Hinweis, Leer } from "../components/ui";
import { useApi } from "../hooks/useApi";
import { api } from "../lib/api";
import { AUFTRAG_TEXT, prozent, vorZeit, wartedauer } from "../lib/format";
import { Icon } from "../components/Icons";
import { DownloadPause } from "../components/DownloadPause";
import { useAdmin } from "../components/Anmeldung";
import type { Anfragelimit, WarteschlangenPause } from "../lib/api";

/** Laufende Auftraege aendern sich staendig - hier lohnt haeufiges Auffrischen. */
const INTERVALL = 3000;

const STATUS_TEXT: Record<string, string> = {
  pending: "wartet",
  running: "läuft",
  failed: "fehlgeschlagen",
  done: "erledigt",
  cancelled: "abgebrochen",
};

function YouTubeSchutz({ zustand, weiterePause }: { zustand: Anfragelimit; weiterePause: boolean }) {
  const zahl = (wert: number | null) => wert === null ? "—" : wert.toLocaleString("de-DE");
  return (
    <section className="youtube-schutz" aria-labelledby="youtube-schutz-titel">
      <h2 id="youtube-schutz-titel">YouTube-Schutz</h2>
      {zustand.pausiert ? <p className="youtube-schutz-pause" role="status">
        <strong>{zustand.bis ? "Vorsorgliche Wartezeit · noch " : "Schutzpause · nächste Prüfung in "}{wartedauer(zustand.rest_s)}</strong>
        {zustand.grund ? <>. {zustand.grund}</> : null}
        {weiterePause ? " Eine weitere Pause bleibt zusätzlich wirksam." : " Danach wird die Freigabe erneut geprüft."}
      </p> : null}
      {zustand.reduziert ? <p>Die Budgets sind vorübergehend reduziert. Die Tabelle zeigt die aktuell wirksamen Grenzen.</p> : null}
      {zustand.anfragen_stunde === null ? <p>Die Zähler sind gerade nicht verfügbar. Downloads warten, bis der Schutz seinen Zustand wieder speichern kann.</p> : null}
      <table className="youtube-schutz-budgets">
        <caption>Verbraucht / Budget · gemeinsam für alle Tunnel und Worker dieser Instanz</caption>
        <thead><tr><th scope="col">Zähler</th><th scope="col">Letzte 60 Minuten</th><th scope="col">Letzte 24 Stunden</th></tr></thead>
        <tbody>
          <tr><th scope="row">Webseiten- und Player-Anfragen</th>
            <td>{zahl(zustand.anfragen_stunde)} / {zahl(zustand.limit_anfragen_stunde)}</td>
            <td>{zahl(zustand.anfragen_tag)} / {zahl(zustand.limit_anfragen_tag)}</td></tr>
          <tr><th scope="row">Videostarts</th>
            <td>{zahl(zustand.videos_stunde)} / {zahl(zustand.limit_videos_stunde)}</td>
            <td>{zahl(zustand.videos_tag)} / {zahl(zustand.limit_videos_tag)}</td></tr>
        </tbody>
      </table>
      <p>Die Zeitfenster sind gleitend. Videostarts zählen Versuche einschließlich Wiederholungen.</p>
      <p>Medienanfragen in den letzten 24 Stunden: {zahl(zustand.medienanfragen_tag)}. Mediensegmente werden separat gezählt.</p>
      <p>Die Standards sind vorsichtige eigene Startwerte, keine garantierten YouTube-Grenzen. <Link to="/einstellungen">Budgets in den Einstellungen ändern</Link></p>
    </section>
  );
}

export function Warteschlangeseite() {
  const admin = useAdmin();
  const { daten, laedt, fehler, neuLaden } = useApi(() => api.auftraege(), [], INTERVALL);
  const { daten: aktiv, fehler: aktivFehler, neuLaden: aktivNeuLaden } = useApi(() => api.aktiveAuftraege(), [], INTERVALL);
  const [pause, setPause] = useState<WarteschlangenPause>();
  useEffect(() => { setPause(aktiv?.pause); }, [aktiv]);
  const [filter, setFilter] = useState("alle");
  const [aktionsFehler, setAktionsFehler] = useState<string | null>(null);
  const [beschaeftigt, setBeschaeftigt] = useState(false);

  async function ausfuehren(aktion: () => Promise<unknown>) {
    if (!admin) return;
    setBeschaeftigt(true);
    setAktionsFehler(null);
    try { await aktion(); neuLaden(); }
    catch (e) { setAktionsFehler(e instanceof Error ? e.message : String(e)); }
    finally { setBeschaeftigt(false); }
  }

  async function abbrechen(id: number) {
    await ausfuehren(() => api.auftragAbbrechen(id));
  }

  async function wiederholen(id: number) {
    await ausfuehren(() => api.auftragWiederholen(id));
  }

  async function alleWiederholen() {
    await ausfuehren(() => api.alleGescheitertenWiederholen());
  }

  if (fehler) return <Fehler text={fehler} erneut={neuLaden} />;

  const laufend = daten?.filter((a) => a.status === "running") ?? [];
  const wartend = daten?.filter((a) => a.status === "pending") ?? [];
  const gescheitert = daten?.filter((a) => a.status === "failed") ?? [];
  const drosselung = aktiv?.drosselung;
  const wartendeArten = Object.entries(aktiv?.nach_art ?? {}).sort((a, b) => b[1] - a[1]);
  const sichtbar = (daten ?? []).filter((a) => filter === "alle" || (filter === "aktiv" ? ["pending", "running"].includes(a.status) : a.status === filter));

  return (
    <div className="verwaltung warteschlange-seite">
      <div className="seiten-kopf">
        <h1>Warteschlange</h1>
        <span className="beiwerk">
          {laufend.length} laufend · {aktiv?.wartend ?? wartend.length} wartend
          {gescheitert.length ? ` · ${gescheitert.length} fehlgeschlagen` : ""}
        </span>
      </div>
      {admin ? <DownloadPause pause={pause} aufAenderung={(zustand) => {
        setPause(zustand);
        aktivNeuLaden();
      }} /> : aktiv?.pause?.aktiv ? <Hinweis>Downloads sind pausiert.</Hinweis> : null}
      {aktivFehler ? <Fehler text={`Der Pausenstatus konnte nicht aktualisiert werden: ${aktivFehler}`} erneut={aktivNeuLaden} /> : null}
      {aktiv?.anfragelimit ? <YouTubeSchutz zustand={aktiv.anfragelimit} weiterePause={Boolean(pause?.aktiv || drosselung?.pausiert)} /> : null}
      <div className="chips" aria-label="Aufträge filtern">
        {[["alle", "Alle"], ["aktiv", "In Arbeit"], ["failed", "Fehlgeschlagen"], ["done", "Abgeschlossen"], ["cancelled", "Abgebrochen"]].map(([wert, text]) => (
          <button key={wert} className="chip" data-aktiv={filter === wert} aria-pressed={filter === wert}
            onClick={() => setFilter(wert)}>{text}</button>
        ))}
      </div>
      {aktionsFehler ? <Fehler text={aktionsFehler} /> : null}

      {/*
        Die Aufschlüsselung steht bewusst oben: Ein Video erzeugt im Lauf
        seines Lebens mehrere Aufträge, deshalb kann die Warteschlange mehr
        Einträge haben, als der Kanal Videos hat. Ohne diese Zeile sieht das
        nach einem Fehler aus.
      */}
      {wartendeArten.length > 1 ? (
        <Hinweis>
          <strong>Es warten {aktiv?.wartend ?? 0} Aufträge.</strong>
          <div style={{ color: "var(--text-gedaempft)", marginTop: 4 }}>
            {wartendeArten.map(([art, n]) => `${n} × ${AUFTRAG_TEXT[art] ?? art}`).join(" · ")}
          </div>
          <div style={{ color: "var(--text-schwach)", marginTop: 4, fontSize: 12.5 }}>
            Ein Video durchläuft nacheinander mehrere Aufträge – erst der Download, später
            die Verkleinerung. Mehr Aufträge als Videos sind deshalb normal.
          </div>
        </Hinweis>
      ) : null}

      {drosselung?.pausiert ? (
        <Hinweis art="arbeit">
          <strong>YouTube weist gerade ab – Downloads pausieren.</strong>
          <div style={{ color: "var(--text-gedaempft)", marginTop: 4 }}>
            {pause?.aktiv
              ? `Die automatische Sperrpause läuft noch ${wartedauer(drosselung.rest_s)}. Downloads starten erst, wenn zusätzlich die manuelle Pause beendet ist.`
              : `Die automatische Sperrpause endet frühestens in ${wartedauer(drosselung.rest_s)}. Anschließend werden auch die YouTube-Budgets erneut geprüft.`}{" "}
            YouTube hat Anfragen abgewiesen. Die Meldung allein belegt keine IP-Sperre.
            Vermeide sofortige Wiederholungen und prüfe die Einstellungen unter YouTube-Schutz.
          </div>
        </Hinweis>
      ) : null}

      {admin && gescheitert.length > 1 ? (
        <Hinweis art="fehler">
          <strong>
            {gescheitert.length} Aufträge sind fehlgeschlagen.
          </strong>
          <div style={{ color: "var(--text-gedaempft)", marginTop: 4 }}>
            Tragen sie alle denselben Fehler, lag es meist nicht an den Videos.
          </div>
          <button className="knopf" disabled={beschaeftigt} style={{ marginTop: 12 }} onClick={() => void alleWiederholen()}>
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
      ) : sichtbar.length === 0 && !laedt ? <Leer titel="Keine Aufträge in dieser Ansicht" text="Wähle einen anderen Filter, um weitere Aufträge zu sehen." /> : (
        <table className="tabelle auftraege-tabelle">
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
            {sichtbar.map((a) => (
              <tr key={a.id}>
                <td data-label="Art" className="auftrag-art"><Icon name={a.art === "channel_sync" ? "refresh" : "download"} size={20} />{AUFTRAG_TEXT[a.art] ?? a.art}</td>
                <td data-label="Gegenstand" className="auftrag-titel" style={{ maxWidth: 320, overflow: "hidden", textOverflow: "ellipsis" }}>
                  {a.ziel && a.art.startsWith("video") ? (
                    <Link to={`/video/${a.ziel}`}>{a.titel ?? a.ziel}</Link>
                  ) : a.ziel && a.art === "channel_sync" ? (
                    <Link to={`/kanal/${a.ziel}`}>{a.titel ?? a.ziel}</Link>
                  ) : (
                    (a.titel ?? a.ziel ?? "–")
                  )}
                  {admin && a.fehler ? (
                    <div style={{ color: "var(--zu-fehler)", fontSize: 12, marginTop: 3 }}>
                      {a.fehler}
                    </div>
                  ) : admin && a.meldung ? (
                    <div style={{ color: "var(--text-schwach)", fontSize: 12, marginTop: 3 }}>
                      {a.meldung}
                    </div>
                  ) : null}
                </td>
                <td data-label="Zustand">
                  <span className="marke-zustand" data-zustand={a.status === "running" ? "encoding" : a.status === "pending" ? "queued" : a.status}>
                    {STATUS_TEXT[a.status] ?? a.status}
                  </span>
                </td>
                <td data-label="Fortschritt">
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
                <td data-label="Angelegt" className="zahl" style={{ color: "var(--text-gedaempft)", fontSize: 12.5 }}>
                  {vorZeit(a.erstellt)}
                </td>
                <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                  {admin && a.status === "failed" ? (
                    <button className="knopf" disabled={beschaeftigt} onClick={() => void wiederholen(a.id)}>
                      Nochmal
                    </button>
                  ) : admin && (a.status === "pending" || a.status === "running") ? (
                    <button className="knopf" disabled={beschaeftigt} onClick={() => void abbrechen(a.id)}>
                      Abbrechen
                    </button>
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
