"""Normalising US place names, so the same rules resolve a station and an endpoint.

This is the shared vocabulary between two things that must agree exactly: the build-time
script that geocodes the CSV's 6,738 truck stops, and the request-time geocoder that
resolves the caller's `start` and `finish`. If they normalised names differently, a city
would be findable as a station but not as an endpoint -- so the rules live in one module
and both sides import them.

Pure string work. No Django, no I/O, no network -- which is what lets the build script
import it without booting a settings module.
"""

from __future__ import annotations

import re

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
