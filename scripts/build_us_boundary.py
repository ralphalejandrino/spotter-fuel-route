"""Build a compact US land-boundary polygon set for the "both within the USA" check.

Why this exists
---------------
The brief says both endpoints must be within the USA. The first implementation used a
latitude/longitude bounding box, which is wrong in the way bounding boxes are always
wrong about countries: it accepted Tijuana, Mexico (32.51, -117.04) as a US location and
happily planned a 1,102-mile route to it.

A box cannot represent a border. This reads the US Census cartographic boundary file for
the nation (public domain, 1:5,000,000, 427 KB) and emits the polygon rings as JSON, so
the runtime check is an actual point-in-polygon test with no new dependency.

Runs once at build time; the output is committed.

Shapefile parsing is done inline rather than pulling in a GIS library: the polygon record
layout is about thirty lines of struct unpacking, and the alternative is a heavyweight
dependency used exactly once.
"""

import json
import struct
import urllib.request
import zipfile
from io import BytesIO
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
URL = "https://www2.census.gov/geo/tiger/GENZ2024/shp/cb_2024_us_nation_5m.zip"
OUT = DATA / "us_boundary.json"

# Rings smaller than this (in squared degrees of bounding box) are dropped. They are tiny
# offshore islands; keeping them triples the file size and changes no routing decision.
MIN_RING_BBOX = 1e-4


def read_polygons(shp: bytes):
    """Yield each ring of every Polygon record in a .shp file as [(lon, lat), ...]."""
    offset = 100  # file header
    n = len(shp)
    while offset < n:
        _num, content_len = struct.unpack_from(">ii", shp, offset)
        offset += 8
        body = offset
        (shape_type,) = struct.unpack_from("<i", shp, body)
        if shape_type == 5:  # Polygon
            num_parts, num_points = struct.unpack_from("<ii", shp, body + 36)
            parts = struct.unpack_from(f"<{num_parts}i", shp, body + 44)
            pts_at = body + 44 + 4 * num_parts
            coords = struct.unpack_from(f"<{2 * num_points}d", shp, pts_at)
            bounds = list(parts) + [num_points]
            for i in range(num_parts):
                s, e = bounds[i], bounds[i + 1]
                yield [(coords[2 * j], coords[2 * j + 1]) for j in range(s, e)]
        offset = body + content_len * 2


def main():
    print(f"downloading {URL}")
    with urllib.request.urlopen(URL, timeout=60) as r:
        blob = r.read()
    print(f"  {len(blob):,} bytes")

    with zipfile.ZipFile(BytesIO(blob)) as z:
        name = next(n for n in z.namelist() if n.endswith(".shp"))
        shp = z.read(name)

    rings, dropped = [], 0
    for ring in read_polygons(shp):
        lons = [p[0] for p in ring]
        lats = [p[1] for p in ring]
        area = (max(lons) - min(lons)) * (max(lats) - min(lats))
        if area < MIN_RING_BBOX:
            dropped += 1
            continue
        rings.append(
            {
                "bbox": [min(lons), min(lats), max(lons), max(lats)],
                # 4 dp is ~11 m, far finer than a 1:20,000,000 source warrants, and it
                # halves the file relative to full precision.
                "ring": [[round(x, 4), round(y, 4)] for x, y in ring],
            }
        )

    rings.sort(key=lambda r: -len(r["ring"]))
    OUT.write_text(json.dumps(rings, separators=(",", ":")))
    pts = sum(len(r["ring"]) for r in rings)
    print(f"  kept {len(rings)} rings ({pts:,} points), dropped {dropped} tiny islands")
    print(f"  -> {OUT} ({OUT.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
