#!/usr/bin/env python3
"""
One-time data preparation: geocode the OPIS truckstop CSV.

The assessment CSV (fuel-prices-for-be-assessment.csv) has no coordinates,
only City/State. We geocode every station offline with the free GeoNames
US postal-code gazetteer (download.geonames.org/export/zip/US.zip,
CC BY 4.0). Every station in the same city shares the city-centroid
coordinates, which is more than accurate enough for corridor search along
a highway route (a truckstop is never more than a couple of miles from
its city centroid, and the route corridor is 15+ miles wide).

Outputs (committed to the repo, so the app itself needs no gazetteer):
  routeopt/data/stations.csv     - one row per unique OPIS Truckstop ID
  routeopt/data/us_places.json   - "city|ST" -> [lat, lon] for geocoding
                                   free-text API inputs like "Chicago, IL"

Usage:
  curl -O https://download.geonames.org/export/zip/US.zip
  unzip US.zip US.txt
  python scripts/build_stations.py --geonames US.txt
"""

import argparse
import csv
import json
import re
import statistics
import sys
from collections import defaultdict
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SRC = BASE / "fuel-prices-for-be-assessment.csv"
OUT_STATIONS = BASE / "routeopt" / "data" / "stations.csv"
OUT_PLACES = BASE / "routeopt" / "data" / "us_places.json"

US_STATES = set(
    "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS "
    "MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV "
    "WI WY DC".split()
)


def norm_place(name: str) -> str:
    """Normalize a place name for matching: 'De Forest' == 'DeForest',
    'Winston Salem' == 'Winston-Salem', "Mc Leansville" == 'McLeansville'."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


def load_gazetteer(path: Path):
    """(normalized_city, state) -> (median_lat, median_lon); also keep the
    full 'city|ST' -> [lat, lon] map for runtime geocoding of API inputs."""
    coords = defaultdict(list)
    display = {}
    with open(path, encoding="utf-8") as fh:
        for row in csv.reader(fh, delimiter="\t"):
            city, state = row[2].strip(), row[4].strip()
            lat, lon = float(row[9]), float(row[10])
            coords[(norm_place(city), state)].append((lat, lon))
            display.setdefault((city.lower(), state), []).append((lat, lon))
    places = {}
    for (city_lower, state), pts in display.items():
        lat = statistics.median(p[0] for p in pts)
        lon = statistics.median(p[1] for p in pts)
        places[f"{city_lower}|{state}"] = [round(lat, 5), round(lon, 5)]
    centroids = {
        key: (statistics.median(p[0] for p in pts), statistics.median(p[1] for p in pts))
        for key, pts in coords.items()
    }
    return centroids, places


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--geonames", required=True, help="path to GeoNames US.txt")
    args = ap.parse_args()

    centroids, places = load_gazetteer(Path(args.geonames))

    # Deduplicate by OPIS Truckstop ID, keeping the lowest price per ID
    # (the raw file repeats some stations).
    best = {}
    with open(SRC, encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            sid = row["OPIS Truckstop ID"].strip()
            price = float(row["Retail Price"])
            if sid not in best or price < float(best[sid]["Retail Price"]):
                best[sid] = row
    print(f"unique stations: {len(best)}")

    kept, skipped_non_us, skipped_no_geo = [], 0, 0
    for row in best.values():
        state = row["State"].strip()
        if state not in US_STATES:
            skipped_non_us += 1  # Canadian stations: out of scope (USA routes)
            continue
        key = (norm_place(row["City"]), state)
        if key not in centroids:
            skipped_no_geo += 1
            print(f"  no geocode: {row['City']}, {state} ({row['Truckstop Name']})")
            continue
        lat, lon = centroids[key]
        kept.append(
            {
                "id": row["OPIS Truckstop ID"].strip(),
                "name": row["Truckstop Name"].strip(),
                "address": row["Address"].strip(),
                "city": row["City"].strip(),
                "state": state,
                "price": f"{float(row['Retail Price']):.4f}",
                "lat": f"{lat:.5f}",
                "lon": f"{lon:.5f}",
            }
        )

    kept.sort(key=lambda r: (r["state"], r["city"], float(r["price"])))
    OUT_STATIONS.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_STATIONS, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(
            fh, fieldnames=["id", "name", "address", "city", "state", "price", "lat", "lon"]
        )
        w.writeheader()
        w.writerows(kept)
    with open(OUT_PLACES, "w", encoding="utf-8") as fh:
        json.dump(places, fh, separators=(",", ":"), sort_keys=True)

    print(f"stations written: {len(kept)} -> {OUT_STATIONS}")
    print(f"skipped (non-US): {skipped_non_us}; skipped (no geocode): {skipped_no_geo}")
    print(f"places written: {len(places)} -> {OUT_PLACES}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
