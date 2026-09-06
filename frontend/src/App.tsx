import { lazy, Suspense, useContext, useEffect, useRef, useState } from "react";
import { Link, Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";

import { Fortschrittsleiste } from "./components/Fortschritt";
import { Abmelden, Anmeldung, AnmeldeSchranke, Sitzungsverwaltung, useAdmin, useAnmeldung } from "./components/Anmeldung";
import { Icon, type IconName } from "./components/Icons";
import { KanalAvatar, KanalKontext } from "./components/KanalAvatar";
import { SeitenFehlergrenze } from "./components/SeitenFehlergrenze";
import { Leer, Skelettgitter } from "./components/ui";
import { api } from "./lib/api";
import { useApi } from "./hooks/useApi";
import { SCHMAL, useMedienabfrage } from "./hooks/useMedienabfrage";
import { KanalAnlegenDialog } from "./pages/KanalAnlegen";
import { Kanalseite } from "./pages/Kanal";
import { Kanaeleseite } from "./pages/Kanaele";
import { Playlistseite } from "./pages/Playlist";
import { Speicherseite } from "./pages/Speicher";
import { Startseite } from "./pages/Start";
import { Suchseite } from "./pages/Suche";
import { Warteschlangeseite } from "./pages/Warteschlange";

// Player und Weltkarte erst laden, wenn sie tatsächlich gebraucht werden.
const Wiedergabeseite = lazy(() => import("./pages/Wiedergabe").then((m) => ({ default: m.Wiedergabeseite })));
const Streamsseite = lazy(() => import("./pages/Streams").then((m) => ({ default: m.Streamsseite })));
const Einstellungenseite = lazy(() => import("./pages/Einstellungen").then((m) => ({ default: m.Einstellungenseite })));

const LEISTE_INTERVALL = 15_000;

function Kopfleiste({ aufLeiste, leisteOffen, aufKanalAnlegen, gesperrt }: {
  aufLeiste: () => void;
  leisteOffen: boolean;
  aufKanalAnlegen: () => void;
  gesperrt: boolean;
}) {
  const admin = useAdmin();
  const navigate = useNavigate();
  const ort = useLocation();
  const [text, setText] = useState("");
  const [sucheOffen, setSucheOffen] = useState(false);
  const suchfeld = useRef<HTMLInputElement>(null);
  const sucheOeffnen = useRef<HTMLButtonElement>(null);
  function sucheSchliessen() {
    setSucheOffen(false);
    window.requestAnimationFrame(() => sucheOeffnen.current?.focus());
  }
  const [hell, setHell] = useState(() => {
    try { return localStorage.getItem("vitrine-thema") === "hell"; } catch { return false; }
  });

  useEffect(() => {
    setText(ort.pathname === "/suche" ? new URLSearchParams(ort.search).get("q") ?? "" : "");
    setSucheOffen(ort.pathname === "/suche" && !new URLSearchParams(ort.search).get("q"));
  }, [ort.pathname, ort.search, ort.key]);

  useEffect(() => {
    document.documentElement.dataset.thema = hell ? "hell" : "dunkel";
    document.documentElement.style.colorScheme = hell ? "light" : "dark";
    document.querySelectorAll('meta[name="theme-color"]').forEach((meta) => {
      meta.setAttribute("content", hell ? "#ffffff" : "#0f0f0f");
    });
    try { localStorage.setItem("vitrine-thema", hell ? "hell" : "dunkel"); } catch { /* Privater Browsermodus */ }
  }, [hell]);

  useEffect(() => { if (sucheOffen) suchfeld.current?.focus(); }, [sucheOffen]);

  useEffect(() => {
    function tastatur(e: KeyboardEvent) {
      if (gesperrt || document.querySelector("dialog[open], [aria-modal='true']")) return;
      const ziel = e.target;
      if (ziel instanceof HTMLElement && (ziel.closest("input, textarea, select, [contenteditable='true']") || ziel.closest(".player"))) return;
      if (e.key === "/" && !e.ctrlKey && !e.metaKey && !e.altKey) {
        e.preventDefault();
        setSucheOffen(true);
        suchfeld.current?.focus();
      }
    }
    window.addEventListener("keydown", tastatur);
    return () => window.removeEventListener("keydown", tastatur);
  }, [gesperrt]);

  return (
    <header className="kopf" data-suche-offen={sucheOffen} inert={gesperrt}>
      <div className="kopf-links">
        <button id="menue-knopf" className="symbol-knopf" onClick={aufLeiste}
          aria-label="Seitenleiste umschalten" aria-controls="hauptnavigation" aria-expanded={leisteOffen}>
          <Icon name="menu" />
        </button>
        <Link to="/" className="marke" aria-label="Vitrine Startseite">
          <svg className="marke-zeichen" viewBox="0 0 32 24" aria-hidden="true">
            <rect x="0" y="1" width="32" height="22" rx="7" fill="#ff0033" />
            <path d="m13 7 9 5-9 5Z" fill="white" />
          </svg>
          <span>Vitrine</span>
        </Link>
      </div>
      <button className="symbol-knopf suche-zurueck" aria-label="Suche schließen" onClick={sucheSchliessen}>
        <Icon name="arrowLeft" />
      </button>
      <form className="suche" role="search" onSubmit={(e) => {
        e.preventDefault();
        if (text.trim()) navigate(`/suche?q=${encodeURIComponent(text.trim())}`);
      }}>
        <div className="suche-feld">
          <input ref={suchfeld} value={text} onChange={(e) => setText(e.target.value)} type="search" enterKeyHint="search"
            onKeyDown={(e) => { if (e.key === "Escape" && sucheOffen) sucheSchliessen(); }}
            placeholder="Suchen" aria-label="Im Archiv suchen" aria-keyshortcuts="/" autoComplete="off" />
          {text ? <button type="button" className="symbol-knopf suche-leeren" aria-label="Suchbegriff löschen"
            onClick={() => { setText(""); suchfeld.current?.focus(); }}><Icon name="close" size={20} /></button> : null}
        </div>
        <button className="suche-absenden" aria-label="Suchen" title="Suchen"><Icon name="search" /></button>
      </form>
      <div className="kopf-rechts">
        <button ref={sucheOeffnen} className="symbol-knopf mobile-suche" aria-label="Suche öffnen" onClick={() => setSucheOffen(true)}>
          <Icon name="search" />
        </button>
        {admin ? <button className="knopf kanal-hinzufuegen" onClick={aufKanalAnlegen} aria-label="Kanal aufnehmen">
          <Icon name="plus" /><span>Kanal aufnehmen</span>
        </button> : null}
        <button className="symbol-knopf thema-knopf" onClick={() => setHell(!hell)}
          aria-label={hell ? "Dunkles Design aktivieren" : "Helles Design aktivieren"}
          title={hell ? "Dunkles Design" : "Helles Design"}>
          <Icon name={hell ? "moon" : "sun"} />
        </button>
        {admin ? <Abmelden /> : <Link to="/anmelden" className="knopf abmelden-knopf" aria-label="Als Administrator anmelden" title="Anmelden">
          <Icon name="login" /><span>Anmelden</span>
        </Link>}
      </div>
    </header>
  );
}

function Seitenleiste({ schmal, handbetrieb, schubladeOffen, aufSchliessen, dialogOffen }: {
  schmal: boolean;
  handbetrieb: boolean;
  schubladeOffen: boolean;
  aufSchliessen: () => void;
  dialogOffen: boolean;
}) {
  const admin = useAdmin();
  const ort = useLocation();
  const kanaele = useContext(KanalKontext);
  const { daten: auftraege } = useApi(() => admin ? api.auftraege() : Promise.resolve([]), [admin], admin ? LEISTE_INTERVALL : undefined);
  const nav = useRef<HTMLElement>(null);
  const offen = auftraege?.filter((a) => a.status === "pending" || a.status === "running").length ?? 0;
  const gruppen: { titel?: string; punkte: { pfad: string; icon: IconName; text: string; zahl?: number }[] }[] = [
    { punkte: [
      { pfad: "/", icon: "home", text: "Start" },
      { pfad: "/kanaele", icon: "channels", text: "Kanäle", zahl: kanaele.length },
    ] },
  ];
  if (admin) gruppen.push({ titel: "Dein Archiv", punkte: [
      { pfad: "/warteschlange", icon: "queue", text: "Warteschlange", zahl: offen || undefined },
      { pfad: "/speicher", icon: "storage", text: "Speicher" },
      { pfad: "/streams", icon: "play", text: "Streams" },
    ] });

  useEffect(() => {
    if (!schubladeOffen) return;
    const frame = window.requestAnimationFrame(() => nav.current?.querySelector<HTMLButtonElement>("button")?.focus());
    return () => {
      window.cancelAnimationFrame(frame);
      window.requestAnimationFrame(() => document.getElementById("menue-knopf")?.focus());
    };
  }, [schubladeOffen]);

  return (
    <nav id="hauptnavigation" aria-label="Hauptnavigation" ref={nav} className="leiste"
      data-schmal={schmal} data-offen={schubladeOffen}
      inert={dialogOffen || (handbetrieb && !schubladeOffen)}
      onKeyDown={(e) => {
        if (e.key === "Escape") aufSchliessen();
        if (e.key === "Tab" && schubladeOffen) {
          const links = nav.current?.querySelectorAll<HTMLElement>("a, button:not(:disabled)");
          if (!links?.length) return;
          const first = links[0], last = links[links.length - 1];
          if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
          if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
        }
      }}>
      {handbetrieb ? <div className="leiste-mobilkopf">
        <span>Navigation</span>
        <button className="symbol-knopf" aria-label="Navigation schließen" onClick={aufSchliessen}><Icon name="close" /></button>
      </div> : null}
      {gruppen.map((g, i) => <div className="leiste-gruppe" key={i}>
        {g.titel ? <div className="leiste-titel">{g.titel}<Icon name="chevronRight" size={18} /></div> : null}
        {g.punkte.map((p) => {
          const aktiv = p.pfad === "/" ? ort.pathname === "/" : ort.pathname.startsWith(p.pfad);
          return <Link key={p.pfad} to={p.pfad} onClick={aufSchliessen} className="nav-punkt"
            data-aktiv={aktiv} aria-current={aktiv ? "page" : undefined} title={p.text}>
            <Icon name={p.icon} className="zeichen" /><span>{p.text}</span>
            {p.zahl ? <span className="nav-zahl">{p.zahl}</span> : null}
          </Link>;
        })}
      </div>)}
      {!schmal && kanaele.length > 0 ? <div className="leiste-gruppe">
        <div className="leiste-titel">Abonnements<Icon name="chevronRight" size={18} /></div>
        {kanaele.map((k) => <Link key={k.id} to={`/kanal/${k.id}`} onClick={aufSchliessen}
          className="nav-punkt kanal-punkt" data-aktiv={ort.pathname === `/kanal/${k.id}`}
          aria-current={ort.pathname === `/kanal/${k.id}` ? "page" : undefined}
          title={`${k.name}: ${k.videos_archiviert} von ${k.videos_gesamt} archiviert`}>
          <KanalAvatar kanalId={k.id} name={k.name} />
          <span className="nav-kanalname">{k.name}</span>
        </Link>)}
      </div> : null}
      {admin ? <div className="leiste-gruppe">
        <Link to="/einstellungen" onClick={aufSchliessen} className="nav-punkt"
          data-aktiv={ort.pathname === "/einstellungen"} aria-current={ort.pathname === "/einstellungen" ? "page" : undefined}
          title="Einstellungen">
          <Icon name="settings" className="zeichen" /><span>Einstellungen</span>
        </Link>
      </div> : null}
      {admin && handbetrieb ? <div className="leiste-gruppe leiste-abmelden"><Abmelden /></div> : null}
    </nav>
  );
}

function MobileNavigation({ gesperrt }: { gesperrt: boolean }) {
  const admin = useAdmin();
  const ort = useLocation();
  const punkte: { pfad: string; icon: IconName; text: string; aktiv: boolean }[] = [
    { pfad: "/", icon: "home", text: "Start", aktiv: ort.pathname === "/" },
    { pfad: "/kanaele", icon: "channels", text: "Kanäle", aktiv: /^\/(kanal|kanaele|playlist)(\/|$)/.test(ort.pathname) },
    { pfad: "/suche", icon: "search", text: "Suchen", aktiv: ort.pathname === "/suche" },
    admin
      ? { pfad: "/einstellungen", icon: "settings", text: "Verwaltung", aktiv: ["/einstellungen", "/speicher", "/warteschlange", "/streams"].includes(ort.pathname) }
      : { pfad: "/anmelden", icon: "login", text: "Anmelden", aktiv: ort.pathname === "/anmelden" },
  ];
  return <nav className="mobile-navigation" aria-label="Mobile Navigation" inert={gesperrt}>
    {punkte.map((p) => <Link key={p.pfad} to={p.pfad} data-aktiv={p.aktiv} aria-current={p.aktiv ? "page" : undefined}>
      <Icon name={p.icon} size={22} /><span>{p.text}</span>
    </Link>)}
  </nav>;
}

export default function App() {
  return <Sitzungsverwaltung><Archiv /></Sitzungsverwaltung>;
}

function Anmeldeseite() {
  const zustand = useAnmeldung();
  return zustand.sitzung?.angemeldet ? <Navigate to="/" replace /> : <Anmeldung key={zustand.wechsel} zustand={zustand} />;
}

function Archiv() {
  const admin = useAdmin();
  const ort = useLocation();
  const aufWiedergabe = ort.pathname.startsWith("/video/");
  const [vonHand, setVonHand] = useState<boolean | null>(null);
  const [dialogOffen, setDialogOffen] = useState(false);
  const handbetrieb = useMedienabfrage(SCHMAL);
  const kompakt = useMedienabfrage("(max-width: 1312px)");
  const [schubladeOffen, setSchubladeOffen] = useState(false);
  const schmal = handbetrieb ? false : (vonHand ?? (aufWiedergabe || kompakt));
  const kanaele = useApi(() => api.kanaele(), [], LEISTE_INTERVALL);
  const inhalt = useRef<HTMLElement>(null);
  const dialogAusloeser = useRef<HTMLElement | null>(null);
  function kanalDialogOeffnen() {
    if (!admin) return;
    dialogAusloeser.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    setDialogOffen(true);
  }
  function kanalDialogSchliessen() {
    setDialogOffen(false);
    kanaele.neuLaden();
    window.requestAnimationFrame(() => {
      if (dialogAusloeser.current?.isConnected) dialogAusloeser.current.focus();
    });
  }

  useEffect(() => {
    setSchubladeOffen(false);
    inhalt.current?.scrollTo({ top: 0 });
  }, [ort.pathname, ort.search, ort.key]);
  useEffect(() => { if (!handbetrieb) setSchubladeOffen(false); }, [handbetrieb]);

  useEffect(() => {
    const namen: Record<string, string> = { kanaele: "Kanäle", kanal: "Kanal", playlist: "Playlist", video: "Wiedergabe", suche: "Suche", warteschlange: "Warteschlange", speicher: "Speicher", streams: "Streams", einstellungen: "Einstellungen", anmelden: "Anmelden" };
    const name = namen[ort.pathname.split("/")[1]];
    document.title = name ? `${name} – Vitrine` : "Vitrine";
  }, [ort.pathname]);

  return (
    <KanalKontext.Provider value={kanaele.daten ?? []}>
      <div className="huelle" data-handbetrieb={handbetrieb || undefined} data-seite={ort.pathname.split("/")[1] || "start"}>
        <a className="sprunglink" href="#inhalt">Zum Inhalt</a>
        <Kopfleiste leisteOffen={handbetrieb ? schubladeOffen : !schmal}
          gesperrt={schubladeOffen || dialogOffen}
          aufLeiste={() => handbetrieb ? setSchubladeOffen((o) => !o) : setVonHand(!schmal)}
          aufKanalAnlegen={kanalDialogOeffnen} />
        {handbetrieb && schubladeOffen ? <div className="leiste-schatten"
          onClick={() => setSchubladeOffen(false)} aria-hidden="true" /> : null}
        <Seitenleiste schmal={schmal} handbetrieb={handbetrieb} dialogOffen={dialogOffen}
          schubladeOffen={handbetrieb && schubladeOffen} aufSchliessen={() => setSchubladeOffen(false)} />
        <main id="inhalt" className="inhalt" ref={inhalt} tabIndex={-1} inert={schubladeOffen || dialogOffen}>
          {admin ? <Fortschrittsleiste /> : null}
          <SeitenFehlergrenze key={ort.pathname + (ort.pathname === "/suche" ? new URLSearchParams(ort.search).get("q") ?? "" : "")}>
          <Suspense fallback={<Skelettgitter anzahl={6} />}>
          <Routes>
            <Route path="/" element={<Startseite />} />
            <Route path="/kanaele" element={<Kanaeleseite aufAnlegen={kanalDialogOeffnen} />} />
            <Route path="/kanal/:kanalId" element={<Kanalseite key={ort.pathname} />} />
            <Route path="/playlist/:playlistId" element={<Playlistseite key={ort.pathname} />} />
            <Route path="/video/:videoId" element={<Wiedergabeseite key={ort.pathname} />} />
            <Route path="/suche" element={<Suchseite />} />
            <Route path="/warteschlange" element={<AnmeldeSchranke><Warteschlangeseite /></AnmeldeSchranke>} />
            <Route path="/speicher" element={<AnmeldeSchranke><Speicherseite /></AnmeldeSchranke>} />
            <Route path="/anmelden" element={<Anmeldeseite />} />
            <Route path="/einstellungen" element={<AnmeldeSchranke><Einstellungenseite /></AnmeldeSchranke>} />
            <Route path="/streams" element={<AnmeldeSchranke><Streamsseite /></AnmeldeSchranke>} />
            <Route path="*" element={<Leer titel="Diese Seite gibt es nicht" text="Der Link ist möglicherweise veraltet. Auf der Startseite findest du dein Videoarchiv."
              kinder={<Link className="knopf" data-art="stark" to="/">Zur Startseite</Link>} />} />
          </Routes>
          </Suspense>
          </SeitenFehlergrenze>
        </main>
        <MobileNavigation gesperrt={schubladeOffen || dialogOffen} />
        {admin && dialogOffen ? <KanalAnlegenDialog aufSchliessen={kanalDialogSchliessen} /> : null}
      </div>
    </KanalKontext.Provider>
  );
}
