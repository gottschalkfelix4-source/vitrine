"""Lokal einrichten oder zuruecksetzen: python -m app.admin."""

import getpass

from app.db import init_db
from app.services import auth


def main() -> None:
    print("Administrator einrichten oder zuruecksetzen. Bestehende Sitzungen werden beendet.")
    try:
        username = input("Benutzername [admin]: ").strip() or "admin"
        password = getpass.getpass("Neues Passwort (mindestens 14 Zeichen): ")
        confirmation = getpass.getpass("Passwort wiederholen: ")
        if password != confirmation:
            raise auth.AuthError("Die Passwoerter stimmen nicht ueberein.", 400)
        auth.validate_password(password)
        init_db()
        auth.set_account(username, password)
    except (auth.AuthError, EOFError, KeyboardInterrupt) as error:
        print(f"Abgebrochen: {error}")
        raise SystemExit(1) from None
    print("Administrator gespeichert. Bitte in der Anwendung anmelden.")


if __name__ == "__main__":
    main()
