`world-land.json` contains SVG land polygons for the Streams map, generated
from Natural Earth 1:110m Land, version 5.1.2. The projection is equirectangular
with viewBox `0 0 1000 500`: x = (longitude + 180) * 1000 / 360,
y = (90 - latitude) * 500 / 180. Render polygon holes with `fill-rule: evenodd`.

Made with [Natural Earth](https://www.naturalearthdata.com/).
The source data is [public domain](https://www.naturalearthdata.com/about/terms-of-use/).
Coordinate precision is reduced to two decimal places in the generated paths.

Regenerate from the repository root with `python scripts/build_world_map.py`.
The script pins the upstream release and verifies its SHA-256 checksum.
No map tiles, analytics, or third-party browser requests are needed at runtime.
