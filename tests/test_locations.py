"""GPS tracks (.gpx) and offline place names: reading tracks, working out
each photo's moment in UTC, placing photos on the track, the day maps' track
lines, naming places, and how both scripts show the result.

    python -m unittest tests.test_locations
"""
import gzip
import json
import re
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from .helpers import (TempDirMixin, TripServer, install_app, make_gpx, make_jpeg, read_json, run_script,
                      slides_of, write_json)

from tripsite import gpx, metadata, places, scan  # noqa: E402  (helpers puts the project on sys.path)
from tripsite.model import DayGroup, MediaItem  # noqa: E402

KYOTO_STATION = (34.9858, 135.7588)
KINKAKUJI = (35.0394, 135.7292)
NARA_PARK = (34.6851, 135.8430)


def item(when="2026-05-01 10:00", gps=None, offset=None, src="", fallback=False):
    lat, lon = gps if gps else (None, None)
    return MediaItem(path=Path("/p/x.jpg"), media_type="image", dt=datetime.fromisoformat(when),
                     dt_is_fallback=fallback, lat=lat, lon=lon, utc_offset=offset, utc_offset_src=src)


def kyoto_track(photos: Path) -> Path:
    """00:00-06:00 UTC on 2026-05-01: Kyoto station -> Kinkakuji (01:00) -> Nara park (05:00), one point a minute."""
    pts = []
    for m in range(0, 361):
        if m <= 60:
            a, b, f = KYOTO_STATION, KINKAKUJI, m / 60
        else:
            a, b, f = KINKAKUJI, NARA_PARK, (m - 60) / 240
        pts.append((f"2026-05-01T{m // 60:02d}:{m % 60:02d}:00Z",
                    round(a[0] + (b[0] - a[0]) * f, 6), round(a[1] + (b[1] - a[1]) * f, 6)))
    return make_gpx(photos / "watch.gpx", [pts])


# ---------------------------------------------------------------- reading GPX

class GpxReadingTests(TempDirMixin, unittest.TestCase):
    def setUp(self):
        self.tmp = self.make_tmp()

    def test_times_in_every_form_gpx_files_use(self):
        base = datetime(2026, 5, 1, 9, 0).replace(tzinfo=__import__("datetime").timezone.utc).timestamp()
        for text, want in (("2026-05-01T09:00:00Z", base), ("2026-05-01T09:00:00.250Z", base + 0.25),
                           ("2026-05-01T11:00:00+02:00", base), ("2026-05-01T11:00:00+0200", base),
                           ("2026-05-01T09:00:00", base), (" 2026-05-01T09:00:00Z\n", base),
                           ("2026-05-01T04:30:00-04:30", base)):
            with self.subTest(text=text):
                self.assertAlmostEqual(gpx.parse_gpx_time(text), want)
        for bad in ("", None, "yesterday", "2026-13-01T09:00:00Z", "2026-05-01 09:00"):
            self.assertIsNone(gpx.parse_gpx_time(bad), bad)

    def test_segments_both_gpx_versions_and_points_without_a_time(self):
        for version in ("1.0", "1.1"):
            with self.subTest(version=version):
                path = make_gpx(self.tmp / f"v{version}.gpx", [
                    [("2026-05-01T09:00:00Z", 50.0, 4.0), ("2026-05-01T09:01:00Z", 50.001, 4.0)],
                    [("2026-05-01T10:00:00Z", 51.0, 4.0)]], version=version)
                segs = gpx.read_gpx(path)
                self.assertEqual([len(s) for s in segs], [2, 1])
                self.assertEqual(segs[0][1][1:], (50.001, 4.0))
        path = self.tmp / "untimed.gpx"
        path.write_text('<gpx xmlns="http://www.topografix.com/GPX/1/1"><trk><trkseg>'
                        '<trkpt lat="50" lon="4"></trkpt><trkpt lat="0" lon="0"><time>2026-05-01T09:00:00Z</time>'
                        '</trkpt><trkpt lat="99" lon="4"><time>2026-05-01T09:00:00Z</time></trkpt>'
                        '</trkseg></trk></gpx>')
        self.assertEqual(gpx.read_gpx(path), [], "no time, 0,0 and impossible points are all skipped")

    def test_a_file_with_a_dtd_is_refused(self):
        # the "billion laughs": entities that expand to gigabytes; GPX never needs a DTD
        path = self.tmp / "bomb.gpx"
        path.write_text('<?xml version="1.0"?><!DOCTYPE gpx [<!ENTITY a "aaaaaaaaaa">'
                        '<!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">]><gpx>&b;</gpx>')
        with self.assertRaises(ValueError):
            gpx.read_gpx(path)
        late = self.tmp / "late.gpx"                      # wherever it is ...
        late.write_text('<?xml version="1.0"?><!--' + "x" * 8000 + '--><!DOCTYPE gpx [<!ENTITY a "a">]><gpx>&a;</gpx>')
        utf16 = self.tmp / "utf16.gpx"                    # ... and however the file is encoded
        utf16.write_bytes('<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE gpx [<!ENTITY a "a">]><gpx>&a;</gpx>'
                          .encode("utf-16"))
        for f in (late, utf16):
            with self.subTest(f=f.name), self.assertRaises(ValueError):
                gpx.read_gpx(f)

    def test_broken_files_are_skipped_with_a_note(self):
        good = make_gpx(self.tmp / "good.gpx", [[("2026-05-01T09:00:00Z", 50.0, 4.0)]])
        (self.tmp / "broken.gpx").write_text("<gpx><trk><trkseg><trkpt")
        (self.tmp / "empty.gpx").write_text('<gpx xmlns="http://www.topografix.com/GPX/1/1"></gpx>')
        err = __import__("io").StringIO()
        with mock.patch("sys.stdout"), mock.patch("sys.stderr", err):
            track = gpx.load_tracks([self.tmp / "broken.gpx", self.tmp / "empty.gpx", good])
        self.assertEqual(len(track), 1)
        self.assertIn("broken.gpx", err.getvalue())
        self.assertIn("empty.gpx", err.getvalue())

    def test_gpx_files_are_found_in_the_photos_folder_and_given_ones_are_added(self):
        photos = self.tmp / "photos"
        make_gpx(photos / "day1.GPX", [])
        make_gpx(photos / ".hidden" / "x.gpx", [])
        make_gpx(photos / "sub" / "day2.gpx", [])
        elsewhere = make_gpx(self.tmp / "elsewhere.gpx", [])
        folder = self.tmp / "tracks"
        make_gpx(folder / "a.gpx", [])
        found = gpx.find_gpx_files(photos, [elsewhere, folder, photos / "day1.GPX"])
        self.assertEqual(sorted(p.name for p in found), ["a.gpx", "day1.GPX", "day2.gpx", "elsewhere.gpx"])


# ---------------------------------------------------------------- the track

def ts(text):
    return gpx.parse_gpx_time(text)


class TrackTests(unittest.TestCase):
    def setUp(self):
        self.track = gpx.Track([
            [(ts("2026-05-01T09:00:00Z"), 50.0, 4.0), (ts("2026-05-01T09:10:00Z"), 50.1, 4.2),
             (ts("2026-05-01T09:40:00Z"), 50.4, 4.2)],                      # a 30-minute gap in the middle
            [(ts("2026-05-01T09:12:00Z"), 60.0, 10.0)],                     # another device, overlapping in time
        ])

    def test_exact_and_interpolated_positions(self):
        self.assertEqual(self.track.position_at(ts("2026-05-01T09:00:00Z")), (50.0, 4.0))
        lat, lon = self.track.position_at(ts("2026-05-01T09:05:00Z"))
        self.assertAlmostEqual(lat, 50.05)
        self.assertAlmostEqual(lon, 4.1)

    def test_no_position_is_made_up_across_a_gap(self):
        self.assertIsNone(self.track.position_at(ts("2026-05-01T09:25:00Z")))    # mid-gap

    def test_where_recordings_overlap_the_closest_point_in_time_wins(self):
        self.assertEqual(self.track.position_at(ts("2026-05-01T09:11:30Z")), (60.0, 10.0))   # 30 s from 09:12
        self.assertEqual(self.track.position_at(ts("2026-05-01T09:10:20Z")), (50.1, 4.2))    # 20 s from 09:10

    def test_just_outside_the_track_its_end_is_used_further_out_nothing(self):
        self.assertEqual(self.track.position_at(ts("2026-05-01T08:59:00Z")), (50.0, 4.0))
        self.assertIsNone(self.track.position_at(ts("2026-05-01T08:50:00Z")))
        self.assertIsNone(gpx.Track([]).position_at(ts("2026-05-01T09:00:00Z")))

    def test_stretches_break_at_pauses_and_keep_each_recording_apart(self):
        stretches = self.track.stretches(ts("2026-05-01T00:00:00Z"), ts("2026-05-02T00:00:00Z"))
        self.assertEqual(stretches, [[(50.0, 4.0), (50.1, 4.2)]])   # 09:40 after a pause, 09:12 alone

    def test_two_devices_recording_at_once_each_keep_their_line(self):
        watch = [(ts(f"2026-05-01T09:{m:02d}:00Z"), 50 + m / 1000, 4.0) for m in range(0, 30, 2)]
        phone = [(ts(f"2026-05-01T09:{m:02d}:00Z"), 50 + m / 1000, 4.001) for m in range(1, 30, 2)]
        stretches = gpx.Track([watch, phone]).stretches(ts("2026-05-01T00:00:00Z"), ts("2026-05-02T00:00:00Z"))
        self.assertEqual(sorted(len(s) for s in stretches), [15, 15])

    def test_the_same_track_given_twice_counts_once(self):
        seg = [(ts(f"2026-05-01T09:{m:02d}:00Z"), 50 + m / 1000, 4.0) for m in range(10)]
        track = gpx.Track([seg, list(seg)])
        self.assertEqual(len(track), 10)
        self.assertEqual(len(track.stretches(0, 4e9)), 1)

    def test_simplify_drops_points_on_a_straight_line_and_keeps_corners(self):
        line = [(50.0, 4.0 + i * 0.001) for i in range(100)]
        self.assertEqual(len(gpx.simplify(line, 5)), 2)
        corner = line + [(50.0 + i * 0.001, 4.099) for i in range(1, 50)]
        self.assertEqual(gpx.simplify(corner, 5), [line[0], line[-1], corner[-1]])

    def test_a_long_wiggly_track_is_cut_down_to_the_point_budget(self):
        import math
        wiggle = [(50 + i * 0.0001, 4 + 0.001 * math.sin(i / 3)) for i in range(20000)]
        out = gpx.simplified_stretches([wiggle], 1, 500)
        self.assertLessEqual(sum(len(s) for s in out), 500)
        self.assertEqual(out[0][0], [50.0, 4.0])


class TimeZoneTests(unittest.TestCase):
    def test_a_file_without_an_offset_takes_its_days_then_the_given_then_the_trips(self):
        phone_day1 = item("2026-05-01 09:00", offset=540, src="exif")
        phone_day2 = item("2026-05-02 09:00", offset=480, src="exif")
        another_day2 = item("2026-05-02 10:00", offset=480, src="apple")
        video = item("2026-05-03 09:00", offset=-300, src="utc")       # an assumed offset: no vote
        zones = gpx.TimeZones([phone_day1, phone_day2, another_day2, video])
        self.assertEqual(zones.for_day("2026-05-01"), 540)
        self.assertEqual(zones.for_day("2026-05-02"), 480)
        self.assertEqual(zones.for_day("2026-05-03"), 480, "the trip's most common")
        self.assertEqual(gpx.TimeZones([phone_day1], tz_offset_hours=2).for_day("2026-05-09"), 120)
        self.assertIsNone(gpx.TimeZones([video]).for_day("2026-05-03"))

    def test_utc_time_of_a_file(self):
        zones = gpx.TimeZones([item(offset=540, src="exif")])
        self.assertEqual(zones.utc_timestamp(item("2026-05-01 10:00")), ts("2026-05-01T01:00:00Z"))
        self.assertEqual(zones.utc_timestamp(item("2026-05-01 10:00", offset=-300, src="utc")),
                         ts("2026-05-01T15:00:00Z"), "its own offset comes first")
        self.assertIsNone(zones.utc_timestamp(item(fallback=True)), "no real date, no match")


class OffsetMetadataTests(TempDirMixin, unittest.TestCase):
    def test_exif_offset_and_gps_time_stamp(self):
        tmp = self.make_tmp()
        stated = metadata.read_image_metadata(make_jpeg(tmp / "a.jpg", "2026:05:01 10:00:00", offset="+09:00"))
        self.assertEqual((stated["utc_offset"], stated["utc_offset_src"]), (540, "exif"))
        self.assertNotIn("utc_offset", metadata.read_image_metadata(make_jpeg(tmp / "b.jpg")))

        from PIL import Image
        path = make_jpeg(tmp / "c.jpg", "2026:05:01 10:03:00", (35.0, 135.7))
        with Image.open(path) as im:
            exif = im.getexif()
            g = exif.get_ifd(0x8825)
            g[29] = "2026:05:01"                       # GPSDateStamp
            g[7] = (1.0, 0.0, 41.0)                    # GPSTimeStamp 01:00:41 UTC, a slightly old fix
            im.save(tmp / "d.jpg", exif=exif)
        found = metadata.read_image_metadata(tmp / "d.jpg")
        self.assertEqual((found["utc_offset"], found["utc_offset_src"]), (540, "gps"))

    def test_an_old_gps_fix_is_no_evidence_of_the_time_zone(self):
        local = datetime(2026, 5, 1, 12, 0)
        stamp = lambda h, m: {"GPSDateStamp": "2026:05:01", "GPSTimeStamp": (h, m, 0)}
        self.assertEqual(metadata._offset_from_gps_stamp(local, stamp(10, 1)), 120)    # a fresh fix
        self.assertEqual(metadata._offset_from_gps_stamp(local, stamp(4, 30)), 450)    # +07:30 exists
        self.assertIsNone(metadata._offset_from_gps_stamp(local, stamp(9, 52)), "8 minutes old: not +02:15")
        self.assertIsNone(metadata._offset_from_gps_stamp(local, {"GPSDateStamp": "nonsense"}))

    def test_the_offset_belongs_to_the_time_that_was_used(self):
        tmp = self.make_tmp()
        from PIL import Image
        exif = Image.Exif()
        ifd = exif.get_ifd(0x8769)
        ifd[36868], ifd[36882] = "2026:05:01 10:00:00", "+09:00"      # only DateTimeDigitized + its offset
        ifd[36880] = "+01:00"                                            # OffsetTime: of DateTime, absent here
        Image.new("RGB", (8, 8)).save(tmp / "digitized.jpg", exif=exif)
        meta = metadata.read_image_metadata(tmp / "digitized.jpg")
        self.assertEqual((meta["dt"], meta["utc_offset"]), (datetime(2026, 5, 1, 10, 0), 540))
        exif = Image.Exif()
        exif[306] = "2026:05:01 10:00:00"
        exif.get_ifd(0x8769)[36881] = "+09:00"                           # OffsetTimeOriginal, but no original time
        Image.new("RGB", (8, 8)).save(tmp / "edited.jpg", exif=exif)
        self.assertNotIn("utc_offset", metadata.read_image_metadata(tmp / "edited.jpg"))

    def test_offsets_are_parsed_and_checked(self):
        for text, want in (("+02:00", 120), ("-05:30", -330), ("+0545", 345), ("+00:00", 0),
                           ("+15:00", None), ("2", None), ("", None), (None, None)):
            self.assertEqual(metadata.parse_utc_offset(text), want, text)


class LocatingTests(unittest.TestCase):
    def test_files_without_a_location_are_placed_and_labelled(self):
        track = gpx.Track([[(ts("2026-05-01T01:00:00Z"), *KINKAKUJI), (ts("2026-05-01T01:05:00Z"), *KYOTO_STATION)]])
        phone = item("2026-05-01 09:00", gps=KYOTO_STATION, offset=540, src="exif")
        camera = item("2026-05-01 10:00")
        undated = item("2026-05-01 10:00", fallback=True)
        far = item("2026-05-01 18:00")
        with mock.patch("sys.stdout"):
            n = gpx.locate_from_track([phone, camera, undated, far], track, gpx.TimeZones([phone]))
        self.assertEqual(n, 1)
        self.assertEqual((camera.lat, camera.lon), KINKAKUJI)
        self.assertIn("(from GPX track)", camera.detail["gps_display"])
        self.assertEqual((phone.lat, phone.lon), KYOTO_STATION, "its own GPS wins")
        self.assertNotIn("GPX", phone.detail.get("gps_display", ""))
        self.assertFalse(undated.has_gps or far.has_gps)

    def test_without_any_time_zone_it_says_what_to_do(self):
        track = gpx.Track([[(ts("2026-05-01T01:00:00Z"), *KINKAKUJI)]])
        out = __import__("io").StringIO()
        with mock.patch("sys.stdout", out):
            self.assertEqual(gpx.locate_from_track([item()], track, gpx.TimeZones([])), 0)
        self.assertIn("--tz-offset", out.getvalue())

    def test_each_day_gets_its_local_part_of_the_track(self):
        # in Japan (+09:00) local midnight of 1 May is 15:00 UTC on 30 April
        track = gpx.Track([[(ts("2026-04-30T14:40:00Z"), 35.0, 135.0), (ts("2026-04-30T14:50:00Z"), 35.01, 135.0),
                            (ts("2026-04-30T15:00:00Z"), 35.02, 135.0), (ts("2026-04-30T15:10:00Z"), 35.03, 135.0)]])
        zones = gpx.TimeZones([item("2026-05-01 09:00", offset=540, src="exif")])
        apr30 = DayGroup("2026-04-30", datetime(2026, 4, 30))
        may1 = DayGroup("2026-05-01", datetime(2026, 5, 1))
        may2 = DayGroup("2026-05-02", datetime(2026, 5, 2))
        unknown = DayGroup(scan.UNKNOWN_DAY_KEY, None, is_unknown=True)
        self.assertEqual(gpx.attach_day_tracks([apr30, may1, may2, unknown], track, zones), 2)
        self.assertEqual(apr30.track, [[[35.0, 135.0], [35.01, 135.0]]])     # 23:40 and 23:50 local
        self.assertEqual(may1.track, [[[35.02, 135.0], [35.03, 135.0]]])     # 00:00 and 00:10 local
        self.assertEqual((may2.track, unknown.track), ([], []))
        self.assertGreater(may1.distance_km, 1)


class DayDistanceTests(unittest.TestCase):
    def test_a_track_of_only_part_of_the_day_does_not_shrink_its_distance(self):
        far = [item(gps=(50.0, 4.0)), item(gps=(50.3, 4.0))]                     # ~33 km apart
        day = DayGroup("2026-05-01", datetime(2026, 5, 1), items=far, track_km=5.0)
        self.assertAlmostEqual(day.distance_km, 33.4, delta=0.5)
        day.track_km = 60.0                                                       # a winding road
        self.assertEqual(day.distance_km, 60.0)

    def test_the_day_track_is_measured_before_it_is_cut_down_for_the_map(self):
        import math
        start = ts("2026-05-01T00:00:00Z")
        wiggle = [(start + i, 50 + i * 0.00002, 4 + 0.0005 * math.sin(i / 20)) for i in range(20000)]
        day = DayGroup("2026-05-01", datetime(2026, 5, 1))
        gpx.attach_day_tracks([day], gpx.Track([wiggle]), gpx.TimeZones([], tz_offset_hours=0), max_points=100)
        self.assertLessEqual(sum(len(s) for s in day.track), 100)
        self.assertLessEqual(sum(len(s) for s in day.track_overview), 300)
        raw = gpx.route_distance_km([(lat, lon) for _, lat, lon in wiggle])
        coarse = sum(gpx.route_distance_km(s) for s in day.track)
        self.assertAlmostEqual(day.track_km, raw, delta=raw * 0.03, msg="the wiggles count")
        self.assertLess(coarse, raw * 0.9, "(the map's copy would have lost them)")


# ---------------------------------------------------------------- place names

def gazetteer(*rows, countries=(("XX", "Testland"),)):
    text = "".join(f"@{cc}\t{name}\n" for cc, name in countries)
    text += "".join("\t".join(map(str, r)) + "\n" for r in rows)
    return places.Gazetteer(gzip.compress(text.encode("utf-8")))


class PlaceNameTests(unittest.TestCase):
    def test_the_bundled_list_names_real_places(self):
        g = places.Gazetteer()
        for (lat, lon), want in ((KYOTO_STATION, "Kyoto, Japan"), (KINKAKUJI, "Kyoto, Japan"),
                                 (NARA_PARK, "Nara, Japan"), ((52.3731, 4.8926), "Amsterdam, The Netherlands"),
                                 ((-33.8568, 151.2153), "Sydney, Australia")):
            with self.subTest(want=want):
                self.assertEqual(places.describe(g.nearest(lat, lon)), want)
        self.assertIsNone(g.nearest(40.0, -40.0), "the middle of the Atlantic")

    def test_a_big_town_wins_over_a_closer_village_and_far_away_is_near(self):
        g = gazetteer(("Bigtown", "XX", 5000, 1000, 1000000), ("Hamlet", "XX", 5003, 1003, 1000))
        here = g.nearest(50.035, 10.035)                         # ~3.5 km from Bigtown, ~0.6 km from Hamlet
        self.assertEqual(here["name"], "Hamlet")
        self.assertEqual(g.nearest(50.06, 10.0)["name"], "Bigtown", "6.7 km out: well within a big town's reach")
        far = g.nearest(50.2, 10.0)
        self.assertEqual(places.describe(far), "near Bigtown, Testland")
        self.assertIsNone(g.nearest(51.0, 10.0), "beyond 50 km: nothing")

    def test_across_the_date_line_and_near_the_poles(self):
        g = gazetteer(("East", "XX", -1650, 17995, 5000), ("North", "XX", 7800, 1560, 2000))
        self.assertEqual(g.nearest(-16.5, -179.98)["name"], "East")
        self.assertEqual(g.nearest(78.1, 16.4)["name"], "North")       # longitude degrees are short up there

    def test_a_days_places_are_its_main_towns_in_visiting_order(self):
        def named(*towns):
            return [SimpleNamespace(place=t) for t in towns]
        self.assertEqual(places.day_places(named("Kyoto", "Kyoto", "Nara", "Nara", "Kyoto")), ["Kyoto", "Nara"])
        many = named(*(["Kyoto"] * 20 + ["Uji"] + ["Nara"] * 10 + ["Osaka"] * 5 + ["Kobe"] * 4))
        self.assertEqual(places.day_places(many), ["Kyoto", "Nara", "Osaka"], "at most 3; one-photo stops left out")
        self.assertEqual(places.day_places(named("", "")), [])

    def test_files_and_days_are_named_and_unknown_days_are_not(self):
        g = gazetteer(("Bigtown", "XX", 5000, 1000, 1000000))
        located, nowhere = item(gps=(50.0, 10.0)), item()
        day = DayGroup("2026-05-01", datetime(2026, 5, 1), items=[located, nowhere])
        unknown = DayGroup(scan.UNKNOWN_DAY_KEY, None, is_unknown=True, items=[item(gps=(50.0, 10.0))])
        self.assertEqual(places.apply_places([located, nowhere] + unknown.items, g), 2)
        self.assertEqual((located.place, located.detail["place"]), ("Bigtown", "Bigtown, Testland"))
        self.assertEqual((day.places, unknown.places), (["Bigtown"], []))
        self.assertNotIn("place", nowhere.detail)

    def test_visitors_never_see_a_town_only_a_hidden_photo_was_taken_in(self):
        shown = item(gps=(50.0, 10.0))
        shown.rel, shown.place = "a.jpg", "Bigtown"
        secret = item(gps=(51.0, 10.0))
        secret.rel, secret.place = "secret.jpg", "Secretville"
        day = DayGroup("2026-05-01", datetime(2026, 5, 1), items=[shown, secret],
                       track=[[[50.0, 10.0], [50.1, 10.0]]], track_overview=[[[50.0, 10.0]]], track_km=11.1)
        self.assertEqual(day.places, ["Bigtown", "Secretville"])
        content = {"photos": {"secret.jpg": {"hidden": True}}}
        [visible] = scan.visible_days([day], content)
        self.assertEqual(visible.places, ["Bigtown"])
        self.assertEqual((visible.track, visible.track_overview, visible.track_km),
                         (day.track, day.track_overview, day.track_km))
        self.assertIs(scan.visible_days([day], content, editable=True)[0], day)


# ---------------------------------------------------------------- the scripts

class StaticSiteLocationTests(TempDirMixin, unittest.TestCase):
    """A day in Kyoto and Nara: a phone photo with GPS and its time zone, two
    camera photos with neither, and the GPS watch's track."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = cls.make_tmp()
        cls.app = install_app(cls.tmp / "app")
        cls.photos = cls.tmp / "photos"
        make_jpeg(cls.photos / "phone.jpg", "2026:05:01 09:00:00", KYOTO_STATION, offset="+09:00")
        make_jpeg(cls.photos / "camera_kinkakuji.jpg", "2026:05:01 10:00:00", color=(1, 200, 1))
        make_jpeg(cls.photos / "camera_nara.jpg", "2026:05:01 14:00:00", color=(1, 1, 200))
        kyoto_track(cls.photos)

    def build(self, name, *extra):
        site = self.tmp / name
        r = run_script(self.app, "generate_site.py", "-i", self.photos, "-o", site, "-c",
                       self.tmp / f"{name}.json", "--workers", "1", *extra)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return site, r.stdout

    def test_camera_photos_are_placed_on_the_track_and_everything_is_named(self):
        site, out = self.build("site")
        self.assertIn("Placed 2 file(s) without a location on the GPX track", out)
        day = (site / "days" / "2026-05-01.html").read_text(encoding="utf-8")
        slides = {s["detail"]["filename"]: s for s in slides_of(day)}
        kinkakuji = slides["camera_kinkakuji.jpg"]
        self.assertEqual((kinkakuji["lat"], kinkakuji["lon"]), KINKAKUJI)
        self.assertIn("(from GPX track)", kinkakuji["detail"]["gps_display"])
        self.assertEqual(slides["camera_nara.jpg"]["detail"]["place"], "Nara, Japan")
        self.assertEqual(slides["phone.jpg"]["detail"]["place"], "Kyoto, Japan")
        self.assertIn('<div class="places">Kyoto · Nara</div>', day)
        self.assertIn("Place names: GeoNames (CC BY 4.0)", day)
        track = json.loads(re.search(r"initDayMap\(.*, (\[\[\[.*?\]\]\])\); }", day, re.S).group(1))
        self.assertEqual(track[0][0], list(KYOTO_STATION))
        index = (site / "index.html").read_text(encoding="utf-8")
        self.assertIn('<div class="places">Kyoto · Nara</div>', index)
        self.assertIn('"track": [[[34.9858, 135.7588]', index)
        km = float(re.search(r'<div class="n">([\d.]+)</div><div class="l">Km', index).group(1))
        self.assertGreater(km, 50, "measured along the track (station, Kinkakuji, Nara)")

    def test_both_can_be_switched_off(self):
        site, out = self.build("site-off", "--no-gpx", "--no-places")
        self.assertNotIn("GPX", out)
        day = (site / "days" / "2026-05-01.html").read_text(encoding="utf-8")
        self.assertNotIn('class="places"', day)
        self.assertNotIn("GeoNames", day)
        slides = {s["detail"]["filename"]: s for s in slides_of(day)}
        self.assertIsNone(slides["camera_nara.jpg"]["lat"])
        self.assertNotIn("place", slides["phone.jpg"]["detail"])

    def test_a_track_given_from_elsewhere_is_used_too(self):
        photos = self.tmp / "photos2"
        make_jpeg(photos / "camera.jpg", "2026:05:01 10:00:00")
        make_jpeg(photos / "phone.jpg", "2026:05:01 09:00:00", KYOTO_STATION, offset="+09:00")
        track = kyoto_track(self.tmp / "tracks")
        site = self.tmp / "site2"
        r = run_script(self.app, "generate_site.py", "-i", photos, "-o", site, "--workers", "1", "--gpx", track)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("Placed 1 file(s)", r.stdout)


class ServerLocationTests(TempDirMixin, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = cls.make_tmp()
        app = install_app(cls.tmp / "app")
        photos = cls.tmp / "photos"
        make_jpeg(photos / "phone.jpg", "2026:05:01 09:00:00", KYOTO_STATION, offset="+09:00")
        make_jpeg(photos / "camera_nara.jpg", "2026:05:01 14:00:00", color=(1, 1, 200))
        kyoto_track(photos)
        write_json(cls.tmp / "content.json", {})
        cls.server = TripServer(app, photos, cls.tmp).start()
        cls.addClassCleanup(cls.server.stop)

    def test_the_live_server_shows_the_same(self):
        day = self.server.text("/day/2026-05-01?preview=1")
        self.assertIn('<div class="places">Kyoto · Nara</div>', day)
        nara = next(s for s in slides_of(day) if s["detail"]["filename"] == "camera_nara.jpg")
        self.assertEqual(nara["detail"]["place"], "Nara, Japan")
        self.assertIn("GeoNames", self.server.text("/?preview=1"))

    def test_the_editor_is_offered_the_places_as_a_title_until_it_has_one(self):
        day = self.server.text("/day/2026-05-01")
        self.assertIn('data-title="Kyoto · Nara"', day)
        self.assertIn('placeholder="Kyoto · Nara"', day)
        status, _ = self.server.post_json("/api/day", {"date": "2026-05-01", "title": "Temples"})
        self.assertEqual(status, 200)
        try:
            self.assertNotIn('id="use-suggestion"', self.server.text("/day/2026-05-01"))
            self.assertNotIn("use-suggestion", self.server.text("/day/2026-05-01?preview=1"))
        finally:
            write_json(self.server.content, {"_reset": 1})

    def test_a_hidden_photos_town_disappears_for_visitors(self):
        write_json(self.server.content, {"photos": {"camera_nara.jpg": {"hidden": True}}})
        try:
            self.assertIn('<div class="places">Kyoto</div>', self.server.text("/day/2026-05-01?preview=1"))
            self.assertIn('<div class="places">Kyoto · Nara</div>', self.server.text("/day/2026-05-01"))
        finally:
            write_json(self.server.content, {"_reset": 2})


if __name__ == "__main__":
    unittest.main()
