"""Build an offline US place-name -> (lat, lon) gazetteer from public-domain
US Census Gazetteer files.

Why this exists
---------------
The supplied fuel-price CSV identifies each truck stop only by City + State; 96.6% of
its `Address` values are highway-relative ("I-44, EXIT 283 & US-69") rather than street
addresses, so a street-level geocoder cannot resolve them. City centroids are both
sufficient (we snap stations to a route corridor tens of miles wide) and obtainable
entirely offline -- which keeps request-time external calls at exactly one.

Run once; the output is committed so a reviewer never geocodes anything.
"""
import csv, io, json, sys, zipfile
from pathlib import Path

# The name-normalising rules are shared with the request-time geocoder and live in the
# app, which is the direction the dependency belongs: a build script may reach into the
# application, never the reverse.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from routing.placenames import SUFFIX, fold  # noqa: E402

DATA = Path(__file__).resolve().parent.parent / "data"

SOURCES = [
    ("gaz.zip", "2024_Gaz_place_national.txt"),
    ("gaz_cousub.zip", "2024_Gaz_cousubs_national.txt"),
]

def add(gaz, name, state, coord):
    if name:
        gaz.setdefault((name, state), coord)


def build():
    gaz = {}
    for zf, member in SOURCES:
        path = DATA / zf
        if not path.exists():
            sys.exit(f"missing {path} -- see README for the download URLs")
        with zipfile.ZipFile(path) as z:
            txt = io.TextIOWrapper(z.open(member), encoding="utf-8")
            reader = csv.reader(txt, delimiter="\t")
            # Census pads header cells with trailing spaces; unstripped names KeyError
            # into an empty result that looks exactly like a healthy one.
            header = [h.strip() for h in next(reader)]
            iU, iN = header.index("USPS"), header.index("NAME")
            iLa, iLo = header.index("INTPTLAT"), header.index("INTPTLONG")
            for row in reader:
                if len(row) <= iLo:
                    continue
                try:
                    coord = (round(float(row[iLa]), 6), round(float(row[iLo].strip()), 6))
                except ValueError:
                    continue
                state, raw = row[iU].strip().upper(), fold(row[iN])
                add(gaz, raw, state, coord)
                stripped = SUFFIX.sub("", raw).strip()
                if stripped != raw:
                    add(gaz, stripped, state, coord)
                if "-" in stripped:
                    add(gaz, stripped.split("-")[0].strip(), state, coord)
                if stripped.endswith(" COUNTY"):
                    add(gaz, stripped[:-7].strip(), state, coord)
                if " " in stripped:
                    add(gaz, stripped.replace(" ", ""), state, coord)
    return gaz


if __name__ == "__main__":
    gaz = build()
    out = DATA / "us_gazetteer.json"
    out.write_text(json.dumps({f"{n}|{s}": c for (n, s), c in gaz.items()}))
    print(f"gazetteer keys: {len(gaz):,} -> {out} ({out.stat().st_size/1e6:.1f} MB)")
