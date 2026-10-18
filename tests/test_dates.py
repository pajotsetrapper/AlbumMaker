"""Day grouping and the date sanity check."""
import argparse
import contextlib
import io
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from .helpers import tc

NOW = datetime(2026, 10, 15)


def item(name, dt, fallback=False, media="image", path=None):
    return tc.MediaItem(path=path or Path("/nowhere") / name, media_type=media, dt=dt,
                        dt_is_fallback=fallback, lat=None, lon=None, rel=name)


def d(y, m, day, h=12):
    return datetime(y, m, day, h)


def names(flagged):
    return sorted(it.rel for it, _ in flagged)


class GroupByDayTests(unittest.TestCase):
    def test_groups_by_calendar_day_in_order(self):
        items = [item("c", d(2026, 5, 2)), item("a", d(2026, 5, 1, 9)), item("b", d(2026, 5, 1, 15))]
        days = tc.group_by_day(items)
        self.assertEqual([x.date_key for x in days], ["2026-05-01", "2026-05-02"])
        self.assertEqual([i.rel for i in days[0].items], ["a", "b"])

    def test_items_inside_a_day_are_sorted_by_time(self):
        items = [item("late", d(2026, 5, 1, 20)), item("early", d(2026, 5, 1, 7)), item("mid", d(2026, 5, 1, 13))]
        self.assertEqual([i.rel for i in tc.group_by_day(items)[0].items], ["early", "mid", "late"])

    def test_midnight_starts_a_new_day(self):
        days = tc.group_by_day([item("a", datetime(2026, 5, 1, 23, 59)), item("b", datetime(2026, 5, 2, 0, 1))])
        self.assertEqual([x.date_key for x in days], ["2026-05-01", "2026-05-02"])

    def test_undated_files_go_to_one_unknown_group_that_sorts_last(self):
        items = [item("x", d(2026, 5, 3), fallback=True), item("a", d(2026, 5, 1)), item("y", d(2024, 1, 1), fallback=True)]
        days = tc.group_by_day(items)
        self.assertEqual(days[-1].date_key, tc.UNKNOWN_DAY_KEY)
        self.assertTrue(days[-1].is_unknown)
        self.assertIsNone(days[-1].date_obj)
        self.assertEqual(sorted(i.rel for i in days[-1].items), ["x", "y"])
        self.assertEqual(len(days), 2)

    def test_a_fallback_time_never_invents_a_day(self):
        days = tc.group_by_day([item("only", d(2026, 5, 1), fallback=True)])
        self.assertEqual([x.date_key for x in days], [tc.UNKNOWN_DAY_KEY])

    def test_counts_and_route(self):
        a = item("a.jpg", d(2026, 5, 1)); a.lat, a.lon = 50.0, 4.0
        v = item("v.mp4", d(2026, 5, 1, 13), media="video"); v.lat, v.lon = 51.0, 4.0
        n = item("n.jpg", d(2026, 5, 1, 14))
        day = tc.group_by_day([a, v, n])[0]
        self.assertEqual((day.photo_count, day.video_count), (2, 1))
        self.assertEqual(day.route, [[50.0, 4.0], [51.0, 4.0]])
        self.assertGreater(day.distance_km, 100)


class SuspiciousDateTests(unittest.TestCase):
    trip = [item(f"t{i}.jpg", d(2026, 5, 1 + i // 4)) for i in range(12)]

    def find(self, extra, **kw):
        return tc.find_suspicious_dates(self.trip + extra, now=NOW, **kw)

    def test_placeholder_epochs_and_future_dates(self):
        flagged = self.find([item("qt.mov", d(1904, 1, 1, 0)), item("unix.mov", d(1970, 1, 1, 0)),
                             item("dos.jpg", d(1980, 1, 1, 0)), item("future.jpg", d(2027, 3, 1))])
        self.assertEqual(names(flagged), ["dos.jpg", "future.jpg", "qt.mov", "unix.mov"])
        reasons = {it.rel: r for it, r in flagged}
        self.assertEqual(reasons["qt.mov"], "before 1995")
        self.assertEqual(reasons["future.jpg"], "in the future")

    def test_the_1995_boundary(self):
        # judged on their own (no other files), so only the absolute rule applies
        self.assertEqual(tc.find_suspicious_dates([item("old.jpg", d(1995, 1, 1, 0))], now=NOW), [])
        flagged = tc.find_suspicious_dates([item("older.jpg", d(1994, 12, 31, 23))], now=NOW)
        self.assertEqual(names(flagged), ["older.jpg"])
        self.assertEqual(flagged[0][1], "before 1995")

    def test_a_1995_date_among_2026_files_is_still_far_from_the_rest(self):
        flagged = self.find([item("old.jpg", d(1995, 6, 1))])
        self.assertEqual(names(flagged), ["old.jpg"])
        self.assertIn("more than a year", flagged[0][1])

    def test_tomorrow_is_tolerated_but_next_week_is_not(self):
        self.assertEqual(self.find([item("tz.jpg", NOW + timedelta(hours=20))]), [])
        self.assertEqual(names(self.find([item("far.jpg", NOW + timedelta(days=7))])), ["far.jpg"])

    def test_camera_with_unset_clock_is_far_from_the_rest(self):
        flagged = self.find([item("reset.jpg", d(2000, 1, 1, 0)), item("reset2.jpg", d(2000, 1, 1, 0))])
        self.assertEqual(names(flagged), ["reset.jpg", "reset2.jpg"])
        self.assertIn("more than a year from the rest", flagged[0][1])
        self.assertIn("2026-05", flagged[0][1])

    def test_nothing_is_judged_as_an_outlier_with_fewer_than_five_files(self):
        small = [item("a", d(2026, 5, 1)), item("b", d(2026, 5, 2)), item("c", d(2000, 1, 1))]
        self.assertEqual(tc.find_suspicious_dates(small, now=NOW), [])

    def test_a_folder_that_really_spans_two_years_is_left_alone(self):
        two = ([item(f"x{i}", d(2024, 8, 1 + i)) for i in range(10)]
               + [item(f"y{i}", d(2026, 5, 1 + i)) for i in range(10)])
        self.assertEqual(tc.find_suspicious_dates(two, now=NOW), [])

    def test_a_long_trip_is_left_alone(self):
        long_trip = [item(f"l{i}", d(2025, 1, 1) + timedelta(days=i * 30)) for i in range(18)]
        self.assertEqual(tc.find_suspicious_dates(long_trip, now=NOW), [])

    def test_files_without_a_date_are_not_judged_again(self):
        self.assertEqual(self.find([item("nodate.jpg", d(2000, 1, 1), fallback=True)]), [])

    def test_ordinary_dates_pass(self):
        self.assertEqual(self.find([item("late.jpg", d(2026, 5, 3, 23))]), [])

    def test_explicit_range_replaces_the_guessing(self):
        rng = (datetime(2026, 5, 1), datetime(2026, 5, 5) + timedelta(days=1))
        flagged = tc.find_suspicious_dates(
            self.trip + [item("old.jpg", d(2025, 1, 1)), item("edge.jpg", d(2026, 5, 5, 23)),
                         item("year1904.jpg", d(1904, 1, 1))], date_range=rng, now=NOW)
        self.assertEqual(names(flagged), ["old.jpg", "year1904.jpg"])
        self.assertIn("2026-05-01 to 2026-05-05", flagged[0][1])

    def test_explicit_range_allows_what_the_heuristics_would_reject(self):
        wide = (datetime(1990, 1, 1), datetime(2027, 1, 1))
        self.assertEqual(tc.find_suspicious_dates(self.trip + [item("old.jpg", d(1999, 1, 1))],
                                                  date_range=wide, now=NOW), [])


class ApplyDateCheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, True))

    def real_item(self, name, dt):
        p = self.tmp / name
        p.write_bytes(b"x")
        os.utime(p, (datetime(2026, 10, 1).timestamp(),) * 2)
        return item(name, dt, path=p)

    def test_wrong_dates_become_unsorted_with_an_explanation(self):
        items = [self.real_item(f"t{i}.jpg", d(2026, 5, 1 + i // 4)) for i in range(8)]
        bad = self.real_item("bad.jpg", d(2000, 1, 1, 0))
        items.append(bad)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            n = tc.apply_date_check(items, now=NOW)
        self.assertEqual(n, 1)
        self.assertTrue(bad.dt_is_fallback)
        self.assertEqual(bad.dt, datetime(2026, 10, 1))       # the file's own time, not the bad one
        self.assertIn("2000-01-01", bad.detail["date_note"])
        self.assertTrue(bad.dt_rejected)
        self.assertEqual(tc.group_by_day(items)[-1].items, [bad])
        self.assertIn("bad.jpg", out.getvalue())
        self.assertIn("--date-range", out.getvalue())

    def test_good_files_are_untouched_and_quiet(self):
        items = [self.real_item(f"t{i}.jpg", d(2026, 5, 1 + i // 4)) for i in range(8)]
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(tc.apply_date_check(items, now=NOW), 0)
        self.assertEqual(out.getvalue(), "")
        self.assertFalse(any(i.dt_is_fallback for i in items))

    def test_quiet_flag_suppresses_the_report(self):
        items = [self.real_item(f"t{i}.jpg", d(2026, 5, 1)) for i in range(6)] + [self.real_item("b.jpg", d(1904, 1, 1))]
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(tc.apply_date_check(items, now=NOW, quiet=True), 1)
        self.assertEqual(out.getvalue(), "")

    def test_report_lists_at_most_eight_files(self):
        items = [self.real_item(f"t{i}.jpg", d(2026, 5, 1)) for i in range(30)]
        items += [self.real_item(f"bad{i}.jpg", d(1904, 1, 1)) for i in range(12)]
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            tc.apply_date_check(items, now=NOW)
        self.assertEqual(out.getvalue().count("dated 1904-01-01"), 8)
        self.assertIn("and 4 more", out.getvalue())


class DateRangeArgumentTests(unittest.TestCase):
    def parser(self):
        p = argparse.ArgumentParser()
        tc.add_date_check_args(p)
        return p

    def test_end_day_is_included(self):
        p = self.parser()
        start, end = tc.resolve_date_range(p.parse_args(["--date-range", "2026-05-01", "2026-05-10"]), p)
        self.assertEqual(start, datetime(2026, 5, 1))
        self.assertEqual(end, datetime(2026, 5, 11))

    def test_no_range_given(self):
        p = self.parser()
        self.assertIsNone(tc.resolve_date_range(p.parse_args([]), p))

    def test_end_before_start_is_an_error(self):
        p = self.parser()
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            tc.resolve_date_range(p.parse_args(["--date-range", "2026-05-10", "2026-05-01"]), p)

    def test_malformed_dates_are_rejected_by_argparse(self):
        for bad in (["May", "2026"], ["2026-13-01", "2026-14-01"], ["2026-05-01"]):
            with self.subTest(bad=bad), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                self.parser().parse_args(["--date-range", *bad])

    def test_no_date_check_flag(self):
        self.assertTrue(self.parser().parse_args(["--no-date-check"]).no_date_check)
        self.assertFalse(self.parser().parse_args([]).no_date_check)


if __name__ == "__main__":
    unittest.main()
