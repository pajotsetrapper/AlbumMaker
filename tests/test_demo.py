# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Pieter Remi Coppens
"""The sample trip's source files (docs/demo-source) stay usable: the trip
file loads, every photo it mentions exists, and the GPX track covers the
moment each sample photo was taken, so tools/build_demo.py can place them."""
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PIL import Image

from albummaker import content
from albummaker.gpx import read_gpx

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "docs" / "demo-source"
PHOTOS = ROOT / "docs" / "demo" / "media"
CEST = timezone(timedelta(hours=2))      # Europe/Zurich in May


class DemoSourceTests(unittest.TestCase):
    def test_the_trip_file_loads_and_names_only_sample_photos(self):
        trip = content.parse_content_file(SOURCE / "trip_content.json")
        self.assertTrue(content.trip_title(trip))
        names = {p.name for p in PHOTOS.glob("*.jpg")}
        self.assertTrue(set(trip["photos"]) <= names)
        self.assertIn(trip["cover"], names)
        for day in trip["days"].values():
            self.assertIn("\n\n", day["description"])   # shows off paragraphs
            self.assertIn(day["cover"], names)

    def test_the_track_covers_every_sample_photo(self):
        segments = read_gpx(SOURCE / "lake-lucerne.gpx")
        self.assertEqual(len(segments), 2)                # one per day
        for photo in sorted(PHOTOS.glob("*.jpg")):
            taken = Image.open(photo).getexif().get(306)
            when = datetime.strptime(taken, "%Y:%m:%d %H:%M:%S").replace(tzinfo=CEST).timestamp()
            self.assertTrue(any(seg[0][0] <= when <= seg[-1][0] for seg in segments),
                            f"{photo.name} at {taken} is outside the track")


if __name__ == "__main__":
    unittest.main()
