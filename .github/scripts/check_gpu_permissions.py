"""GPU-Geraeterechte im isolierten Container ohne echte GPU pruefen."""

import subprocess

SCRIPT = r'''
set -eu
mkdir -p /dev/dri
# Harmlose /dev/null-Aliase innerhalb dieses kurzlebigen Containers.
mknod /dev/dri/renderD128 c 1 3
mknod /dev/dri/renderD129 c 1 3
mknod /dev/dri/renderD130 c 1 3
touch /dev/dri/renderD131
groupadd -g 31002 vorhandene-gpu-gruppe
groupadd -g 31003 fremde-gruppe
chown 0:31001 /dev/dri/renderD128
chown 0:31002 /dev/dri/renderD129
chown 0:0 /dev/dri/renderD130
chown 0:31003 /dev/dri/renderD131
chmod 660 /dev/dri/renderD12* /dev/dri/renderD13*
# Die Gruppeneinrichtung muss auch einen erneuten Start vertragen.
/app/entrypoint.sh true
/app/entrypoint.sh python - <<'PY'
import os
import stat
from pathlib import Path
assert os.geteuid() == 99 and os.getegid() == 100
groups = set(os.getgroups())
assert {31001, 31002} <= groups
assert 0 not in groups and 31003 not in groups
for name, gid in [('renderD128', 31001), ('renderD129', 31002), ('renderD130', 0)]:
    path = Path('/dev/dri') / name
    info = path.stat()
    assert info.st_gid == gid and stat.S_IMODE(info.st_mode) == 0o660
    if gid:
        with path.open('wb') as device:
            device.write(b'gpu-permission-test')
    else:
        assert not os.access(path, os.R_OK | os.W_OK)
print('GPU-Zusatzgruppen und unveraenderte Geraeterechte erfolgreich geprueft.')
PY
'''


if __name__ == "__main__":
    subprocess.run([
        "docker", "run", "--rm", "-i", "--entrypoint", "sh",
        "-e", "PUID=99", "-e", "PGID=100", "vitrine:ci", "-s",
    ], input=SCRIPT, text=True, check=True, timeout=45)
