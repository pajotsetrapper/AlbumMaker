# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Pieter Remi Coppens
"""Small pure helpers: GPS validation, date parsing, the info-panel text
formatters, ignore rules and distance maths."""
import argparse
import unittest
from datetime import datetime
from pathlib import Path

from .helpers import tc


class GpsTests(unittest.TestCase):
    def test_real_coordinates_are_accepted(self):
        self.assertTrue(tc.valid_gps(50.85, 4.35))
        self.assertTrue(tc.valid_gps(-90, 180))
        self.assertTrue(tc.valid_gps(0, 12.5))        # on the equator is fine

    def test_no_fix_placeholder_is_rejected(self):
        self.assertFalse(tc.valid_gps(0, 0))

    def test_impossible_values_are_rejected(self):
        for lat, lon in [(91, 0), (-91, 10), (10, 181), (10, -181),
                         (float("nan"), 5), (5, float("inf"))]:
            with self.subTest(lat=lat, lon=lon):
                self.assertFalse(tc.valid_gps(lat, lon))

    def test_gps_display_uses_hemispheres(self):
        self.assertEqual(tc.format_gps_display(50.85, 4.35), "50.85000\u00b0 N, 4.35000\u00b0 E")
        self.assertEqual(tc.format_gps_display(-33.9, -70.6), "33.90000\u00b0 S, 70.60000\u00b0 W")


class IsoDatetimeTests(unittest.TestCase):
    def test_common_forms(self):
        self.assertEqual(tc._parse_iso_datetime("2026-05-01T10:00:00+02:00").utcoffset().total_seconds(), 7200)
        self.assertEqual(tc._parse_iso_datetime("2026-05-01T10:00:00Z").utcoffset().total_seconds(), 0)

    def test_offset_without_colon(self):
        # what iPhones write; older Pythons reject it unless it is normalised
        self.assertEqual(tc._parse_iso_datetime("2026-05-01T10:00:00+0200").utcoffset().total_seconds(), 7200)
        self.assertEqual(tc._parse_iso_datetime("2026-05-01T10:00:00-0530").utcoffset().total_seconds(), -19800)

    def test_garbage_gives_none(self):
        for bad in ("", "not a date", "2026-13-45T99:00:00"):
            with self.subTest(bad=bad):
                self.assertIsNone(tc._parse_iso_datetime(bad))


class TextFormatterTests(unittest.TestCase):
    def test_camera_does_not_repeat_the_make(self):
        self.assertEqual(tc.format_camera("Canon", "Canon EOS R5"), "Canon EOS R5")
        self.assertEqual(tc.format_camera("Apple", "iPhone 15"), "Apple iPhone 15")
        self.assertEqual(tc.format_camera(None, "X100V"), "X100V")
        self.assertEqual(tc.format_camera("Sony", None), "Sony")
        self.assertIsNone(tc.format_camera("", "  "))

    def test_aperture(self):
        self.assertEqual(tc.format_fnumber(1.8), "f/1.8")
        self.assertEqual(tc.format_fnumber(8), "f/8")
        self.assertIsNone(tc.format_fnumber(0))
        self.assertIsNone(tc.format_fnumber("junk"))

    def test_exposure(self):
        self.assertEqual(tc.format_exposure(0.004), "1/250s")
        self.assertEqual(tc.format_exposure(2), "2s")
        self.assertIsNone(tc.format_exposure(0))

    def test_iso_accepts_tuples(self):
        self.assertEqual(tc.format_iso(400), "ISO 400")
        self.assertEqual(tc.format_iso((800, 0)), "ISO 800")
        self.assertIsNone(tc.format_iso(None))

    def test_focal_length_with_35mm_equivalent(self):
        self.assertEqual(tc.format_focal(24), "24mm")
        self.assertEqual(tc.format_focal(5.7, 26), "5.7mm (35mm: 26mm)")

    def test_altitude_below_sea_level(self):
        self.assertEqual(tc.format_altitude(120.4, 0), "120 m")
        self.assertEqual(tc.format_altitude(30, 1), "-30 m")

    def test_filesize(self):
        self.assertEqual(tc.human_filesize(512), "512 B")
        self.assertEqual(tc.human_filesize(2048), "2.0 KB")
        self.assertEqual(tc.human_filesize(5 * 1024 * 1024), "5.0 MB")
        self.assertIsNone(tc.human_filesize("n/a"))

    def test_duration(self):
        self.assertEqual(tc.format_duration(5), "0:05")
        self.assertEqual(tc.format_duration(75), "1:15")
        self.assertEqual(tc.format_duration(3725), "1:02:05")
        self.assertIsNone(tc.format_duration(None))

    def test_long_date_is_portable(self):
        # no %-d / %#d, which differ between Linux/macOS and Windows
        self.assertEqual(tc.format_long_date(datetime(2026, 5, 1)), "Friday, May 1, 2026")
        self.assertEqual(tc.format_long_date(datetime(2026, 12, 25)), "Friday, December 25, 2026")


class ThumbSizeArgumentTests(unittest.TestCase):
    def test_range(self):
        self.assertEqual(tc.positive_thumb_size("400"), 400)
        self.assertEqual(tc.positive_thumb_size("16"), 16)
        self.assertEqual(tc.positive_thumb_size("4096"), 4096)

    def test_rejects_out_of_range_and_non_numbers(self):
        for bad in ("15", "4097", "0", "-5", "abc", "12.5", ""):
            with self.subTest(bad=bad), self.assertRaises(argparse.ArgumentTypeError):
                tc.positive_thumb_size(bad)


class IgnoredPathTests(unittest.TestCase):
    root = Path("/photos")

    def ignored(self, rel):
        return tc.is_ignored_path(self.root / rel, self.root)

    def test_normal_files_are_kept(self):
        self.assertFalse(self.ignored("IMG_1.jpg"))
        self.assertFalse(self.ignored("Day 1/IMG_2.JPG"))

    def test_hidden_and_apple_double_files_are_skipped(self):
        self.assertTrue(self.ignored(".hidden.jpg"))
        self.assertTrue(self.ignored("._IMG_1234.JPG"))     # macOS companion files
        self.assertTrue(self.ignored("trip/.thumbs/x.jpg"))

    def test_nas_and_recycle_folders_are_skipped(self):
        for folder in ("@eaDir", "#recycle", "$RECYCLE.BIN"):
            with self.subTest(folder=folder):
                self.assertTrue(self.ignored(f"{folder}/x.jpg"))

    def test_a_dot_in_the_root_itself_does_not_matter(self):
        # only the part below the photos folder counts
        self.assertFalse(tc.is_ignored_path(Path("/home/me/.photos/a.jpg"), Path("/home/me/.photos")))


class DistanceTests(unittest.TestCase):
    def test_known_distance(self):
        km = tc._route_distance_km([(50.8503, 4.3517), (48.8566, 2.3522)])   # Brussels -> Paris
        self.assertAlmostEqual(km, 264, delta=3)

    def test_fewer_than_two_points_is_zero(self):
        self.assertEqual(tc._route_distance_km([]), 0.0)
        self.assertEqual(tc._route_distance_km([(1, 2)]), 0.0)

    def test_adds_up_each_leg(self):
        a, b, c = (50.0, 4.0), (51.0, 4.0), (52.0, 4.0)
        self.assertAlmostEqual(tc._route_distance_km([a, b, c]),
                               tc._route_distance_km([a, b]) + tc._route_distance_km([b, c]))


if __name__ == "__main__":
    unittest.main()
