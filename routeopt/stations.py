"""
Station index: loads the pre-geocoded truckstop data once at startup and
builds a scipy KD-tree over unit-sphere (ECEF) coordinates, so corridor
queries along a route are a handful of fast ball queries instead of a
6,626-row scan per request.

Corridor extraction: the route polyline is resampled to points at most
~1 mile apart, each tagged with its cumulative route distance. Stations
within `corridor_miles` of any sample point become candidates; a station's
"route mile" is the cumulative distance of its nearest sample point
(error < ~0.6 miles, negligible for fuel planning). Stations closer than
0.5 route-miles to each other are merged, keeping the cheapest — several
truckstops often share one city centroid.
"""

import csv
import math
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree

from .optimizer import Station

DATA_FILE = Path(__file__).resolve().parent / "data" / "stations.csv"
EARTH_RADIUS_MILES = 3958.8
RESAMPLE_STEP_MILES = 1.0
MERGE_EPSILON_MILES = 0.5


def haversine_miles(a, b):
    lat1, lon1 = math.radians(a[0]), math.radians(a[1])
    lat2, lon2 = math.radians(b[0]), math.radians(b[1])
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * EARTH_RADIUS_MILES * math.asin(math.sqrt(h))


def _ecef(lat, lon):
    la, lo = math.radians(lat), math.radians(lon)
    return (math.cos(la) * math.cos(lo), math.cos(la) * math.sin(lo), math.sin(la))


class StationIndex:
    def __init__(self, rows):
        self.rows = rows  # list of dicts: id,name,address,city,state,price,lat,lon
        self._tree = cKDTree([_ecef(r["lat"], r["lon"]) for r in rows])

    @classmethod
    def load(cls, path=DATA_FILE):
        with open(path, newline="", encoding="utf-8") as fh:
            rows = [
                {
                    "id": r["id"], "name": r["name"], "address": r["address"],
                    "city": r["city"], "state": r["state"],
                    "price": float(r["price"]), "lat": float(r["lat"]), "lon": float(r["lon"]),
                }
                for r in csv.DictReader(fh)
            ]
        return cls(rows)

    def __len__(self):
        return len(self.rows)

    def nearest(self, lat, lon):
        """The single closest station to a point (used for the start fill-up)."""
        _, idx = self._tree.query(_ecef(lat, lon), k=1)
        return self.rows[int(idx)]

    def _resample(self, geometry):
        """geometry: [[lon, lat], ...] -> (points [(lat, lon)], miles [cum.])"""
        pts, miles = [], []
        total = 0.0
        prev = None
        for lon, lat in geometry:
            cur = (lat, lon)
            if prev is None:
                pts.append(cur)
                miles.append(0.0)
            else:
                seg = haversine_miles(prev, cur)
                steps = max(1, math.ceil(seg / RESAMPLE_STEP_MILES))
                for s in range(1, steps + 1):
                    f = s / steps
                    pts.append((prev[0] + (cur[0] - prev[0]) * f,
                                prev[1] + (cur[1] - prev[1]) * f))
                    miles.append(total + seg * f)
                total += seg
            prev = cur
        return pts, miles

    def corridor_candidates(self, geometry, total_miles, corridor_miles=15.0):
        """
        Optimizer-ready Station list for a route: stations within the
        corridor, each tagged with its route mile, merged and sorted.
        The caller injects the start station separately.
        """
        pts, miles = self._resample(geometry)
        sample_ecef = np.array([_ecef(p[0], p[1]) for p in pts])
        # Chord length for a great-circle radius of corridor_miles.
        chord = 2 * math.sin((corridor_miles / EARTH_RADIUS_MILES) / 2)
        hits = self._tree.query_ball_point(sample_ecef, r=chord)
        best = {}  # station idx -> route mile of its nearest sample point
        for sample_i, station_idxs in enumerate(hits):
            for si in station_idxs:
                mile = miles[sample_i]
                if si not in best or mile < best[si]:
                    best[si] = mile
        raw = []
        for si, mile in best.items():
            r = self.rows[si]
            raw.append(Station(mile=min(mile, total_miles), price=r["price"], ref=r))
        raw.sort(key=lambda s: s.mile)
        # Merge near-coincident stations, keeping the cheapest of each cluster.
        merged = []
        for st in raw:
            if merged and st.mile - merged[-1].mile <= MERGE_EPSILON_MILES:
                if st.price < merged[-1].price:
                    merged[-1] = Station(mile=merged[-1].mile, price=st.price, ref=st.ref)
            else:
                merged.append(st)
        return merged


_INDEX = None


def get_index():
    """Process-wide singleton; built lazily on first use (~0.2 s)."""
    global _INDEX
    if _INDEX is None:
        _INDEX = StationIndex.load()
    return _INDEX
