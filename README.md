# Fuel-optimal route API

Give it two US locations. It returns the route, the cost-optimal places to refuel a
500-mile-range truck along it, and what the fuel will cost at 10 mpg.

```
GET /api/v1/route/?start=Los Angeles, CA&finish=New York, NY
```

```
2,810.7 miles · 18 fuel stops · 281.1 gallons · $868.33
1 external API call · ~1.1 s cold · 23 ms cached
```

There is also a map at `/map/?start=Los Angeles, CA&finish=New York, NY`.

---

## Run it

```bash
docker compose up --build      # http://127.0.0.1:8000/map/
```

No API key, no account, no `.env` to fill in. Or without Docker:

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py load_fuel_prices     # under 1 s; reads the CSV in data/
python manage.py runserver
```

`docs/spotter-fuel-route.postman_collection.json` is a Postman collection covering the
happy paths, both input formats and all three error paths. Run requests 2 and 3 back to
back to watch the second one report `external_api_calls: 0`.

```bash
python manage.py test routing         # 41 tests, no network required
```

---

## The design, and why

Two of the brief's requirements are performance requirements, and they are the only two
with a stated target: *"the quicker the better"* and *"one call to the map/route API is
ideal"*. Everything below follows from taking those as the real specification.

### One external call, and everything else local

```
POST /api/v1/route/  {"start": "...", "finish": "..."}
  │
  1. Resolve both endpoints to coordinates    offline gazetteer     0 calls
  2. Fetch the route                          ★ THE ONE CALL ★      1 call  (0 if cached)
  3. Decode polyline → cumulative distances   numpy                 ~2 ms
  4. Find stations near the route             spatial join          ~22 ms
  5. Choose stops and quantities              O(n) greedy           ~0.2 ms
  6. Respond with geometry, stops, cost and a timing breakdown
```

Geocoding the two endpoints is the obvious place a naive implementation quietly adds two
more API calls per request. Here it uses the same offline gazetteer that placed the fuel
stations, so the count stays at one however the caller phrases the request — `"Denver, CO"`
and `"39.7392,-104.9903"` both cost zero lookups.

### Where the time actually goes

Measured on this machine, Los Angeles → New York:

| | cold | cached |
|---|---:|---:|
| geocode | 0.3 ms | 0.04 ms |
| **routing API** | **1,063 ms** | 0 ms |
| corridor search | 23 ms | 22 ms |
| solver | 0.25 ms | 0.2 ms |
| **total** | **~1,087 ms** | **23 ms** |

Cold is dominated entirely by the one upstream call, and that call is not stable: the
same route measured 1,063 ms, 1,652 ms and 2,780 ms across one evening against the public
OSRM demo server. Everything this app does is the ~23 ms, cold or warm.

The routing call is 98% of a cold request and there is nothing to optimise inside it —
a bare `overview=false` round trip to the public OSRM demo server still takes 0.61 s, so
that is the network floor, not our code. Which is exactly why the design spends its one
call and then caches the result.

Every response carries this breakdown and its own call count:

```json
"performance": {
  "external_api_calls": 1,
  "route_api_cached": false,
  "timings_ms": { "geocode": 0.22, "route_api": 1063.14,
                  "corridor": 23.12, "solver": 0.25, "total": 1086.84 }
}
```

The brief grades call count, so the API answers that question in its own payload rather
than asking anyone to take it on trust — and `routing/tests/test_api.py` asserts it:

```python
def test_exactly_one_external_api_call(self):
    ...
    self.assertEqual(provider.calls, 1)
```

### Why OSRM's public demo server

OpenRouteService, Mapbox and GraphHopper are all faster from this location. OSRM's demo
server was chosen anyway for one reason that outweighs it: **it needs no API key.** You
clone this repo and it works. The provider sits behind a `RoutingProvider` interface, and
pointing `OSRM_BASE_URL` at a self-hosted OSRM drops the cold call to single-digit
milliseconds without touching a line of application code.

### The solver is provably optimal, not a heuristic

Minimum-cost refuelling with a fixed tank is the classic *gas station problem*. It has an
O(n) greedy solution that is genuinely optimal — no dynamic programming, no search:

> At each station, look ahead as far as one tank can carry you.
> If a cheaper station is reachable, buy **exactly enough to reach it**.
> Otherwise this is the cheapest fuel you will see for a full tank, so **fill up**.

Both branches are locally forced, which is what makes the greedy globally optimal.

`routing/tests/test_solver.py` computes each expected optimum by hand in the test's own
docstring, and pairs it with a **negative control** — the "only buy what the next hop
needs" greedy most people write first — shown to cost strictly more ($320 vs $310 on the
worked example). A test that only asserts the solver returned *something* proves nothing.

The suite was also **mutation-tested**. Four deliberate breakages: two were caught
immediately, one exposed a real gap (an infeasibility test that passed via the wrong code
path — now fixed), and one turned out to be an *equivalent* mutant that cannot change any
output. That last one is documented as such in `solver.py` rather than left looking
untested.

### "Both within the USA" is a polygon test, not a bounding box

The brief requires both endpoints to be in the United States. The first implementation
checked a latitude/longitude rectangle, which is wrong in the way rectangles are always
wrong about countries — it accepted **Tijuana, Mexico** and planned a 1,102-mile
international route.

Containment is now an actual point-in-polygon test against the US Census cartographic
boundary for the nation (1:5,000,000, public domain, 295 rings, built offline by
`scripts/build_us_boundary.py`). 0.095 ms per check, no new dependency.

`routing/tests/test_geocode.py` pins the hard pairs — Tijuana vs San Diego, Ciudad Juárez
vs El Paso, and **Windsor, Ontario vs Detroit, 1.5 km apart across the river**. It also
carries a negative control asserting that at least one *rejected* point falls inside the
US bounding envelope, so the suite cannot be satisfied by quietly reverting to a box.

### Cost model, stated rather than assumed

For "total money spent on fuel" to mean anything, every gallon burned must be a gallon
paid for. So the tank starts empty, mile 0 is a departure fill, and the final leg never
buys more than is needed to arrive. That gives an invariant the tests assert directly:

```
total_gallons_purchased × mpg == route_miles
```

No free starting tank making the trip look cheap, no leftover fuel making it look dear.

---

## What is actually in the supplied CSV

The data needed real work before it could be used. Everything here is measured, not assumed.

| | |
|---|---|
| Rows | 8,151 |
| Distinct OPIS truckstop IDs | 6,738 |
| **Canadian rows** (AB, BC, MB, NB, NS, ON, QC, SK, YT) | **620 — excluded**, the brief says USA |
| IDs appearing more than once | 678 |
| …of those, **disagreeing on price** | **597** |
| **Addresses that are highway-relative**, not street addresses | **7,875 = 96.6%** |
| Rows with untrimmed whitespace | 1,259 |
| **Stations loaded** | **6,626 — 100% geocoded** |

**Repeated stations.** 678 IDs repeat, and 597 of those disagree on price while agreeing
on city, state and Rack ID — there is no date or fuel-grade column to separate them, so
they are repeated observations of one pump. They are aggregated by **median** (spread
within a single station reaches $0.90, and the median shrugs that off). `--aggregate
mean|min|max` is available on the loader.

**Geocoding.** 96.6% of `Address` values look like `"I-44, EXIT 283 & US-69"` — a highway
junction, not a street address — so street-level geocoding is impossible in principle, not
merely inconvenient. City centroids are used instead, which is ample when stations are
matched to a 10-mile-wide corridor. Coverage:

| source | resolved |
|---|---|
| US Census Gazetteer — places + county subdivisions (public domain, offline) | 95.1% |
| Nominatim, one-off for unincorporated communities (Breezewood PA, Clines Corners NM) | the remaining 4.9% |
| **Total** | **100%** |

Both run **once**, at build time (`scripts/`), and the results are committed. A request
never geocodes. Anything that could not be resolved would be **excluded and counted, never
guessed** — a station placed in the wrong state could be picked as an optimal stop that
does not exist.

> **⚠ The dataset has 8 stations in the whole of California**, all in the Imperial Valley,
> the nearest 132 miles from Los Angeles. A route out of LA therefore has no station for
> its first 250 miles, and the departure fill is priced off one 250 miles down the road.
> The API says so — `is_origin_fill`, a `priced_from` block naming the real station, and a
> plain-English `note` — rather than printing a Nevada truck stop's name next to
> Los Angeles and hoping nobody looks.

---

## Making the corridor search fast

Finding which of 6,626 stations lie near a 34,000-point polyline is the only part of a
request we control. Done naively it is 220M distance calculations.

1. **Downsample the route** to a point every ~2 miles.
2. **Spatial join, not a cross product.** Stations are bucketed once at startup into
   0.5° grid cells; route samples are bucketed the same way; each cell's samples are
   compared only against stations in that cell's neighbourhood.
3. **Guaranteed superset, then exact refinement.** Distance to a sampled polyline can only
   *overstate* the truth, so the coarse pass runs with a margin and every survivor is
   re-measured against the full-resolution geometry before the real cutoff is applied.

Step 3 exists because of a bug this found: three Plainfield IL stations at a true 9.898
miles measured 10.025 against a 10.0-mile cutoff and silently vanished. Silently is the
problem — a missing station makes a trip look *cheaper*, so it never looks like an error.

Verified against a brute-force all-pairs reference on four transcontinental routes:

| route | brute force | this | speed-up | missed | extra |
|---|---:|---:|---:|---:|---:|
| Los Angeles → New York | 8,349 ms | 22.4 ms | 373× | 0 | 0 |
| Seattle → Miami | 8,823 ms | 20.5 ms | 431× | 0 | 0 |
| Denver → Chicago | 19,101 ms | 8.6 ms | 2,223× | 0 | 0 |
| Portland → Boston | 9,501 ms | 24.2 ms | 392× | 0 | 0 |

---

## API

### `GET|POST /api/v1/route/`

| parameter | required | default | notes |
|---|---|---|---|
| `start` | yes | — | `"City, ST"`, `"City"` if unambiguous, or `"lat,lon"` |
| `finish` | yes | — | same |
| `mpg` | no | `10` | |
| `range_miles` | no | `500` | tank capacity is derived as `range_miles / mpg` |
| `corridor_miles` | no | `10` | how far off the road a station may sit |

Errors are typed and specific: `400 geocode` (with a suggestion), `400 infeasible_route`
(naming the gap in miles and where it starts), `502 routing_provider`.

### `GET /api/v1/health/`
Station count and the configured provider.

### `GET /map/`
Leaflet map — route, numbered stops, cost panel, live timing breakdown.

---

## Layout

```
routing/solver.py       the greedy, the cost model, the fail-loud feasibility check
routing/corridor.py     grid index + spatial join + exact refinement
routing/providers.py    RoutingProvider interface, OSRM implementation, caching
routing/geocode.py      offline endpoint resolution
routing/service.py      the pipeline and its timing instrumentation
scripts/                one-off build-time geocoding (not on the request path)
```

## Known limits

- Fuel prices are a static snapshot; there is no date column in the source, so "current"
  is whatever the CSV says.
- City-centroid geocoding places a station within a few miles of its true position. That
  is well inside the corridor tolerance but it is not a street address.
- The public OSRM demo server has no SLA, and its latency is genuinely variable — cold
  calls measured between 0.6 s and 2.8 s for the same route on the same night. Nothing in
  this app changes across those runs; `OSRM_BASE_URL` repoints it at a self-hosted
  instance if that variance matters.
- US containment is resolved at 1:5,000,000. That is good to roughly a kilometre, which
  is enough to separate Detroit from Windsor, but it is not a survey-grade border.
- The route cache is in-process (`LocMemCache`). Multi-worker deployments should point
  `CACHES` at Redis so the cache is shared.
