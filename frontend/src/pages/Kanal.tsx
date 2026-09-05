import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";

import { Fehler, Gitter, Hinweis, Leer, Skelettgitter, Videokachel } from "../components/ui";
import { Icon } from "../components/Icons";
import { useApi, useVideostapel } from "../hooks/useApi";
import type { AlleLadenErgebnis, Sammlung } from "../lib/api";
import { api, thumbUrl } from "../lib/api";
import { bytes, datum } from "../lib/format";
import "../styles/browse.css";

/**
 * Was „Alle laden" wirklich getan hat, in einem Satz.
 *
 * Vorher stand hier nur „N Videos eingereiht." Beim zweiten Klick war das
 * „0 Videos eingereiht." - richtig, aber es beantwortet die Frage nicht, die
 * man dann hat: Warum kommt nichts dazu, und wo sind die anderen geblieben?
 */
function ladenBericht(r: AlleLadenErgebnis): string {
  const teile: string[] = [];
  if (r.eingereiht) teile.push(`${r.eingereiht} neu eingereiht`);
  if (r.wartete_schon) teile.push(`${r.wartete_schon} warteten schon`);
  if (r.laeuft_gerade) teile.push(`${r.laeuft_gerade} laufen gerade`);
  if (r.bereits_archiviert) teile.push(`${r.bereits_archiviert} bereits archiviert`);
  if (r.regeln) teile.push(`${r.regeln} durch Kanalregeln ausgeschlossen`);
  if (r.nicht_verfuegbar) teile.push(`${r.nicht_verfuegbar} bei der Quelle gelöscht`);
  return teile.length ? teile.join(", ") + "." : "Es gibt hier nichts zu laden.";
}

/** Grobe Dauerangabe fuer die Nachfrage - Sekunden helfen dort niemandem. */
function dauerGrob(sekunden: number): string {
  const std = sekunden / 3600;
  if (std < 1) return `${Math.round(sekunden / 60)} Minuten`;
  if (std < 48) return `${Math.round(std)} Stunden`;
  return `${Math.round(std / 24)} Tage`;
}

/** Reihenfolge der Tabs wie bei YouTube: Videos, Shorts, Livestreams, Playlists. */
const ART_TEXT: Record<Sammlung["art"], string> = {
  uploads: "Videos",
  shorts: "Shorts",
  live: "Livestreams",
  playlist: "Playlist",
};

type Tab = "videos" | "shorts" | "live" | "playlists";

export function Kanalseite() {
  const { kanalId = "" } = useParams();
  const navigate = useNavigate();
  const [suchparameter, setSuchparameter] = useSearchParams();
  const tabWert = suchparameter.get("tab");
  const tab: Tab = tabWert === "shorts" || tabWert === "live" || tabWert === "playlists" ? tabWert : "videos";
  const sortWert = suchparameter.get("sort");
  const sortierung = sortWert === "alt" || sortWert === "aufrufe" ? sortWert : "neu";
  const [beschreibungOffen, setBeschreibungOffen] = useState(false);
  const [abgleichLaeuft, setAbgleichLaeuft] = useState(false);
  const [entfernenOffen, setEntfernenOffen] = useState(false);
  // Herunterladen in zwei Schritten: Bei einem grossen Kanal geht es um Tage
  // und hunderte Gigabyte - das soll niemand mit einem Klick ausloesen, ohne
  // die Zahl gesehen zu haben.
  const [ladenNachfrage, setLadenNachfrage] = useState(false);
  const [ladenLaeuft, setLadenLaeuft] = useState(false);
  const [ladenMeldung, setLadenMeldung] = useState<string | null>(null);
  const [aktionsFehler, setAktionsFehler] = useState<string | null>(null);

  const kanal = useApi(() => api.kanal(kanalId), [kanalId]);
  const offene = useApi(() => api.kanalOffene(kanalId), [kanalId]);

  // Die Videos kommen serverseitig gefiltert und seitenweise. Der Filter
  // MUSS auf dem Server liegen: Wer Seite fuer Seite laedt und erst im Browser
  // die Shorts aussiebt, bekommt mal 60, mal 3 sichtbare Videos je Seite.
  const stapel = useVideostapel({
    kanal: kanalId,
    art: tab === "playlists" ? "videos" : tab,
    nur_archiviert: false,
    sortierung,
  });

  if (kanal.fehler) return <Fehler text={kanal.fehler} erneut={kanal.neuLaden} />;
  if (!kanal.daten) return <Skelettgitter anzahl={8} />;

  const d = kanal.daten;
  const playlists = d.sammlungen.filter((s) => s.art === "playlist");
  const gesamtImTab =
    tab === "playlists"
      ? playlists.length
      : tab === "shorts"
        ? d.zaehler.shorts
        : tab === "live"
          ? d.zaehler.live
          : d.zaehler.videos;

  async function abgleichen() {
    setAbgleichLaeuft(true);
    setAktionsFehler(null);
    try {
      await api.kanalAbgleichen(kanalId, true);
      setLadenMeldung("Der Kanalabgleich wurde eingereiht.");
    } catch (e) {
      setAktionsFehler(e instanceof Error ? e.message : String(e));
    } finally {
      setAbgleichLaeuft(false);
    }
  }

  const tabs: { wert: Tab; text: string; zahl: number }[] = [
    { wert: "videos", text: "Videos", zahl: d.zaehler.videos },
    { wert: "shorts", text: "Shorts", zahl: d.zaehler.shorts },
    { wert: "live", text: "Livestreams", zahl: d.zaehler.live },
    { wert: "playlists", text: "Playlists", zahl: playlists.length },
  ];

  return (
    <section className="kanal-seite">
      {aktionsFehler ? <Fehler text={aktionsFehler} /> : null}
      {ladenMeldung ? (
        <Hinweis>
          <div>
            {ladenMeldung} Der Fortschritt steht oben und in der{" "}
            <Link to="/warteschlange" style={{ textDecoration: "underline" }}>
              Warteschlange
            </Link>
            .
          </div>
        </Hinweis>
      ) : null}
      <div className="kanal-kopf">
        {thumbUrl(d.banner) ? <img className="banner" src={thumbUrl(d.banner)!} alt="" /> : null}
        <div className="kanal-zeile">
          {thumbUrl(d.kanal.avatar) ? (
            <img className="avatar-gross" src={thumbUrl(d.kanal.avatar)!} alt="" />
          ) : (
            <span className="avatar-gross kanal-initial" aria-hidden="true">{d.kanal.name.charAt(0).toLocaleUpperCase("de")}</span>
          )}
          <div className="kanal-identitaet">
            <h1>{d.kanal.name}</h1>
            <div className="browse-meta">
              {d.kanal.handle ? <span>{d.kanal.handle} · </span> : null}
              <span>
                {d.kanal.videos_archiviert} von {d.kanal.videos_gesamt} archiviert
              </span>
              {d.kanal.belegung_bytes > 0 ? <span> · {bytes(d.kanal.belegung_bytes)}</span> : null}
            </div>
            {d.kanal.zuletzt_abgeglichen ? (
              <div className="browse-meta kanal-abgleich">
                zuletzt abgeglichen {datum(d.kanal.zuletzt_abgeglichen)}
              </div>
            ) : null}
            {d.beschreibung ? (
              <div className="kanal-beschreibung">
                <p id="kanal-beschreibung" data-offen={beschreibungOffen}>{d.beschreibung}</p>
                <button type="button" aria-expanded={beschreibungOffen} aria-controls="kanal-beschreibung" onClick={() => setBeschreibungOffen(!beschreibungOffen)}>
                  {beschreibungOffen ? "Weniger anzeigen" : "Mehr anzeigen"}
                </button>
              </div>
            ) : null}
          <div className="aktionen kanal-aktionen">
            {offene.daten && offene.daten.anzahl > 0 ? (
              ladenNachfrage ? (
                <>
                  <span style={{ color: "var(--text-gedaempft)", fontSize: 13 }}>
                    {offene.daten.anzahl} Videos, {dauerGrob(offene.daten.dauer_s)}, grob{" "}
                    {bytes(offene.daten.bytes_geschaetzt)}
                  </span>
                  <button
                    className="knopf"
                    data-art="stark"
                    disabled={ladenLaeuft}
                    onClick={async () => {
                      setLadenLaeuft(true);
                      setAktionsFehler(null);
                      try {
                        const r = await api.kanalAlleLaden(kanalId);
                        setLadenMeldung(ladenBericht(r));
                        setLadenNachfrage(false);
                        offene.neuLaden();
                        stapel.neuLaden();
                      } catch (e) {
                        setAktionsFehler(e instanceof Error ? e.message : String(e));
                      } finally {
                        setLadenLaeuft(false);
                      }
                    }}
                  >
                    {ladenLaeuft ? "wird eingereiht …" : "Ja, alle laden"}
                  </button>
                  <button className="knopf" onClick={() => setLadenNachfrage(false)}>
                    Abbrechen
                  </button>
                </>
              ) : (
                <button className="knopf" data-art="stark" onClick={() => setLadenNachfrage(true)}>
                  <Icon name="download" size={18} /> Alle laden ({offene.daten.anzahl})
                </button>
              )
            ) : null}
            <button className="knopf" onClick={abgleichen} disabled={abgleichLaeuft}>
              <Icon name="refresh" size={18} />
              {abgleichLaeuft ? "wird eingereiht …" : "Jetzt abgleichen"}
            </button>
            <button className="knopf" data-art="gefahr" onClick={() => setEntfernenOffen(true)}>
              Entfernen
            </button>
          </div>
          </div>
        </div>
      </div>

      <div className="tabs">
        {tabs
          // Leere Tabs gar nicht erst zeigen - nicht jeder Kanal hat Shorts.
          .filter((t) => t.zahl > 0 || t.wert === "videos")
          .map((t) => (
            <button
              key={t.wert}
              className="tab"
              data-aktiv={tab === t.wert}
              aria-pressed={tab === t.wert}
              onClick={() => setSuchparameter((vorher) => {
                const neu = new URLSearchParams(vorher);
                if (t.wert === "videos") neu.delete("tab"); else neu.set("tab", t.wert);
                return neu;
              })}
            >
              {t.text}
              <span className="zahl">{t.zahl}</span>
            </button>
          ))}
      </div>

      {tab !== "playlists" ? (
        <div className="chips kanal-sortierung" aria-label="Videos sortieren">
          {([{ wert: "neu", text: "Neueste" }, { wert: "aufrufe", text: "Beliebt" }, { wert: "alt", text: "Älteste" }] as const).map((s) => (
            <button className="chip" key={s.wert} data-aktiv={sortierung === s.wert} aria-pressed={sortierung === s.wert} onClick={() => setSuchparameter((vorher) => {
              const neu = new URLSearchParams(vorher);
              if (s.wert === "neu") neu.delete("sort"); else neu.set("sort", s.wert);
              return neu;
            })}>{s.text}</button>
          ))}
        </div>
      ) : null}

      {tab === "playlists" ? (
        playlists.length > 0 ? (
          <Gitter>
            {playlists.map((p) => (
              <Link key={p.id} className="kachel playlist-kachel" to={`/playlist/${p.id}`}>
                <div className="kachel-bild">
                  {thumbUrl(p.thumb) ? (
                    <img src={thumbUrl(p.thumb)!} alt="" loading="lazy" />
                  ) : (
                    <div className="platzhalter"><Icon name="playlist" size={42} /></div>
                  )}
                  <span className="dauer playlist-anzahl"><Icon name="playlist" size={16} />{p.anzahl} Videos</span>
                </div>
                <div className="kachel-text">
                  <div style={{ minWidth: 0 }}>
                    <h3 className="kachel-titel">{p.titel}</h3>
                    <div className="kachel-zeile">{ART_TEXT[p.art]}</div>
                    <div className="playlist-anzeigen">Vollständige Playlist ansehen</div>
                  </div>
                </div>
              </Link>
            ))}
          </Gitter>
        ) : (
          <Leer zeichen="☰" titel="Keine Playlists" text="Dieser Kanal führt keine öffentlichen Playlists." />
        )
      ) : (
        <div className="kanal-videoergebnisse" data-art={tab}>
          {stapel.fehler ? <Fehler text={stapel.fehler} erneut={stapel.neuLaden} /> : null}
          {stapel.laedt && stapel.videos.length === 0 ? (
            <Skelettgitter />
          ) : stapel.videos.length > 0 ? (
            <>
              <Gitter>
                {stapel.videos.map((v) => (
                  <Videokachel key={v.id} video={v} ohneKanal />
                ))}
              </Gitter>
              <div className="mehr-laden">
                <span>
                  {stapel.videos.length} von {gesamtImTab}
                </span>
                {!stapel.ende ? (
                  <button className="knopf" onClick={stapel.mehrLaden} disabled={stapel.laedt}>
                    {stapel.laedt ? "lädt …" : "Mehr laden"}
                  </button>
                ) : null}
              </div>
            </>
          ) : !stapel.fehler ? (
            <Leer
              titel="Hier ist noch nichts"
              text="Sobald der Abgleich durch ist, erscheinen die Videos dieses Kanals hier."
            />
          ) : null}
        </div>
      )}

      {entfernenOffen ? (
        <KanalEntfernenDialog
          kanalId={kanalId}
          name={d.kanal.name}
          videos={d.kanal.videos_gesamt}
          archiviert={d.kanal.videos_archiviert}
          belegung={d.kanal.belegung_bytes}
          aufSchliessen={() => setEntfernenOffen(false)}
          aufFertig={() => navigate("/kanaele")}
        />
      ) : null}
    </section>
  );
}

function KanalEntfernenDialog({
  kanalId,
  name,
  videos,
  archiviert,
  belegung,
  aufSchliessen,
  aufFertig,
}: {
  kanalId: string;
  name: string;
  videos: number;
  archiviert: number;
  belegung: number;
  aufSchliessen: () => void;
  aufFertig: () => void;
}) {
  const [dateien, setDateien] = useState(true);
  const [laeuft, setLaeuft] = useState(false);
  const [fehler, setFehler] = useState<string | null>(null);
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const el = dialog.current;
    el?.showModal();
    return () => el?.close();
  }, []);

  async function bestaetigen() {
    setLaeuft(true);
    setFehler(null);
    try {
      await api.kanalLoeschen(kanalId, dateien);
      aufFertig();
    } catch (e) {
      setFehler(e instanceof Error ? e.message : String(e));
      setLaeuft(false);
    }
  }

  return (
    <dialog
      ref={dialog}
      className="kanal-dialog"
      aria-labelledby="kanal-entfernen-titel"
      onCancel={(e) => { if (laeuft) e.preventDefault(); else aufSchliessen(); }}
      onClick={(e) => {
        if (e.target === e.currentTarget && !laeuft) aufSchliessen();
      }}
    >
      <div className="dialog">
        <h2 id="kanal-entfernen-titel">„{name}“ entfernen?</h2>
        <p className="erklaerung">
          Der Kanal verschwindet mit allen {videos} erfassten Videos, den Playlists und den
          zugehörigen Aufträgen aus der Verwaltung. Das lässt sich nicht rückgängig machen –
          ein erneutes Aufnehmen muss alles neu erfassen.
        </p>

        <label className="schalter">
          <input type="checkbox" checked={dateien} onChange={(e) => setDateien(e.target.checked)} />
          <span>
            Auch die Videodateien löschen
            <div style={{ color: "var(--text-schwach)", fontSize: 12 }}>
              {archiviert} archivierte Videos, {bytes(belegung)}. Ohne Haken bleiben die Bündel
              auf der Platte liegen – aber ohne Datenbank sind sie nicht abspielbar.
            </div>
          </span>
        </label>

        {fehler ? (
          <div className="hinweis" data-art="fehler" style={{ marginTop: 14, marginBottom: 0 }}>
            <div>{fehler}</div>
          </div>
        ) : null}

        <div className="dialog-fuss">
          <button type="button" className="knopf" autoFocus onClick={aufSchliessen} disabled={laeuft}>
            Abbrechen
          </button>
          <button
            type="button"
            className="knopf"
            data-art="gefahr-stark"
            onClick={() => void bestaetigen()}
            disabled={laeuft}
          >
            {laeuft ? "wird entfernt …" : dateien ? "Kanal und Dateien löschen" : "Nur Kanal entfernen"}
          </button>
        </div>
      </div>
    </dialog>
  );
}
