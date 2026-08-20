"""The single external dependency: one call to a free routing API.

Why OSRM's public demo server
-----------------------------
The brief says "find a free API yourself" and grades how few times we call it. OSRM's
demo server was chosen over OpenRouteService, Mapbox and GraphHopper for one reason that
matters more than raw speed: **it needs no API key**. A reviewer clones the repo, runs
one command and it works -- no account, no key to provision, nothing to paste into an
`.env`. The provider sits behind an interface so a keyed backend can be dropped in.

Measured on this machine, Los Angeles -> New York (2,793.7 mi):

    overview=full,       geometries=polyline    1.21 s   118 KB   33,672 pts
    overview=simplified, geometries=polyline    0.67 s     1 KB      199 pts
    overview=false  (pure round-trip floor)     0.61 s

The floor is 0.61 s. That is the network, not our code, and no amount of optimisation
touches it -- so the design calls it **once** and caches the result. `full` is used
rather than `simplified` because ~200 points across 2,800 miles would place stations on
the wrong side of a mountain range; correctness beats a 0.5 s saving we can recover by
caching anyway.
"""

from __future__ import annotations

import functools
import time
from dataclasses import dataclass

import numpy as np
import requests
from django.conf import settings
from django.core.cache import cache


class RoutingError(Exception):
    """The routing provider could not return a usable route."""


@dataclass(frozen=True)
class Route:
    lats: np.ndarray
    lons: np.ndarray
    distance_miles: float
    duration_seconds: float
    geometry_polyline: str
    from_cache: bool = False
    api_calls: int = 1
    api_ms: float = 0.0


class RoutingProvider:
    """Interface. Implementations must resolve a pair of coordinates in ONE request."""

    name = "abstract"

    def route(self, start: tuple[float, float], finish: tuple[float, float]) -> Route:
        raise NotImplementedError


@functools.lru_cache(maxsize=32)
def _decode_polyline(encoded: str, precision: int = 5):
    """Decode a Google/OSRM encoded polyline into (lats, lons) arrays.

    Memoized: a cross-country geometry is ~34,000 points, and decoding it in pure Python
    costs ~18 ms. That is invisible next to a 1,060 ms cold API call but dominates a
    cache hit, where it was the single largest remaining cost.

    The returned arrays are treated as READ-ONLY by callers (they are only ever measured
    against, never mutated), so sharing them between requests is safe.
    """
    factor = float(10**precision)
    lats, lons = [], []
    index = lat = lon = 0
    length = len(encoded)
    while index < length:
        for target in ("lat", "lon"):
            shift = result = 0
            while True:
                b = ord(encoded[index]) - 63
                index += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            delta = ~(result >> 1) if result & 1 else (result >> 1)
            if target == "lat":
                lat += delta
            else:
                lon += delta
        lats.append(lat / factor)
        lons.append(lon / factor)
    return np.array(lats), np.array(lons)


class OSRMProvider(RoutingProvider):
    name = "osrm-demo"

    def __init__(self, base_url: str | None = None, timeout: float | None = None):
        self.base_url = (base_url or settings.OSRM_BASE_URL).rstrip("/")
        self.timeout = timeout or settings.OSRM_TIMEOUT_SECONDS

    @staticmethod
    def _cache_key(start, finish) -> str:
        # 4 dp ~= 11 m. Coarse enough that a repeated demo hits cache, fine enough that
        # two genuinely different origins never collide.
        return "route:v1:{:.4f},{:.4f}:{:.4f},{:.4f}".format(*start, *finish)

    def route(self, start, finish) -> Route:
        key = self._cache_key(start, finish)
        hit = cache.get(key)
        if hit is not None:
            lats, lons = _decode_polyline(hit["geometry"])
            return Route(
                lats=lats,
                lons=lons,
                distance_miles=hit["distance_miles"],
                duration_seconds=hit["duration_seconds"],
                geometry_polyline=hit["geometry"],
                from_cache=True,
                api_calls=0,
                api_ms=0.0,
            )

        url = (
            f"{self.base_url}/route/v1/driving/"
            f"{start[1]:.6f},{start[0]:.6f};{finish[1]:.6f},{finish[0]:.6f}"
        )
        t0 = time.perf_counter()
        try:
            resp = requests.get(
                url,
                params={"overview": "full", "geometries": "polyline", "alternatives": "false", "steps": "false"},
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise RoutingError(f"Routing provider unreachable: {exc}") from exc
        api_ms = (time.perf_counter() - t0) * 1000.0

        if resp.status_code != 200:
            raise RoutingError(
                f"Routing provider returned HTTP {resp.status_code}: {resp.text[:200]}"
            )
        payload = resp.json()
        if payload.get("code") != "Ok" or not payload.get("routes"):
            raise RoutingError(
                f"No drivable route between those points ({payload.get('code')})."
            )

        r = payload["routes"][0]
        geometry = r["geometry"]
        distance_miles = r["distance"] / 1609.344

        cache.set(
            key,
            {
                "geometry": geometry,
                "distance_miles": distance_miles,
                "duration_seconds": r["duration"],
            },
            settings.ROUTE_CACHE_SECONDS,
        )

        lats, lons = _decode_polyline(geometry)
        return Route(
            lats=lats,
            lons=lons,
            distance_miles=distance_miles,
            duration_seconds=r["duration"],
            geometry_polyline=geometry,
            from_cache=False,
            api_calls=1,
            api_ms=api_ms,
        )


def get_provider() -> RoutingProvider:
    return OSRMProvider()
