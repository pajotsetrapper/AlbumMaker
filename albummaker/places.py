# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Pieter Remi Coppens
"""Place names without the internet: the nearest town to each photo, from a
list of ~160,000 towns shipped in albummaker/data/places.tsv.gz (GeoNames,
CC BY 4.0; rebuilt by tools/build_places.py).

"Nearest" weighs distance against size: a photo in a city's outskirts is
"Kyoto", not the village whose centre happens to be a little closer. Each
town gets a reach that grows with its population; a photo within that reach
is in the town ("Kyoto, Japan"), further out it is "near" it.
"""

from __future__ import annotations

import gzip
from collections import Counter
from math import cos, log10, radians, sqrt
from typing import Optional

from .util import read_resource_bytes

ATTRIBUTION = "Place names: GeoNames (CC BY 4.0)"
SEARCH_KM = 50          # no town within this distance: no place name
MAX_DAY_PLACES = 3


def reach_km(population: int) -> float:
    """How far from its centre a town still counts as "in" it: 1.5 km for a
    village of 1000, about 9 km for a city of a million."""
    return 1.5 + 2.5 * log10(max(population, 1000) / 1000)


class Gazetteer:
    """The town list, indexed by 1-degree grid cells. Loaded on first use."""

    def __init__(self, data: Optional[bytes] = None):
        self._data = data
        self._cells = None
        self.countries = {}

    def _load(self):
        raw = self._data if self._data is not None else read_resource_bytes("data", "places.tsv.gz")
        cells = {}
        for line in gzip.decompress(raw).decode("utf-8").splitlines():
            if line.startswith("@"):
                cc, _, name = line[1:].partition("\t")
                self.countries[cc] = name
                continue
            parts = line.split("\t")
            if len(parts) != 5:
                continue
            name, cc, lat, lon, pop = parts
            lat, lon = int(lat) / 100, int(lon) / 100
            cells.setdefault((int(lat // 1), int(lon // 1)), []).append((lat, lon, name, cc, int(pop)))
        self._cells = cells

    def nearest(self, lat: float, lon: float) -> Optional[dict]:
        """{'name', 'country', 'km', 'inside'} for the town that best describes
        (lat, lon), or None when there is none within SEARCH_KM."""
        if self._cells is None:
            self._load()
        k = cos(radians(lat))
        rings = 1 + int(SEARCH_KM / 111 / max(k, 0.05))   # cells to look at east and west
        best, best_score = None, None
        cy, cx = int(lat // 1), int(lon // 1)
        for dy in (-1, 0, 1):
            for dx in range(-rings, rings + 1):
                x = (cx + dx + 180) % 360 - 180            # across the date line
                for p_lat, p_lon, name, cc, pop in self._cells.get((cy + dy, x), ()):
                    dlon = (p_lon - lon + 180) % 360 - 180
                    km = 111.2 * sqrt((p_lat - lat) ** 2 + (dlon * k) ** 2)
                    if km > SEARCH_KM:
                        continue
                    score = km / reach_km(pop)
                    if best_score is None or score < best_score:
                        best, best_score = (name, cc, km, pop), score
        if best is None:
            return None
        name, cc, km, pop = best
        return {"name": name, "country": self.countries.get(cc, cc), "km": km,
                "inside": km <= reach_km(pop)}


_GAZETTEER = Gazetteer()


def describe(place: dict) -> str:
    where = f"{place['name']}, {place['country']}"
    return where if place["inside"] else f"near {where}"


def day_places(items: list) -> list:
    """The day's main towns in the order they were first visited: those that
    account for a fair share of the day's located files, at most three."""
    names = [it.place for it in items if it.place]
    if not names:
        return []
    counts = Counter(names)
    enough = max(1, len(names) // 10)
    main = [n for n, c in counts.most_common(MAX_DAY_PLACES) if c >= enough]
    seen = []
    for n in names:
        if n in main and n not in seen:
            seen.append(n)
    return seen


def apply_places(items: list, gazetteer: Optional[Gazetteer] = None) -> int:
    """Name the place of every located file (detail row "Place"; the days'
    places follow from these, see DayGroup.places). Returns how many got one."""
    gazetteer = gazetteer or _GAZETTEER
    named = 0
    for it in items:
        if not it.has_gps:
            continue
        place = gazetteer.nearest(it.lat, it.lon)
        if place is None:
            continue
        it.place = place["name"]
        it.detail["place"] = describe(place)
        named += 1
    return named
