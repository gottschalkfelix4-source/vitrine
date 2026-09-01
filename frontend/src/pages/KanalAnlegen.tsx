import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { api } from "../lib/api";

export function KanalAnlegenDialog({ aufSchliessen }: { aufSchliessen: () => void }) {
  const navigate = useNavigate();
  const [url, setUrl] = useState("");
  const [sofort, setSofort] = useState(true);
  const [shorts, setShorts] = useState(false);
  const [live, setLive] = useState(false);
  const [laeuft, setLaeuft] = useState(false);
  const [fehler, setFehler] = useState<string | null>(null);

  async function absenden(e: React.FormEvent) {
    e.preventDefault();
    if (!url.trim()) return;
    setLaeuft(true);
    setFehler(null);
    try {
      const kanal = await api.kanalAnlegen({
        url: url.trim(),
        sofort_archivieren: sofort,
        shorts,
        livestreams: live,
      });
      aufSchliessen();
      navigate(`/kanal/${kanal.id}`);
    } catch (e) {
      setFehler(e instanceof Error ? e.message : String(e));
    } finally {
      setLaeuft(false);
    }
  }

  return (
    <div
      className="schleier"
      onClick={(e) => {
        if (e.target === e.currentTarget) aufSchliessen();
      }}
    >
      <form className="dialog" onSubmit={absenden}>
        <h2>Kanal aufnehmen</h2>
        <p className="erklaerung">
          Adresse, Handle oder Kanal-ID. Vitrine liest die Videos und Playlists des Kanals und lädt
          sie im Hintergrund – das kann bei großen Kanälen eine Weile dauern.
        </p>

        <div className="feld">
          <label htmlFor="kanal-url">Kanal</label>
          <input
            id="kanal-url"
            type="text"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="@handle, youtube.com/@handle oder UC…"
            autoFocus
          />
        </div>

        <label className="schalter">
          <input type="checkbox" checked={sofort} onChange={(e) => setSofort(e.target.checked)} />
          <span>
            Videos gleich archivieren
            <div style={{ color: "var(--text-schwach)", fontSize: 12 }}>
              Ohne Haken wird der Kanal nur beobachtet; einzelne Videos lassen sich später gezielt
              holen.
            </div>
          </span>
        </label>

        <label className="schalter">
          <input type="checkbox" checked={shorts} onChange={(e) => setShorts(e.target.checked)} />
          <span>Shorts mit aufnehmen</span>
        </label>

        <label className="schalter">
          <input type="checkbox" checked={live} onChange={(e) => setLive(e.target.checked)} />
          <span>
            Livestream-Aufzeichnungen mit aufnehmen
            <div style={{ color: "var(--text-schwach)", fontSize: 12 }}>
              Oft sehr lang – das schlägt spürbar auf den Speicher durch.
            </div>
          </span>
        </label>

        {fehler ? (
          <div className="hinweis" data-art="fehler" style={{ marginTop: 14, marginBottom: 0 }}>
            <div>{fehler}</div>
          </div>
        ) : null}

        <div className="dialog-fuss">
          <button type="button" className="knopf" onClick={aufSchliessen}>
            Abbrechen
          </button>
          <button type="submit" className="knopf" data-art="stark" disabled={laeuft || !url.trim()}>
            {laeuft ? "wird geprüft …" : "Aufnehmen"}
          </button>
        </div>
      </form>
    </div>
  );
}
