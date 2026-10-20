"""
Geocoding of free-text API inputs ("New York, NY", "Chicago", "41.8,-87.6").

Strategy, fastest first:
  1. "lat,lon" literals are parsed directly.
  2. A bundled GeoNames-derived gazetteer (routeopt/data/us_places.json,
     ~29.5k US places) resolves city[/state] inputs offline — no network,
     no rate limits.
  3. Anything else (street addresses etc.) falls back to OpenStreetMap
     Nominatim, with results cached in Django's cache so repeat inputs
     never hit the network twice.
"""

import json
import re
from functools import lru_cache
from pathlib import Path

import requests
from django.core.cache import cache

PLACES_FILE = Path(__file__).resolve().parent / "data" / "us_places.json"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "spotter-fuel-route-api/1.0 (backend assessment demo)"
LATLON_RE = re.compile(r"^\s*(-?\d{1,3}(?:\.\d+)?)\s*,\s*(-?\d{1,3}(?:\.\d+)?)\s*$")
STATE_NAME_TO_CODE = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE",
    "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ",
    "new mexico": "NM", "new york": "NY", "north carolina": "NC",
    "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR",
    "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT",
    "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
    "district of columbia": "DC",
}


class GeocodeError(Exception):
    pass


@lru_cache(maxsize=1)
def _places():
    with open(PLACES_FILE, encoding="utf-8") as fh:
        return json.load(fh)


def _gazetteer_lookup(query: str):
    """Return (lat, lon, display_name) or None."""
    places = _places()
    q = query.strip().lower()
    parts = [p.strip() for p in q.split(",") if p.strip()]
    if len(parts) >= 2:
        city, state_part = parts[0], parts[-1]
        code = STATE_NAME_TO_CODE.get(state_part, state_part.upper() if len(state_part) == 2 else None)
        if code:
            hit = places.get(f"{city}|{code}")
            if hit:
                return hit[0], hit[1], f"{city.title()}, {code}"
        return None
    # City only: succeed only if the name is unambiguous (one state).
    hits = [(k, v) for k, v in places.items() if k.startswith(q + "|")]
    if len(hits) == 1:
        (key, (lat, lon)) = hits[0]
        city, code = key.split("|")
        return lat, lon, f"{city.title()}, {code}"
    return None


def _nominatim(query: str):
    cache_key = "geocode:" + re.sub(r"\s+", " ", query.strip().lower())
    cached = cache.get(cache_key)
    if cached:
        return cached
    try:
        resp = requests.get(
            NOMINATIM_URL,
            params={"q": query, "format": "json", "limit": 1, "countrycodes": "us"},
            headers={"User-Agent": USER_AGENT},
            timeout=(3, 10),
        )
        resp.raise_for_status()
        results = resp.json()
    except (requests.RequestException, ValueError) as exc:
        raise GeocodeError(f"geocoding service failed for {query!r}: {exc}") from exc
    if not results:
        raise GeocodeError(f"could not geocode location: {query!r}")
    result = (float(results[0]["lat"]), float(results[0]["lon"]), results[0]["display_name"])
    cache.set(cache_key, result, timeout=60 * 60 * 24 * 30)
    return result


def geocode(query: str):
    """
    Resolve a user-supplied location to (lat, lon, display_name).
    Accepts "lat,lon", "City, ST", "City, State", a bare unambiguous city
    name, or a free-form US address (Nominatim fallback, cached).
    """
    if not query or not str(query).strip():
        raise GeocodeError("location must not be empty")
    query = str(query).strip()
    m = LATLON_RE.match(query)
    if m:
        lat, lon = float(m.group(1)), float(m.group(2))
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise GeocodeError(f"coordinates out of range: {query!r}")
        return lat, lon, query
    hit = _gazetteer_lookup(query)
    if hit:
        return hit
    return _nominatim(query)
