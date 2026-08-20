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
import csv, io, json, re, sys, zipfile
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"

SOURCES = [
    ("gaz.zip", "2024_Gaz_place_national.txt"),
    ("gaz_cousub.zip", "2024_Gaz_cousubs_national.txt"),
]

# One trailing LSAD-style descriptor. Applied ADDITIVELY -- we index the raw name too --
# because stripping destructively turns "BAY CITY" into "BAY" and loses a real match.
SUFFIX = re.compile(
    r"\s+(city and borough|consolidated government|unified government|"
    r"metropolitan government|urban county government|charter township|township|"
    r"municipality|CDP|city|town|village|borough|plantation|comunidad|"
    r"zona urbana|gore|grant|location|reservation|district|precinct|division)$",
    re.I,
)

ACCENTS = str.maketrans("ÁÀÂÄÃÅÉÈÊËÍÌÎÏÓÒÔÖÕÚÙÛÜÑÇ", "AAAAAAEEEEIIIIOOOOOUUUUNC")


def fold(s: str) -> str:
    return s.strip().upper().translate(ACCENTS)


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


def variants(city: str):
    """Spelling variants seen between OPIS city names and Census place names."""
    c = fold(city)
    seen = set()
    for v in (
        c, c.replace(".", ""), c.replace("'", ""), c.replace("-", " "),
        c.replace(" ", ""), c + " CITY",
        re.sub(r"^ST\.? ", "SAINT ", c), re.sub(r"^SAINT ", "ST ", c),
        re.sub(r"^MT\.? ", "MOUNT ", c), re.sub(r"^FT\.? ", "FORT ", c),
        re.sub(r"^N ", "NORTH ", c), re.sub(r"^S ", "SOUTH ", c),
        re.sub(r"^E ", "EAST ", c), re.sub(r"^W ", "WEST ", c),
        # OPIS drops the apostrophe the Census keeps: ODONNELL -> O'DONNELL.
        re.sub(r"^O([A-Z])", r"O'\1", c),
    ):
        v = v.strip()
        if v and v not in seen:
            seen.add(v)
            yield v


def lookup(gaz, city, state):
    st = state.strip().upper()
    for v in variants(city):
        hit = gaz.get((v, st))
        if hit:
            return hit
    return None


if __name__ == "__main__":
    gaz = build()
    out = DATA / "us_gazetteer.json"
    out.write_text(json.dumps({f"{n}|{s}": c for (n, s), c in gaz.items()}))
    print(f"gazetteer keys: {len(gaz):,} -> {out} ({out.stat().st_size/1e6:.1f} MB)")
