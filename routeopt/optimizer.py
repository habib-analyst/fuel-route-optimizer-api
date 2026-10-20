"""
Minimum-cost refueling planner.

Problem: drive `total_miles` along a route. The vehicle does `mpg` miles per
gallon and can travel at most `range_miles` on a full tank (tank capacity =
range_miles / mpg gallons). Candidate fuel stations sit at known positions
("route miles") along the route, each with a price per gallon. Decide how
much to buy at each station so the trip completes at minimum total cost.

Algorithm — the classic greedy, which is provably optimal for this problem
(divisible fuel, no per-stop fee): at each station, look ahead to the first
station with a *lower* price (the destination counts as price 0 — never
arrive carrying fuel you paid for and don't need). Buy exactly enough to
reach that target, unless it is further than one tank's range, in which case
fill the tank. Never carry more than needed to reach cheaper fuel.

Assumption (also in README): the vehicle starts at mile 0 with an EMPTY
tank, at a station in the start city (the caller injects that station at
mile 0). Total money spent is therefore simply the sum of all purchases,
and the vehicle arrives at the destination with ~0 fuel.
"""

from dataclasses import dataclass, field

EPS = 1e-9


class InfeasibleRouteError(Exception):
    """Raised when some leg between consecutive fueling points exceeds the
    vehicle's range, so the trip cannot be completed with these stations."""


@dataclass
class Station:
    mile: float  # position along the route, in miles from the start
    price: float  # USD per gallon
    ref: dict = field(default_factory=dict)  # display data (name, city, ...)


@dataclass
class Purchase:
    station: Station
    gallons: float
    cost: float


def plan_fuel_stops(stations, total_miles, mpg=10.0, range_miles=500.0):
    """
    stations: iterable of Station, must include one at mile 0 (the start).
    Returns (purchases, total_cost, total_gallons) where purchases only
    contains stations where fuel is actually bought.
    """
    tank_gal = range_miles / mpg
    pts = sorted(stations, key=lambda s: s.mile)
    if not pts or pts[0].mile > EPS:
        raise InfeasibleRouteError("a fuel station at the start (mile 0) is required")
    # Destination sentinel: price 0 so the planner never buys more than
    # needed to get there.
    pts = pts + [Station(mile=float(total_miles), price=0.0, ref={"destination": True})]

    # Feasibility: every consecutive gap must fit in one tank's range.
    for a, b in zip(pts, pts[1:]):
        if b.mile - a.mile > range_miles + EPS:
            raise InfeasibleRouteError(
                f"gap of {b.mile - a.mile:.1f} miles between route mile "
                f"{a.mile:.1f} and {b.mile:.1f} exceeds vehicle range "
                f"{range_miles:.0f} miles"
            )

    # next_cheaper[i] = index of the first station after i with a lower
    # price (monotonic stack, O(n)). The destination sentinel (price 0) is
    # cheaper than everything, so every index resolves.
    n = len(pts)
    next_cheaper = [n - 1] * n
    stack = []
    for i in range(n - 1, -1, -1):
        while stack and pts[stack[-1]].price >= pts[i].price:
            stack.pop()
        next_cheaper[i] = stack[-1] if stack else n - 1
        stack.append(i)

    purchases = []
    fuel = 0.0  # gallons in tank on arrival at station i; starts EMPTY
    total_cost = 0.0
    total_gallons = 0.0
    for i in range(n - 1):  # never buy at the destination sentinel
        here = pts[i]
        target_mile = pts[next_cheaper[i]].mile
        # Can't drive further than one full tank from here.
        reach_mile = min(target_mile, here.mile + range_miles)
        need_gal = (reach_mile - here.mile) / mpg
        buy = min(max(need_gal - fuel, 0.0), tank_gal - fuel)
        if buy > EPS:
            cost = buy * here.price
            purchases.append(Purchase(station=here, gallons=buy, cost=cost))
            total_cost += cost
            total_gallons += buy
            fuel += buy
        # Drive to the next candidate station.
        fuel -= (pts[i + 1].mile - here.mile) / mpg
        fuel = max(fuel, 0.0)  # guard against float dust
    return purchases, total_cost, total_gallons
