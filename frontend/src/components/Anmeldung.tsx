import { useEffect, useState, useSyncExternalStore, type ReactNode } from "react";
import { flushSync } from "react-dom";

import { ApiFehler, auth, type Anmeldezustand } from "../lib/auth";
import { Icon } from "./Icons";
import { Dialog } from "./Dialog";

export function AnmeldeSchranke({ children }: { children: ReactNode }) {
  const zustand = useSyncExternalStore(auth.abonnieren, auth.zustand, auth.zustand);
  useEffect(() => {
    void auth.pruefen();
    const pruefen = () => { void auth.pruefen(); };
    // Vor einer bfcache-Aufnahme sämtliche privaten React-Daten entfernen.
    const verlassen = () => { flushSync(() => auth.sperren()); };
    const zurueck = (e: PageTransitionEvent) => {
      if (e.persisted) flushSync(() => auth.sperren());
      pruefen();
    };
    const tabAbmeldungenEntfernen = auth.tabAbmeldungenBeachten();
    window.addEventListener("focus", pruefen);
    window.addEventListener("pageshow", zurueck);
    window.addEventListener("pagehide", verlassen);
    const intervall = window.setInterval(() => {
      if (document.visibilityState === "visible" && auth.zustand().sitzung?.angemeldet) pruefen();
    }, 30_000);
    return () => {
      tabAbmeldungenEntfernen();
      window.removeEventListener("focus", pruefen);
      window.removeEventListener("pageshow", zurueck);
      window.removeEventListener("pagehide", verlassen);
      window.clearInterval(intervall);
    };
  }, []);
  if (zustand.art !== "bereit" || !zustand.sitzung?.angemeldet) return <Anmeldung key={zustand.wechsel} zustand={zustand} />;
  return <div className="angemeldete-app" key={zustand.wechsel}>{children}</div>;
}

export function Anmeldung({ zustand }: { zustand: Anmeldezustand }) {
  const [benutzer, setBenutzer] = useState(zustand.benutzerVorschlag ?? "admin");
  const [passwort, setPasswort] = useState("");
  const [sendet, setSendet] = useState(false);
  const [fehler, setFehler] = useState<string | null>(null);
  const [einrichtungOffen, setEinrichtungOffen] = useState(zustand.art === "bereit" && zustand.sitzung?.eingerichtet === false);
  const kannEinrichten = zustand.art === "bereit" && zustand.sitzung?.eingerichtet === false;
  function einrichtungSchliessen() {
    setEinrichtungOffen(false);
    window.requestAnimationFrame(() => document.getElementById("administrator-einrichten")?.focus());
  }
  return <main className="anmeldung">
    <div className="anmeldung-inhalt">
      <div className="marke" aria-label="Vitrine">
        <svg className="marke-zeichen" viewBox="0 0 32 24" aria-hidden="true">
          <rect x="0" y="1" width="32" height="22" rx="7" fill="#ff0033" />
          <path d="m13 7 9 5-9 5Z" fill="white" />
        </svg><span>Vitrine</span>
      </div>
      <h1>Bei Vitrine anmelden</h1>
      {zustand.art === "pruefen" ? <p role="status">Anmeldung wird geprüft …</p>
        : zustand.art === "fehler" ? <>
          <p className="auth-fehler" role="alert">{zustand.meldung}</p>
          <button className="knopf" onClick={() => { auth.sperren(); void auth.pruefen(); }}>Erneut versuchen</button>
        </> : kannEinrichten ? <>
          <p role="status">Administrator noch nicht eingerichtet.</p>
          <button id="administrator-einrichten" className="knopf" data-art="stark" aria-haspopup="dialog"
            onClick={() => setEinrichtungOffen(true)}>Administrator einrichten</button>
        </>
          : <form className="zugang-formular" onSubmit={async (e) => {
            e.preventDefault();
            if (sendet) return;
            setSendet(true); setFehler(null);
            try { await auth.anmelden(benutzer, passwort); }
            catch (e) {
              setPasswort("");
              setFehler(e instanceof ApiFehler
                ? e.status === 401 ? "Benutzername oder Passwort ist falsch."
                  : e.status === 429 ? "Zu viele Anmeldeversuche. Bitte warte kurz und versuche es erneut."
                    : e.message
                : "Keine Verbindung zum Server. Bitte versuche es erneut.");
            } finally { setSendet(false); }
          }}>
            {zustand.meldung ? <p role="status">{zustand.meldung}</p> : null}
            <div className="feld"><label htmlFor="login-benutzer">Benutzername</label>
              <input id="login-benutzer" name="username" type="text" autoComplete="username" autoCapitalize="none"
                spellCheck={false} required maxLength={64} value={benutzer} disabled={sendet}
                onChange={(e) => setBenutzer(e.target.value)} /></div>
            <div className="feld"><label htmlFor="login-passwort">Passwort</label>
              <input id="login-passwort" name="password" type="password" autoComplete="current-password" required
                maxLength={256} value={passwort} disabled={sendet} onChange={(e) => setPasswort(e.target.value)} /></div>
            {fehler ? <p className="auth-fehler" role="alert">{fehler}</p> : null}
            <button className="knopf" data-art="stark" disabled={sendet}>{sendet ? "Wird angemeldet …" : "Anmelden"}</button>
          </form>}
    </div>
    {kannEinrichten && einrichtungOffen ? <EinrichtungsDialog aufSchliessen={einrichtungSchliessen} /> : null}
  </main>;
}

export function einrichtungsFehler(e: unknown): string {
  if (!(e instanceof ApiFehler)) return "Die Einrichtung konnte nicht bestätigt werden. Bitte prüfe die Verbindung und versuche es erneut.";
  if (e.status === 403 && e.message.includes("fremden Herkunft")) {
    return "Öffne Vitrine über die konfigurierte HTTPS-Adresse. Falls der Fehler dort bleibt, prüfe die Weiterleitung am Reverse Proxy.";
  }
  if (e.status === 403 && e.message.includes("Einrichtungscode")) {
    return "Der Einrichtungscode ist falsch oder nicht mehr gültig. Verwende den aktuellen Code aus dem Containerprotokoll.";
  }
  if (e.status === 429) return "Zu viele Einrichtungsversuche. Bitte warte kurz und versuche es erneut.";
  return e.message;
}

export function EinrichtungsDialog({ aufSchliessen }: { aufSchliessen: () => void }) {
  const [code, setCode] = useState("");
  const [benutzer, setBenutzer] = useState("admin");
  const [passwort, setPasswort] = useState("");
  const [wiederholung, setWiederholung] = useState("");
  const [sendet, setSendet] = useState(false);
  const [fehler, setFehler] = useState<string | null>(null);
  return <Dialog titelId="einrichtung-titel" beschreibungId="einrichtung-hinweis" aufSchliessen={aufSchliessen} schliessenGesperrt={sendet}>
    <form className="dialog zugang-formular einrichtung-dialog" onSubmit={async (e) => {
      e.preventDefault();
      if (sendet) return;
      if (!code.trim() || !benutzer.trim()) { setFehler("Bitte gib den Einrichtungscode und einen Benutzernamen ein."); return; }
      if (passwort !== wiederholung) { setFehler("Die Passwörter stimmen nicht überein."); return; }
      setSendet(true); setFehler(null);
      try { await auth.einrichten(code.trim(), benutzer.trim(), passwort); }
      catch (e) { setFehler(einrichtungsFehler(e)); }
      finally { setSendet(false); }
    }}>
      <div className="dialog-kopf"><h2 id="einrichtung-titel">Administrator einrichten</h2>
        <button type="button" className="symbol-knopf" aria-label="Dialog schließen" disabled={sendet} onClick={aufSchliessen}><Icon name="close" /></button>
      </div>
      <p id="einrichtung-hinweis" className="erklaerung">Den einmaligen Einrichtungscode findest du in Unraid im Protokoll des Vitrine-Containers. Nach einem Neustart gilt der neue Code.</p>
      <div className="feld"><label htmlFor="einrichtung-code">Einmaliger Einrichtungscode</label>
        <input id="einrichtung-code" name="setup-code" type="text" autoComplete="off" autoCapitalize="none" spellCheck={false}
          data-dialog-fokus required maxLength={256} value={code} disabled={sendet} onChange={(e) => setCode(e.target.value)} /></div>
      <div className="feld"><label htmlFor="einrichtung-benutzer">Benutzername</label>
        <input id="einrichtung-benutzer" name="username" type="text" autoComplete="username" autoCapitalize="none" spellCheck={false}
          required maxLength={64} value={benutzer} disabled={sendet} onChange={(e) => setBenutzer(e.target.value)} /></div>
      <div className="feld"><label htmlFor="einrichtung-passwort">Neues Passwort</label>
        <input id="einrichtung-passwort" name="new-password" type="password" autoComplete="new-password" required
          minLength={14} maxLength={256} aria-describedby="einrichtung-passwort-laenge" value={passwort} disabled={sendet}
          onChange={(e) => setPasswort(e.target.value)} /><p id="einrichtung-passwort-laenge">Mindestens 14 Zeichen.</p></div>
      <div className="feld"><label htmlFor="einrichtung-wiederholung">Neues Passwort wiederholen</label>
        <input id="einrichtung-wiederholung" name="confirm-password" type="password" autoComplete="new-password" required
          minLength={14} maxLength={256} value={wiederholung} disabled={sendet} onChange={(e) => setWiederholung(e.target.value)} /></div>
      {fehler ? <p className="auth-fehler" role="alert">{fehler}</p> : null}
      <div className="dialog-fuss">
        <button type="button" className="knopf" disabled={sendet} onClick={aufSchliessen}>Abbrechen</button>
        <button type="submit" className="knopf" data-art="stark" disabled={sendet}>{sendet ? "Wird eingerichtet …" : "Administrator einrichten"}</button>
      </div>
    </form>
  </Dialog>;
}

export function Abmelden() {
  const [sendet, setSendet] = useState(false);
  const [fehler, setFehler] = useState<string | null>(null);
  return <div className="abmelden-aktion">
    <button className="knopf abmelden-knopf" disabled={sendet} aria-label={sendet ? "Wird abgemeldet" : "Abmelden"}
      title="Abmelden" onClick={async () => {
        setSendet(true); setFehler(null);
        try { await auth.abmelden(); }
        catch { setFehler("Abmelden fehlgeschlagen. Du bist noch angemeldet. Bitte versuche es erneut."); }
        finally { setSendet(false); }
      }}><Icon name="logout" /><span>{sendet ? "Wird abgemeldet …" : "Abmelden"}</span></button>
    {fehler ? <p className="abmelden-fehler auth-fehler" role="alert">{fehler}</p> : null}
  </div>;
}

export function PasswortAendern() {
  const [aktuell, setAktuell] = useState("");
  const [neu, setNeu] = useState("");
  const [wiederholung, setWiederholung] = useState("");
  const [sendet, setSendet] = useState(false);
  const [fehler, setFehler] = useState<string | null>(null);
  return <section className="einst-gruppe">
    <h2>Passwort ändern</h2>
    <p>Nach der Änderung wirst du auf allen Geräten abgemeldet.</p>
    <form className="zugang-formular" onSubmit={async (e) => {
      e.preventDefault();
      if (sendet) return;
      if (neu !== wiederholung) { setFehler("Die neuen Passwörter stimmen nicht überein."); return; }
      setSendet(true); setFehler(null);
      try { await auth.passwortAendern(aktuell, neu); }
      catch (e) { setFehler(e instanceof ApiFehler ? e.message : "Keine Verbindung zum Server. Bitte versuche es erneut."); }
      finally { setSendet(false); }
    }}>
      <div className="feld"><label htmlFor="passwort-aktuell">Aktuelles Passwort</label>
        <input id="passwort-aktuell" type="password" autoComplete="current-password" value={aktuell} required
          maxLength={256} disabled={sendet} onChange={(e) => setAktuell(e.target.value)} /></div>
      <div className="feld"><label htmlFor="passwort-neu">Neues Passwort</label>
        <input id="passwort-neu" type="password" autoComplete="new-password" value={neu} required
          minLength={14} maxLength={256} aria-describedby="passwort-laenge" disabled={sendet} onChange={(e) => setNeu(e.target.value)} />
        <p id="passwort-laenge">Mindestens 14 Zeichen.</p></div>
      <div className="feld"><label htmlFor="passwort-wiederholung">Neues Passwort wiederholen</label>
        <input id="passwort-wiederholung" type="password" autoComplete="new-password" value={wiederholung} required
          minLength={14} maxLength={256} disabled={sendet} onChange={(e) => setWiederholung(e.target.value)} /></div>
      {fehler ? <p className="auth-fehler" role="alert">{fehler}</p> : null}
      <button className="knopf" data-art="stark" disabled={sendet}>{sendet ? "Wird geändert …" : "Passwort ändern"}</button>
    </form>
  </section>;
}
