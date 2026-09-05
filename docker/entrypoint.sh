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

# Die eingebundenen GPU-Geraete gehoeren oft einer render/video-Gruppe des
# Hosts. gosu baut die Zusatzgruppen aus /etc/group neu auf; ein Device-Mapping
# allein reicht bei 0660 deshalb nicht. Nur die benoetigten Geraetegruppen
# uebernehmen, keine Rechte am Host-Geraet aendern und niemals Gruppe root.
for gpu_device in /dev/dri/renderD[0-9]* /dev/nvidia[0-9]* /dev/nvidiactl /dev/nvidia-uvm /dev/nvidia-uvm-tools /dev/nvidia-caps/nvidia-cap[0-9]*; do
  [ -c "$gpu_device" ] || continue
  gpu_gid="$(stat -c '%g' "$gpu_device")"
  [ "$gpu_gid" != 0 ] && [ "$gpu_gid" != "$PGID" ] || continue
  gpu_group="$(getent group "$gpu_gid" | cut -d: -f1)"
  if [ -z "$gpu_group" ]; then
    gpu_group="vitrine-gpu-$gpu_gid"
    groupadd -g "$gpu_gid" "$gpu_group"
  fi
  usermod -a -G "$gpu_group" archiv
done

# Nur die Verzeichnisse selbst, nicht rekursiv: Ein Kaltspeicher mit
# Terabytes an Buendeln wuerde sonst bei jedem Start minutenlang chown'en.
# Neue Dateien entstehen ohnehin unter der richtigen ID.
for d in /data /data/bundles /data/cache /data/thumbs /data/tmp; do
  mkdir -p "$d"
  chown "$PUID:$PGID" "$d" 2>/dev/null || true
done

echo "vitrine: laufe als $PUID:$PGID"
exec gosu archiv "$@"
