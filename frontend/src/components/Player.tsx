import { useEffect, useRef, useState } from "react";

import type { Kapitel } from "../lib/api";
import { api, streamUrl, untertitelUrl } from "../lib/api";
import { prozent } from "../lib/format";

/**
 * Der Player.
 *
 * Die Besonderheit gegenueber einem gewoehnlichen <video>-Element: Die Quelle
 * ist nicht immer sofort da. Kann der Browser den Archivcodec, liefert der
 * Server die Bytes direkt aus dem Buendel und es geht ohne Umweg los. Sonst
 * antwortet er mit 202 und bereitet im Hintergrund eine Heisskopie vor - dann
 * zeigen wir den Fortschritt, statt den Nutzer vor einem schwarzen Bild sitzen
 * zu lassen.
 *
 * Deshalb wird die Quelle vorher angetastet, statt sie dem Element einfach
 * hinzuwerfen: Ein <video>, das eine 202-Antwort mit JSON bekommt, meldet nur
 * "Format nicht unterstuetzt" - und niemand wuesste, warum.
 */

type Lage =
  | { art: "pruefen" }
  | { art: "bereit"; quelle: string; modus: string | null }
  | { art: "vorbereitung"; grund: string; anteil: number }
  | { art: "fehler"; text: string };

interface Props {
  videoId: string;
  startSekunde?: number;
  dauerS?: number | null;
  kapitel?: Kapitel[];
  untertitel?: { sprache: string; automatisch: boolean }[];
  aufKapitel?: (index: number | null) => void;
}

/** Wie oft der Player meldet, dass noch geschaut wird. Deutlich haeufiger als
 *  die serverseitige Lease lang ist, damit ein verlorener Herzschlag nicht
 *  gleich zum Abraeumen fuehrt. */
const HERZSCHLAG_MS = 30_000;
const FORTSCHRITT_MS = 5_000;

export function Player({
  videoId,
  startSekunde = 0,
  dauerS,
  kapitel = [],
  untertitel = [],
  aufKapitel,
}: Props) {
  const [lage, setLage] = useState<Lage>({ art: "pruefen" });
  const videoRef = useRef<HTMLVideoElement>(null);
  const gesprungen = useRef(false);

  // ---- Quelle beschaffen -------------------------------------------------
  useEffect(() => {
    let abgebrochen = false;
    let versuch = 0;
    gesprungen.current = false;
    setLage({ art: "pruefen" });

    async function antasten(): Promise<void> {
      try {
        // Nur das erste Byte anfordern: Das reicht, um zu erfahren, ob
        // ausgeliefert wird oder erst vorbereitet werden muss - und laedt
        // nicht versehentlich das ganze Video.
        const antwort = await fetch(streamUrl(videoId), { headers: { Range: "bytes=0-0" } });
        if (abgebrochen) return;

        if (antwort.status === 202) {
          const koerper = await antwort.json();
          setLage({
            art: "vorbereitung",
            grund: koerper.grund ?? "wird vorbereitet",
            anteil: koerper.fortschritt ?? 0,
          });
          // Anfangs haeufiger nachfragen, dann ruhiger werden.
          const wartezeit = Math.min(5000, 1000 + versuch * 500);
          versuch += 1;
          window.setTimeout(() => void antasten(), wartezeit);
          return;
        }

        if (!antwort.ok && antwort.status !== 206) {
          const koerper = await antwort.json().catch(() => ({}));
          setLage({ art: "fehler", text: koerper.detail ?? `Fehler ${antwort.status}` });
          return;
        }

        setLage({
          art: "bereit",
          quelle: streamUrl(videoId),
          modus: antwort.headers.get("X-Wiedergabe-Modus"),
        });
      } catch (e) {
        if (!abgebrochen) {
          setLage({ art: "fehler", text: e instanceof Error ? e.message : String(e) });
        }
      }
    }

    void antasten();
    return () => {
      abgebrochen = true;
    };
  }, [videoId]);

  // ---- Lease und Fortschritt --------------------------------------------
  useEffect(() => {
    if (lage.art !== "bereit") return;
    const el = videoRef.current;
    if (!el) return;

    let herz: number | undefined;
    let merken: number | undefined;

    const starten = () => {
      if (herz === undefined) {
        void api.herzschlag(videoId);
        herz = window.setInterval(() => void api.herzschlag(videoId), HERZSCHLAG_MS);
      }
      if (merken === undefined) {
        merken = window.setInterval(() => {
          if (el.currentTime > 0) void api.fortschrittMerken(videoId, el.currentTime);
        }, FORTSCHRITT_MS);
      }
    };

    const anhalten = () => {
      if (herz !== undefined) window.clearInterval(herz);
      if (merken !== undefined) window.clearInterval(merken);
      herz = undefined;
      merken = undefined;
    };

    const beenden = () => {
      anhalten();
      if (el.currentTime > 0) void api.fortschrittMerken(videoId, el.currentTime);
      void api.wiedergabeBeendet(videoId);
    };

    el.addEventListener("play", starten);
    el.addEventListener("pause", anhalten);
    el.addEventListener("ended", beenden);
    // Beim Schliessen des Tabs zaehlt nur noch, was mit keepalive rausgeht.
    window.addEventListener("pagehide", beenden);

    return () => {
      el.removeEventListener("play", starten);
      el.removeEventListener("pause", anhalten);
      el.removeEventListener("ended", beenden);
      window.removeEventListener("pagehide", beenden);
      beenden();
    };
  }, [lage.art, videoId]);

  // ---- Kapitel mitverfolgen ---------------------------------------------
  useEffect(() => {
    if (lage.art !== "bereit" || !aufKapitel || kapitel.length === 0) return;
    const el = videoRef.current;
    if (!el) return;

    const beobachten = () => {
      const t = el.currentTime;
      let index: number | null = null;
      for (let i = kapitel.length - 1; i >= 0; i--) {
        if (t >= kapitel[i].start_s) {
          index = i;
          break;
        }
      }
      aufKapitel(index);
    };
    el.addEventListener("timeupdate", beobachten);
    return () => el.removeEventListener("timeupdate", beobachten);
  }, [lage.art, kapitel, aufKapitel]);

  // ---- Tastatur ----------------------------------------------------------
  useEffect(() => {
    if (lage.art !== "bereit") return;
    const beim = (e: KeyboardEvent) => {
      const ziel = e.target as HTMLElement | null;
      // In Eingabefeldern hat der Player nichts zu melden.
      if (ziel && (ziel.tagName === "INPUT" || ziel.tagName === "TEXTAREA" || ziel.isContentEditable))
        return;
      const el = videoRef.current;
      if (!el) return;

      // Die Belegung, die YouTube-Nutzer im Muskelgedaechtnis haben.
      switch (e.key.toLowerCase()) {
        case " ":
        case "k":
          e.preventDefault();
          el.paused ? void el.play() : el.pause();
          break;
        case "j":
          el.currentTime = Math.max(0, el.currentTime - 10);
          break;
        case "l":
          el.currentTime += 10;
          break;
        case "arrowleft":
          el.currentTime = Math.max(0, el.currentTime - 5);
          break;
        case "arrowright":
          el.currentTime += 5;
          break;
        case "m":
          el.muted = !el.muted;
          break;
        case "f":
          if (document.fullscreenElement) void document.exitFullscreen();
          else void el.requestFullscreen?.();
          break;
        default:
          if (/^[0-9]$/.test(e.key) && dauerS) {
            el.currentTime = (Number(e.key) / 10) * dauerS;
          }
      }
    };
    window.addEventListener("keydown", beim);
    return () => window.removeEventListener("keydown", beim);
  }, [lage.art, dauerS]);

  // ---- Darstellung -------------------------------------------------------
  if (lage.art === "pruefen") {
    return (
      <div className="buehne">
        <div style={{ color: "var(--text-gedaempft)", fontSize: 13 }}>wird geöffnet …</div>
      </div>
    );
  }

  if (lage.art === "vorbereitung") {
    return (
      <div className="buehne">
        <div style={{ textAlign: "center", padding: 24, maxWidth: 440 }}>
          <div style={{ fontSize: 30, marginBottom: 12 }}>📦</div>
          <div style={{ fontWeight: 500, marginBottom: 6 }}>Video wird vorbereitet</div>
          <div style={{ color: "var(--text-gedaempft)", fontSize: 13, marginBottom: 16 }}>
            {lage.grund}. Das passiert nur beim ersten Abspielen auf diesem Gerät.
          </div>
          <div className="balken" style={{ margin: "0 auto", maxWidth: 240 }}>
            <span style={{ width: `${Math.max(4, lage.anteil * 100)}%` }} />
          </div>
          <div style={{ color: "var(--text-schwach)", fontSize: 12, marginTop: 8 }}>
            {prozent(lage.anteil)}
          </div>
        </div>
      </div>
    );
  }

  if (lage.art === "fehler") {
    return (
      <div className="buehne">
        <div style={{ textAlign: "center", padding: 24, maxWidth: 420 }}>
          <div style={{ fontSize: 30, marginBottom: 12 }}>⚠</div>
          <div style={{ fontWeight: 500, marginBottom: 6 }}>Wiedergabe nicht möglich</div>
          <div style={{ color: "var(--text-gedaempft)", fontSize: 13 }}>{lage.text}</div>
        </div>
      </div>
    );
  }

  return (
    <div className="buehne">
      <video
        ref={videoRef}
        src={lage.quelle}
        controls
        autoPlay
        playsInline
        preload="metadata"
        data-modus={lage.modus ?? undefined}
        onLoadedMetadata={(e) => {
          // Nur einmal an die gemerkte Stelle springen, nicht bei jedem
          // erneuten Puffern - sonst zieht es den Nutzer beim Spulen zurueck.
          if (!gesprungen.current && startSekunde > 1) {
            e.currentTarget.currentTime = startSekunde;
          }
          gesprungen.current = true;
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
    </div>
  );
}
