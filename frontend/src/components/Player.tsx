import { useCallback, useEffect, useRef, useState } from "react";

import { Icon } from "./Icons";
import { PlayerEinstellungen, PlayerUntertitel } from "./PlayerEinstellungen";
import { usePlayerAusrichtung } from "../hooks/usePlayerAusrichtung";
import type { Kapitel } from "../lib/api";
import { api, untertitelUrl } from "../lib/api";
import { useAdmin } from "./Anmeldung";
import { ApiFehler } from "../lib/auth";
import { dauer } from "../lib/format";
import { lokalFortschrittMerken } from "../lib/wiedergabeFortschritt";
import { wiedergabeStarten, wiedergabeMelden, wiedergabeBeenden, type Wiedergabesitzung, type WiedergabeQualitaet, type Qualitaetsangebot } from "../lib/wiedergabe";
import {
  istVollbild,
  vollbildUmschalten as umschaltenVollbild,
  wegErmitteln,
} from "../lib/vollbild";

/**
 * Der Player.
 *
 * Eigene Steuerung statt der Browser-Bedienelemente - aus zwei Gruenden. Zum
 * einen sehen die nativen Steuerungen in jedem Browser anders aus und lassen
 * sich nicht um Kapitel, Tempo oder Theater-Modus erweitern. Zum anderen
 * verhaelt sich die Buehne selbst nach dem Inhalt: Ein hochkantiges Short darf
 * nicht in ein 16:9-Fenster gepresst werden, sondern bekommt eine Buehne, die
 * sich an der Bildschirmhoehe orientiert.
 *
 * Die Quelle ist nicht immer sofort da: Kann der Browser den Archivcodec, kommt
 * das Video direkt aus dem Buendel. Andernfalls liefert der Server kurze
 * HLS-Abschnitte, die erst beim Abruf live transkodiert werden.
 */

type Lage =
  | { art: "pruefen" }
  | { art: "bereit"; quelle: string; modus: "direct" | "transcode"; sitzung: Wiedergabesitzung }
  | { art: "fehler"; text: string };

interface Props {
  videoId: string;
  poster?: string;
  titel?: string;
  startSekunde?: number;
  sprungSekunde?: number;
  dauerS?: number | null;
  kapitel?: Kapitel[];
  untertitel?: { sprache: string; automatisch: boolean }[];
  aufKapitel?: (index: number | null) => void;
  theater?: boolean;
  aufTheater?: (an: boolean) => void;
  minimiert?: boolean;
  aufMinimieren?: () => void;
  aufVergroessern?: () => void;
  aufSchliessen?: () => void;
}

/** Wie oft der Player meldet, dass noch geschaut wird. Deutlich haeufiger als
 *  die serverseitige Lease lang ist, damit ein verlorener Herzschlag nicht
 *  gleich zum Abraeumen fuehrt. */
const HERZSCHLAG_MS = 15_000;
const FORTSCHRITT_MS = 5_000;
/** Nach so viel Ruhe verschwindet die Steuerung waehrend der Wiedergabe. */
const AUSBLENDEN_MS = 2600;
const SPEICHER_SCHLUESSEL = "vitrine.wiedergabe";

interface Gemerkt {
  lautstaerke: number;
  stumm: boolean;
  tempo: number;
}

function gemerktLesen(): Gemerkt {
  try {
    const roh = localStorage.getItem(SPEICHER_SCHLUESSEL);
    if (roh) {
      const g = JSON.parse(roh) as Partial<Gemerkt>;
      return {
        lautstaerke: typeof g.lautstaerke === "number" && Number.isFinite(g.lautstaerke) ? Math.max(0, Math.min(1, g.lautstaerke)) : 1,
        stumm: !!g.stumm,
        tempo: typeof g.tempo === "number" && Number.isFinite(g.tempo) ? Math.max(0.25, Math.min(3, g.tempo)) : 1,
      };
    }
  } catch {
    /* privates Fenster o. ae. */
  }
  return { lautstaerke: 1, stumm: false, tempo: 1 };
}

function gemerktSchreiben(g: Gemerkt) {
  try {
    localStorage.setItem(SPEICHER_SCHLUESSEL, JSON.stringify(g));
  } catch {
    /* ignorieren */
  }
}

function bereiche(tr: TimeRanges): [number, number][] {
  const aus: [number, number][] = [];
  for (let i = 0; i < tr.length; i++) aus.push([tr.start(i), tr.end(i)]);
  return aus;
}

export function Player({
  videoId,
  poster,
  titel,
  startSekunde = 0,
  sprungSekunde,
  dauerS,
  kapitel = [],
  untertitel = [],
  aufKapitel,
  theater = false,
  aufTheater,
  minimiert = false,
  aufMinimieren,
  aufVergroessern,
  aufSchliessen,
}: Props) {
  const admin = useAdmin();
  const adminRef = useRef(admin);
  adminRef.current = admin;
  const [lage, setLage] = useState<Lage>({ art: "pruefen" });
  const [ladeVersuch, setLadeVersuch] = useState(0);
  const [transkodieren, setTranskodieren] = useState(false);
  const [qualitaet, setQualitaet] = useState<WiedergabeQualitaet>("auto");
  const [angebote, setAngebote] = useState<Qualitaetsangebot[]>([{ value: "auto", label: "Automatisch" }]);
  const wiederaufnahme = useRef<number | null>(null);
  const sollLaufen = useRef(true);
  const quellenwechsel = useRef(true);
  const videoRef = useRef<HTMLVideoElement>(null);
  const huelleRef = useRef<HTMLDivElement>(null);
  const leisteRef = useRef<HTMLDivElement>(null);
  const gesprungen = useRef(false);

  // ---- Zustand der Steuerung
  const [laeuft, setLaeuft] = useState(false);
  const [zeit, setZeit] = useState(0);
  const [gesamt, setGesamt] = useState(dauerS ?? 0);
  const [gepuffert, setGepuffert] = useState<[number, number][]>([]);
  const [wartet, setWartet] = useState(false);
  const [hochkant, setHochkant] = useState(false);
  const [gemerkt, setGemerkt] = useState<Gemerkt>(gemerktLesen);
  const gemerktRef = useRef(gemerkt);
  gemerktRef.current = gemerkt;
  const [nativesVollbild, setNativesVollbild] = useState(false);
  const [appVollbild, setAppVollbild] = useState(false);
  const vollbild = nativesVollbild || appVollbild;
  const ausrichtung = usePlayerAusrichtung();
  const [sichtbar, setSichtbar] = useState(true);
  const [menue, setMenue] = useState<"einstellungen" | "untertitel" | null>(null);
  const [spur, setSpur] = useState(-1); // -1 = aus
  const spurRef = useRef(spur);
  spurRef.current = spur;
  const [zeiger, setZeiger] = useState<{ x: number; zeit: number } | null>(null);
  const [touchBedienung, setTouchBedienung] = useState(false);
  const [hinweis, setHinweis] = useState<string | null>(null);
  const zieht = useRef(false);
  const ausblender = useRef<number | undefined>(undefined);
  const wischStart = useRef<{ x: number; y: number } | null>(null);
  const gewischt = useRef(false);

  // Vor src/load/Effect-Cleanup lesen: Danach setzt der Browser Zeit und Pause zurück.
  const wiedergabeMerken = useCallback(() => {
    const el = videoRef.current;
    if (!quellenwechsel.current && el && el.readyState >= 1) {
      wiederaufnahme.current = el.currentTime;
      sollLaufen.current = !el.paused;
      gemerktRef.current = { lautstaerke: el.volume, stumm: el.muted, tempo: el.playbackRate };
    }
    quellenwechsel.current = true;
  }, []);

  useEffect(() => {
    wiederaufnahme.current = null;
    sollLaufen.current = true;
    quellenwechsel.current = true;
    setQualitaet("auto");
    setTranskodieren(false);
    setAngebote([{ value: "auto", label: "Automatisch" }]);
    setZeit(0);
    setGepuffert([]);
    setHochkant(false);
    setSpur(-1);
    setMenue(null);
    setHinweis(null);
  }, [videoId]);

  useEffect(() => {
    if (!hinweis) return;
    const timer = window.setTimeout(() => setHinweis(null), 4500);
    return () => window.clearTimeout(timer);
  }, [hinweis]);

  // ---- Quelle beschaffen -------------------------------------------------
  useEffect(() => {
    let abgebrochen = false;
    let sitzung: Wiedergabesitzung | null = null;
    quellenwechsel.current = true;
    gesprungen.current = false;
    setWartet(false);
    setGepuffert([]);
    setLage({ art: "pruefen" });

    async function oeffnen(): Promise<void> {
      try {
        sitzung = await wiedergabeStarten(videoId, transkodieren, qualitaet);
        if (abgebrochen) {
          void wiedergabeBeenden(sitzung.token).catch(() => {});
          return;
        }
        setAngebote(sitzung.available_qualities?.length ? sitzung.available_qualities : [{ value: "auto", label: "Automatisch" }]);
        setLage({ art: "bereit", quelle: sitzung.url, modus: sitzung.mode, sitzung });
      } catch (e) {
        if (!abgebrochen) setLage({ art: "fehler", text: e instanceof Error ? e.message : String(e) });
      }
    }

    void oeffnen();
    return () => {
      abgebrochen = true;
      if (sitzung) void wiedergabeBeenden(sitzung.token).catch(() => {});
    };
  }, [videoId, ladeVersuch, transkodieren, qualitaet]);

  // Bei Bedarf werden nur angeforderte Abschnitte umgewandelt. Der Browser
  // kann deshalb sofort beginnen und später auch weit nach vorne springen.
  useEffect(() => {
    if (lage.art !== "bereit") return;
    const el = videoRef.current;
    if (!el) return;
    let geschlossen = false;
    let entfernen: (() => void) | undefined;
    if (lage.modus === "direct") {
      el.src = lage.quelle;
    } else if (el.canPlayType("application/vnd.apple.mpegurl")) {
      el.src = lage.quelle;
    } else {
      void import("hls.js").then(({ default: Hls }) => {
        if (geschlossen) return;
        if (!Hls.isSupported()) {
          setLage({ art: "fehler", text: "Dieser Browser unterstützt die Live-Wiedergabe nicht. Bitte verwende einen aktuellen Browser." });
          return;
        }
        const hls = new Hls({ enableWorker: false, maxBufferLength: 12, maxMaxBufferLength: 18,
          backBufferLength: 12, startPosition: wiederaufnahme.current ?? sprungSekunde ?? startSekunde,
          fragLoadPolicy: { default: { ...Hls.DefaultConfig.fragLoadPolicy.default,
            maxTimeToFirstByteMs: 90_000, maxLoadTimeMs: 120_000 } },
        });
        entfernen = () => hls.destroy();
        hls.on(Hls.Events.ERROR, (_ereignis, daten) => {
          if (geschlossen || !daten.fatal) return;
          wiedergabeMerken();
          setLage({ art: "fehler", text: "Die Live-Wiedergabe wurde unterbrochen. Bitte versuche es erneut." });
        });
        hls.attachMedia(el);
        hls.loadSource(lage.quelle);
      }).catch(() => {
        if (!geschlossen) setLage({ art: "fehler", text: "Der Player konnte nicht geladen werden. Bitte versuche es erneut." });
      });
    }
    return () => { geschlossen = true; entfernen?.(); el.pause(); el.removeAttribute("src"); el.load(); };
    // Ein Kapitelwechsel spult die bestehende Sitzung, statt sie neu aufzubauen.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lage]);

  // ---- Medienereignisse --------------------------------------------------
  useEffect(() => {
    if (lage.art !== "bereit") return;
    const el = videoRef.current;
    if (!el) return;

    el.volume = gemerktRef.current.lautstaerke;
    el.muted = gemerktRef.current.stumm;
    el.playbackRate = gemerktRef.current.tempo;

    const beiMeta = () => {
      if (el.readyState < 1) return;
      setGesamt(Number.isFinite(el.duration) ? el.duration : dauerS || 0);
      setHochkant(el.videoHeight > el.videoWidth);
      // Nur einmal an die gemerkte Stelle springen, nicht bei jedem
      // erneuten Puffern - sonst zieht es den Nutzer beim Spulen zurueck.
      const start = wiederaufnahme.current ?? sprungSekunde ?? startSekunde;
      if (!gesprungen.current && Number.isFinite(start) && start > 0) {
        el.currentTime = Number.isFinite(el.duration) ? Math.min(start, el.duration) : start;
        setZeit(el.currentTime);
      }
      gesprungen.current = true;
      wiederaufnahme.current = null;
      for (let i = 0; i < el.textTracks.length; i++) el.textTracks[i].mode = i === spurRef.current ? "showing" : "disabled";
      // preload=metadata darf den Start nicht von einem canplay abhängig
      // machen, das manche Browser erst nach play() auslösen.
      if (quellenwechsel.current && sollLaufen.current) void el.play().catch(() => {});
    };
    const beiZeit = () => {
      if (!zieht.current) setZeit(el.currentTime);
    };
    const beiPuffer = () => setGepuffert(bereiche(el.buffered));
    const an = () => { setLaeuft(true); if (!quellenwechsel.current) sollLaufen.current = true; };
    const aus = () => { setLaeuft(false); if (!quellenwechsel.current) sollLaufen.current = false; };
    const warten = () => setWartet(true);
    const weiter = () => setWartet(false);
    const bereit = () => {
      weiter();
      if (!quellenwechsel.current) return;
      quellenwechsel.current = false;
      if (sollLaufen.current) void el.play().catch(() => { /* Browser kann den ersten manuellen Klick verlangen. */ });
      else el.pause();
    };
    const beiTon = () =>
      setGemerkt((g) => {
        const neu = { ...g, lautstaerke: el.volume, stumm: el.muted };
        gemerktSchreiben(neu);
        return neu;
      });
    const beiTempo = () =>
      setGemerkt((g) => {
        const neu = { ...g, tempo: el.playbackRate };
        gemerktSchreiben(neu);
        return neu;
      });

    el.addEventListener("loadedmetadata", beiMeta);
    el.addEventListener("durationchange", beiMeta);
    el.addEventListener("timeupdate", beiZeit);
    el.addEventListener("progress", beiPuffer);
    el.addEventListener("play", an);
    el.addEventListener("pause", aus);
    el.addEventListener("ended", aus);
    el.addEventListener("waiting", warten);
    el.addEventListener("playing", weiter);
    el.addEventListener("canplay", bereit);
    el.addEventListener("volumechange", beiTon);
    el.addEventListener("ratechange", beiTempo);
    if (el.readyState >= 1) beiMeta();
    setLaeuft(!el.paused);
    return () => {
      el.removeEventListener("loadedmetadata", beiMeta);
      el.removeEventListener("durationchange", beiMeta);
      el.removeEventListener("timeupdate", beiZeit);
      el.removeEventListener("progress", beiPuffer);
      el.removeEventListener("play", an);
      el.removeEventListener("pause", aus);
      el.removeEventListener("ended", aus);
      el.removeEventListener("waiting", warten);
      el.removeEventListener("playing", weiter);
      el.removeEventListener("canplay", bereit);
      el.removeEventListener("volumechange", beiTon);
      el.removeEventListener("ratechange", beiTempo);
    };
    // gemerkt bewusst nicht als Abhaengigkeit: Es wird hier nur als Startwert
    // angewandt, danach fuehrt das Element selbst.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lage, videoId, dauerS, startSekunde, sprungSekunde]);

  // Ein neuer Zeitstempel desselben Videos spult den bestehenden Player.
  // Ein Remount würde dessen aktive Server-Lease zunächst freigeben.
  useEffect(() => {
    const el = videoRef.current;
    if (!el || el.readyState < 1 || sprungSekunde === undefined || !Number.isFinite(sprungSekunde)) return;
    const ziel = Math.max(0, Number.isFinite(el.duration) ? Math.min(sprungSekunde, el.duration) : sprungSekunde);
    el.currentTime = ziel;
    setZeit(ziel);
  }, [lage.art, sprungSekunde]);

  // ---- Lease und Fortschritt --------------------------------------------
  useEffect(() => {
    if (lage.art !== "bereit") return;
    const el = videoRef.current;
    if (!el) return;

    const token = lage.sitzung.token;
    let beendet = false;
    let erneuert = false;
    const erneuern = () => {
      if (erneuert) return;
      erneuert = true;
      wiedergabeMerken();
      setLadeVersuch((n) => n + 1);
    };
    const fortschrittSpeichern = (beimVerlassen = false) => {
      if (quellenwechsel.current || !Number.isFinite(el.currentTime) || el.readyState < 1) return;
      if (adminRef.current) {
        void api.fortschrittMerken(videoId, el.currentTime, el.ended || undefined, beimVerlassen).catch(() => {});
      } else lokalFortschrittMerken(videoId, el.currentTime, el.ended || undefined);
    };
    const herzschlag = () => {
      if (beendet || quellenwechsel.current) return;
      const status = el.paused ? "paused" : el.readyState < 3 ? "buffering" : "playing";
      void wiedergabeMelden(token, el.currentTime, status).catch((e) => {
        // Ein lange angehaltener Hintergrundtab kann seine Server-Lease
        // verlieren. Seine Wiedergabe wird an derselben Position erneuert.
        if (!beendet && e instanceof ApiFehler && e.status === 404) erneuern();
      });
    };
    const starten = () => {
      if (beendet) { erneuern(); return; }
      herzschlag();
    };
    const pausieren = () => {
      fortschrittSpeichern();
      herzschlag();
    };
    const beenden = (beimVerlassen = false) => {
      if (beendet) return;
      beendet = true;
      fortschrittSpeichern(beimVerlassen);
      void wiedergabeBeenden(token).catch(() => { /* Sitzung läuft auch ohne Abschlussmeldung ab. */ });
    };
    const amEnde = () => beenden();
    const beimVerlassen = () => beenden(true);
    const beimZurueckkehren = (e: PageTransitionEvent) => { if (e.persisted) setLadeVersuch((n) => n + 1); };
    const nachSprung = () => { fortschrittSpeichern(); if (beendet) erneuern(); else herzschlag(); };
    const herz = window.setInterval(herzschlag, HERZSCHLAG_MS);
    const merken = window.setInterval(() => { if (!el.paused) fortschrittSpeichern(); }, FORTSCHRITT_MS);
    el.addEventListener("play", starten);
    el.addEventListener("pause", pausieren);
    el.addEventListener("waiting", herzschlag);
    el.addEventListener("playing", herzschlag);
    el.addEventListener("canplay", herzschlag);
    el.addEventListener("seeked", nachSprung);
    el.addEventListener("ended", amEnde);
    window.addEventListener("pagehide", beimVerlassen);
    window.addEventListener("pageshow", beimZurueckkehren);
    herzschlag();
    return () => {
      window.clearInterval(herz);
      window.clearInterval(merken);
      el.removeEventListener("play", starten);
      el.removeEventListener("pause", pausieren);
      el.removeEventListener("waiting", herzschlag);
      el.removeEventListener("playing", herzschlag);
      el.removeEventListener("canplay", herzschlag);
      el.removeEventListener("seeked", nachSprung);
      el.removeEventListener("ended", amEnde);
      window.removeEventListener("pagehide", beimVerlassen);
      window.removeEventListener("pageshow", beimZurueckkehren);
      beenden(true);
    };
  }, [lage, videoId]);

  // ---- Kapitel mitverfolgen ---------------------------------------------
  useEffect(() => {
    if (!aufKapitel || kapitel.length === 0) return;
    let index: number | null = null;
    for (let i = kapitel.length - 1; i >= 0; i--) {
      if (zeit >= kapitel[i].start_s) {
        index = i;
        break;
      }
    }
    aufKapitel(index);
  }, [zeit, kapitel, aufKapitel]);

  // ---- Vollbild ----------------------------------------------------------
  //
  // Drei Ereignisnamen, weil es drei Wege ins Vollbild gibt. Vorher wurde nur
  // "fullscreenchange" beachtet - auf dem iPhone, wo Apples eigener Player
  // aufgeht, kam davon nie etwas an, und das Symbol blieb auf "Vollbild"
  // stehen, obwohl das Video bildfuellend lief.
  useEffect(() => {
    const el = videoRef.current;
    const beim = () => setNativesVollbild(istVollbild(videoRef.current));
    document.addEventListener("fullscreenchange", beim);
    document.addEventListener("webkitfullscreenchange", beim);
    el?.addEventListener("webkitbeginfullscreen", beim);
    el?.addEventListener("webkitendfullscreen", beim);
    return () => {
      document.removeEventListener("fullscreenchange", beim);
      document.removeEventListener("webkitfullscreenchange", beim);
      el?.removeEventListener("webkitbeginfullscreen", beim);
      el?.removeEventListener("webkitendfullscreen", beim);
    };
  }, [lage.art]);

  // Beim Drehen fehlt die von iOS für natives Vollbild verlangte Klick-Geste.
  // App-Vollbild behält das Videoelement und unsere Steuerung unverändert.
  useEffect(() => {
    if (ausrichtung.touch) setAppVollbild(ausrichtung.quer && !minimiert);
  }, [ausrichtung.touch, ausrichtung.quer, minimiert]);

  useEffect(() => {
    if (!minimiert) return;
    setAppVollbild(false);
    setMenue(null);
    if (istVollbild(videoRef.current)) void umschaltenVollbild(huelleRef.current, videoRef.current).catch(() => {});
  }, [minimiert]);

  useEffect(() => {
    if (!appVollbild) return;
    const el = huelleRef.current;
    if (!el) return;
    const vorher = document.activeElement as HTMLElement | null;
    const hintergrund = [...document.querySelectorAll<HTMLElement>(".kopf, .leiste, .mobile-navigation, .watch-information, .watch-neben")];
    const gesperrt = hintergrund.map((h) => h.inert);
    hintergrund.forEach((h) => { h.inert = true; });
    el.focus({ preventScroll: true });
    const fokusHalten = (e: KeyboardEvent) => {
      if (e.key !== "Tab") return;
      const knoepfe = [...el.querySelectorAll<HTMLElement>('button:not(:disabled), [tabindex="0"]')]
        .filter((k) => k.getClientRects().length > 0);
      const erster = knoepfe[0];
      const letzter = knoepfe.at(-1);
      if (e.shiftKey && (document.activeElement === erster || document.activeElement === el)) {
        e.preventDefault(); letzter?.focus();
      } else if (!e.shiftKey && (document.activeElement === letzter || document.activeElement === el)) {
        e.preventDefault(); erster?.focus();
      }
    };
    el.addEventListener("keydown", fokusHalten);
    return () => {
      el.removeEventListener("keydown", fokusHalten);
      hintergrund.forEach((h, i) => { h.inert = gesperrt[i]; });
      if (vorher?.isConnected) vorher.focus({ preventScroll: true });
    };
  }, [appVollbild]);

  // ---- Steuerung ein-/ausblenden ----------------------------------------
  const zeigen = useCallback(() => {
    setSichtbar(true);
    window.clearTimeout(ausblender.current);
    ausblender.current = window.setTimeout(() => {
      if (menue === null && !zieht.current) setSichtbar(false);
    }, AUSBLENDEN_MS);
  }, [menue]);

  useEffect(() => {
    if (!laeuft) {
      setSichtbar(true);
      window.clearTimeout(ausblender.current);
    } else {
      zeigen();
    }
    return () => window.clearTimeout(ausblender.current);
  }, [laeuft, zeigen]);

  // ---- Bedienung ---------------------------------------------------------
  const umschalten = useCallback(() => {
    const el = videoRef.current;
    if (!el || lage.art !== "bereit") return;
    sollLaufen.current = el.paused;
    if (el.paused) void el.play().catch(() => { /* Ein weiterer Klick kann die Wiedergabe starten. */ });
    else el.pause();
  }, [lage.art]);

  const tonUmschalten = useCallback(() => {
    const el = videoRef.current;
    if (!el) return;
    if (el.muted || el.volume === 0) {
      el.muted = false;
      if (el.volume === 0) el.volume = 1;
    } else el.muted = true;
  }, []);

  const springe = useCallback(
    (t: number) => {
      const el = videoRef.current;
      if (!el || el.readyState < 1 || !Number.isFinite(t)) return;
      const ziel = Math.max(0, Math.min(gesamt || el.duration || 0, t));
      el.currentTime = ziel;
      setZeit(ziel);
    },
    [gesamt],
  );

  const vollbildUmschalten = useCallback(() => {
    if (appVollbild) { setAppVollbild(false); return; }
    const weg = wegErmitteln(huelleRef.current, videoRef.current);
    if (!istVollbild(videoRef.current) && (ausrichtung.touch || weg === "webkit-video" || weg === "keiner")) {
      setAppVollbild(true);
      return;
    }
    void umschaltenVollbild(huelleRef.current, videoRef.current).catch(() => {
      setAppVollbild(true);
    });
  }, [appVollbild, ausrichtung.touch]);

  const bildImBild = useCallback(() => {
    const el = videoRef.current;
    if (!el || el.readyState < 1 || !document.pictureInPictureEnabled || typeof el.requestPictureInPicture !== "function") return;
    const wechsel = document.pictureInPictureElement ? document.exitPictureInPicture() : el.requestPictureInPicture();
    void wechsel.catch(() => setHinweis("Bild im Bild ist gerade nicht verfügbar."));
  }, []);

  const spurWaehlen = useCallback((index: number) => {
    const el = videoRef.current;
    if (!el) return;
    for (let i = 0; i < el.textTracks.length; i++) {
      el.textTracks[i].mode = i === index ? "showing" : "disabled";
    }
    setSpur(index);
    setMenue(null);
  }, []);

  const tempoSetzen = useCallback((t: number) => {
    const neu = { ...gemerktRef.current, tempo: t };
    gemerktRef.current = neu;
    setGemerkt(neu);
    gemerktSchreiben(neu);
    const el = videoRef.current;
    if (el) el.playbackRate = t;
    setMenue(null);
  }, []);

  function qualitaetWaehlen(q: WiedergabeQualitaet) {
    if (q === qualitaet && lage.art !== "fehler") return;
    wiedergabeMerken();
    setQualitaet(q);
    // Einen erkannten Decoderfehler für Live-Stufen behalten. Nur eine
    // ausdrückliche Originalwahl probiert die direkte Quelle erneut.
    if (q === "original") setTranskodieren(false);
    if (q === qualitaet) setLadeVersuch((n) => n + 1);
  }
  function liveVersuchen() {
    wiedergabeMerken();
    // Auch eine native numerische Stufe kann außerhalb des Live-Budgets
    // liegen. Auto wählt die tatsächlich verfügbare Umwandlungsstufe.
    setQualitaet("auto");
    setTranskodieren(true);
  }

  // ---- Tastatur ----------------------------------------------------------
  useEffect(() => {
    if (lage.art !== "bereit" || minimiert) return;
    const beim = (e: KeyboardEvent) => {
      const ziel = e.target as HTMLElement | null;
      if (e.defaultPrevented || e.ctrlKey || e.metaKey || e.altKey || document.querySelector("dialog[open]") || huelleRef.current?.closest("[inert]") || ziel?.closest("input, textarea, select, [contenteditable]:not([contenteditable=false]), [role=textbox]"))
        return;
      if (ziel?.closest('[role="menu"]')) return;
      // Navigation und andere Formulare behalten ihre eigene Tastaturbedienung.
      if (ziel?.closest("button, a, [role=slider]") && !huelleRef.current?.contains(ziel)) return;
      // Leertaste auf einem fokussierten Knopf gehört dem Knopf. Die übrigen
      // Player-Kürzel bleiben auch nach einem Klick auf die Steuerung aktiv.
      if ((e.key === " " || e.key === "Enter") && ziel?.closest("button, a")) return;
      const el = videoRef.current;
      if (!el) return;
      zeigen();

      // Die Belegung, die YouTube-Nutzer im Muskelgedaechtnis haben.
      switch (e.key.toLowerCase()) {
        case "escape":
          if (menue !== null) setMenue(null);
          else setAppVollbild(false);
          break;
        case " ":
        case "k":
          e.preventDefault();
          umschalten();
          break;
        case "j":
          springe(el.currentTime - 10);
          break;
        case "l":
          springe(el.currentTime + 10);
          break;
        case "arrowleft":
          e.preventDefault();
          springe(el.currentTime - 5);
          break;
        case "arrowright":
          e.preventDefault();
          springe(el.currentTime + 5);
          break;
        case "arrowup":
          e.preventDefault();
          el.volume = Math.min(1, el.volume + 0.05);
          break;
        case "arrowdown":
          e.preventDefault();
          el.volume = Math.max(0, el.volume - 0.05);
          break;
        case "m":
          tonUmschalten();
          break;
        case "f":
          vollbildUmschalten();
          break;
        case "t":
          aufTheater?.(!theater);
          break;
        case "c":
          spurWaehlen(spur >= 0 ? -1 : untertitel.length > 0 ? 0 : -1);
          break;
        case "i":
          bildImBild();
          break;
        case "<":
        case ",":
          el.playbackRate = Math.max(0.25, el.playbackRate - 0.25);
          break;
        case ">":
        case ".":
          el.playbackRate = Math.min(3, el.playbackRate + 0.25);
          break;
        case "home":
          e.preventDefault();
          springe(0);
          break;
        case "end":
          e.preventDefault();
          springe(gesamt);
          break;
        default:
          if (/^[0-9]$/.test(e.key) && gesamt) springe((Number(e.key) / 10) * gesamt);
      }
    };
    window.addEventListener("keydown", beim);
    return () => window.removeEventListener("keydown", beim);
  }, [
    lage.art, gesamt, umschalten, springe, vollbildUmschalten, aufTheater, theater,
    spurWaehlen, spur, untertitel.length, bildImBild, zeigen, tonUmschalten, menue, minimiert,
  ]);

  // ---- Leiste: Zeiger und Ziehen ----------------------------------------
  const zeitAmZeiger = (clientX: number) => {
    const r = leisteRef.current?.getBoundingClientRect();
    if (!r || r.width === 0) return { x: 0, zeit: 0 };
    const anteil = Math.min(1, Math.max(0, (clientX - r.left) / r.width));
    return { x: anteil * r.width, zeit: anteil * gesamt };
  };

  const kapitelBei = (t: number): Kapitel | null => {
    let gefunden: Kapitel | null = null;
    for (const k of kapitel) if (t >= k.start_s) gefunden = k;
    return gefunden;
  };

  // ---- Darstellung -------------------------------------------------------
  const gespielt = gesamt > 0 ? Math.max(0, Math.min(100, (zeit / gesamt) * 100)) : 0;
  const steuerungSichtbar = minimiert || lage.art !== "bereit" || sichtbar || !laeuft || menue !== null;
  const zeigerKapitel = zeiger ? kapitelBei(zeiger.zeit) : null;

  return (
    <div
      ref={huelleRef}
      className="buehne player"
      data-hochkant={hochkant}
      data-vollbild={vollbild}
      data-app-vollbild={appVollbild}
      data-mini={minimiert}
      data-theater={theater}
      data-steuerung={steuerungSichtbar}
      data-laeuft={laeuft}
      data-status={lage.art}
      tabIndex={-1}
      onPointerMove={(e) => { if (e.pointerType === "mouse") zeigen(); }}
      onFocus={zeigen}
      onPointerLeave={(e) => {
        if (e.pointerType === "mouse" && laeuft && menue === null && !zieht.current) setSichtbar(false);
      }}
    >
      <video
        ref={videoRef}
        poster={poster}
        aria-label={titel ?? "Video"}
        playsInline
        preload="metadata"
        data-modus={lage.art === "bereit" ? lage.modus : undefined}
        onPointerDown={(e) => {
          setTouchBedienung(e.pointerType !== "mouse");
          gewischt.current = false;
          wischStart.current = e.pointerType === "touch" ? { x: e.clientX, y: e.clientY } : null;
        }}
        onPointerUp={(e) => {
          const start = wischStart.current;
          wischStart.current = null;
          if (!start || minimiert || e.clientY - start.y < 70 || Math.abs(e.clientX - start.x) > 60) return;
          gewischt.current = true;
          if (vollbild) vollbildUmschalten();
          else aufMinimieren?.();
        }}
        onPointerCancel={() => { wischStart.current = null; }}
        onClick={() => {
          if (gewischt.current) { gewischt.current = false; return; }
          if (minimiert) { aufVergroessern?.(); return; }
          if (!touchBedienung) { umschalten(); return; }
          if (!sichtbar) zeigen();
          else if (laeuft) setSichtbar(false);
        }}
        onDoubleClick={() => { if (!touchBedienung) vollbildUmschalten(); }}
        onError={(e) => {
          const fehler = e.currentTarget.error;
          if (lage.art === "bereit" && fehler && fehler.code !== MediaError.MEDIA_ERR_ABORTED) {
            wiedergabeMerken();
            if (lage.modus === "direct" && !transkodieren) liveVersuchen();
            else setLage({ art: "fehler", text: "Das Video konnte nicht abgespielt werden. Bitte versuche es erneut." });
          }
        }}
      >
        {untertitel.map((u) => (
          <track
            key={`${u.sprache}-${u.automatisch}`}
            kind="subtitles"
            srcLang={u.sprache}
            label={u.automatisch ? `${u.sprache} (automatisch)` : u.sprache}
            src={untertitelUrl(videoId, u.sprache)}
          />
        ))}
      </video>

      <div className="player-kopf" onClick={(e) => e.stopPropagation()}>
        {minimiert ? <>
          <button className="steuer-knopf" onClick={aufVergroessern} aria-label="Video vergrößern" title="Video vergrößern"><Icon name="expand" /></button>
          <span className="player-kopf-titel">{titel}</span>
          <button className="steuer-knopf" onClick={aufSchliessen} aria-label="Video schließen" title="Video schließen"><Icon name="close" /></button>
        </> : <>
          {aufMinimieren ? <button className="steuer-knopf" onClick={aufMinimieren} aria-label="Video minimieren" title="Weitersehen und stöbern"><Icon name="chevronDown" /></button> : null}
          <span className="player-kopf-titel">{vollbild ? titel : ""}</span>
          <PlayerEinstellungen offen={menue === "einstellungen"} aufOffen={(offen) => setMenue(offen ? "einstellungen" : null)}
            bereich={huelleRef} vollbild={vollbild} touch={ausrichtung.touch} angebote={angebote} qualitaet={qualitaet}
            bezeichnung={lage.art === "bereit" ? lage.sitzung.quality_label || "Automatisch" : angebote.find((q) => q.value === qualitaet)?.label || "Automatisch"}
            aufQualitaet={qualitaetWaehlen} tempo={gemerkt.tempo} aufTempo={tempoSetzen} />
        </>}
      </div>

      {lage.art === "pruefen" ? <div className="buehne-meldung player-status" role="status">Video wird geöffnet …</div> : null}
      {lage.art === "fehler" ? <div className="buehne-meldung player-status" role="alert">
        <Icon name="info" className="player-meldung-icon" size={36} />
        <h2>Wiedergabe nicht möglich</h2><p>{lage.text}</p>
        <button className="knopf player-erneut" onClick={() => { wiedergabeMerken(); setLadeVersuch((n) => n + 1); }}>Erneut versuchen</button>
        {!transkodieren ? <button className="knopf player-erneut" onClick={liveVersuchen}>Mit Live-Transkodierung versuchen</button> : null}
      </div> : null}
      {lage.art === "bereit" && wartet && laeuft ? <div className="player-lader" aria-hidden="true" /> : null}
      {hinweis ? <div className="player-hinweis" role="status">{hinweis}</div> : null}

      {lage.art === "bereit" && (minimiert || !laeuft || ((touchBedienung || ausrichtung.touch) && steuerungSichtbar && menue === null)) ? (
        <button className="player-gross" onClick={() => { umschalten(); zeigen(); }} aria-label={laeuft ? "Pause" : "Abspielen"}>
          <svg viewBox="0 0 24 24" width="34" height="34" aria-hidden="true">
            <path d={laeuft ? "M6 5h4v14H6zm8 0h4v14h-4z" : "M8 5v14l11-7z"} fill="currentColor" />
          </svg>
        </button>
      ) : null}

      <div className="player-steuerung" onClick={(e) => e.stopPropagation()}>
        {/* ---- Zeitleiste */}
        <div
          ref={leisteRef}
          className="zeitleiste"
          role="slider"
          tabIndex={lage.art === "bereit" && gesamt > 0 ? 0 : -1}
          aria-label="Position"
          aria-disabled={lage.art !== "bereit" || gesamt <= 0}
          aria-valuetext={`${dauer(zeit)} von ${dauer(gesamt)}`}
          aria-valuemin={0}
          aria-valuemax={Math.round(gesamt)}
          aria-valuenow={Math.round(zeit)}
          onKeyDown={(e) => {
            let ziel: number;
            if (e.key === "ArrowLeft" || e.key === "ArrowDown") ziel = zeit - 5;
            else if (e.key === "ArrowRight" || e.key === "ArrowUp") ziel = zeit + 5;
            else if (e.key === "Home") ziel = 0;
            else if (e.key === "End") ziel = gesamt;
            else return;
            e.preventDefault();
            e.stopPropagation();
            springe(ziel);
          }}
          onPointerDown={(e) => {
            if (lage.art !== "bereit" || gesamt <= 0 || e.button !== 0) return;
            e.preventDefault();
            zieht.current = true;
            zeigen();
            (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
            springe(zeitAmZeiger(e.clientX).zeit);
          }}
          onPointerMove={(e) => {
            const p = zeitAmZeiger(e.clientX);
            setZeiger(p);
            if (zieht.current) springe(p.zeit);
          }}
          onPointerUp={(e) => {
            if (!zieht.current) return;
            springe(zeitAmZeiger(e.clientX).zeit);
            zieht.current = false;
            if (e.currentTarget.hasPointerCapture(e.pointerId)) e.currentTarget.releasePointerCapture(e.pointerId);
            if (e.pointerType !== "mouse") setZeiger(null);
            zeigen();
          }}
          onPointerCancel={() => { zieht.current = false; setZeiger(null); }}
          onLostPointerCapture={() => { zieht.current = false; }}
          onPointerLeave={() => {
            if (!zieht.current) setZeiger(null);
          }}
        >
          <div className="leiste-spur">
            {gesamt > 0
              ? gepuffert.map(([a, b], i) => (
                  <div
                    key={i}
                    className="leiste-puffer"
                    style={{ left: `${(a / gesamt) * 100}%`, width: `${((b - a) / gesamt) * 100}%` }}
                  />
                ))
              : null}
            <div className="leiste-gespielt" style={{ width: `${gespielt}%` }} />
            {/* Kapitelgrenzen als kleine Luecken - so, wie man es von YouTube kennt. */}
            {gesamt > 0
              ? kapitel.slice(1).map((k, i) => (
                  <div key={i} className="leiste-trenner" style={{ left: `${(k.start_s / gesamt) * 100}%` }} />
                ))
              : null}
          </div>
          <div className="leiste-griff" style={{ left: `${gespielt}%` }} />
          {zeiger ? (
            <div className="leiste-tipp" style={{ left: `clamp(62px, ${zeiger.x}px, calc(100% - 62px))` }}>
              {zeigerKapitel ? <div className="leiste-tipp-kapitel">{zeigerKapitel.titel}</div> : null}
              <div>{dauer(zeiger.zeit)}</div>
            </div>
          ) : null}
        </div>

        {/* ---- Knopfzeile */}
        <div className="steuer-zeile">
          <button className="steuer-knopf" disabled={lage.art !== "bereit"} onClick={umschalten} aria-label={laeuft ? "Pause" : "Abspielen"} title={laeuft ? "Pause (k)" : "Abspielen (k)"}>
            {laeuft ? (
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <path d="M6 5h4v14H6zm8 0h4v14h-4z" fill="currentColor" />
              </svg>
            ) : (
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <path d="M8 5v14l11-7z" fill="currentColor" />
              </svg>
            )}
          </button>

          <div className="steuer-ton">
            <button
              className="steuer-knopf"
              disabled={lage.art !== "bereit"}
              onClick={tonUmschalten}
              aria-label={gemerkt.stumm || gemerkt.lautstaerke === 0 ? "Ton an" : "Stumm"}
              title={gemerkt.stumm || gemerkt.lautstaerke === 0 ? "Ton an (m)" : "Stumm (m)"}
            >
              {gemerkt.stumm || gemerkt.lautstaerke === 0 ? (
                <svg viewBox="0 0 24 24" aria-hidden="true">
                  <path d="M16.5 12A4.5 4.5 0 0 0 14 8v2.2l2.5 2.5zM19 12c0 .9-.2 1.8-.5 2.6l1.5 1.5A8.8 8.8 0 0 0 21 12a9 9 0 0 0-7-8.8v2.1A7 7 0 0 1 19 12zM4.3 3 3 4.3 7.7 9H3v6h4l5 5v-6.7l4.3 4.3a7 7 0 0 1-2.3 1.2v2.1a9 9 0 0 0 3.7-1.8l2 2 1.3-1.3zM12 4 9.9 6.1 12 8.2z" fill="currentColor" />
                </svg>
              ) : (
                <svg viewBox="0 0 24 24" aria-hidden="true">
                  <path d="M3 9v6h4l5 5V4L7 9zm13.5 3A4.5 4.5 0 0 0 14 8v8a4.5 4.5 0 0 0 2.5-4zM14 3.2v2.1a7 7 0 0 1 0 13.4v2.1a9 9 0 0 0 0-17.6z" fill="currentColor" />
                </svg>
              )}
            </button>
            <input
              className="steuer-regler"
              type="range"
              disabled={lage.art !== "bereit"}
              min={0}
              max={1}
              step={0.02}
              value={gemerkt.stumm ? 0 : gemerkt.lautstaerke}
              aria-label="Lautstärke"
              onChange={(e) => {
                const el = videoRef.current;
                if (!el) return;
                el.volume = Number(e.target.value);
                el.muted = el.volume === 0;
              }}
            />
          </div>

          <span className="steuer-zeit">
            {dauer(zeit)} <span className="steuer-zeit-trenner">/</span> {dauer(gesamt)}
          </span>

          <div className="steuer-luecke" />

          {/* Untertitel */}
          {untertitel.length > 0 ? <PlayerUntertitel offen={menue === "untertitel"}
            aufOffen={(offen) => setMenue(offen ? "untertitel" : null)} bereich={huelleRef} vollbild={vollbild} touch={ausrichtung.touch}
            untertitel={untertitel} spur={spur} aufSpur={spurWaehlen} /> : null}

          {/* Bild-im-Bild */}
          {typeof document !== "undefined" && document.pictureInPictureEnabled ? (
            <button className="steuer-knopf steuer-pip" disabled={lage.art !== "bereit"} onClick={bildImBild} aria-label="Bild im Bild" title="Bild im Bild (i)">
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <path d="M19 11h-8v6h8zm4 8V4.9A2 2 0 0 0 21 3H3a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h18a2 2 0 0 0 2-2zm-2 0H3V5h18z" fill="currentColor" />
              </svg>
            </button>
          ) : null}

          {/* Theater */}
          {aufTheater && !vollbild ? (
            <button
              className="steuer-knopf steuer-theater"
              onClick={() => aufTheater(!theater)}
              aria-label={theater ? "Normale Ansicht" : "Kinomodus"}
              title={theater ? "Normale Ansicht (t)" : "Kinomodus (t)"}
            >
              {theater ? (
                <svg viewBox="0 0 24 24" aria-hidden="true">
                  <path d="M3 4h18v16H3zm2 2v12h14V6z" fill="currentColor" />
                </svg>
              ) : (
                <svg viewBox="0 0 24 24" aria-hidden="true">
                  <path d="M2 6h20v12H2zm2 2v8h16V8z" fill="currentColor" />
                </svg>
              )}
            </button>
          ) : null}

          {/* Auf Touch-Geräten bleiben unsere Bedienelemente im App-Vollbild. */}
          <button
            className="steuer-knopf"
            onClick={vollbildUmschalten}
            aria-label={vollbild ? "Vollbild beenden" : "Vollbild"}
            title={vollbild ? "Vollbild beenden (f)" : "Vollbild (f)"}
          >
            {vollbild ? (
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <path d="M5 16h3v3h2v-5H5zm3-8H5v2h5V5H8zm6 11h2v-3h3v-2h-5zm2-11V5h-2v5h5V8z" fill="currentColor" />
              </svg>
            ) : (
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <path d="M7 14H5v5h5v-2H7zm-2-4h2V7h3V5H5zm12 7h-3v2h5v-5h-2zM14 5v2h3v3h2V5z" fill="currentColor" />
              </svg>
            )}
          </button>
        </div>
      </div>
    </div>
  );
}
