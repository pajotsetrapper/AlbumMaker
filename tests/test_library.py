"""A library of trips (--library): the list file, building every trip plus
the page linking them, and serving them all from one server.

    python -m unittest tests.test_library
"""
import argparse
import json
import os
import re
import subprocess
import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from .helpers import (TempDirMixin, TripServer, install_app, make_jpeg, read_json, run_script, slides_of,
                      standard_trip, write_json)

from tripsite import build, content as content_mod, library, server  # noqa: E402
from tripsite.model import DayGroup, MediaItem  # noqa: E402


def trip_list(path: Path, text: str) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def cards(index_html: str) -> list:
    """[(href, title), ...] of the trip cards on a library page, in page order."""
    return re.findall(r'<a class="day-card trip-card" href="([^"]+)">.*?<h3>(.*?)</h3>', index_html, re.S)


# ---------------------------------------------------------------- the list file

class TripListTests(TempDirMixin, unittest.TestCase):
    def setUp(self):
        self.tmp = self.make_tmp()

    def read(self, text):
        return library.read_trip_list(trip_list(self.tmp / "lists" / "trips.txt", text))

    def test_folders_names_options_comments_and_relative_paths(self):
        entries = self.read(
            "# my trips\n"
            "\n"
            "/abs/Japan 2024\n"
            "  ../Photos/Ísland — Summer!   | \n"
            "~/Pictures/Italy | italy\n"
            "Belgium | | --tz-offset 2 --no-places\n"
            "Wales | wales | -c notes/wales.json\n")
        self.assertEqual([e.name for e in entries], ["japan-2024", "island-summer", "italy", "belgium", "wales"])
        self.assertEqual(entries[0].path, Path("/abs/Japan 2024"))
        # relative to the list file's folder
        self.assertEqual(entries[1].path.resolve(), (self.tmp / "Photos" / "Ísland — Summer!").resolve())
        self.assertEqual(entries[2].path, Path.home() / "Pictures" / "Italy")
        self.assertEqual(entries[3].options, ["--tz-offset", "2", "--no-places"])
        self.assertEqual([e.line for e in entries], [3, 4, 5, 6, 7])

    def test_paths_in_quotes_as_typed_in_a_terminal_are_used_as_written(self):
        entries = self.read('"/Volumes/Photos/Japan 2024"\n'
                            "'/Volumes/Photos/Italy' | 'italy'\n"
                            '"/Volumes/Photos/Wales" | | --gpx "my track.gpx"\n')
        self.assertEqual([str(e.path) for e in entries],
                         ["/Volumes/Photos/Japan 2024", "/Volumes/Photos/Italy", "/Volumes/Photos/Wales"])
        self.assertEqual(entries[1].name, "italy")
        self.assertEqual(entries[2].options, ["--gpx", "my track.gpx"])

    def test_windows_drive_prefix_paths_are_normalized(self):
        with mock.patch.object(library.os.path, "exists", return_value=True):
            self.assertEqual(library._normalize_path_str("/c/Users/piete/Pictures"), "C:/Users/piete/Pictures")
            self.assertEqual(library._normalize_path_str("/C/Users/piete/Pictures"), "C:/Users/piete/Pictures")
        self.assertEqual(library._normalize_path_str("/Users/piete/Pictures"), "/Users/piete/Pictures")
        self.assertEqual(library._normalize_path_str("~/Pictures/Italy"), "~/Pictures/Italy")

    @unittest.skipUnless(os.name == "nt", "Windows path semantics")
    def test_slash_root_path_keeps_posix_spelling_and_relative_paths(self):
        root = library._path_for("/photos")
        child = library._path_for("/photos/sub/dir/a.jpg")
        self.assertEqual(str(root), "/photos")
        self.assertEqual(child.relative_to(root).as_posix(), "sub/dir/a.jpg")

    def test_a_missing_folder_says_how_its_line_was_read(self):
        entries = self.read("/Volumes/Gone/Japan\nVolumes/Photos/Iceland\n")
        list_file = self.tmp / "lists" / "trips.txt"
        absolute = library.missing_folder(entries[0], list_file)
        self.assertIn("Input folder not found: /Volumes/Gone/Japan", absolute)
        self.assertIn("line 1", absolute)
        relative = library.missing_folder(entries[1], list_file)
        self.assertIn("'Volumes/Photos/Iceland'", relative)
        self.assertIn("relative to the list file's folder", relative)
        (self.tmp / "lists" / "Volumes" / "Photos" / "Iceland").mkdir(parents=True)
        self.assertIsNone(library.missing_folder(entries[1], list_file))

    def test_names_are_made_unique_and_given_names_win(self):
        entries = self.read("/a/Trip\n/b/Trip\n/c/Other | trip\n/d/Assets\n")
        self.assertEqual([e.name for e in entries], ["trip-2", "trip-3", "trip", "assets-trip"])

    def test_mistakes_say_which_line(self):
        for text, needle in (("/a | Bad Name\n", "line 1"), ("/a | assets\n", "assets"),
                             ("/a | x\n/b | x\n", "used twice"), ("/a\n/a\n", "listed twice"),
                             ("/a | x | --tz-offset 2 | more\n", "too many"), ("# nothing\n", "no trips"),
                             ('/a | | --gpx "unclosed\n', "line 1")):
            with self.subTest(text=text):
                with self.assertRaises(library.TripListError) as e:
                    self.read(text)
                self.assertIn(needle, str(e.exception))
        with self.assertRaises(library.TripListError):
            library.read_trip_list(self.tmp / "missing.txt")

    def test_a_trips_options_override_only_what_they_set(self):
        list_file = trip_list(self.tmp / "lists" / "trips.txt", "x")
        args = argparse.Namespace(input=None, content=None, tz_offset=1.0, no_dedupe=False, no_live_photos=False,
                                  date_range=None, no_date_check=False, gpx=[], no_gpx=False, no_places=False,
                                  thumb_size=400)
        entry = library.TripEntry(Path("/photos/Belgium"), "belgium",
                                  ["--no-places", "-c", "notes.json", "--gpx", "t.gpx", "--gpx", "/abs.gpx",
                                   "--date-range", "2026-05-01", "2026-05-03"])
        trip, date_range = library.trip_args(args, entry, list_file)
        self.assertEqual((trip.input, trip.no_places, trip.tz_offset, trip.thumb_size),
                         (Path("/photos/Belgium"), True, 1.0, 400))
        self.assertEqual(trip.content, (self.tmp / "lists").resolve() / "notes.json")
        self.assertEqual(trip.gpx, [(self.tmp / "lists").resolve() / "t.gpx", Path("/abs.gpx")])
        self.assertEqual(date_range, (datetime(2026, 5, 1), datetime(2026, 5, 4)))
        self.assertFalse(args.no_places, "the command line's own settings are untouched")
        for bad in (["--port", "9"], ["--tz-offset", "x"], ["--date-range", "2026-05-03", "2026-05-01"]):
            with self.subTest(bad=bad), self.assertRaises(library.TripListError):
                library.trip_args(args, library.TripEntry(Path("/p"), "p", bad, 4), list_file)
        with self.assertRaisesRegex(library.TripListError, "either --timezone or --tz-offset"):
            library.trip_args(
                args,
                library.TripEntry(Path("/p"), "p", ["--timezone", "Asia/Tokyo", "--tz-offset", "2"]),
                list_file,
            )

    def test_what_the_command_line_sets_for_all_trips_is_kept_unless_a_line_says_otherwise(self):
        list_file = trip_list(self.tmp / "lists" / "trips.txt", "x")
        args = argparse.Namespace(input=None, content=None, tz_offset=None, no_dedupe=True, no_live_photos=False,
                                  date_range=None, no_date_check=False, gpx=[Path("/tracks")], no_gpx=False,
                                  no_places=False)
        plain, _ = library.trip_args(args, library.TripEntry(Path("/p"), "p", []), list_file)
        self.assertEqual((plain.gpx, plain.no_dedupe, plain.content), ([Path("/tracks")], True, None))
        own, _ = library.trip_args(args, library.TripEntry(Path("/p"), "p", ["--gpx", "/mine.gpx"]), list_file)
        self.assertEqual(own.gpx, [Path("/mine.gpx")])
        if os.name == "nt":
            self.assertEqual(str(own.gpx[0]), "/mine.gpx")

    def test_one_content_file_or_date_window_for_every_trip_is_refused(self):
        base = dict(content=None, date_range=None)
        self.assertIsNone(library.check_library_args(argparse.Namespace(**base)))
        self.assertIn("-c/--content", library.check_library_args(argparse.Namespace(**{**base, "content": Path("x")})))
        self.assertIn("--date-range", library.check_library_args(
            argparse.Namespace(**{**base, "date_range": (datetime(2026, 1, 1), datetime(2026, 1, 2))})))

    def test_slugs(self):
        for text, want in (("Ísland 2024!", "island-2024"), ("  ", "trip"), ("日本", "trip"),
                           ("A" * 80, "a" * 48), ("Côte d'Azur", "cote-d-azur")):
            self.assertEqual(library.slugify(text), want, text)


class SummaryTests(unittest.TestCase):
    def test_date_spans(self):
        d = lambda s: datetime.fromisoformat(s)
        self.assertEqual(library.format_date_span(d("2026-05-01"), d("2026-05-01")), "1 May 2026")
        self.assertEqual(library.format_date_span(d("2026-05-01"), d("2026-05-12")), "1 – 12 May 2026")
        self.assertEqual(library.format_date_span(d("2026-04-28"), d("2026-05-03")), "28 Apr – 3 May 2026")
        self.assertEqual(library.format_date_span(d("2025-12-28"), d("2026-01-03")),
                         "28 Dec 2025 – 3 Jan 2026")
        self.assertEqual(library.format_date_span(None, None), "Date unknown")

    def test_an_untitled_trip_shows_its_folders_name_and_the_map_line_is_capped(self):
        class Urls:
            def thumb(self, item, from_index):
                return f"thumbs/{item.path.name}"

            def hi_thumb(self, item, from_index):
                return None
        items = [MediaItem(path=Path(f"/p/{i}.jpg"), media_type="image", dt=datetime(2026, 5, 1, 9),
                           dt_is_fallback=False, lat=50 + i / 1000, lon=4.0) for i in range(500)]
        day = DayGroup("2026-05-01", datetime(2026, 5, 1), items=items)
        summary = library.trip_summary("x", [day], {"title": "My Trip"}, Urls(), "x/index.html", base="x/",
                                       folder_title="Belgium Weekend")
        self.assertEqual(summary["title"], "Belgium Weekend")
        self.assertEqual(summary["cover"], "x/thumbs/0.jpg")
        self.assertLessEqual(len(summary["points"]), library.MAX_MAP_POINTS + 1)
        self.assertEqual(summary["points"][-1], [50.499, 4.0])
        named = library.trip_summary("x", [day], {"title": "Ardennes"}, Urls(), "x/", folder_title="Belgium")
        self.assertEqual(named["title"], "Ardennes")


class ContentFileLocationTests(TempDirMixin, unittest.TestCase):
    def test_a_trip_in_a_library_never_uses_the_shared_current_folder(self):
        tmp = self.make_tmp()
        photos, cwd = tmp / "photos", tmp / "cwd"
        photos.mkdir()
        cwd.mkdir()
        (cwd / "trip_content.json").write_text("{}")               # an old-style file in the current folder
        old = os.getcwd()
        os.chdir(cwd)
        self.addCleanup(os.chdir, old)
        with mock.patch("sys.stderr"):
            self.assertEqual(content_mod.resolve_content_path(None, photos, fallback=tmp / "own.json"),
                             photos / "trip_content.json")
            with mock.patch("os.access", return_value=False):        # a read-only photos folder
                self.assertEqual(content_mod.resolve_content_path(None, photos, fallback=tmp / "own.json"),
                                 tmp / "own.json")
            (tmp / "own.json").write_text("{}")                      # notes kept there while it was read-only
            self.assertEqual(content_mod.resolve_content_path(None, photos, fallback=tmp / "own.json"),
                             tmp / "own.json", "now writable, but the notes are in the other file")


# ---------------------------------------------------------------- the static library

class StaticLibraryTests(TempDirMixin, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = cls.make_tmp()
        cls.app = install_app(cls.tmp / "app")
        standard_trip(cls.tmp / "Belgium Weekend")
        make_jpeg(cls.tmp / "Japan" / "kyoto.jpg", "2026:06:10 09:00:00", (34.9858, 135.7588), offset="+09:00")
        make_jpeg(cls.tmp / "Japan" / "nara.jpg", "2026:06:11 09:00:00", (34.6851, 135.8430), offset="+09:00")
        cls.list = trip_list(cls.tmp / "trips.txt",
                             "Belgium Weekend\n"
                             "Japan | japan | --no-places\n"
                             "/Volumes/NotPluggedIn/Iceland\n")
        cls.site = cls.tmp / "site"
        cls.first = cls.build()

    @classmethod
    def build(cls, *extra, list_file=None):
        return run_script(cls.app, "generate_site.py", "--library", list_file or cls.list, "-o", cls.site,
                          "--workers", "1", *extra)

    def test_every_trip_that_can_be_built_is_and_the_page_links_them(self):
        r = self.first
        self.assertEqual(r.returncode, 1, "a trip failed, which the exit code says")
        self.assertIn("NotPluggedIn", r.stderr)
        index = (self.site / "index.html").read_text(encoding="utf-8")
        self.assertEqual(cards(index), [("japan/index.html", "Japan"),                  # newest first
                                        ("belgium-weekend/index.html", "Belgium Weekend")])
        for f in ("assets/style.css", "assets/script.js", "japan/index.html", "belgium-weekend/days/2026-05-01.html"):
            self.assertTrue((self.site / f).is_file(), f)
        self.assertIn("initLibraryMap('library-map', ", index)
        self.assertIn("1 – 2 May 2026", index)
        record = read_json(self.site / ".trip_library.json")
        self.assertEqual(sorted(record["trips"]), ["belgium-weekend", "japan"])

    def test_trip_pages_link_back_to_the_library(self):
        home = (self.site / "japan" / "index.html").read_text(encoding="utf-8")
        day = (self.site / "japan" / "days" / "2026-06-10.html").read_text(encoding="utf-8")
        self.assertIn('class="crumb library-link" href="../index.html"', home)
        self.assertIn('initHomeNav("../index.html")', home)
        self.assertIn('class="crumb library-link" href="../../index.html"', day)

    def test_options_on_a_trips_line_apply_to_that_trip_only(self):
        japan = (self.site / "japan" / "days" / "2026-06-10.html").read_text(encoding="utf-8")
        belgium = (self.site / "belgium-weekend" / "days" / "2026-05-01.html").read_text(encoding="utf-8")
        self.assertNotIn('class="places"', japan)                                    # --no-places
        self.assertIn('class="places"', belgium)

    def test_a_new_trips_title_is_its_folders_name(self):
        self.assertEqual(read_json(self.tmp / "Japan" / "trip_content.json")["title"], "Japan")
        self.assertIn("<title>Japan</title>", (self.site / "japan" / "index.html").read_text(encoding="utf-8"))

    def test_an_unavailable_trip_keeps_its_last_version_and_a_removed_one_is_reported(self):
        os.rename(self.tmp / "Japan", self.tmp / "Japan-away")                       # the drive is unplugged
        try:
            r = self.build()
            self.assertIn("keeps showing the version of this trip built earlier", r.stdout)
            self.assertIn(("japan/index.html", "Japan"), cards((self.site / "index.html").read_text(encoding="utf-8")))
        finally:
            os.rename(self.tmp / "Japan-away", self.tmp / "Japan")
        shorter = trip_list(self.tmp / "shorter.txt", "Belgium Weekend\n")
        r = self.build(list_file=shorter)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(f"{self.site / 'japan'} is no longer in", r.stdout)
        self.assertEqual(cards((self.site / "index.html").read_text(encoding="utf-8")),
                         [("belgium-weekend/index.html", "Belgium Weekend")])
        self.assertTrue((self.site / "japan" / "index.html").is_file(), "the folder itself is left alone")
        self.build()                                                                 # back as it was

    def test_a_library_inside_a_photos_folder_is_not_mistaken_for_photos(self):
        photos = self.tmp / "Inside"
        make_jpeg(photos / "one.jpg", "2026:07:01 09:00:00")
        inner = trip_list(self.tmp / "inner.txt", f"{photos}\n")
        site = photos / "library"
        for _ in range(2):                         # the second run would find the first one's assets
            r = run_script(self.app, "generate_site.py", "--library", inner, "-o", site, "--workers", "1")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("Found 1 media files", r.stdout)

    def test_one_trip_or_a_library_not_both(self):
        r = run_script(self.app, "generate_site.py", "--library", self.list, "-i", self.tmp, "-o", self.site)
        self.assertEqual(r.returncode, 2)
        self.assertIn("not both", r.stderr)
        for extra in (["-c", "x.json"], ["--date-range", "2026-05-01", "2026-05-02"]):
            r = run_script(self.app, "generate_site.py", "--library", self.list, "-o", self.site, *extra)
            self.assertEqual(r.returncode, 2, extra)
            self.assertIn("put it on that trip's line", r.stderr)

    @unittest.skipUnless(sys.platform in ("darwin", "win32"), "needs a disk that ignores letter case")
    def test_a_trips_output_folder_is_never_its_own_photos_folder(self):
        # the list's folder as -o: "Italy" would be built into "italy", the very same folder
        base = self.tmp / "case"
        make_jpeg(base / "Italy" / "a.jpg", "2026:08:01 09:00:00")
        trips = trip_list(base / "trips.txt", "Italy\n")
        r = run_script(self.app, "generate_site.py", "--library", trips, "-o", base, "--workers", "1")
        self.assertEqual(r.returncode, 1)
        self.assertIn("can't be the photos folder itself", r.stderr)
        self.assertEqual(sorted(p.name for p in (base / "Italy").iterdir()), ["a.jpg"], "nothing written into it")

    def test_a_trip_that_crashes_does_not_stop_the_others(self):
        site = self.tmp / "crash-site"
        args = build.build_parser().parse_args(["--library", str(self.list), "-o", str(site), "--workers", "1"])
        real = build.build_trip
        calls = []

        def flaky(trip, *a, **kw):
            calls.append(trip.input.name)
            if trip.input.name == "Belgium Weekend":
                raise RuntimeError("the network share went away")
            return real(trip, *a, **kw)
        with mock.patch.object(build, "build_trip", flaky), mock.patch("sys.stdout"), \
                mock.patch("sys.stderr"):
            self.assertEqual(build.build_library(args), 1)
        self.assertEqual(calls, ["Belgium Weekend", "Japan"], "(Iceland's folder is missing: not even tried)")
        self.assertEqual(cards((site / "index.html").read_text(encoding="utf-8")), [("japan/index.html", "Japan")])


# ---------------------------------------------------------------- the live library

class ServerLibraryTests(TempDirMixin, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = cls.make_tmp()
        app = install_app(cls.tmp / "app")
        standard_trip(cls.tmp / "Belgium")
        make_jpeg(cls.tmp / "Japan" / "kyoto.jpg", "2026:06:10 09:00:00", (34.9858, 135.7588), offset="+09:00")
        make_jpeg(cls.tmp / "Japan" / "secret.jpg", "2026:06:10 10:00:00", (35.0, 135.7), color=(9, 9, 9))
        write_json(cls.tmp / "Japan" / "trip_content.json", {"title": "Kansai", "photos": {"secret.jpg": {"hidden": True}}})
        trips = trip_list(cls.tmp / "trips.txt", "Belgium\nJapan | japan\n/Volumes/NotPluggedIn/Iceland\n")
        cls.server = TripServer(app, None, cls.tmp, "--library", trips).start()
        cls.addClassCleanup(cls.server.stop)

    def test_the_library_page_links_every_trip_that_loaded(self):
        html = self.server.text("/")
        self.assertEqual(cards(html), [("/japan/", "Kansai"), ("/belgium/", "Belgium")])
        log = self.server.log()
        self.assertIn("This trip is left out", log)
        self.assertIn(f"http://127.0.0.1:{self.server.port}/japan/", log)

    def test_each_trip_lives_under_its_name(self):
        home = self.server.text("/japan/")
        self.assertIn('class="crumb library-link" href="/"', home)
        self.assertIn('initHomeNav("/")', home)
        day = self.server.text("/japan/day/2026-06-10")
        self.assertIn('window.API_BASE = "/japan";', day)
        thumb = re.search(r'<img src="(/japan/thumb/[^"]+)"', day).group(1)
        self.assertEqual(self.server.get(thumb)[0], 200)
        self.assertEqual(self.server.get(thumb.replace("/japan/", "/belgium/"))[0], 404)
        self.assertEqual(self.server.get(thumb.replace("/japan", ""))[0], 404)
        self.assertEqual(self.server.get("/assets/style.css")[0], 200)
        self.assertEqual(self.server.get("/nowhere/")[0], 404)

    def test_edits_go_to_their_own_trips_file(self):
        status, _ = self.server.post_json("/belgium/api/day", {"date": "2026-05-01", "title": "Waffles"})
        self.assertEqual(status, 200)
        self.assertEqual(read_json(self.tmp / "Belgium" / "trip_content.json")["days"]["2026-05-01"]["title"],
                         "Waffles")
        self.assertNotIn("2026-05-01", read_json(self.tmp / "Japan" / "trip_content.json").get("days", {}))
        self.assertEqual(self.server.post_json("/japan/api/day", {"date": "2026-05-01", "title": "x"})[0], 400,
                         "that day belongs to the other trip")
        self.assertEqual(self.server.post_json("/api/day", {"date": "2026-05-01", "title": "x"})[0], 404)

    def test_hidden_photos_stay_out_of_the_library_for_visitors(self):
        visitor = {"Via": "1.1 proxy"}
        html = self.server.text("/", visitor)
        japan = html[html.index('href="/japan/"'):html.index('href="/belgium/"')]
        self.assertIn("1 photo", japan)
        day = self.server.text("/japan/day/2026-06-10")
        secret = next(s for s in slides_of(day) if s["detail"]["filename"] == "secret.jpg")
        cover = re.search(r"background-image:url\('([^']+)'\)", japan).group(1)
        self.assertNotIn(secret["key"], cover)

    def test_the_library_page_follows_changes_to_a_trips_content(self):
        count = lambda: re.search(r'href="/belgium/".*?(\d+) photos', self.server.text("/"), re.S).group(1)
        before = int(count())
        self.assertEqual(self.server.post_json("/belgium/api/photo", {"rel": "b2.jpg", "hidden": True})[0], 200)
        try:
            self.assertEqual(int(count()), before - 1)
        finally:
            self.server.post_json("/belgium/api/photo", {"rel": "b2.jpg", "hidden": False})
        self.assertEqual(int(count()), before)

    def test_the_editor_link_works_at_the_library_page(self):
        token = re.search(r"\?key=([\w-]+)", self.server.log()).group(1)
        status, headers, _ = self.server.get(f"/?key={token}", {"Via": "1.1 proxy"})
        self.assertEqual((status, headers["location"]), (303, "/"))
        cookie = headers["set-cookie"].split(";")[0]
        status, _ = self.server.post_json("/japan/api/photo", {"rel": "kyoto.jpg", "favorite": True},
                                          {"Cookie": cookie, "Via": "1.1 proxy"})
        self.assertEqual(status, 200)


class SharedResourceTests(unittest.TestCase):
    def test_trips_share_one_memory_budget_but_never_each_others_images(self):
        args = argparse.Namespace(memory_cache_mb=64, render_threads=2, port=0)
        shared = server.Shared(args)
        store = mock.Mock()
        a = server.App(args, [], [], store, shared=shared, prefix="/a")
        b = server.App(args, [], [], store, shared=shared, prefix="/b")
        self.assertIs(a.thumb_cache, b.thumb_cache)
        self.assertIs(a.renderer, b.renderer)
        self.assertEqual(a.editor_token, b.editor_token)
        item = MediaItem(path=Path("/x.jpg"), media_type="image", dt=datetime(2026, 1, 1), dt_is_fallback=False,
                         lat=None, lon=None, key="0123456789")
        with mock.patch.object(server, "image_thumbnails", side_effect=[{100: b"A"}, {100: b"B"}]):
            self.assertEqual(a.thumbnail(item, 100, False, "v1"), b"A")
            self.assertEqual(b.thumbnail(item, 100, False, "v1"), b"B", "same key in another trip: its own image")


class ServerLibraryStartupTests(TempDirMixin, unittest.TestCase):
    def test_settings_that_cant_apply_to_every_trip_are_refused(self):
        tmp = self.make_tmp()
        app = install_app(tmp / "app")
        trips = trip_list(tmp / "trips.txt", "/x\n")
        (tmp / "index.json").write_text("{}")
        for extra, needle in ((["-c", "x.json"], "that trip's line"), (["--index-cache", str(tmp / "index.json")],
                                                                      "is a folder")):
            r = subprocess.run([sys.executable, str(app / "trip_server.py"), "--library", str(trips), *extra],
                               capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 2, extra)
            self.assertIn(needle, r.stderr)

    def test_with_no_trip_to_serve_it_stops(self):
        tmp = self.make_tmp()
        app = install_app(tmp / "app")
        trips = trip_list(tmp / "trips.txt", "/Volumes/NotPluggedIn/A\n")
        r = subprocess.run([sys.executable, str(app / "trip_server.py"), "--library", str(trips), "--port", "0"],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 1)
        self.assertIn("None of the trips could be loaded", r.stderr)


if __name__ == "__main__":
    unittest.main()
