# Solution Summary — Spotter Backend Django Engineer Assessment

**Assignment:** Django API taking a start and finish location (USA) that
returns a map of the route, the cost-optimal fuel stops along it (vehicle
range 500 miles, multiple stops allowed), and the total fuel cost at
10 mpg — using the provided OPIS truckstop price file. One free routing
API, used as little as possible; responses should be fast; deliver GitHub
code + a ≤5-minute Loom demo.

## Approach

1. **Data first.** The OPIS CSV (~8,150 rows) has no coordinates, so I
   geocoded it **offline, once**, with the free GeoNames US gazetteer:
   deduplicated by OPIS Truckstop ID (lowest price kept), dropped 112
   Canadian stations (assignment is USA-only), matched every remaining US
   station to its city centroid → **6,626 stations** in
   `routeopt/data/stations.csv` (committed; `scripts/build_stations.py`
   regenerates it). Zero US stations failed to geocode.
2. **Routing.** OSRM (free, no key). Exactly **one** routing call per
   unique (start, end); the response is cached for 7 days, so repeats
   make **zero** calls. Input geocoding is also offline-first (bundled
   29.5k-place gazetteer; Nominatim only for free-form addresses, cached).
3. **Corridor extraction.** At startup the stations are indexed in a
   scipy KD-tree (unit-sphere coords). Per request, the route polyline is
   resampled to ~1 point/mile and stations within a 15-mile corridor
   (auto-widening to 30/60 mi only if a leg would exceed range) become
   candidates, each tagged with its route mile; stations within 0.5 miles
   of each other collapse to the cheapest.
4. **Optimization.** The classic minimum-cost refueling greedy — at each
   station buy only enough to reach the first cheaper station ahead (the
   destination counts as price 0), filling the tank only when no cheaper
   fuel is within the 500-mile range. Provably optimal for divisible fuel
   with no per-stop fee; an automated test compares it against an exact
   dynamic-programming optimum on 300 randomized routes and it never
   loses.
5. **Stated assumption.** The vehicle starts with an **empty tank**; the
   first fill-up is at the truckstop nearest the start point (stop 1,
   route mile 0). Total money spent = sum of purchases; arrival fuel ≈ 0.

## Endpoint contract

`GET /api/route/?start=...&end=...` or `POST /api/route/` with JSON
`{"start": "...", "end": "..."}`. Locations accept "City, ST",
"City, State", a bare unambiguous city, "lat,lon", or a US address.

Response: `start`/`end` (resolved name + `[lon, lat]`), `route`
(`distance_miles`, `duration_hours`, full GeoJSON `geometry` LineString —
the route map — plus a ≤200-point `geometry_preview`), `assumptions`,
`fuel_stops[]` (station id/name/address/city/state, location, route mile,
distance from previous stop, price/gallon, gallons, cost), `totals`
(distance, gallons, **total_fuel_cost**, stop count), and `performance`
(`routing_calls`: 1 or 0, `compute_ms`). Errors: 400 bad input, 422
infeasible (a fueling gap > 500 mi), 502 routing service failure.
`GET /api/health/` reports status + station count. Full real response:
`sample_response.json`.

## Tested sample results (live, real OSRM + real data)

**New York, NY → Chicago, IL** — 793.4 mi, 14.8 h, 1 routing call:

| # | Station | City | Route mile | $/gal | Gallons | Cost |
|---|---|---|---|---|---|---|
| 1 | BOLLA MARKET | Elizabeth, NJ | 0.0 | 3.099 | 5.00 | $15.51 |
| 2 | ACI TRUCK STOP | Columbia, NJ | 50.0 | 3.079 | 32.64 | $100.51 |
| 3 | SHEETZ #639 | Youngstown, OH | 376.5 | 3.059 | 16.71 | $51.12 |
| 4 | S&G #88 | Toledo, OH | 543.6 | 3.009 | 24.98 | $75.17 |
| | **Total** | | **793.4 mi** | | **79.34 gal** | **$242.31** |

Note the planner's behavior: only 5 gal at the first (pricier) station —
just enough to reach cheaper fuel — and no stop buys fuel that a cheaper
station ahead could have supplied. Total gallons = 793.4 / 10 mpg exactly.

**Dallas, TX → Denver, CO** — 796.5 mi, 3 stops, 79.65 gal, **$217.29**
(full 50-gal fill at the cheapest station, Harrold, TX @ $2.6873).
**New York, NY → Los Angeles, CA** — 2,796.8 mi, 19 stops, 279.68 gal,
**$848.40**, largest gap between stops 399.7 mi (< 500 ✓).
**Seattle, WA → Portland, OR** (POST, 173.0 mi, 2 stops, $64.72) and error
handling (unresolvable location → 400 JSON) were also exercised live.

## Performance

- Cold request: ~3.0 s end-to-end, dominated by the single OSRM call.
- Warm request (route cached): **0.35–0.95 s** total server time.
- Index build at startup: ~0.2 s (6,626 stations). No per-request
  geocoding of stations, no database queries.

## Tests

`python manage.py test` — **11 tests, all passing**: hand-computed
optimizer cases (cheaper-ahead, rising prices, exact-need, infeasible
gap), the 300-route randomized greedy-vs-DP optimality check, and API
contract tests (POST/GET, totals arithmetic, 400/422 paths, health).

## Still needed from Habib

1. **Create an empty GitHub repo** (agents cannot create repos) — the
   project is a clean local git repo, ready to `git remote add` + push.
2. **Record the ≤5-minute Loom** (Postman demo + code walkthrough); a
   shot-by-shot outline is at the end of `README.md`.
3. Submit the GitHub link + Loom in the Teamtailor message thread
   **before the 2026-10-23 deadline**.
