import { useEffect, useState } from "react";
import { Link, Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";

import { Fortschrittsleiste } from "./components/Fortschritt";
import { api } from "./lib/api";
import { thumbUrl } from "./lib/api";
import { useApi } from "./hooks/useApi";
import { SCHMAL, useMedienabfrage } from "./hooks/useMedienabfrage";
import { KanalAnlegenDialog } from "./pages/KanalAnlegen";
import { Kanalseite } from "./pages/Kanal";
import { Einstellungenseite } from "./pages/Einstellungen";
import { Kanaeleseite } from "./pages/Kanaele";
import { Playlistseite } from "./pages/Playlist";
import { Speicherseite } from "./pages/Speicher";
import { Startseite } from "./pages/Start";
import { Suchseite } from "./pages/Suche";
import { Warteschlangeseite } from "./pages/Warteschlange";
import { Wiedergabeseite } from "./pages/Wiedergabe";

/** Wie oft die Seitenleiste ihre Zahlen auffrischt. Nicht zu haeufig - es ist
 *  eine Randinformation, kein Messgeraet. */
const LEISTE_INTERVALL = 15_000;

function Kopfleiste({
  aufLeiste,
  aufKanalAnlegen,
}: {
  aufLeiste: () => void;
  aufKanalAnlegen: () => void;
}) {
  const navigate = useNavigate();
  const ort = useLocation();
  const [text, setText] = useState("");

  // Beim Verlassen der Suche das Feld leeren, damit die alte Anfrage nicht
  // stehen bleibt und beim naechsten Fokus verwirrt.
  useEffect(() => {
    if (!ort.pathname.startsWith("/suche")) setText("");
  }, [ort.pathname]);

  return (
    <header className="kopf">
      <button className="knopf" onClick={aufLeiste} aria-label="Seitenleiste umschalten">
        ☰
      </button>
      <Link to="/" className="marke">
        <span className="marke-zeichen">▶</span>
        <span>Vitrine</span>
      </Link>

      <form
        className="suche"
        onSubmit={(e) => {
          e.preventDefault();
          if (text.trim()) navigate(`/suche?q=${encodeURIComponent(text.trim())}`);
        }}
      >
        <span style={{ color: "var(--text-schwach)" }}>⌕</span>
        <input
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Im Archiv suchen"
          aria-label="Im Archiv suchen"
        />
      </form>

      <div className="kopf-rechts">
        <button className="knopf" data-art="stark" onClick={aufKanalAnlegen}>
          + Kanal
        </button>
      </div>
    </header>
  );
}

function Seitenleiste({
  schmal,
  schubladeOffen,
  aufSchliessen,
}: {
  schmal: boolean;
  /** Nur auf schmalen Geraeten: Liegt die Leiste gerade ueber dem Inhalt? */
  schubladeOffen: boolean;
  aufSchliessen: () => void;
}) {
  const ort = useLocation();
  const { daten: kanaele } = useApi(() => api.kanaele(), [], LEISTE_INTERVALL);
  const { daten: auftraege } = useApi(() => api.auftraege(), [], LEISTE_INTERVALL);

  const offen = auftraege?.filter((a) => a.status === "pending" || a.status === "running").length ?? 0;
  const punkte = [
    { pfad: "/", zeichen: "⌂", text: "Start" },
    { pfad: "/kanaele", zeichen: "≡", text: "Kanäle", zahl: kanaele?.length },
    { pfad: "/warteschlange", zeichen: "⇅", text: "Warteschlange", zahl: offen || undefined },
    { pfad: "/speicher", zeichen: "▤", text: "Speicher" },
    { pfad: "/einstellungen", zeichen: "⚙", text: "Einstellungen" },
  ];

  return (
    <nav className="leiste" data-schmal={schmal} data-offen={schubladeOffen}>
      <div className="leiste-gruppe">
        {punkte.map((p) => (
          <Link
            key={p.pfad}
            to={p.pfad}
            onClick={aufSchliessen}
            className="nav-punkt"
            data-aktiv={p.pfad === "/" ? ort.pathname === "/" : ort.pathname.startsWith(p.pfad)}
          >
            <span className="zeichen">{p.zeichen}</span>
            <span>{p.text}</span>
            {p.zahl ? <span className="nav-zahl">{p.zahl}</span> : null}
          </Link>
        ))}
      </div>

      {!schmal && kanaele && kanaele.length > 0 ? (
        <div className="leiste-gruppe">
          <div className="leiste-titel">Abonnements</div>
          {kanaele.map((k) => (
            <Link
              key={k.id}
              to={`/kanal/${k.id}`}
              onClick={aufSchliessen}
              className="nav-punkt kanal-punkt"
              data-aktiv={ort.pathname === `/kanal/${k.id}`}
              title={`${k.videos_archiviert} von ${k.videos_gesamt} archiviert`}
            >
              {thumbUrl(k.avatar) ? (
                <img className="avatar" src={thumbUrl(k.avatar)!} alt="" />
              ) : (
                <span className="avatar" />
              )}
              <span style={{ overflow: "hidden", textOverflow: "ellipsis" }}>{k.name}</span>
              <span className="nav-zahl">{k.videos_archiviert}</span>
            </Link>
          ))}
        </div>
      ) : null}
    </nav>
  );
}

export default function App() {
  // Die Seitenleiste klappt auf der Wiedergabeseite von selbst ein - dort
  // zaehlt jeder Pixel fuer den Player, so macht YouTube es auch.
  const ort = useLocation();
  const aufWiedergabe = ort.pathname.startsWith("/video/");
  const [vonHand, setVonHand] = useState<boolean | null>(null);
  const [dialogOffen, setDialogOffen] = useState(false);

  // Auf dem Telefon hat derselbe Knopf eine andere Bedeutung: Dort gibt es
  // keine schmale Leiste neben dem Inhalt - sie waere breiter als das Geraet -,
  // sondern eine Schublade, die darueberliegt. Standard ist zu.
  const handbetrieb = useMedienabfrage(SCHMAL);
  const [schubladeOffen, setSchubladeOffen] = useState(false);
  const schmal = handbetrieb ? false : (vonHand ?? aufWiedergabe);

  // Beim Seitenwechsel schliessen. Sonst bliebe die Schublade nach einem
  // Zurueck-Wisch offen ueber der Seite liegen, auf die man wollte.
  useEffect(() => setSchubladeOffen(false), [ort.pathname]);

  // Solange die Schublade offen ist, darf der Inhalt darunter nicht mitscrollen.
  useEffect(() => {
    if (!schubladeOffen) return;
    const vorher = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = vorher;
    };
  }, [schubladeOffen]);

  return (
    <div className="huelle" data-handbetrieb={handbetrieb || undefined}>
      <Kopfleiste
        aufLeiste={() =>
          handbetrieb ? setSchubladeOffen((o) => !o) : setVonHand(!schmal)
        }
        aufKanalAnlegen={() => setDialogOffen(true)}
      />
      {/* Der Schatten liegt zwischen Inhalt und Schublade: Ein Tipp daneben
          schliesst sie, wie man es von jeder App kennt. */}
      {handbetrieb && schubladeOffen ? (
        <div
          className="leiste-schatten"
          onClick={() => setSchubladeOffen(false)}
          aria-hidden="true"
        />
      ) : null}
      <Seitenleiste
        schmal={schmal}
        schubladeOffen={handbetrieb && schubladeOffen}
        aufSchliessen={() => setSchubladeOffen(false)}
      />
      <main className="inhalt">
        <Fortschrittsleiste />
        <Routes>
          <Route path="/" element={<Startseite />} />
          <Route path="/kanaele" element={<Kanaeleseite aufAnlegen={() => setDialogOffen(true)} />} />
          <Route path="/kanal/:kanalId" element={<Kanalseite />} />
          <Route path="/playlist/:playlistId" element={<Playlistseite />} />
          <Route path="/video/:videoId" element={<Wiedergabeseite />} />
          <Route path="/suche" element={<Suchseite />} />
          <Route path="/warteschlange" element={<Warteschlangeseite />} />
          <Route path="/speicher" element={<Speicherseite />} />
          <Route path="/einstellungen" element={<Einstellungenseite />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
      {dialogOffen ? <KanalAnlegenDialog aufSchliessen={() => setDialogOffen(false)} /> : null}
    </div>
  );
}
