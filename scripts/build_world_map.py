"""Rebuild the local SVG land paths from Natural Earth 5.1.2 (public domain)."""

import hashlib
import json
from pathlib import Path
from urllib.request import urlopen

SOURCE = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/v5.1.2/geojson/ne_110m_land.geojson"
SHA256 = "9e0729ee253ca7d7a5c4ae9395fb1902264c5377c52e224d13dd85010e2835d9"
TARGET = Path(__file__).resolve().parents[1] / "frontend/src/data/world-land.json"


def main():
    with urlopen(SOURCE, timeout=30) as response:
        data = response.read()
    if hashlib.sha256(data).hexdigest() != SHA256:
        raise ValueError("Natural Earth source checksum mismatch")
    paths = []
    for feature in json.loads(data)["features"]:
        geometry = feature["geometry"]
        polygons = [geometry["coordinates"]] if geometry["type"] == "Polygon" else geometry["coordinates"]
        for polygon in polygons:
            rings = []
            for ring in polygon:
                points = [f"{(lon + 180) * 1000 / 360:.2f},{(90 - lat) * 500 / 180:.2f}" for lon, lat in ring]
                rings.append("M" + "L".join(points) + "Z")
            paths.append("".join(rings))
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(json.dumps(paths, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"Generated {len(paths)} polygons ({TARGET.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
