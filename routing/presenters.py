"""Turning a solved Plan into the JSON body the API returns.

This lives apart from `solver.py` on purpose. That module is the one a reviewer is most
likely to read closely -- it carries the argument for why the greedy is provably optimal
-- and response shape, rounding and user-facing prose are not part of that argument.
Keeping them here leaves the solver as the algorithm and nothing else.
"""

from __future__ import annotations

from routing.solver import Plan, Stop


def _priced_from(stop: Stop) -> dict:
    """The departure fill borrows a price from a real station; say which, and why.

    Only a departure fill carries this, so for every ordinary stop it contributes
    nothing to the payload.
    """
    src = stop.priced_from
    if src is None:
        return {}
    return {
        "priced_from": {
            "station_id": src.station_id,
            "name": src.name,
            "city": src.city,
            "state": src.state,
            "mile_marker": round(src.distance_along_route, 1),
        },
        "note": (
            "Departure fill at the origin. No station in the dataset lies on the "
            f"corridor before mile {src.distance_along_route:.1f}, so this fill is "
            "priced at the first one that does."
        ),
    }


def stop_as_dict(stop: Stop, order: int) -> dict:
    c = stop.candidate
    return {
        "order": order,
        "station_id": c.station_id,
        "name": c.name,
        "address": c.address,
        "city": c.city,
        "state": c.state,
        "lat": c.lat,
        "lon": c.lon,
        "price_per_gallon": round(c.price, 4),
        "gallons": round(stop.gallons, 3),
        "cost_usd": round(stop.cost, 2),
        "mile_marker": round(c.distance_along_route, 1),
        # How far off the route this stop actually sits. The corridor search already
        # measures it exactly, so reporting it costs nothing and lets a driver see
        # that an "optimal" stop is not a 9-mile detour they were never told about.
        "detour_miles": c.detour_miles,
        "is_origin_fill": stop.is_origin,
        **_priced_from(stop),
    }


def plan_as_dict(plan: Plan) -> dict:
    return {
        "total_fuel_cost_usd": round(plan.total_cost, 2),
        "total_gallons": round(plan.total_gallons, 3),
        "fuel_stops": [stop_as_dict(s, i + 1) for i, s in enumerate(plan.stops)],
    }
