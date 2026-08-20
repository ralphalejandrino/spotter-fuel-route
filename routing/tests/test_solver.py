"""Solver tests.

Every optimum here is computed BY HAND in the test's own docstring, and every case
carries a negative control -- an alternative strategy that a reasonable person might
implement, shown to cost strictly more. A test that only asserts "the solver returned
something" proves nothing; these assert that this particular answer is the best one.
"""

from django.test import SimpleTestCase

from routing.solver import Candidate, InfeasibleRoute, Plan, solve


def stn(mile: float, price: float, sid: int = 0) -> Candidate:
    return Candidate(
        station_id=sid or int(mile),
        name=f"STOP@{mile:g}",
        address="I-00, EXIT 1",
        city="Testville",
        state="TX",
        lat=0.0,
        lon=0.0,
        price=price,
        distance_along_route=mile,
    )


def buy_only_enough_to_reach_next(stations, route_miles, mpg=10.0, range_miles=500.0):
    """NEGATIVE CONTROL: a plausible-but-wrong greedy.

    'Never carry more fuel than you need for the next hop.' It is the strategy most
    people write first. It is not optimal, because it declines to stock up when fuel
    is cheap and the next station is dearer.
    """
    cost = 0.0
    pts = list(stations) + [None]
    for i, here in enumerate(stations):
        nxt = route_miles if i + 1 == len(stations) else stations[i + 1].distance_along_route
        cost += ((nxt - here.distance_along_route) / mpg) * here.price
    return cost


class SolverTests(SimpleTestCase):
    def test_single_station_buys_exactly_the_trip(self):
        """100 mi at 10 mpg = 10 gal, all at $3.00 => $30.00."""
        plan = solve([stn(0, 3.00)], 100.0)
        self.assertEqual(len(plan.stops), 1)
        self.assertAlmostEqual(plan.total_gallons, 10.0, places=6)
        self.assertAlmostEqual(plan.total_cost, 30.00, places=6)

    def test_defers_purchase_to_a_cheaper_station_ahead(self):
        """Stations: mile 0 $3.00, mile 50 $2.00. Route 100 mi.

        Optimal by hand: buy only the 5 gal needed to reach mile 50 ($15), then the
        remaining 5 gal at the cheaper price ($10). Total $25.00.
        """
        plan = solve([stn(0, 3.00), stn(50, 2.00)], 100.0)
        self.assertAlmostEqual(plan.total_cost, 25.00, places=6)
        self.assertEqual([round(s.gallons, 3) for s in plan.stops], [5.0, 5.0])

        # Negative control: filling the tank at the first (dearer) station.
        naive_fill = 50.0 * 3.00
        self.assertLess(plan.total_cost, naive_fill)

    def test_stocks_up_when_fuel_ahead_is_dearer(self):
        """Stations: mile 0 $2.00, mile 50 $3.00. Route 100 mi.

        Optimal by hand: buy all 10 gal at $2.00 => $20.00, and never stop again.
        """
        plan = solve([stn(0, 2.00), stn(50, 3.00)], 100.0)
        self.assertAlmostEqual(plan.total_cost, 20.00, places=6)
        self.assertEqual(len(plan.stops), 1, "should not stop at the dearer station")

    def test_range_forces_a_purchase_at_a_dearer_station(self):
        """The case that separates a real solver from a price-sorter.

        Route 1000 mi, 10 mpg, 500 mi range (50 gal tank).
        Stations: mile 0 $3.00, mile 400 $4.00, mile 800 $2.00.

        Hand-computed optimum:
          * The trip burns 100 gal and every gallon must be bought.
          * The $2.00 fuel at mile 800 can only serve the final 200 mi = 20 gal.
          * A tank holds 50 gal, so at most 50 gal of the cheap $3.00 fuel can be
            carried out of mile 0 -- that reaches mile 500.
          * The remaining 30 gal must therefore be bought at $4.00 at mile 400.
          => 50*3.00 + 30*4.00 + 20*2.00 = 150 + 120 + 40 = $310.00
        """
        stations = [stn(0, 3.00), stn(400, 4.00), stn(800, 2.00)]
        plan = solve(stations, 1000.0)

        self.assertAlmostEqual(plan.total_cost, 310.00, places=6)
        self.assertEqual([round(s.gallons, 3) for s in plan.stops], [50.0, 30.0, 20.0])

        # Negative control: the "only buy what the next hop needs" greedy costs $320.
        control = buy_only_enough_to_reach_next(stations, 1000.0)
        self.assertAlmostEqual(control, 320.00, places=6)
        self.assertLess(plan.total_cost, control)

    def test_every_gallon_burned_is_a_gallon_paid_for(self):
        """The invariant the whole cost model rests on."""
        stations = [stn(0, 3.10), stn(220, 2.90), stn(560, 3.60), stn(900, 2.75)]
        route = 1180.0
        plan = solve(stations, route, mpg=10.0, range_miles=500.0)
        self.assertAlmostEqual(plan.total_gallons * 10.0, route, places=4)

    def test_never_overbuys_on_the_final_leg(self):
        """Cheap fuel 20 mi from the end must not trigger a full 50-gal tank.

        Stations are spaced within the 500-mile range so the route is feasible; the
        point under test is the last stop, where only 2 gal (20 mi / 10 mpg) is needed
        however cheap the fuel is.
        """
        stations = [stn(0, 4.00), stn(450, 4.00), stn(900, 4.00), stn(980, 1.00)]
        plan = solve(stations, 1000.0)
        last = plan.stops[-1]
        self.assertAlmostEqual(last.candidate.distance_along_route, 980.0, places=3)
        self.assertAlmostEqual(last.gallons, 2.0, places=6)

    def test_origin_fill_is_flagged_and_attributed_not_faked(self):
        """A station 30 mi in still lets the trip start -- and the output is honest.

        The departure fill must sit at the ORIGIN's coordinates, carry no borrowed
        station identity, and name the station whose price it used.
        """
        plan = solve([stn(30, 3.00)], 100.0, origin_coord=(40.0, -100.0))
        first = plan.stops[0]
        self.assertTrue(first.is_origin)
        self.assertEqual(first.candidate.distance_along_route, 0.0)
        self.assertEqual((first.candidate.lat, first.candidate.lon), (40.0, -100.0))
        self.assertEqual(first.candidate.station_id, -1)
        self.assertIsNotNone(first.priced_from)
        self.assertEqual(first.priced_from.distance_along_route, 30.0)
        self.assertAlmostEqual(plan.total_gallons * 10.0, 100.0, places=6)

        payload = plan.as_dict()["fuel_stops"][0]
        self.assertTrue(payload["is_origin_fill"])
        self.assertEqual(payload["priced_from"]["mile_marker"], 30.0)
        self.assertIn("priced at the first one that does", payload["note"])

    def test_short_route_needs_no_stop_beyond_the_first(self):
        plan = solve([stn(0, 3.00)], 50.0)
        self.assertEqual(len(plan.stops), 1)
        self.assertAlmostEqual(plan.total_gallons, 5.0, places=6)

    def test_zero_length_route_is_free(self):
        plan = solve([stn(0, 3.00)], 0.0)
        self.assertEqual(plan.stops, [])
        self.assertEqual(plan.total_cost, 0.0)

    # ---- fail-loud cases: a wrong answer here is worse than an error ----------------

    def test_gap_longer_than_range_is_rejected(self):
        """A mid-route gap must be caught by the mid-route check specifically.

        The final leg here is deliberately SHORT (50 mi) and therefore legal, so the
        end-of-route check cannot mask a broken mid-route check. An earlier version of
        this test used a route whose final leg was also over-range, and it passed even
        with the mid-route check disabled -- it was testing the wrong branch.
        """
        with self.assertRaises(InfeasibleRoute) as ctx:
            solve([stn(0, 3.00), stn(600, 3.00), stn(700, 3.00)], 750.0)
        self.assertAlmostEqual(ctx.exception.gap_miles, 600.0, places=3)
        self.assertAlmostEqual(ctx.exception.after_mile, 0.0, places=3)

    def test_look_ahead_will_not_defer_to_a_cheaper_station_out_of_range(self):
        """Cheap fuel 900 mi ahead is irrelevant: you cannot carry that far.

        Route 1000 mi, stations at mile 0 ($5.00), 450 ($5.00) and 900 ($1.00).
        At mile 0 the only correct move is to fill the 50-gal tank at $5.00 -- deferring
        toward the $1.00 station would strand the vehicle.
          50*5.00 (mile 0) + 40*5.00 (mile 450, to reach mile 900) + 10*1.00
          = 250 + 200 + 10 = $460.00
        """
        plan = solve([stn(0, 5.00), stn(450, 5.00), stn(900, 1.00)], 1000.0)
        self.assertAlmostEqual(plan.stops[0].gallons, 50.0, places=6)
        self.assertAlmostEqual(plan.total_cost, 460.00, places=6)

    def test_final_leg_longer_than_range_is_rejected(self):
        with self.assertRaises(InfeasibleRoute) as ctx:
            solve([stn(0, 3.00), stn(100, 3.00)], 900.0)
        self.assertAlmostEqual(ctx.exception.gap_miles, 800.0, places=3)

    def test_no_stations_at_all_is_rejected(self):
        with self.assertRaises(InfeasibleRoute):
            solve([], 100.0)

    def test_invalid_vehicle_parameters_are_rejected(self):
        with self.assertRaises(ValueError):
            solve([stn(0, 3.0)], 100.0, mpg=0)
        with self.assertRaises(ValueError):
            solve([stn(0, 3.0)], 100.0, range_miles=0)

    def test_plan_serialises_with_ordered_stops(self):
        plan: Plan = solve([stn(0, 3.00), stn(400, 4.00), stn(800, 2.00)], 1000.0)
        d = plan.as_dict()
        self.assertEqual(d["total_fuel_cost_usd"], 310.00)
        self.assertEqual([s["order"] for s in d["fuel_stops"]], [1, 2, 3])
        self.assertEqual([s["mile_marker"] for s in d["fuel_stops"]], [0.0, 400.0, 800.0])
