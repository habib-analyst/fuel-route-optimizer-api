"""
Routing via the free OSRM demo server (router.project-osrm.org) — no API
key, and exactly ONE routing call per (start, end) pair: results are cached
in Django's cache, so repeat requests cost zero routing calls.

Returns the route geometry (GeoJSON LineString, [lon, lat] order) — this
is the "map of the route" in machine-readable form, renderable by any map
client (Leaflet, MapLibre, Google Maps via GeoJSON import, geojson.io).
"""

import requests
from django.core.cache import cache

OSRM_URL = "https://router.project-osrm.org/route/v1/driving/{lon1},{lat1};{lon2},{lat2}"
EARTH_RADIUS_MILES = 3958.8


class RoutingError(Exception):
    pass


def get_route(start, end):
    """
    start / end: (lat, lon) tuples.
    Returns dict with distance_miles, duration_hours, geometry (list of
    [lon, lat]) and 'cached' flag.
    """
    (lat1, lon1), (lat2, lon2) = start, end
    cache_key = f"osrm:{lat1:.5f},{lon1:.5f};{lat2:.5f},{lon2:.5f}"
    cached = cache.get(cache_key)
    if cached:
        return {**cached, "cached": True}
    try:
        resp = requests.get(
            OSRM_URL.format(lon1=lon1, lat1=lat1, lon2=lon2, lat2=lat2),
            params={"overview": "full", "geometries": "geojson", "steps": "false"},
            timeout=(3, 20),
        )
        resp.raise_for_status()
        payload = resp.json()
    except (requests.RequestException, ValueError) as exc:
        raise RoutingError(f"routing service failed: {exc}") from exc
    if payload.get("code") != "Ok" or not payload.get("routes"):
        raise RoutingError(f"no route found between the given locations ({payload.get('code')})")
    route = payload["routes"][0]
    result = {
        "distance_miles": route["distance"] / 1609.344,
        "duration_hours": route["duration"] / 3600.0,
        "geometry": route["geometry"]["coordinates"],
        "cached": False,
    }
    cache.set(cache_key, {k: result[k] for k in ("distance_miles", "duration_hours", "geometry")},
              timeout=60 * 60 * 24 * 7)
    return result
