"""
Tests for the fuel-route API.

Three layers:
  1. Optimizer unit tests with hand-computed expected answers.
  2. A randomized optimality proof-check: the greedy planner is compared
     against an exact dynamic-programming optimum (fuel discretized to
     0.5 gal on grid-aligned instances) over hundreds of random routes.
     The greedy must never cost more than the DP optimum.
  3. API contract tests with the routing service mocked out.
"""

import random
from unittest import mock

from django.test import Client, SimpleTestCase

from routeopt.optimizer import InfeasibleRouteError, Station, plan_fuel_stops


def st(mile, price, **ref):
    return Station(mile=float(mile), price=float(price), ref=ref)


class OptimizerHandComputedTests(SimpleTestCase):
    def test_cheaper_ahead_buys_minimum(self):
        # 300 mi; $4.00 at start, $3.00 at mile 100.
        # Buy 10 gal @4 (just enough to reach cheap fuel), then 20 gal @3.
        purchases, cost, gallons = plan_fuel_stops([st(0, 4.0), st(100, 3.0)], 300)
        self.assertAlmostEqual(cost, 10 * 4.0 + 20 * 3.0, places=6)
        self.assertAlmostEqual(gallons, 30.0, places=6)
        self.assertEqual(len(purchases), 2)

    def test_rising_prices_fill_up_early(self):
        # 900 mi; prices rise $3 -> $4 -> $5 every 300 mi.
        # Fill 50 @3, top 30 @4, last 10 @5 = $320.
        purchases, cost, gallons = plan_fuel_stops(
            [st(0, 3.0), st(300, 4.0), st(600, 5.0)], 900
        )
        self.assertAlmostEqual(cost, 50 * 3.0 + 30 * 4.0 + 10 * 5.0, places=6)
        self.assertAlmostEqual(gallons, 90.0, places=6)

    def test_never_buys_more_than_trip_needs(self):
        # Single station, 120-mile trip: exactly 12 gallons.
        purchases, cost, gallons = plan_fuel_stops([st(0, 3.5)], 120)
        self.assertAlmostEqual(gallons, 12.0, places=6)
        self.assertAlmostEqual(cost, 42.0, places=6)

    def test_gap_beyond_range_is_infeasible(self):
        with self.assertRaises(InfeasibleRouteError):
            plan_fuel_stops([st(0, 3.0), st(600, 3.0)], 900)

    def test_missing_start_station_is_infeasible(self):
        with self.assertRaises(InfeasibleRouteError):
            plan_fuel_stops([st(50, 3.0)], 100)


def dp_optimum(stations, total_miles, mpg=10.0, range_miles=500.0, step=0.5):
    """Exact optimum on grid-aligned instances via dynamic programming
    over (station, fuel-in-tank) with purchases in `step`-gallon units."""
    tank = range_miles / mpg
    pts = sorted(stations, key=lambda s: s.mile)
    levels = int(round(tank / step))
    INF = float("inf")
    # dp[fuel_level] = min cost arriving at current station with that fuel
    dp = {0: 0.0}
    for i, here in enumerate(pts):
        nxt_mile = pts[i + 1].mile if i + 1 < len(pts) else float(total_miles)
        leg_gal = (nxt_mile - here.mile) / mpg
        leg_steps = int(round(leg_gal / step))
        new = {}
        for have_steps, cost in dp.items():
            max_buy = levels - have_steps
            for buy_steps in range(max_buy + 1):
                after = have_steps + buy_steps - leg_steps
                if after < 0:
                    continue
                c = cost + buy_steps * step * here.price
                if c < new.get(after, INF):
                    new[after] = c
        dp = new
        if not dp:
            return INF
    return min(dp.values())


class OptimizerOptimalityTests(SimpleTestCase):
    def test_greedy_matches_dp_optimum_on_random_routes(self):
        rng = random.Random(20261020)
        for trial in range(300):
            n_legs = rng.randint(1, 7)
            # Grid-aligned legs: multiples of 25 miles (2.5 gal at 10 mpg),
            # each leg <= 500 so the instance is feasible.
            legs = [25 * rng.randint(1, 20) for _ in range(n_legs)]
            miles = [0]
            for leg in legs:
                miles.append(miles[-1] + leg)
            total = miles[-1]
            stations = [st(m, round(rng.uniform(2.7, 6.4), 3)) for m in miles[:-1]]
            # Sometimes add extra mid-leg stations (also grid-aligned).
            for _ in range(rng.randint(0, 3)):
                m = 25 * rng.randint(0, total // 25)
                if all(abs(s.mile - m) > 1e-9 for s in stations):
                    stations.append(st(m, round(rng.uniform(2.7, 6.4), 3)))
            _, greedy_cost, _ = plan_fuel_stops(stations, total)
            optimal = dp_optimum(stations, total)
            self.assertLessEqual(
                greedy_cost, optimal + 1e-6,
                f"trial {trial}: greedy {greedy_cost} > DP optimum {optimal}",
            )
            # And the discretized DP can only overshoot the true optimum by
            # a small rounding margin.
            self.assertLess(optimal - greedy_cost, 2.0, f"trial {trial}")


class FakeIndex:
    """Stand-in for StationIndex with three fixture stations."""

    ROWS = {
        "S0": {"id": "S0", "name": "START FUEL", "address": "1 Start Way",
               "city": "New York", "state": "NY", "price": 3.50, "lat": 40.75, "lon": -73.99},
        "S1": {"id": "S1", "name": "CHEAP FUEL", "address": "2 Mid Rd",
               "city": "Midtown", "state": "PA", "price": 3.00, "lat": 40.90, "lon": -77.00},
        "S2": {"id": "S2", "name": "PRICEY FUEL", "address": "3 Far Ave",
               "city": "Farburg", "state": "OH", "price": 3.20, "lat": 41.20, "lon": -81.00},
    }

    def __len__(self):
        return len(self.ROWS)

    def nearest(self, lat, lon):
        return self.ROWS["S0"]

    def corridor_candidates(self, geometry, total_miles, corridor_miles=15.0):
        if total_miles > 500:  # long synthetic route: no mid stations -> infeasible
            return []
        return [
            Station(mile=100.0, price=3.00, ref=self.ROWS["S1"]),
            Station(mile=200.0, price=3.20, ref=self.ROWS["S2"]),
        ]


FAKE_ROUTE = {
    "distance_miles": 300.0,
    "duration_hours": 5.0,
    "geometry": [[-73.99, 40.75], [-80.0, 41.0], [-87.63, 41.88]],
    "cached": False,
}


class ApiContractTests(SimpleTestCase):
    def setUp(self):
        self.client = Client()
        patches = [
            mock.patch("routeopt.views.get_index", return_value=FakeIndex()),
            mock.patch("routeopt.views.geocode",
                       side_effect=lambda q: (40.75, -73.99, q)),
            mock.patch("routeopt.views.get_route", return_value=dict(FAKE_ROUTE)),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_post_route_contract_and_totals(self):
        resp = self.client.post(
            "/api/route/", data='{"start": "New York, NY", "end": "Chicago, IL"}',
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        # Start @3.50 -> buy 10 gal to reach $3.00 at mile 100; then 20 gal @3.00.
        self.assertEqual(data["totals"]["total_fuel_cost"], 95.00)
        self.assertEqual(data["totals"]["total_fuel_gallons"], 30.00)
        self.assertEqual(data["totals"]["number_of_stops"], 2)
        self.assertEqual(data["fuel_stops"][0]["name"], "START FUEL")
        self.assertEqual(data["fuel_stops"][1]["name"], "CHEAP FUEL")
        self.assertEqual(data["fuel_stops"][1]["route_mile"], 100.0)
        self.assertEqual(data["route"]["geometry"]["type"], "LineString")
        self.assertEqual(data["performance"]["routing_calls"], 1)
        self.assertEqual(data["assumptions"]["mpg"], 10.0)
        self.assertEqual(data["assumptions"]["range_miles"], 500.0)

    def test_get_route_works_too(self):
        resp = self.client.get("/api/route/", {"start": "A", "end": "B"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["totals"]["number_of_stops"], 2)

    def test_missing_params_is_400(self):
        resp = self.client.get("/api/route/", {"start": "A"})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("error", resp.json())

    def test_infeasible_route_is_422(self):
        with mock.patch("routeopt.views.get_route",
                        return_value={**FAKE_ROUTE, "distance_miles": 900.0}):
            resp = self.client.get("/api/route/", {"start": "A", "end": "B"})
        self.assertEqual(resp.status_code, 422)
        self.assertIn("error", resp.json())

    def test_health(self):
        resp = self.client.get("/api/health/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["stations_loaded"], 3)
