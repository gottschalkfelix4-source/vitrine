"""Dateipfade innerhalb der vorgesehenen Ablage halten."""

from __future__ import annotations

import os
import stat
from pathlib import Path, PureWindowsPath


class UnsafePath(ValueError):
    """Ein gespeicherter oder eingehender Pfad verlaesst seine Ablage."""


def component(name: str) -> str:
    """Ein einzelner Dateiname, auch unter Windows ohne Sonderbedeutung.

    Bewusst unabhaengig vom Betriebssystem: Eine auf Linux angelegte Ablage
    kann spaeter auf einem Windows-Rechner geoeffnet werden.
    """
    if (
        not name
        or name in {".", ".."}
        or name != name.strip()
        or name.endswith(".")
        or any(c in name for c in '/\\:<>"|?*')
        or any(ord(c) < 32 or ord(c) == 127 for c in name)
        or PureWindowsPath(name).is_reserved()
    ):
        raise UnsafePath("Ungueltiger Dateiname")
    return name


def contained(root: Path, target: Path) -> Path:
    """Prueft einen gespeicherten Pfad vor Lese- oder Loeschoperationen.

    Die konfigurierte Wurzel darf selbst ein Mount oder Link sein. Darunter
    werden Links und Windows-Junctions abgelehnt, damit ein vermeintlicher
    Kanal oder ein Vorschaubild nicht auf fremde Daten zeigen kann.
    """
    raw = str(target)
    if os.name != "nt" and ("\\" in raw or PureWindowsPath(raw).drive):
        raise UnsafePath("Windows-Pfad ausserhalb der Ablage")
    try:
        base = Path(os.path.abspath(root))
        path = Path(os.path.abspath(target))
        relative = path.relative_to(base)
        if not relative.parts:
            raise UnsafePath("Die Ablage selbst ist kein Dateiziel")
        for part in relative.parts:
            component(part)
        resolved_base = base.resolve()
        resolved = path.resolve()
        if resolved == resolved_base or not resolved.is_relative_to(resolved_base):
            raise UnsafePath("Pfad ausserhalb der Ablage")

        current = base
        for part in relative.parts:
            current /= part
            try:
                info = current.lstat()
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(info.st_mode) or (
                getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
            ):
                raise UnsafePath("Verknuepfungen innerhalb der Ablage sind nicht erlaubt")
        return resolved
    except (OSError, RuntimeError, ValueError) as e:
        raise UnsafePath("Ungueltiger Pfad innerhalb der Ablage") from e


def child(root: Path, name: str) -> Path:
    return contained(root, root / component(name))
