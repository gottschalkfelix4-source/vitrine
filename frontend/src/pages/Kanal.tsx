import { useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";

import { Fehler, Gitter, Leer, Skelettgitter, Videokachel } from "../components/ui";
import { useApi } from "../hooks/useApi";
import type { Sammlung } from "../lib/api";
import { api, thumbUrl } from "../lib/api";
import { bytes, datum } from "../lib/format";

/** Reihenfolge der Tabs wie bei YouTube: Videos, Shorts, Livestreams, Playlists. */
const ART_TEXT: Record<Sammlung["art"], string> = {
  uploads: "Videos",
  shorts: "Shorts",
  live: "Livestreams",
  playlist: "Playlist",
};

export function Kanalseite() {
  const { kanalId = "" } = useParams();
  const [suchparameter, setSuchparameter] = useSearchParams();
  const tab = suchparameter.get("tab") ?? "videos";
  const [abgleichLaeuft, setAbgleichLaeuft] = useState(false);

  const kanal = useApi(() => api.kanal(kanalId), [kanalId]);
  const videos = useApi(
    () => api.videos({ kanal: kanalId, limit: 90, nur_archiviert: false }),
    [kanalId],
  );

  if (kanal.fehler) return <Fehler text={kanal.fehler} erneut={kanal.neuLaden} />;
  if (!kanal.daten) return <Skelettgitter anzahl={8} />;

  const d = kanal.daten;
  const playlists = d.sammlungen.filter((s) => s.art === "playlist");
  const alleVideos = videos.daten ?? [];
  // Aus der einen Videoliste die Tabs bedienen, statt drei Anfragen zu stellen.
  const gefiltert = alleVideos.filter((v) =>
    tab === "shorts" ? v.ist_short : tab === "live" ? v.war_live : !v.ist_short && !v.war_live,
  );

  async function abgleichen() {
    setAbgleichLaeuft(true);
    try {
      await api.kanalAbgleichen(kanalId, true);
    } finally {
      setAbgleichLaeuft(false);
    }
  }

  return (
    <>
      <div className="kanal-kopf">
        {thumbUrl(d.banner) ? <img className="banner" src={thumbUrl(d.banner)!} alt="" /> : null}
        <div className="kanal-zeile">
          {thumbUrl(d.kanal.avatar) ? (
            <img className="avatar-gross" src={thumbUrl(d.kanal.avatar)!} alt="" />
          ) : (
            <span className="avatar-gross" />
          )}
          <div style={{ minWidth: 0 }}>
            <h1>{d.kanal.name}</h1>
            <div style={{ color: "var(--text-gedaempft)", fontSize: 13.5 }}>
              {d.kanal.handle ? <span>{d.kanal.handle} · </span> : null}
              <span>
                {d.kanal.videos_archiviert} von {d.kanal.videos_gesamt} archiviert
              </span>
              {d.kanal.belegung_bytes > 0 ? <span> · {bytes(d.kanal.belegung_bytes)}</span> : null}
            </div>
            {d.kanal.zuletzt_abgeglichen ? (
              <div style={{ color: "var(--text-schwach)", fontSize: 12.5, marginTop: 2 }}>
                zuletzt abgeglichen {datum(d.kanal.zuletzt_abgeglichen)}
              </div>
            ) : null}
          </div>
          <div className="aktionen">
            <button className="knopf" onClick={abgleichen} disabled={abgleichLaeuft}>
              {abgleichLaeuft ? "wird eingereiht …" : "Jetzt abgleichen"}
            </button>
          </div>
        </div>
        {d.beschreibung ? (
          <div className="beschreibung" data-zu="true" style={{ marginTop: 16 }}>
            {d.beschreibung}
          </div>
        ) : null}
      </div>

      <div className="tabs">
        {[
          { wert: "videos", text: "Videos", zahl: alleVideos.filter((v) => !v.ist_short && !v.war_live).length },
          { wert: "shorts", text: "Shorts", zahl: alleVideos.filter((v) => v.ist_short).length },
          { wert: "live", text: "Livestreams", zahl: alleVideos.filter((v) => v.war_live).length },
          { wert: "playlists", text: "Playlists", zahl: playlists.length },
        ]
          // Leere Tabs gar nicht erst zeigen - nicht jeder Kanal hat Shorts.
          .filter((t) => t.zahl > 0 || t.wert === "videos")
          .map((t) => (
            <button
              key={t.wert}
              className="tab"
              data-aktiv={tab === t.wert}
              onClick={() => setSuchparameter(t.wert === "videos" ? {} : { tab: t.wert })}
            >
              {t.text}
              <span className="zahl">{t.zahl}</span>
            </button>
          ))}
      </div>

      {tab === "playlists" ? (
        playlists.length > 0 ? (
          <Gitter>
            {playlists.map((p) => (
              <Link key={p.id} className="kachel" to={`/playlist/${p.id}`}>
                <div className="kachel-bild">
                  {thumbUrl(p.thumb) ? (
                    <img src={thumbUrl(p.thumb)!} alt="" loading="lazy" />
                  ) : (
                    <div className="platzhalter">☰</div>
                  )}
                  <span className="dauer">{p.anzahl} Videos</span>
                </div>
                <div className="kachel-text">
                  <div style={{ minWidth: 0 }}>
                    <h3 className="kachel-titel">{p.titel}</h3>
                    <div className="kachel-zeile">{ART_TEXT[p.art]}</div>
                  </div>
                </div>
              </Link>
            ))}
          </Gitter>
        ) : (
          <Leer zeichen="☰" titel="Keine Playlists" text="Dieser Kanal führt keine öffentlichen Playlists." />
        )
      ) : videos.laedt && !videos.daten ? (
        <Skelettgitter />
      ) : gefiltert.length > 0 ? (
        <Gitter>
          {gefiltert.map((v) => (
            <Videokachel key={v.id} video={v} ohneKanal />
          ))}
        </Gitter>
      ) : (
        <Leer
          titel="Hier ist noch nichts"
          text="Sobald der Abgleich durch ist, erscheinen die Videos dieses Kanals hier."
        />
      )}
    </>
  );
}
