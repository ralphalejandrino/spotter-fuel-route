"""Resolve the user's start/finish strings to coordinates -- without a network call.

The brief grades how often we call the external routing/map API. Geocoding the two
endpoints is the obvious place where a naive implementation quietly adds two more calls
per request (and two more failure modes). Instead the same offline gazetteer that placed
the fuel stations resolves the endpoints, so the external call count stays at exactly one
no matter how the caller phrases the request.

Accepted forms:
    "Los Angeles, CA"       city + state       (gazetteer)
    "Los Angeles"           city alone         (gazetteer, if unambiguous nationally)
    "34.0522,-118.2437"     raw coordinates    (no lookup at all)
"""

from __future__ import annotations

import functools
import json
import re
import sys
from pathlib import Path

from django.conf import settings

sys.path.insert(0, str(Path(settings.BASE_DIR) / "scripts"))
from build_gazetteer import fold, variants  # noqa: E402

COORD_RE = re.compile(
    r"^\s*(-?\d+(?:\.\d+)?)\s*[,/ ]\s*(-?\d+(?:\.\d+)?)\s*$"
)

# A cheap pre-filter only -- NOT the actual containment test. See `_in_us`.
US_BBOX = (15.0, 72.0, -180.0, -64.0)  # lat_min, lat_max, lon_min, lon_max

STATE_ABBR = {
    "ALABAMA": "AL", "ALASKA": "AK", "ARIZONA": "AZ", "ARKANSAS": "AR",
    "CALIFORNIA": "CA", "COLORADO": "CO", "CONNECTICUT": "CT", "DELAWARE": "DE",
    "DISTRICT OF COLUMBIA": "DC", "FLORIDA": "FL", "GEORGIA": "GA", "HAWAII": "HI",
    "IDAHO": "ID", "ILLINOIS": "IL", "INDIANA": "IN", "IOWA": "IA", "KANSAS": "KS",
    "KENTUCKY": "KY", "LOUISIANA": "LA", "MAINE": "ME", "MARYLAND": "MD",
    "MASSACHUSETTS": "MA", "MICHIGAN": "MI", "MINNESOTA": "MN", "MISSISSIPPI": "MS",
    "MISSOURI": "MO", "MONTANA": "MT", "NEBRASKA": "NE", "NEVADA": "NV",
    "NEW HAMPSHIRE": "NH", "NEW JERSEY": "NJ", "NEW MEXICO": "NM", "NEW YORK": "NY",
    "NORTH CAROLINA": "NC", "NORTH DAKOTA": "ND", "OHIO": "OH", "OKLAHOMA": "OK",
    "OREGON": "OR", "PENNSYLVANIA": "PA", "RHODE ISLAND": "RI",
    "SOUTH CAROLINA": "SC", "SOUTH DAKOTA": "SD", "TENNESSEE": "TN", "TEXAS": "TX",
    "UTAH": "UT", "VERMONT": "VT", "VIRGINIA": "VA", "WASHINGTON": "WA",
    "WEST VIRGINIA": "WV", "WISCONSIN": "WI", "WYOMING": "WY",
}


class GeocodeError(ValueError):
    """The endpoint string could not be resolved to a US location."""


@functools.lru_cache(maxsize=1)
def _tables():
    data = Path(settings.BASE_DIR) / "data"
    gaz = {
        tuple(k.split("|")): tuple(v)
        for k, v in json.loads((data / "us_gazetteer.json").read_text()).items()
    }
    gap_path = data / "gap_geocodes.json"
    gaps = {}
    if gap_path.exists():
        for k, v in json.loads(gap_path.read_text()).items():
            name, state = k.split("|")
            gaps[(name, state)] = tuple(v)

    by_name: dict[str, list[tuple[str, tuple[float, float]]]] = {}
    for (name, state), coord in {**gaz, **gaps}.items():
        by_name.setdefault(name, []).append((state, coord))
    return {**gaz, **gaps}, by_name


@functools.lru_cache(maxsize=1)
def _boundary():
    """US land rings from the Census cartographic boundary file, as numpy arrays."""
    import numpy as np

    raw = json.loads((Path(settings.BASE_DIR) / "data" / "us_boundary.json").read_text())
    return [
        (tuple(r["bbox"]), np.asarray(r["ring"], dtype=np.float64)) for r in raw
    ]


def _point_in_ring(lon: float, lat: float, ring) -> bool:
    """Standard ray-casting test, vectorised over the ring's edges."""
    import numpy as np

    x, y = ring[:, 0], ring[:, 1]
    x2, y2 = np.roll(x, -1), np.roll(y, -1)
    # Edges that straddle the horizontal line through the point.
    straddles = (y > lat) != (y2 > lat)
    if not straddles.any():
        return False
    xs, ys, xs2, ys2 = x[straddles], y[straddles], x2[straddles], y2[straddles]
    # Longitude at which each straddling edge crosses that line.
    crossing_lon = xs + (lat - ys) * (xs2 - xs) / (ys2 - ys)
    return bool(np.count_nonzero(crossing_lon > lon) % 2 == 1)


def _in_us(lat: float, lon: float) -> bool:
    """Is this coordinate on US land?

    A latitude/longitude box cannot represent a border. The first version of this
    function used one, and it accepted Tijuana, Mexico (32.51, -117.04) as a US location
    -- the API then planned a 1,102-mile route from Denver to another country, which is
    a plain failure of the brief's "both within the USA".

    So: a box as a cheap reject, then an actual point-in-polygon test against the US
    Census nation boundary (1:20,000,000, public domain, 82 rings).
    """
    la0, la1, lo0, lo1 = US_BBOX
    if not (la0 <= lat <= la1 and lo0 <= lon <= lo1):
        return False
    for (bx0, by0, bx1, by1), ring in _boundary():
        if bx0 <= lon <= bx1 and by0 <= lat <= by1 and _point_in_ring(lon, lat, ring):
            return True
    return False


def resolve(text: str) -> tuple[float, float]:
    """Return (lat, lon) for a user-supplied location string. Never hits the network."""
    if not text or not text.strip():
        raise GeocodeError("Location is empty.")
    raw = text.strip()

    m = COORD_RE.match(raw)
    if m:
        lat, lon = float(m.group(1)), float(m.group(2))
        if not _in_us(lat, lon):
            raise GeocodeError(
                f"{lat},{lon} is outside the United States; this API routes US trips only."
            )
        return lat, lon

    exact, by_name = _tables()

    parts = [p.strip() for p in raw.split(",") if p.strip()]
    if len(parts) >= 2:
        city = ", ".join(parts[:-1])
        state_token = fold(parts[-1])
        state = STATE_ABBR.get(state_token, state_token)
        if len(state) == 2:
            for v in variants(city):
                hit = exact.get((v, state))
                if hit:
                    return hit
            raise GeocodeError(
                f"Could not find '{city}' in {state}. Try 'City, ST' or 'lat,lon'."
            )

    # No state given: accept only if the name is unambiguous nationally.
    for v in variants(raw):
        matches = by_name.get(v)
        if not matches:
            continue
        distinct = {c for _, c in matches}
        if len(distinct) == 1:
            return matches[0][1]
        states = sorted({s for s, _ in matches})
        raise GeocodeError(
            f"'{raw}' is ambiguous -- it exists in {len(states)} states "
            f"({', '.join(states[:6])}{'...' if len(states) > 6 else ''}). "
            f"Add a state, e.g. '{raw}, {states[0]}'."
        )

    raise GeocodeError(
        f"Could not resolve '{raw}' to a US location. "
        f"Use 'City, ST' (e.g. 'Denver, CO') or 'lat,lon'."
    )
