import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { api } from "../lib/api";
import { Icon } from "../components/Icons";
import { Dialog } from "../components/Dialog";

export function KanalAnlegenDialog({ aufSchliessen }: { aufSchliessen: () => void }) {
  const navigate = useNavigate();
  const [url, setUrl] = useState("");
  const [sofort, setSofort] = useState(false);
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
    <Dialog titelId="kanal-dialog-titel" schliessenGesperrt={laeuft} aufSchliessen={aufSchliessen}>
      <form className="dialog" onSubmit={absenden}>
        <div className="dialog-kopf"><h2 id="kanal-dialog-titel">Kanal aufnehmen</h2>
          <button type="button" className="symbol-knopf" aria-label="Dialog schließen" disabled={laeuft} onClick={aufSchliessen}><Icon name="close" /></button>
        </div>
        <p className="erklaerung">
          Adresse, Handle oder Kanal-ID. Vitrine erfasst zunächst nur, welche Videos und Playlists
          es gibt – das dauert bei großen Kanälen einige Minuten. Heruntergeladen wird erst, wenn
          du es auslöst.
        </p>

        <div className="feld">
          <label htmlFor="kanal-url">Kanal</label>
          <input
            id="kanal-url"
            type="text"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="@handle, youtube.com/@handle oder UC…"
            data-dialog-fokus
          />
        </div>

        <label className="schalter">
          <input type="checkbox" checked={sofort} onChange={(e) => setSofort(e.target.checked)} />
          <span>
            Videos sofort herunterladen
            <div style={{ color: "var(--text-schwach)", fontSize: 12 }}>
              Ohne Haken wird der Kanal nur erfasst – du siehst dann, was es gibt, und lädst
              einzeln oder alles auf einmal. Bei großen Kanälen ist das der ruhigere Weg: Ein
              Kanal mit 3000 Videos beschäftigt die Warteschlange sonst tagelang.
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
          <div className="hinweis" data-art="fehler" role="alert" style={{ marginTop: 14, marginBottom: 0 }}>
            <div>{fehler}</div>
          </div>
        ) : null}

        <div className="dialog-fuss">
          <button type="button" className="knopf" disabled={laeuft} onClick={aufSchliessen}>
            Abbrechen
          </button>
          <button type="submit" className="knopf" data-art="stark" disabled={laeuft || !url.trim()}>
            {laeuft ? "wird geprüft …" : "Aufnehmen"}
          </button>
        </div>
      </form>
    </Dialog>
  );
}
