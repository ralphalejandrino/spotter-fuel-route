"""Minimum-cost refuelling along a fixed route.

The problem
-----------
Given stations at known distances along a route, each with a price per gallon, a tank
that holds `range_miles / mpg` gallons, and a route of length `D`: choose where to stop
and how much to buy so that total spend is minimised.

This is the classic *gas station problem*, and it has a **provably optimal O(n) greedy
solution** -- no dynamic programming and no search required:

    At each station, look ahead as far as one full tank can carry you.
      - If a strictly cheaper station is reachable, buy exactly enough to reach it.
        (Any more would be fuel bought at today's higher price that you could have
        bought cheaper a moment later.)
      - Otherwise, this is the cheapest fuel you will see for a full tank, so fill up.
        (Any less would force a purchase at a strictly higher price later.)

Both branches are locally forced, which is why the greedy is globally optimal rather
than merely a good heuristic.

The cost model, stated explicitly
---------------------------------
The brief says "return the total money spent on fuel". For that number to mean anything,
every gallon burned must be a gallon paid for, so:

  * The tank starts EMPTY, and mile 0 is a departure fill: the vehicle leaves fuelled.
    That fill is priced at the first station the route actually passes, and the response
    says so explicitly (`is_origin_fill`, plus a `priced_from` block naming the station
    and its real mile marker). It is placed at the ORIGIN's coordinates, never the
    station's -- the supplied dataset has just 8 stations in all of California, so a
    Los Angeles departure is priced off a truck stop 280 miles away, and the API should
    say that rather than imply a pump exists downtown.
  * On the final leg we never buy more than is needed to arrive.

Together these give an invariant the test suite asserts directly:

    total_gallons_purchased * mpg == route_miles          (to floating-point tolerance)

i.e. you pay for exactly the fuel the trip consumes -- no free starting tank inflating
how cheap the trip looks, and no leftover fuel inflating how expensive it looks.
"""

from __future__ import annotations

from dataclasses import dataclass, field

EPS = 1e-9


class InfeasibleRoute(Exception):
    """No legal fuelling plan exists -- there is a gap longer than one tank."""

    def __init__(self, message: str, *, gap_miles: float, after_mile: float):
        super().__init__(message)
        self.gap_miles = gap_miles
        self.after_mile = after_mile


@dataclass(frozen=True)
class Candidate:
    """A station that lies on the route corridor, with its distance along the route."""

    station_id: int
    name: str
    address: str
    city: str
    state: str
    lat: float
    lon: float
    price: float
    distance_along_route: float
    detour_miles: float = 0.0


@dataclass
class Stop:
    candidate: Candidate
    gallons: float
    cost: float
    is_origin: bool = False
    priced_from: Candidate | None = None
    """For a departure fill: the real station whose price was used."""


@dataclass
class Plan:
    stops: list[Stop] = field(default_factory=list)
    total_cost: float = 0.0
    total_gallons: float = 0.0
    route_miles: float = 0.0


def _first_cheaper_within_range(
    stations: list[Candidate], i: int, range_miles: float
) -> int | None:
    """Index of the first station after `i` that is both reachable and strictly cheaper.

    Note on the range guard: it is defensive redundancy, not load-bearing arithmetic.
    If a cheaper station were returned from beyond one tank's range, the caller computes
    `min(reach, gallons_to_finish, capacity)`, and `reach > capacity` holds exactly when
    the station is out of range -- so the expression already collapses to the same value
    the `None` branch produces. Mutation testing confirmed this: deleting the guard is an
    *equivalent* mutant that no test can kill, because it cannot change any output. The
    guard stays because it states the intent, and because a future refactor that drops
    the capacity cap would otherwise turn a silent redundancy into a real bug.
    """
    origin = stations[i].distance_along_route
    price = stations[i].price
    for j in range(i + 1, len(stations)):
        if stations[j].distance_along_route - origin > range_miles + EPS:
            return None
        if stations[j].price < price - EPS:
            return j
    return None


def _check_feasible(stations: list[Candidate], route_miles: float, range_miles: float) -> None:
    """Fail loud on any gap a full tank cannot cross.

    A silently-dropped leg would still produce a confident, cheap-looking answer, which
    is a far worse failure than an error the caller can see.
    """
    if not stations:
        raise InfeasibleRoute(
            "No fuel stations found within the search corridor for this route.",
            gap_miles=route_miles,
            after_mile=0.0,
        )

    prev_mile = 0.0
    prev_label = "the route origin"
    for s in stations:
        gap = s.distance_along_route - prev_mile
        if gap > range_miles + EPS:
            raise InfeasibleRoute(
                f"No station within the {range_miles:.0f}-mile vehicle range after "
                f"{prev_label} (mile {prev_mile:.1f}); next station is "
                f"{gap:.1f} miles further on.",
                gap_miles=gap,
                after_mile=prev_mile,
            )
        prev_mile = s.distance_along_route
        where = f", {s.city} {s.state}".rstrip() if s.city else ""
        prev_label = f"{s.name}{where}"

    final_gap = route_miles - prev_mile
    if final_gap > range_miles + EPS:
        raise InfeasibleRoute(
            f"The final {final_gap:.1f} miles after {prev_label} (mile {prev_mile:.1f}) "
            f"exceed the {range_miles:.0f}-mile vehicle range.",
            gap_miles=final_gap,
            after_mile=prev_mile,
        )


def solve(
    stations: list[Candidate],
    route_miles: float,
    *,
    mpg: float = 10.0,
    range_miles: float = 500.0,
    origin_coord: tuple[float, float] | None = None,
) -> Plan:
    """Return the provably cost-optimal refuelling plan for this route.

    `stations` must be sorted by `distance_along_route` and de-duplicated so that at
    most one (the cheapest) station represents any given point on the route.
    """
    if mpg <= 0:
        raise ValueError("mpg must be positive")
    if range_miles <= 0:
        raise ValueError("range_miles must be positive")

    plan = Plan(route_miles=route_miles)
    if route_miles <= EPS:
        return plan

    capacity = range_miles / mpg  # gallons

    # Mile 0 is a purchase point priced at the first station on the corridor -- see the
    # module docstring. Without it the tank starts empty and the vehicle cannot move,
    # and with a free full tank the reported cost would understate the trip.
    # Stations at or beyond the destination are irrelevant -- you never buy fuel after
    # arriving. They must be dropped rather than merely skipped: snapping routinely
    # places a station in the destination city a mile or two past the end of the route,
    # and iterating into one made the solver buy just enough to reach the DESTINATION,
    # then charge itself for the extra mile to the station and report a negative tank.
    working = [s for s in stations if s.distance_along_route < route_miles - EPS]
    priced_from: Candidate | None = None
    if working and working[0].distance_along_route > EPS:
        # The departure fill is placed AT THE ORIGIN and labelled as such. It borrows the
        # price of the first station on the corridor but never its name or coordinates --
        # claiming a Nevada truck stop sits in Los Angeles would be a plainly wrong
        # answer dressed up as a precise one. The output says which station set the price.
        priced_from = working[0]
        working.insert(
            0,
            Candidate(
                station_id=-1,
                name="Departure fill (origin)",
                address="Vehicle departs fuelled",
                city="",
                state="",
                lat=origin_coord[0] if origin_coord else priced_from.lat,
                lon=origin_coord[1] if origin_coord else priced_from.lon,
                price=priced_from.price,
                distance_along_route=0.0,
                detour_miles=0.0,
            ),
        )

    _check_feasible(working, route_miles, range_miles)

    fuel = 0.0  # gallons in the tank
    for i, here in enumerate(working):
        if i > 0:
            fuel -= (here.distance_along_route - working[i - 1].distance_along_route) / mpg
            if fuel < -1e-6:  # pragma: no cover - _check_feasible rules this out
                raise InfeasibleRoute(
                    "Ran out of fuel en route; the station set is inconsistent.",
                    gap_miles=0.0,
                    after_mile=here.distance_along_route,
                )
            fuel = max(fuel, 0.0)

        remaining = route_miles - here.distance_along_route
        if remaining <= EPS:
            break

        gallons_to_finish = remaining / mpg
        cheaper = _first_cheaper_within_range(working, i, range_miles)

        if cheaper is not None:
            reach = (
                working[cheaper].distance_along_route - here.distance_along_route
            ) / mpg
            target = min(reach, gallons_to_finish, capacity)
        else:
            # Nothing cheaper is reachable: this is the best price for a tankful, so
            # fill -- but never more than is needed to arrive.
            target = min(capacity, gallons_to_finish)

        buy = target - fuel
        if buy > EPS:
            cost = buy * here.price
            plan.stops.append(
                Stop(
                    candidate=here,
                    gallons=buy,
                    cost=cost,
                    is_origin=(i == 0 and priced_from is not None),
                    priced_from=priced_from if (i == 0 and priced_from is not None) else None,
                )
            )
            plan.total_cost += cost
            plan.total_gallons += buy
            fuel += buy

    return plan
