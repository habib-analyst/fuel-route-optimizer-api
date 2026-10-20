# Fuel Route API — Spotter Backend (Django) Assessment

A Django API that, given a start and finish location in the USA, returns:

- the **route** (road geometry as a GeoJSON LineString — the map of the route),
- the **optimal (cheapest) fuel stops** along that route for a vehicle with a
  **500-mile range**, using the provided OPIS truckstop price data,
- the **total money spent on fuel** at **10 miles per gallon**.

Fuel-stop selection uses the classic minimum-cost refueling greedy
(provably optimal for this problem): never buy more than needed to reach
cheaper fuel ahead; fill up only when nothing cheaper is within range.
Optimality is verified by an automated test that compares the planner
against an exact dynamic-programming optimum on 300 random routes.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python manage.py runserver
```

No database or migrations are needed — the station dataset ships
pre-geocoded in `routeopt/data/stations.csv` and is loaded into memory.

Try it:

```bash
curl "http://127.0.0.1:8000/api/route/?start=New%20York,%20NY&end=Chicago,%20IL"
```

or POST JSON:

```bash
curl -X POST http://127.0.0.1:8000/api/route/ \
  -H 'Content-Type: application/json' \
  -d '{"start": "Dallas, TX", "end": "Denver, CO"}'
```

Run the test suite and the live demo:

```bash
python manage.py test        # 11 tests, incl. optimality proof-check
python scripts/demo.py "New York, NY" "Chicago, IL"
```

## API

### `GET /api/health/`
`{"status": "ok", "stations_loaded": 6626}`

### `GET|POST /api/route/`

Parameters: `start`, `end` — each may be `"City, ST"`, `"City, State"`,
a bare (unambiguous) city name, `"lat,lon"`, or a free-form US address.

Response:

```json
{
  "start": {"query": "New York, NY", "name": "New York, NY", "location": [-73.9772, 40.76]},
  "end":   {"query": "Chicago, IL",  "name": "Chicago, IL",  "location": [-87.68615, 41.85]},
  "route": {
    "distance_miles": 793.4,
    "duration_hours": 14.8,
    "geometry":         {"type": "LineString", "coordinates": [[-73.99, 40.75], "..."]},
    "geometry_preview": {"type": "LineString", "coordinates": ["... at most ~200 points"]}
  },
  "assumptions": {
    "mpg": 10.0, "range_miles": 500.0, "tank_gallons": 50.0,
    "start_fuel": "vehicle starts with an empty tank; the first fill-up is at the truckstop nearest the start point (shown as stop 1, route mile 0)"
  },
  "fuel_stops": [
    {"order": 1, "truckstop_id": "52322", "name": "BOLLA MARKET",
     "address": "I-95, EXIT 13", "city": "Elizabeth", "state": "NJ",
     "location": [-74.2161, 40.66785], "route_mile": 0.0,
     "distance_from_previous_stop_miles": 0.0,
     "price_per_gallon": 3.099, "gallons": 5.0, "cost": 15.51}
  ],
  "totals": {"total_distance_miles": 793.4, "total_fuel_gallons": 79.34,
             "total_fuel_cost": 242.31, "number_of_stops": 4},
  "performance": {"routing_calls": 1, "compute_ms": 3092.3}
}
```

Errors: `{"error": "..."}` with **400** (unparseable/unknown location,
missing params), **422** (route cannot be completed — a gap between
fueling points exceeds 500 miles), **502** (routing service failure).

The full real response for New York → Chicago is in `sample_response.json`.

## Assumptions (stated explicitly)

1. **Fuel at departure:** the vehicle starts with an **empty tank** at a
   truckstop in the start city — specifically the station nearest the start
   point, shown as stop 1 at route mile 0. "Total money spent" is therefore
   exactly the sum of all purchases, and the vehicle arrives with ~0 fuel.
2. **Station positions:** the OPIS CSV has no coordinates, so stations are
   geocoded to their **city centroid** (GeoNames gazetteer). Stations in the
   same city share coordinates; the planner uses the cheapest. Positions
   along the route are accurate to well under a mile.
3. **Detours:** stations are considered if they lie within a 15-mile
   corridor of the route (auto-widened to 30/60 miles if a leg would
   otherwise exceed the vehicle's range). The small detour to a station is
   not added to the trip distance.
4. **Scope:** routes are within the USA, per the assignment. The ~112
   Canadian stations in the CSV and duplicated OPIS IDs (kept: lowest
   price) are excluded/deduplicated in the data build — 6,626 stations
   remain.

## Design decisions

| Concern | Choice | Why |
|---|---|---|
| Routing | **OSRM** public server (`router.project-osrm.org`) | Free, no API key, fast. Exactly **one** call per unique (start, end); results cached 7 days in Django's file cache, repeats cost **zero** calls. |
| Geocoding inputs | Bundled GeoNames gazetteer (~29.5k US places) first, Nominatim fallback (cached) | City inputs resolve **offline in microseconds**; only free-form addresses touch the network. |
| Station geocoding | Offline, one-time (`scripts/build_stations.py`) using GeoNames US postal data (CC BY 4.0) | No per-request geocoding at all; the resulting CSV is committed. |
| Spatial search | scipy `cKDTree` on unit-sphere coordinates, built once at startup | Corridor query = ball queries around ~1 sample point per route mile; no 6,626-row scans. |
| Optimization | Greedy with next-cheaper-station look-ahead (monotonic stack, O(n)) | Provably optimal here; verified against a DP optimum in tests. |
| Framework | Django 6.1 (latest stable), no DB | The assignment asks for Django; the data is read-only and memory-resident, so a DB would only add latency. |

## Performance (measured on the dev machine)

- Cold request (one OSRM call): ~3.0 s end-to-end, of which the OSRM
  network call is the majority.
- Warm request (route cached): **0.35–0.95 s** including geocoding,
  corridor extraction over thousands of stations, and optimization.
- Station index build at startup: ~0.2 s for 6,626 stations.
- Response field `performance.compute_ms` reports per-request server time;
  `performance.routing_calls` is 1 on a cache miss, 0 on a hit.

## Project layout

```
manage.py, requirements.txt
config/            settings, urls, wsgi/asgi
routeopt/
  views.py         API endpoints + response contract
  optimizer.py     minimum-cost refueling planner (pure logic)
  stations.py      station loading, KD-tree corridor extraction
  routing.py       OSRM client (single call + cache)
  geo.py           input geocoding (gazetteer -> Nominatim)
  data/            stations.csv (pre-geocoded), us_places.json
  tests.py         optimizer proofs, DP optimality check, API contract tests
scripts/
  build_stations.py  one-time data build (needs GeoNames US.txt)
  demo.py            live end-to-end demo, writes sample_response.json
```

## For the Loom demo (5 min)

1. `python manage.py runserver`, open Postman.
2. GET `api/health/` — show 6,626 stations loaded.
3. GET `api/route/` New York → Chicago — walk the 4 stops: small first fill
   (only enough to reach cheaper fuel), big cheap fills, total $242.31.
4. Repeat the same request — `routing_calls: 0`, sub-second: caching.
5. Paste `route.geometry` into geojson.io to show the map + stops.
6. Show `optimizer.py` (the greedy + why it's optimal) and
   `python manage.py test` (the DP optimality test passing).
