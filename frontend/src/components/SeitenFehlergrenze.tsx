import { Component, type ReactNode } from "react";
import { Link } from "react-router-dom";

/** Ein Fehler in einer Seite darf die Navigation nicht unbenutzbar machen. */
export class SeitenFehlergrenze extends Component<{ children: ReactNode }, { fehler: boolean }> {
  state = { fehler: false };

  static getDerivedStateFromError() { return { fehler: true }; }

  render() {
    if (!this.state.fehler) return this.props.children;
    return <section className="leer" role="alert">
      <h2>Die Seite konnte nicht angezeigt werden</h2>
      <p>Lade sie erneut. Deine archivierten Videos bleiben erhalten.</p>
      <div className="fehler-aktionen">
        <button className="knopf" data-art="stark" onClick={() => window.location.reload()}>Seite neu laden</button>
        <Link className="knopf" to="/">Zur Startseite</Link>
      </div>
    </section>;
  }
}
