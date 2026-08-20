"""Tier-2 geocoding for the ~5% of CSV cities the offline Census gazetteer cannot resolve.

These are unincorporated communities (Breezewood PA, Clines Corners NM) -- real truck-stop
locations at highway junctions that are not incorporated Census places. Nominatim resolves
them; the Census street-level geocoder cannot, because the CSV has no street addresses.

This runs ONCE at build time and its output is committed. Nothing geocodes at request time.
Rate-limited to Nominatim's 1 req/sec acceptable-use policy.
"""
import csv, json, sys, time, urllib.parse, urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_gazetteer import lookup  # noqa: E402

DATA = Path(__file__).resolve().parent.parent / "data"
OUT = DATA / "gap_geocodes.json"
UA = "spotter-fuel-route-assessment/1.0 (one-off build-time geocoding)"

STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
    "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois",
    "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana",
    "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan",
    "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri", "MT": "Montana",
    "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey",
    "NM": "New Mexico", "NY": "New York", "NC": "North Carolina", "ND": "North Dakota",
    "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania",
    "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee",
    "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming", "PR": "Puerto Rico",
}
CANADA = {"AB", "BC", "MB", "NB", "NL", "NS", "NT", "NU", "ON", "PE", "QC", "SK", "YT"}


def geocode(city, state):
    q = f"{city}, {STATES.get(state, state)}, USA"
    url = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(
        {"q": q, "format": "json", "limit": 1, "countrycodes": "us"}
    )
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            hits = json.load(r)
    except Exception as e:
        return None, f"ERR {type(e).__name__}"
    if not hits:
        return None, "no match"
    return (round(float(hits[0]["lat"]), 6), round(float(hits[0]["lon"]), 6)), hits[0]["display_name"][:60]


def main():
    gaz = {tuple(k.split("|")): tuple(v)
           for k, v in json.loads((DATA / "us_gazetteer.json").read_text()).items()}
    rows = list(csv.DictReader(
        open(DATA / "fuel-prices-for-be-assessment.csv", encoding="utf-8-sig")))
    pairs = sorted({(r["City"].strip(), r["State"].strip().upper())
                    for r in rows if r["State"].strip().upper() not in CANADA})
    gaps = [p for p in pairs if not lookup(gaz, *p)]

    done = json.loads(OUT.read_text()) if OUT.exists() else {}
    print(f"gaps: {len(gaps)}  already cached: {len(done)}", flush=True)

    for i, (city, state) in enumerate(gaps, 1):
        key = f"{city.upper()}|{state}"
        if key in done:
            continue
        coord, note = geocode(city, state)
        if coord:
            done[key] = coord
        print(f"  [{i}/{len(gaps)}] {city}, {state} -> {coord} {note}", flush=True)
        OUT.write_text(json.dumps(done, indent=0))
        time.sleep(1.15)  # Nominatim acceptable use: max 1 req/sec

    print(f"\nresolved {len(done)}/{len(gaps)}; unresolved stay EXCLUDED, never guessed.", flush=True)


if __name__ == "__main__":
    main()
