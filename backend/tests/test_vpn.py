"""Die WireGuard-Tunnel: Konfiguration lesen, Ausgaenge waehlen, wechseln.

Der Zweck der Funktion ist Bandbreite, nicht Verschleierung: YouTube zaehlt je
IP-Adresse und laesst als Gast rund 300 Videos in der Stunde durch. Vier Tunnel
sind vier Budgets - aber nur, wenn zwei Dinge stimmen, und beide werden hier
geprueft:

* Eine Konfiguration vom Anbieter muss ohne Nacharbeit angenommen werden, und
  eine kaputte muss beim Hochladen auffallen statt beim ersten Download.
* Eine Sperre muss den betroffenen Ausgang treffen und nur ihn. Wuerde sie
  weiter prozessweit gelten, waere die ganze Funktion wertlos - man haette vier
  Tunnel und trotzdem nach fuenfzig Videos Stillstand.
"""

from __future__ import annotations

import pytest

from app.services import ausgang, drosselung, vpn

#: Eine echte Konfiguration, wie ein Anbieter sie ausliefert - mit
#: PresharedKey, IPv6 und den wg-quick-Zeilen, die wireproxy nicht kennt.
MULLVAD_STIL = """\
[Interface]
# Device: fluffy-hamster
PrivateKey = qGzD7VBH1cJfLQ2xNvKpYtR8sWmA3eZuI5oJ4kHnBd0=
Address = 10.64.222.51/32,fc00:bbbb:bbbb:bb01::1:de32/128
DNS = 10.64.0.1
MTU = 1380
PostUp = iptables -I OUTPUT ! -o %i -m mark ! --mark $(wg show %i fwmark) -j REJECT
PreDown = iptables -D OUTPUT ! -o %i -j REJECT

[Peer]
PublicKey = mR7tYuI2oP4aSdF6gH8jK0lZxCvB3nM5qW9eRtY1uI0=
PresharedKey = zXcVbNmA1sD2fG3hJ4kL5pO6iU7yT8rE9wQ0aZxSdC4=
AllowedIPs = 0.0.0.0/0,::0/0
Endpoint = 193.138.218.74:51820
PersistentKeepalive = 25
"""


# ------------------------------------------------------------- Konfiguration


def test_anbieterdatei_wird_unveraendert_angenommen():
    """Der Normalfall: herunterladen, hochladen, fertig.

    Muesste der Nutzer die Datei erst von Hand aufraeumen, waere die Funktion
    fuer die meisten unbenutzbar - Anbieter liefern genau das hier.
    """
    k = vpn.konfig_lesen(MULLVAD_STIL)
    assert k.private_key.endswith("=")
    assert k.address == ["10.64.222.51/32", "fc00:bbbb:bbbb:bb01::1:de32/128"]
    assert k.endpoint == "193.138.218.74:51820"
    assert k.dns == ["10.64.0.1"]
    assert k.mtu == 1380
    assert k.preshared_key is not None
    assert k.keepalive == 25


def test_wg_quick_zeilen_landen_nicht_in_der_laufkonfiguration():
    """PostUp und PreDown sind Anweisungen an ein Kommandozeilenwerkzeug, das
    hier gar nicht laeuft. Durchgereicht bricht wireproxy daran ab - mit einem
    Fehler ueber eine Zeile, die der Anbieter so geliefert hat."""
    text = vpn.wireproxy_konfig(vpn.konfig_lesen(MULLVAD_STIL), 51800)
    assert "PostUp" not in text
    assert "PreDown" not in text
    assert "iptables" not in text


def test_laufkonfiguration_bindet_nur_lokal():
    """Ein Tunnel, der auf allen Schnittstellen lauscht, waere im Heimnetz ein
    offener Weiterleitungsdienst fuer jeden, der die Portnummer kennt."""
    text = vpn.wireproxy_konfig(vpn.konfig_lesen(MULLVAD_STIL), 51803)
    assert "[Socks5]" in text
    assert "BindAddress = 127.0.0.1:51803" in text
    assert "0.0.0.0:51803" not in text


def test_kommentare_werden_nicht_als_werte_gelesen():
    k = vpn.konfig_lesen(MULLVAD_STIL)
    assert "Device" not in k.private_key
    assert not k.private_key.startswith("#")


@pytest.mark.parametrize(
    ("teil", "erwartet"),
    [
        ("PrivateKey", "PrivateKey"),
        ("Endpoint", "Endpoint"),
        ("PublicKey", "PublicKey"),
        ("Address", "Address"),
    ],
)
def test_fehlende_pflichtangabe_wird_benannt(teil, erwartet):
    """Die Meldung muss sagen, WAS fehlt. "Konfiguration ungueltig" schickt den
    Nutzer auf die Suche in einer Datei, die er selbst nicht gebaut hat."""
    gekuerzt = "\n".join(z for z in MULLVAD_STIL.splitlines() if not z.startswith(teil))
    with pytest.raises(vpn.VpnFehler, match=erwartet):
        vpn.konfig_lesen(gekuerzt)


def test_halb_kopierter_schluessel_faellt_auf():
    """Der haeufigste Bedienfehler ueberhaupt: beim Markieren ein Stueck der
    Zeile verloren. Ohne Pruefung startet der Tunnel und schweigt."""
    kaputt = MULLVAD_STIL.replace(
        "qGzD7VBH1cJfLQ2xNvKpYtR8sWmA3eZuI5oJ4kHnBd0=", "qGzD7VBH1cJfLQ2xNvKp"
    )
    with pytest.raises(vpn.VpnFehler, match="Base64"):
        vpn.konfig_lesen(kaputt)


def test_endpoint_ohne_port_wird_abgelehnt():
    ohne = MULLVAD_STIL.replace("193.138.218.74:51820", "193.138.218.74")
    with pytest.raises(vpn.VpnFehler, match="host:port"):
        vpn.konfig_lesen(ohne)


def test_fehlende_allowed_ips_bedeuten_alles():
    """wireproxy setzt dann selbst 0.0.0.0/0 - wir schreiben es hin, damit in
    der erzeugten Datei steht, was wirklich gilt."""
    ohne = "\n".join(z for z in MULLVAD_STIL.splitlines() if not z.startswith("AllowedIPs"))
    assert vpn.konfig_lesen(ohne).allowed_ips == ["0.0.0.0/0", "::/0"]


def test_zweiter_peer_wird_ignoriert():
    """Mehrere [Peer] sind erlaubt und bringen configparser zu Fall - deshalb
    der eigene Leser. Fuer einen Ausgang ins Internet zaehlt der erste."""
    zwei = MULLVAD_STIL + """
[Peer]
PublicKey = aB1cD2eF3gH4iJ5kL6mN7oP8qR9sT0uV1wX2yZ3aB4c=
Endpoint = 10.9.8.7:51820
"""
    assert vpn.konfig_lesen(zwei).endpoint == "193.138.218.74:51820"


# ------------------------------------------------------------------ Auswahl


@pytest.fixture
def sauber():
    drosselung.zuruecksetzen()
    ausgang.zuruecksetzen()
    vpn._tunnel.clear()
    yield
    vpn._tunnel.clear()
    ausgang.zuruecksetzen()
    drosselung.zuruecksetzen()


def _tunnel_vortaeuschen(anzahl: int) -> list[vpn.Tunnel]:
    """Fertig gestartete Tunnel, ohne einen Prozess zu starten."""
    konfig = vpn.konfig_lesen(MULLVAD_STIL)
    gebaut = []
    for i in range(1, anzahl + 1):
        t = vpn.Tunnel(
            id=i, name=f"Tunnel {i}", port=vpn.PORT_BASIS + i, konfig=konfig, bereit=True
        )
        vpn._tunnel[i] = t
        gebaut.append(t)
    return gebaut


def test_ohne_vpn_gibt_es_genau_einen_ausgang(sauber, monkeypatch):
    """Alles bleibt, wie es war, solange niemand einen Tunnel einrichtet."""
    monkeypatch.setattr(vpn.settings, "vpn_aktiv", False)
    _tunnel_vortaeuschen(3)  # eingerichtet, aber der Schalter ist aus
    assert vpn.ausgang_ids() == [ausgang.DIREKT]
    assert vpn.waehlen().proxy is None


def test_mit_vpn_faellt_die_eigene_leitung_weg(sauber, monkeypatch):
    """Die vorsichtige Vorgabe: Wer Tunnel einrichtet, will nicht, dass bei
    deren Ausfall stillschweigend wieder die Hausleitung benutzt wird."""
    monkeypatch.setattr(vpn.settings, "vpn_aktiv", True)
    monkeypatch.setattr(vpn.settings, "vpn_nur_tunnel", True)
    _tunnel_vortaeuschen(2)
    assert vpn.ausgang_ids() == ["tunnel-1", "tunnel-2"]

    monkeypatch.setattr(vpn.settings, "vpn_nur_tunnel", False)
    assert ausgang.DIREKT in vpn.ausgang_ids()


def test_nicht_gestartete_tunnel_werden_nicht_gewaehlt(sauber, monkeypatch):
    monkeypatch.setattr(vpn.settings, "vpn_aktiv", True)
    tunnel = _tunnel_vortaeuschen(2)
    tunnel[0].bereit = False
    assert vpn.ausgang_ids() == ["tunnel-2"]


def test_reihum_statt_immer_derselbe(sauber, monkeypatch):
    """Ohne Wechsel liefe der erste Tunnel in die Sperre, waehrend die anderen
    drei Budget haetten - genau der Zustand, den die Funktion beseitigen soll."""
    monkeypatch.setattr(vpn.settings, "vpn_aktiv", True)
    _tunnel_vortaeuschen(3)
    gewaehlt = [vpn.waehlen().id for _ in range(6)]
    assert set(gewaehlt) == {"tunnel-1", "tunnel-2", "tunnel-3"}
    # Und zwar gleichmaessig: bei sechs Wahlen zweimal jeder.
    assert all(gewaehlt.count(name) == 2 for name in set(gewaehlt))


def test_belegte_tunnel_kommen_zuletzt(sauber, monkeypatch):
    """Bei drei parallelen Downloads sollen es drei verschiedene Adressen sein.
    Zwei Downloads ueber dieselbe Adresse verbrauchen dasselbe Budget doppelt
    so schnell - das ist der ganze Punkt der Uebung."""
    monkeypatch.setattr(vpn.settings, "vpn_aktiv", True)
    _tunnel_vortaeuschen(3)

    erster = vpn.waehlen()
    with vpn.benutzen(erster):
        zweiter = vpn.waehlen()
        with vpn.benutzen(zweiter):
            dritter = vpn.waehlen()
            assert len({erster.id, zweiter.id, dritter.id}) == 3


def test_gesperrter_tunnel_wird_uebersprungen(sauber, monkeypatch):
    """Der Kern der Sache: Sperre heisst wechseln, nicht warten."""
    monkeypatch.setattr(vpn.settings, "vpn_aktiv", True)
    _tunnel_vortaeuschen(2)

    drosselung.melden("Sign in to confirm you're not a bot", ausgang="tunnel-1")
    for _ in range(4):
        assert vpn.waehlen().id == "tunnel-2"
    assert vpn.wartezeit() == 0, "solange einer frei ist, wird nicht gewartet"


def test_erst_wenn_alle_gesperrt_sind_gibt_es_keinen_ausgang(sauber, monkeypatch):
    monkeypatch.setattr(vpn.settings, "vpn_aktiv", True)
    _tunnel_vortaeuschen(2)
    drosselung.melden("abgewiesen", ausgang="tunnel-1")
    drosselung.melden("abgewiesen", ausgang="tunnel-2")

    assert vpn.waehlen() is None
    assert vpn.wartezeit() > 0


def test_benutzen_setzt_den_proxy_fuer_yt_dlp(sauber, monkeypatch):
    """Die Verbindung zwischen Auswahl und Wirkung. Ohne sie waehlt das Archiv
    zwar brav einen Tunnel und laedt trotzdem ueber die eigene Leitung."""
    from app.services import ytdlp

    monkeypatch.setattr(vpn.settings, "vpn_aktiv", True)
    monkeypatch.setattr(ytdlp.settings, "ytdlp_cookies_file", None)
    _tunnel_vortaeuschen(1)

    assert "proxy" not in ytdlp._base_opts()
    with vpn.benutzen(vpn.waehlen()):
        opts = ytdlp._base_opts()
    assert opts["proxy"] == f"socks5h://127.0.0.1:{vpn.PORT_BASIS + 1}"
    # Danach wieder ohne - sonst laedt der naechste Auftrag ueber einen
    # Tunnel, der laengst abgeschaltet sein kann.
    assert "proxy" not in ytdlp._base_opts()


def test_socks5h_damit_auch_dns_durch_den_tunnel_geht(sauber, monkeypatch):
    """Mit schlichtem socks5 fragt der Server selbst beim DNS des Providers:
    Der Verkehr liefe getunnelt, die Namensaufloesung darueber nicht."""
    monkeypatch.setattr(vpn.settings, "vpn_aktiv", True)
    tunnel = _tunnel_vortaeuschen(1)[0]
    assert tunnel.proxy.startswith("socks5h://")


# ------------------------------------------------- Wirkung auf die Arbeiter


def _strang_laufen_lassen(werk, gruppe, dauer=0.3):
    """Laesst einen Arbeiterstrang kurz laufen und beendet ihn wieder."""
    import threading
    import time

    t = threading.Thread(target=werk._arbeiten, args=(gruppe, 0), daemon=True)
    t.start()
    time.sleep(dauer)
    werk._stop.set()
    t.join(timeout=2.0)
    assert not t.is_alive(), "Strang haengt - das Warten muss unterbrechbar bleiben"


class _Auftrag:
    """Ein Auftrag, so viel davon, wie der Arbeiterstrang anfasst."""

    type = "video_archive"
    target_id = "vid1"


def _bearbeiter_einsetzen(monkeypatch, runner, aufzeichnung: list[str]) -> None:
    """Laesst den Strang einen Auftrag holen und merkt sich dessen Ausgang.

    Gemessen wird ausdruecklich IM Bearbeiter und nicht beim Holen: Der Ausgang
    wird erst fuer den Auftrag selbst belegt. Waehrend der Strang nur alle zwei
    Sekunden in eine leere Warteschlange schaut, soll kein Tunnel als
    beschaeftigt gelten.
    """
    from contextlib import contextmanager

    @contextmanager
    def sitzung():
        yield None

    monkeypatch.setattr(runner, "session_scope", sitzung)
    monkeypatch.setattr(runner.jobs, "claim_next", lambda *a, **kw: _Auftrag())
    monkeypatch.setattr(
        runner.jobs, "HANDLERS",
        {"video_archive": lambda db, job: aufzeichnung.append(ausgang.aktiv().id)},
    )


def test_gesperrter_tunnel_haelt_die_warteschlange_nicht_an(sauber, monkeypatch):
    """Das eigentliche Versprechen der Funktion.

    Frueher stand nach einer Abweisung alles still - eine Sperre galt dem
    ganzen Prozess. Mit mehreren Tunneln muss der Strang stattdessen den
    naechsten freien Ausgang nehmen und weiterarbeiten. Geprueft wird beides:
    dass ueberhaupt weitergearbeitet wird, und dass es ueber den anderen
    Tunnel geschieht.
    """
    from app.workers import runner

    monkeypatch.setattr(vpn.settings, "vpn_aktiv", True)
    _tunnel_vortaeuschen(2)
    drosselung.melden("Sign in to confirm you're not a bot", ausgang="tunnel-1")

    benutzt: list[str] = []
    _bearbeiter_einsetzen(monkeypatch, runner, benutzt)
    monkeypatch.setattr(runner, "DROSSEL_TAKT_S", 0.02)
    monkeypatch.setattr(runner, "LEERLAUF_S", 0.02)

    netz = next(g for g in runner._gruppen() if g.name == "netz")
    werk = runner.Arbeiterwerk()
    werk._soll["netz"] = 1
    _strang_laufen_lassen(werk, netz)

    assert benutzt, "der Strang muss weiterarbeiten - ein Tunnel ist ja frei"
    assert set(benutzt) == {"tunnel-2"}, "und zwar ueber den nicht gesperrten"


def test_leerlauf_belegt_keinen_tunnel(sauber, monkeypatch):
    """Ohne Auftrag darf kein Tunnel als beschaeftigt gelten.

    Sonst zeigte die Oberflaeche dauerhaft "laedt", und die Auswahl miede einen
    Tunnel, der in Wahrheit nichts tut.
    """
    from app.workers import runner

    monkeypatch.setattr(vpn.settings, "vpn_aktiv", True)
    tunnel = _tunnel_vortaeuschen(2)
    monkeypatch.setattr(runner.jobs, "claim_next", lambda *a, **kw: None)
    monkeypatch.setattr(runner, "LEERLAUF_S", 0.02)

    netz = next(g for g in runner._gruppen() if g.name == "netz")
    werk = runner.Arbeiterwerk()
    werk._soll["netz"] = 1
    _strang_laufen_lassen(werk, netz)

    assert all(t.belegt == 0 for t in tunnel)


def test_ohne_freien_ausgang_wird_kein_auftrag_geholt(sauber, monkeypatch):
    """Die Gegenprobe. Sind alle Tunnel gesperrt, gilt weiter das Alte: nicht
    zugreifen. Jeder Versuch verlaengert die Sperre, statt sie zu loesen."""
    from app.workers import runner

    monkeypatch.setattr(vpn.settings, "vpn_aktiv", True)
    _tunnel_vortaeuschen(2)
    drosselung.melden("abgewiesen", ausgang="tunnel-1")
    drosselung.melden("abgewiesen", ausgang="tunnel-2")

    geholt: list[str] = []
    monkeypatch.setattr(runner.jobs, "claim_next", lambda *a, **kw: geholt.append("x"))
    monkeypatch.setattr(runner, "DROSSEL_TAKT_S", 0.02)
    monkeypatch.setattr(runner, "LEERLAUF_S", 0.02)

    netz = next(g for g in runner._gruppen() if g.name == "netz")
    werk = runner.Arbeiterwerk()
    werk._soll["netz"] = 1
    _strang_laufen_lassen(werk, netz)
    assert geholt == []


def test_recodierung_belegt_keinen_tunnel(sauber, monkeypatch):
    """Sie redet nicht mit YouTube. Wuerde sie einen Tunnelplatz belegen,
    stuende ein Download an, waehrend nebenan eine Datei umgerechnet wird."""
    from app.workers import runner

    monkeypatch.setattr(vpn.settings, "vpn_aktiv", True)
    tunnel = _tunnel_vortaeuschen(1)[0]

    gesehen: list[str] = []
    monkeypatch.setattr(
        runner.jobs, "claim_next", lambda *a, **kw: gesehen.append(ausgang.aktiv().id)
    )
    monkeypatch.setattr(runner, "LEERLAUF_S", 0.02)

    recode = next(g for g in runner._gruppen() if g.name == "recodierung")
    werk = runner.Arbeiterwerk()
    werk._soll["recodierung"] = 1
    _strang_laufen_lassen(werk, recode)

    assert gesehen and set(gesehen) == {ausgang.DIREKT}
    assert tunnel.belegt == 0


def test_mehrere_adressen_stehen_in_einer_zeile():
    """Gegen wireproxy 1.1.3 nachgemessen, kein Geschmack.

    Bei zwei ``Address``-Zeilen liest wireproxy nur die erste - eine bewusst
    unsinnige zweite Zeile nimmt es anstandslos an, was beweist, dass es sie
    gar nicht ansieht. Anbieter liefern IPv4 UND IPv6; die Folge waere ein
    Tunnel, der laeuft und dabei still die halbe Erreichbarkeit verliert.
    """
    text = vpn.wireproxy_konfig(vpn.konfig_lesen(MULLVAD_STIL), 51800)
    adresszeilen = [z for z in text.splitlines() if z.startswith("Address")]
    assert len(adresszeilen) == 1
    assert "10.64.222.51/32" in adresszeilen[0]
    assert "fc00:bbbb:bbbb:bb01::1:de32/128" in adresszeilen[0]


def test_ohne_dns_steht_keine_leere_zeile_drin():
    """Ein leeres ``DNS =`` laesst wireproxy an ParseAddr("") scheitern."""
    ohne = "\n".join(z for z in MULLVAD_STIL.splitlines() if not z.startswith("DNS"))
    text = vpn.wireproxy_konfig(vpn.konfig_lesen(ohne), 51800)
    assert "DNS" not in text


# -------------------------------------------------------------- Portvergabe


def test_neuer_tunnel_bekommt_keinen_belegten_port(sauber):
    """Der Fallstrick beim Loeschen.

    Die Nummer aus der Listenposition abzuleiten geht schief, sobald jemand
    einen Tunnel entfernt: Die uebrigen rutschen in der Liste hoch, behalten
    aber ihre laufenden Prozesse und damit ihre Ports. Der naechste neue
    bekaeme eine Nummer, auf der schon jemand lauscht - wireproxy startet dann
    nicht, und "address already in use" deutet auf alles Moegliche hin, nur
    nicht auf den wahren Grund.
    """
    _tunnel_vortaeuschen(3)
    assert sorted(t.port for t in vpn._tunnel.values()) == [
        vpn.PORT_BASIS + 1, vpn.PORT_BASIS + 2, vpn.PORT_BASIS + 3
    ]

    # Der erste faellt weg; die uebrigen behalten ihre Ports.
    del vpn._tunnel[1]
    assert vpn._freier_port() == vpn.PORT_BASIS
    # Und die naechste Nummer danach ist wieder eine freie, keine belegte.
    vpn._tunnel[9] = vpn.Tunnel(
        id=9, name="neu", port=vpn._freier_port(), konfig=vpn.konfig_lesen(MULLVAD_STIL)
    )
    assert len({t.port for t in vpn._tunnel.values()}) == len(vpn._tunnel)


def test_zwei_straenge_greifen_nicht_denselben_tunnel(sauber, monkeypatch):
    """Die Luecke zwischen Wahl und Arbeitsbeginn.

    Der Strang waehlt seinen Ausgang, holt dann erst den Auftrag aus der
    Datenbank und beginnt danach. In dieser Zeit gilt der Tunnel noch als
    unbelegt - ohne Buchung bei der Wahl griffe der zweite Strang denselben,
    und beide Downloads liefen ueber eine Adresse, obwohl daneben einer frei
    ist.
    """
    monkeypatch.setattr(vpn.settings, "vpn_aktiv", True)
    _tunnel_vortaeuschen(2)

    erster = vpn.waehlen()      # noch nicht belegt: der Auftrag wird gerade geholt
    zweiter = vpn.waehlen()
    assert erster.id != zweiter.id
