#!/bin/sh
# Startet als root, richtet die Rechte ein und gibt sie dann ab.
#
# Warum nicht einfach USER im Dockerfile: Dann waere die Nutzer-ID im Image
# festgebacken. Auf einem NAS muessen die Dateien aber dem gehoeren, der den
# Share benutzt - auf Unraid ist das nobody:users (99:100), anderswo 1000:1000.
# Deshalb PUID/PGID zur Laufzeit, wie man es von den linuxserver-Images kennt.
set -eu

PUID="${PUID:-1000}"
PGID="${PGID:-1000}"

if [ "$(id -u archiv)" != "$PUID" ] || [ "$(id -g archiv)" != "$PGID" ]; then
  groupmod -o -g "$PGID" archiv
  usermod -o -u "$PUID" -g "$PGID" archiv
fi

# Nur die Verzeichnisse selbst, nicht rekursiv: Ein Kaltspeicher mit
# Terabytes an Buendeln wuerde sonst bei jedem Start minutenlang chown'en.
# Neue Dateien entstehen ohnehin unter der richtigen ID.
for d in /data /data/bundles /data/cache /data/thumbs /data/tmp; do
  mkdir -p "$d"
  chown "$PUID:$PGID" "$d" 2>/dev/null || true
done

echo "vitrine: laufe als $PUID:$PGID"
exec gosu archiv "$@"
