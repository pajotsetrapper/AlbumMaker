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
import hashlib
import json
import sys
from collections import Counter
from math import cos, log10, radians, sqrt
from pathlib import Path
from typing import Optional

from .util import atomic_write_text, read_resource_bytes

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
        self._memo = {}

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
            cells.setdefault((int(lat // 1), int(lon // 1)), []).append((lat, lon, name, cc, int(pop), reach_km(int(pop))))
        self._cells = cells

    def nearest(self, lat: float, lon: float) -> Optional[dict]:
        key = (lat, lon)
        if key in self._memo:
            return self._memo[key]
        self._memo[key] = result = self._nearest(lat, lon)
        return result

    def _nearest(self, lat: float, lon: float) -> Optional[dict]:
        if self._cells is None:
            self._load()
        k = max(cos(radians(lat)), 0.05)
        rings = 1 + int(SEARCH_KM / 111 / k)                # cells to look at east and west
        box = SEARCH_KM / 111.2                              # in degrees of latitude
        limit2 = box * box
        best, best_score = None, None
        cy, cx = int(lat // 1), int(lon // 1)
        cells = self._cells
        for dy in (-1, 0, 1):
            for dx in range(-rings, rings + 1):
                x = (cx + dx + 180) % 360 - 180            # across the date line
                for p_lat, p_lon, name, cc, pop, reach in cells.get((cy + dy, x), ()):
                    dlat = p_lat - lat
                    if dlat > box or dlat < -box:            # cheap reject before any real work
                        continue
                    dlon = ((p_lon - lon + 180) % 360 - 180) * k
                    d2 = dlat * dlat + dlon * dlon           # compared squared: no sqrt per town
                    if d2 > limit2:
                        continue
                    score = d2 / (reach * reach)
                    if best_score is None or score < best_score:
                        best, best_score = (name, cc, d2, reach), score
        if best is None:
            return None
        name, cc, d2, reach = best
        km = 111.2 * sqrt(d2)
        return {"name": name, "country": self.countries.get(cc, cc), "km": km,
                "inside": km <= reach}

    def version(self) -> str:
        """Identifies the town list, so a cache of answers knows when it is out of date."""
        raw = self._data if self._data is not None else read_resource_bytes("data", "places.tsv.gz")
        return hashlib.sha1(raw).hexdigest()[:16]


_GAZETTEER = Gazetteer()

PLACE_CACHE_FORMAT = 1   # bump when nearest() starts answering differently


class PlaceCache:
    """Answers of Gazetteer.nearest kept in a JSON file between runs ({"lat,lon": place or
    null}), so a restart does not search the town list again (nor even load it) for
    positions it has seen. Keyed by position, not by file: positions taken from a GPX track
    are covered too. Thrown away when the town list or PLACE_CACHE_FORMAT changes."""

    def __init__(self, path: Optional[Path], gazetteer: Optional[Gazetteer] = None):
        self.path = Path(path) if path else None
        self.gazetteer = gazetteer or _GAZETTEER
        self.entries = {}
        self.used = set()
        self.dirty = False
        self._version = None
        if self.path and self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if (isinstance(data, dict) and isinstance(data.get("places"), dict)
                        and data.get("format") == PLACE_CACHE_FORMAT
                        and data.get("version") == self.version):
                    self.entries = data["places"]
            except (OSError, ValueError) as e:
                print(f"! ignoring unreadable place cache {self.path} ({e})", file=sys.stderr)

    @property
    def version(self) -> str:
        if self._version is None:
            self._version = self.gazetteer.version()
        return self._version

    def lookup(self, lat: float, lon: float) -> Optional[dict]:
        key = f"{lat},{lon}"
        self.used.add(key)
        if key in self.entries:
            return self.entries[key]
        place = self.gazetteer.nearest(lat, lon)
        self.entries[key] = place
        self.dirty = True
        return place

    def save(self):
        if not self.path:
            return
        stale = set(self.entries) - self.used
        for key in stale:
            del self.entries[key]
        if not (self.dirty or stale):
            return
        try:
            atomic_write_text(self.path, json.dumps({"format": PLACE_CACHE_FORMAT, "version": self.version,
                                                     "places": self.entries}, separators=(",", ":")) + "\n")
            self.dirty = False
        except OSError as e:
            print(f"! could not save the place cache {self.path}: {e}", file=sys.stderr)


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


def apply_places(items: list, gazetteer: Optional[Gazetteer] = None,
                 cache: Optional[PlaceCache] = None) -> int:
    """Name the place of every located file (detail row "Place"; the days'
    places follow from these, see DayGroup.places). Returns how many got one."""
    gazetteer = gazetteer or _GAZETTEER
    named = 0
    for it in items:
        if not it.has_gps:
            continue
        place = cache.lookup(it.lat, it.lon) if cache is not None else gazetteer.nearest(it.lat, it.lon)
        if place is None:
            continue
        it.place = place["name"]
        it.detail["place"] = describe(place)
        named += 1
    if cache is not None:
        cache.save()
    return named
