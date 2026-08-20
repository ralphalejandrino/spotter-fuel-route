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

# Rough continental-US + Alaska/Hawaii envelope. The brief says both endpoints are
# within the USA; a silent typo that puts a route in the Atlantic should be rejected,
# not routed.
US_BOUNDS = (18.0, 72.0, -180.0, -66.0)  # lat_min, lat_max, lon_min, lon_max

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


def _in_us(lat: float, lon: float) -> bool:
    la0, la1, lo0, lo1 = US_BOUNDS
    return la0 <= lat <= la1 and lo0 <= lon <= lo1


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
