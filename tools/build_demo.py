#!/usr/bin/env python3
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Pieter Remi Coppens
"""Rebuild the sample trip in docs/demo with the real site builder:

    python tools/build_demo.py

The input is docs/demo-source (trip_content.json with the titles, day stories
and captions, and a GPX track for both days) plus the four sample photos in
docs/demo/media. The photos have capture dates but no GPS: the GPX track puts
them on the map and draws the route, just as it would for a camera without
GPS. The Leaflet library and fonts are taken from docs/demo/assets/vendor, so
no download is needed. docs/demo/NOTICE.txt is kept; everything else in
docs/demo is replaced by the new build.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "docs" / "demo-source"
DEMO = ROOT / "docs" / "demo"
KEEP = {"NOTICE.txt"}
TIMEZONE = "Europe/Zurich"


def main() -> int:
    photos = sorted((DEMO / "media").glob("*.jpg"))
    vendor = DEMO / "assets" / "vendor"
    if not photos or not (vendor / "VERSIONS.txt").is_file():
        print(f"Expected the sample photos in {DEMO / 'media'} and the offline assets in {vendor}.",
              file=sys.stderr)
        return 1
    with tempfile.TemporaryDirectory(prefix="albummaker-demo-") as temporary:
        work = Path(temporary)
        trip, site, config = work / "Lake Lucerne", work / "site", work / "config"
        trip.mkdir()
        for photo in photos:
            shutil.copy2(photo, trip / photo.name)
        for name in ("trip_content.json", "lake-lucerne.gpx"):
            shutil.copy2(SOURCE / name, trip / name)
        # the builder copies offline assets from AlbumMaker's own folder
        shutil.copytree(vendor, config / "offline-assets")
        env = dict(os.environ, ALBUMMAKER_CONFIG_DIR=str(config))
        subprocess.run([sys.executable, str(ROOT / "generate_site.py"), "-i", str(trip),
                        "-o", str(site), "--timezone", TIMEZONE,
                        "--large-thumb-size", "1600"],
                       cwd=work, env=env, check=True)
        (site / ".trip_site_manifest.json").unlink(missing_ok=True)   # only needed to rebuild

        for entry in DEMO.iterdir():
            if entry.name in KEEP:
                continue
            shutil.rmtree(entry) if entry.is_dir() else entry.unlink()
        for entry in site.iterdir():
            target = DEMO / entry.name
            shutil.copytree(entry, target) if entry.is_dir() else shutil.copy2(entry, target)
    print(f"Sample trip rebuilt in {DEMO}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
