"""
API views.

POST /api/route/  {"start": "New York, NY", "end": "Chicago, IL"}
GET  /api/route/?start=New+York,+NY&end=Chicago,+IL
GET  /api/health/

Response (200):
{
  "start": {"query": ..., "name": ..., "location": [lon, lat]},
  "end":   {...},
  "route": {
    "distance_miles": 790.6, "duration_hours": 12.4,
    "geometry": {"type": "LineString", "coordinates": [[lon, lat], ...]},
    "geometry_preview": {"type": "LineString", "coordinates": [... <=200 pts]}
  },
  "assumptions": {"mpg": 10, "range_miles": 500, "tank_gallons": 50,
                  "start_fuel": "empty tank; first fill-up at the truckstop
                                 nearest the start point"},
  "fuel_stops": [
    {"order": 1, "truckstop_id": "...", "name": ..., "address": ...,
     "city": ..., "state": ..., "location": [lon, lat],
     "route_mile": 0.0, "distance_from_previous_stop_miles": 0.0,
     "price_per_gallon": 3.45, "gallons": 42.1, "cost": 145.25}, ...
  ],
  "totals": {"total_distance_miles": ..., "total_fuel_gallons": ...,
             "total_fuel_cost": ..., "number_of_stops": ...},
  "performance": {"routing_calls": 0|1, "compute_ms": ...}
}

Errors are JSON: {"error": "..."} with 400 (bad input), 422 (route
infeasible with the available stations) or 502 (routing service down).
"""

import json
import time

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .geo import GeocodeError, geocode
from .optimizer import InfeasibleRouteError, Station, plan_fuel_stops
from .routing import RoutingError, get_route
from .stations import get_index

MPG = 10.0
RANGE_MILES = 500.0
CORRIDOR_LADDER = (15.0, 30.0, 60.0)  # widen only if a leg is infeasible


def health(request):
    index = get_index()
    return JsonResponse({"status": "ok", "stations_loaded": len(index)})


def _error(message, status):
    return JsonResponse({"error": message}, status=status)


@csrf_exempt
@require_http_methods(["GET", "POST"])
def route(request):
    started = time.perf_counter()
    if request.method == "POST":
        try:
            body = json.loads(request.body or b"{}")
        except ValueError:
            return _error("request body must be valid JSON", 400)
        start_q, end_q = body.get("start"), body.get("end")
    else:
        start_q, end_q = request.GET.get("start"), request.GET.get("end")
    if not start_q or not end_q:
        return _error("both 'start' and 'end' are required", 400)

    try:
        s_lat, s_lon, s_name = geocode(str(start_q))
        e_lat, e_lon, e_name = geocode(str(end_q))
    except GeocodeError as exc:
        return _error(str(exc), 400)

    try:
        route_data = get_route((s_lat, s_lon), (e_lat, e_lon))
    except RoutingError as exc:
        return _error(str(exc), 502)
    total_miles = route_data["distance_miles"]
    if total_miles < 1:
        return _error("start and end are effectively the same location", 400)

    index = get_index()
    # Start assumption (documented): empty tank; the first fill-up happens
    # at the truckstop nearest the start point, treated as route mile 0.
    start_station_row = index.nearest(s_lat, s_lon)
    start_station = Station(mile=0.0, price=start_station_row["price"], ref=start_station_row)

    purchases = total_cost = total_gallons = None
    last_exc = None
    for corridor in CORRIDOR_LADDER:
        candidates = index.corridor_candidates(route_data["geometry"], total_miles, corridor)
        candidates = [c for c in candidates if c.ref.get("id") != start_station_row["id"]]
        try:
            purchases, total_cost, total_gallons = plan_fuel_stops(
                [start_station] + candidates, total_miles, mpg=MPG, range_miles=RANGE_MILES
            )
            break
        except InfeasibleRouteError as exc:
            last_exc = exc
    if purchases is None:
        return _error(f"route cannot be completed with available fuel stations: {last_exc}", 422)

    stops = []
    prev_mile = 0.0
    for order, p in enumerate(purchases, start=1):
        ref = p.station.ref
        stops.append(
            {
                "order": order,
                "truckstop_id": ref.get("id"),
                "name": ref.get("name"),
                "address": ref.get("address"),
                "city": ref.get("city"),
                "state": ref.get("state"),
                "location": [ref.get("lon"), ref.get("lat")],
                "route_mile": round(p.station.mile, 1),
                "distance_from_previous_stop_miles": round(p.station.mile - prev_mile, 1),
                "price_per_gallon": round(p.station.price, 4),
                "gallons": round(p.gallons, 2),
                "cost": round(p.cost, 2),
            }
        )
        prev_mile = p.station.mile

    geometry = route_data["geometry"]
    step = max(1, len(geometry) // 200)
    preview = geometry[::step]
    if preview[-1] != geometry[-1]:
        preview.append(geometry[-1])

    return JsonResponse(
        {
            "start": {"query": start_q, "name": s_name, "location": [s_lon, s_lat]},
            "end": {"query": end_q, "name": e_name, "location": [e_lon, e_lat]},
            "route": {
                "distance_miles": round(total_miles, 1),
                "duration_hours": round(route_data["duration_hours"], 1),
                "geometry": {"type": "LineString", "coordinates": geometry},
                "geometry_preview": {"type": "LineString", "coordinates": preview},
            },
            "assumptions": {
                "mpg": MPG,
                "range_miles": RANGE_MILES,
                "tank_gallons": RANGE_MILES / MPG,
                "start_fuel": (
                    "vehicle starts with an empty tank; the first fill-up is at the "
                    "truckstop nearest the start point (shown as stop 1, route mile 0)"
                ),
            },
            "fuel_stops": stops,
            "totals": {
                "total_distance_miles": round(total_miles, 1),
                "total_fuel_gallons": round(total_gallons, 2),
                "total_fuel_cost": round(total_cost, 2),
                "number_of_stops": len(stops),
            },
            "performance": {
                "routing_calls": 0 if route_data["cached"] else 1,
                "compute_ms": round((time.perf_counter() - started) * 1000, 1),
            },
        }
    )
