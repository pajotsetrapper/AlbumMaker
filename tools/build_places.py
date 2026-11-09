#!/usr/bin/env python3
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Pieter Remi Coppens
"""Rebuild albummaker/data/places.tsv.gz, the offline list of towns used for
place names, from GeoNames (https://www.geonames.org/, CC BY 4.0):

    python tools/build_places.py

It downloads cities1000.zip (every populated place with at least 1000
inhabitants) and countryInfo.txt, keeps real towns (not districts of a city,
abandoned or historical places), and writes one compact, gzipped text file:

    @JP<TAB>Japan                                   one line per country
    Kyoto<TAB>JP<TAB>3502<TAB>13575<TAB>1500000     name, country, lat and lon
                                                   in 1/100 degree, population
                                                   (2 significant digits)

Run it again now and then to pick up GeoNames' corrections; the output is
deterministic, so an unchanged source gives a byte-identical file.
"""

import gzip
import io
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "albummaker" / "data" / "places.tsv.gz"
SOURCE = "https://download.geonames.org/export/dump/"

# GeoNames feature codes for real towns and villages. Left out: PPLX (a
# section of a city, e.g. a ward), PPLH/PPLQ/PPLW/PPLCH (historical,
# abandoned, destroyed), which would make "near ..." less useful.
KEEP = {"PPL", "PPLA", "PPLA2", "PPLA3", "PPLA4", "PPLA5", "PPLC", "PPLG", "PPLL",
        "PPLS", "PPLF", "PPLR", "STLMT"}


def fetch(name: str) -> bytes:
    req = urllib.request.Request(SOURCE + name, headers={"User-Agent": "AlbumMaker-build-places/1.0"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.read()


# administrative suffixes GeoNames keeps in many Japanese and Korean names
# ("Nara-shi", "Yeosu-si"), which read oddly in "near Nara-shi"
SUFFIXES = {"JP": ("-shi", "-machi", "-mura", "-ch\u014d", "-cho", "-son"),
            "KR": ("-si", "-gun", "-eup", "-myeon")}


def clean(s: str) -> str:
    return " ".join(s.replace("\t", " ").split())


def town_name(name: str, cc: str) -> str:
    for suffix in SUFFIXES.get(cc, ()):
        if name.lower().endswith(suffix) and len(name) > len(suffix) + 1:
            return name[:-len(suffix)]
    return name


def rounded(population: int) -> int:
    """Two significant digits: it only weighs a big town against a small one,
    and the rounding makes the file compress noticeably better."""
    if population < 100:
        return population
    return round(population, -(len(str(population)) - 2))


def main() -> int:
    countries = {}
    for line in fetch("countryInfo.txt").decode("utf-8").splitlines():
        if line and not line.startswith("#"):
            cols = line.split("\t")
            countries[cols[0]] = clean(cols[4])

    with zipfile.ZipFile(io.BytesIO(fetch("cities1000.zip"))) as z:
        raw = z.read("cities1000.txt").decode("utf-8")
    places = []
    for line in raw.splitlines():
        cols = line.split("\t")
        if len(cols) < 15 or cols[7] not in KEEP:
            continue
        cc = cols[8]
        name = town_name(clean(cols[1]), cc)
        try:
            lat, lon, pop = float(cols[4]), float(cols[5]), int(cols[14] or 0)
        except ValueError:
            continue
        if name and cc in countries:
            places.append((name, cc, round(lat * 100), round(lon * 100), rounded(pop)))
    places.sort(key=lambda p: (p[2], p[3], p[0]))

    out = io.StringIO()
    for cc in sorted({p[1] for p in places}):
        out.write(f"@{cc}\t{countries[cc]}\n")
    for p in places:
        out.write("\t".join(map(str, p)) + "\n")
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    with open(TARGET, "wb") as f, gzip.GzipFile(fileobj=f, mode="wb", mtime=0, compresslevel=9) as gz:
        gz.write(out.getvalue().encode("utf-8"))
    print(f"Wrote {TARGET} ({len(places)} places, {TARGET.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
