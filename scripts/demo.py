#!/usr/bin/env python3
"""
Live end-to-end demo: calls the real API (real OSRM routing, real station
data) through Django's test client and prints a human-readable summary.
Writes the full JSON response to sample_response.json next to this script's
project root.

Usage:  python scripts/demo.py ["New York, NY"] ["Chicago, IL"]
"""

import json
import os
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django  # noqa: E402

django.setup()

from django.test import Client  # noqa: E402


def main():
    start = sys.argv[1] if len(sys.argv) > 1 else "New York, NY"
    end = sys.argv[2] if len(sys.argv) > 2 else "Chicago, IL"
    client = Client()
    resp = client.get("/api/route/", {"start": start, "end": end})
    data = resp.json()
    if resp.status_code != 200:
        print(f"HTTP {resp.status_code}: {data}")
        return 1
    out = BASE / "sample_response.json"
    out.write_text(json.dumps(data, indent=2), encoding="utf-8")

    print(f"{data['start']['name']}  ->  {data['end']['name']}")
    print(f"Distance: {data['route']['distance_miles']} miles | "
          f"Duration: {data['route']['duration_hours']} h | "
          f"Routing calls: {data['performance']['routing_calls']} | "
          f"Compute: {data['performance']['compute_ms']} ms")
    print(f"{'Stop':<5}{'Station':<38}{'City':<18}{'Mile':>7}{'$/gal':>8}{'Gal':>7}{'Cost':>9}")
    for s in data["fuel_stops"]:
        print(f"{s['order']:<5}{s['name'][:37]:<38}{(s['city'] + ', ' + s['state']):<18}"
              f"{s['route_mile']:>7}{s['price_per_gallon']:>8.2f}{s['gallons']:>7.1f}{s['cost']:>9.2f}")
    t = data["totals"]
    print(f"TOTAL: {t['number_of_stops']} stops, {t['total_fuel_gallons']} gallons, "
          f"${t['total_fuel_cost']:.2f}")
    print(f"Full JSON written to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
