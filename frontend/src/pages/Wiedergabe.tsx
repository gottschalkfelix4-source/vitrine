import { useCallback, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";

import { Player } from "../components/Player";
import { Fehler, Gitter, Skelettgitter, Videokachel, Zustand } from "../components/ui";
import { useApi } from "../hooks/useApi";
import { api } from "../lib/api";
import { aufrufe, bytes, dauer, datum, prozent } from "../lib/format";

export function Wiedergabeseite() {
  const { videoId = "" } = useParams();
  const [parameter] = useSearchParams();
  // Sprungziel aus der Untertitelsuche. Ist keines gesetzt, wird der gemerkte
  // Fortschritt benutzt.
  const sprungziel = Number(parameter.get("t"));
  const [beschreibungOffen, setBeschreibungOffen] = useState(false);
  const [aktivesKapitel, setAktivesKapitel] = useState<number | null>(null);
  const [technikOffen, setTechnikOffen] = useState(false);
  // Kinomodus: Player ueber die volle Breite, die Seitenspalte rutscht darunter.
  const [theater, setTheater] = useState(false);
  const navigate = useNavigate();
  // Entfernen in zwei Schritten - ein einzelner Klick, der Dateien loescht,
  // waere zu leicht danebengegangen.
  const [entfernenNachfrage, setEntfernenNachfrage] = useState(false);
  const [entfernenLaeuft, setEntfernenLaeuft] = useState(false);

  const detail = useApi(() => api.video(videoId), [videoId]);
  const weitere = useApi(
    () =>
      detail.daten?.video.kanal_id
        ? api.videos({ kanal: detail.daten.video.kanal_id, limit: 16 })
        : Promise.resolve([]),
    [detail.daten?.video.kanal_id],
  );

  const beiKapitel = useCallback((i: number | null) => setAktivesKapitel(i), []);

  if (detail.fehler) return <Fehler text={detail.fehler} erneut={detail.neuLaden} />;
  if (!detail.daten) return <Skelettgitter anzahl={4} />;

  const { video: v, technik, kapitel, untertitel } = detail.daten;
  const ersparnis =
    technik.quelle_bytes && technik.buendel_bytes
      ? 1 - technik.buendel_bytes / technik.quelle_bytes
      : null;

  function springe(sekunde: number) {
    const el = document.querySelector<HTMLVideoElement>(".buehne video");
    if (el) {
      el.currentTime = sekunde;
      void el.play();
    }
  }

  return (
    <div className="watch" data-theater={theater}>
      <div>
        <Player
          videoId={videoId}
          startSekunde={Number.isFinite(sprungziel) && sprungziel > 0 ? sprungziel : v.fortschritt_s}
          dauerS={v.dauer_s}
          kapitel={kapitel}
          untertitel={untertitel}
          aufKapitel={beiKapitel}
          theater={theater}
          aufTheater={setTheater}
        />

        <h1>{v.titel}</h1>

        <div className="watch-zeile">
          {v.kanal_id ? (
            <Link to={`/kanal/${v.kanal_id}`} style={{ display: "flex", alignItems: "center", gap: 12 }}>
              <span className="avatar" />
              <div>
                <div style={{ fontWeight: 500 }}>{v.kanal_name}</div>
                <div style={{ color: "var(--text-gedaempft)", fontSize: 12.5 }}>
                  {datum(v.hochgeladen)}
                </div>
              </div>
            </Link>
          ) : null}
          <div className="aktionen">
            {v.gesehen ? <span className="knopf">✓ gesehen</span> : null}
            <button className="knopf" onClick={() => setTechnikOffen(!technikOffen)}>
              Technik
            </button>
            {v.status === "archived" ? (
              entfernenNachfrage ? (
                <>
                  <span style={{ color: "var(--text-gedaempft)", fontSize: 13 }}>
                    Dateien löschen? Der Eintrag bleibt beim Kanal.
                  </span>
                  <button
                    className="knopf"
                    data-art="gefahr-stark"
                    disabled={entfernenLaeuft}
                    onClick={async () => {
                      setEntfernenLaeuft(true);
                      try {
                        await api.videoEntfernen(videoId);
                        navigate(v.kanal_id ? `/kanal/${v.kanal_id}` : "/");
                      } finally {
                        setEntfernenLaeuft(false);
                      }
                    }}
                  >
                    {entfernenLaeuft ? "wird entfernt …" : "Ja, entfernen"}
                  </button>
                  <button className="knopf" onClick={() => setEntfernenNachfrage(false)}>
                    Nein
                  </button>
                </>
              ) : (
                <button className="knopf" data-art="gefahr" onClick={() => setEntfernenNachfrage(true)}>
                  Aus dem Archiv entfernen
                </button>
              )
            ) : null}
          </div>
        </div>

        <div
          className="beschreibung"
          data-zu={!beschreibungOffen}
          onClick={() => setBeschreibungOffen(!beschreibungOffen)}
          style={{ cursor: "pointer" }}
        >
          <div className="beschreibung-kopf">
            {aufrufe(v.aufrufe)}
            {v.hochgeladen ? ` · ${datum(v.hochgeladen)}` : ""}
          </div>
          {detail.daten.beschreibung || "Keine Beschreibung vorhanden."}
        </div>

        {technikOffen ? (
          <div className="beschreibung" style={{ marginTop: 12 }}>
            <div className="beschreibung-kopf">Wie dieses Video gespeichert ist</div>
            <table className="tabelle" style={{ marginTop: 4 }}>
              <tbody>
                <tr>
                  <td>Codec</td>
                  <td className="zahl">
                    {technik.videocodec ?? "?"} / {technik.audiocodec ?? "?"}
                    {technik.recodiert ? " (verkleinert)" : " (Original)"}
                  </td>
                </tr>
                <tr>
                  <td>Auflösung</td>
                  <td className="zahl">
                    {technik.breite}×{technik.hoehe}
                    {technik.fps ? ` @ ${Math.round(technik.fps)} fps` : ""}
                  </td>
                </tr>
                <tr>
                  <td>Bündel</td>
                  <td className="zahl">{bytes(technik.buendel_bytes)}</td>
                </tr>
                {technik.quelle_bytes ? (
                  <tr>
                    <td>Quelle beim Download</td>
                    <td className="zahl">
                      {bytes(technik.quelle_bytes)}
                      {ersparnis && ersparnis > 0.01 ? (
                        <span style={{ color: "var(--zu-archiviert)" }}>
                          {" "}
                          – {prozent(ersparnis)} gespart
                        </span>
                      ) : null}
                    </td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </div>
        ) : null}

        {v.status !== "archived" ? (
          <div style={{ marginTop: 12 }}>
            <Zustand status={v.status} />
            {detail.daten.statusmeldung ? (
              <div style={{ color: "var(--text-gedaempft)", fontSize: 13, marginTop: 6 }}>
                {detail.daten.statusmeldung}
              </div>
            ) : null}
          </div>
        ) : null}
      </div>

      <aside>
        {kapitel.length > 0 ? (
          <div style={{ marginBottom: 24 }}>
            <div className="beschreibung-kopf">Kapitel</div>
            <div className="kapitel">
              {kapitel.map((k, i) => (
                <button
                  key={i}
                  data-aktiv={aktivesKapitel === i}
                  onClick={() => springe(k.start_s)}
                >
                  <span className="zeit">{dauer(k.start_s)}</span>
                  <span>{k.titel}</span>
                </button>
              ))}
            </div>
          </div>
        ) : null}

        {detail.daten.in_playlists.length > 0 ? (
          <div style={{ marginBottom: 24 }}>
            <div className="beschreibung-kopf">In diesen Playlists</div>
            {detail.daten.in_playlists.map((p) => (
              <Link key={p.id} to={`/playlist/${p.id}`} className="nav-punkt">
                <span className="zeichen">☰</span>
                <span>{p.titel}</span>
              </Link>
            ))}
          </div>
        ) : null}

        <div className="beschreibung-kopf">Mehr von {v.kanal_name}</div>
        {(() => {
          const andere = (weitere.daten ?? []).filter((x) => x.id !== videoId).slice(0, 10);
          if (andere.length === 0 && !weitere.laedt) {
            return (
              <div style={{ color: "var(--text-schwach)", fontSize: 13, lineHeight: 1.5 }}>
                Noch keine weiteren archivierten Videos dieses Kanals.{" "}
                {v.kanal_id ? (
                  <Link to={`/kanal/${v.kanal_id}`} style={{ textDecoration: "underline" }}>
                    Zum Kanal
                  </Link>
                ) : null}
              </div>
            );
          }
          return (
            <Gitter form="liste">
              {andere.map((x) => (
                <Videokachel key={x.id} video={x} ohneKanal />
              ))}
            </Gitter>
          );
        })()}
      </aside>
    </div>
  );
}
