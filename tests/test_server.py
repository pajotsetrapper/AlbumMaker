"""album_server.py running for real: pages, images, byte ranges, the Host check,
hot-reloading of trip_content.json, and the date check."""
import json
import re
import shutil
import threading
import unittest
from pathlib import Path

from PIL import Image
import io

from .helpers import (TempDirMixin, AlbumMaker, day_nav_args, install_app, item_ids, make_jpeg, make_video,
                      needs_ffmpeg, read_json, run_script, size_toggle, slide_for, slides_of, standard_trip, tc,
                      write_json)


class ServerBasicsTests(TempDirMixin, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = cls.make_tmp()
        cls.app = install_app(cls.tmp / "app")
        cls.photos = cls.tmp / "photos"
        standard_trip(cls.photos)
        make_jpeg(cls.photos / "nodate.jpg", None, color=(1, 2, 3))
        make_jpeg(cls.photos / "Caf\u00e9 \u2603.jpg", "2026:05:02 14:00:00", color=(4, 5, 6))
        shutil.copy(cls.photos / "a1.jpg", cls.photos / "copy_of_a1.jpg")
        write_json(cls.tmp / "content.json", {"title": "Belgium 2026", "days": {"2026-05-01": {"title": "Canals"}}})
        cls.server = AlbumMaker(cls.app, cls.photos, cls.tmp).start()
        cls.addClassCleanup(cls.server.stop)
        cls.ids1 = item_ids(cls.server, "2026-05-01")      # a1, a2, a3 in time order

    # ---- pages
    def test_homepage_and_day_pages(self):
        status, headers, body = self.server.get("/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers["content-type"])
        self.assertIn("Belgium 2026", body.decode())
        self.assertEqual(self.server.get("/day/2026-05-01")[0], 200)
        self.assertIn("Canals", self.server.text("/day/2026-05-01"))

    def test_the_unsorted_page_exists_for_undated_files(self):
        self.assertEqual(self.server.get("/day/unknown")[0], 200)
        self.assertIn("nodate.jpg", self.server.text("/day/unknown"))

    def test_unknown_days_and_odd_paths_are_404(self):
        for path in ("/day/1999-01-01", "/day/", "/day/..", "/day/2026-05-01/extra", "/nothing", "/index.html"):
            with self.subTest(path=path):
                self.assertEqual(self.server.get(path)[0], 404)

    def test_stylesheet_and_script_are_served(self):
        status, headers, body = self.server.get("/assets/style.css")
        self.assertEqual((status, headers["content-type"].split(";")[0]), (200, "text/css"))
        status, headers, body = self.server.get("/assets/script.js")
        self.assertEqual(status, 200)
        self.assertIn("javascript", headers["content-type"])
        self.assertIn(b"initSlideshow", body)

    def test_duplicates_are_not_listed(self):
        html = self.server.text("/day/2026-05-01")
        names = [s["detail"]["filename"] for s in slides_of(html)]
        self.assertEqual(names.count("a1.jpg"), 1)
        self.assertNotIn("copy_of_a1.jpg", names)

    def test_the_footer_does_not_claim_nothing_is_written_to_disk(self):
        self.assertIn("Served live by album_server.py", self.server.text("/"))
        self.assertNotIn("nothing written", self.server.text("/"))

    def test_the_startup_message_explains_editing_and_offline_options(self):
        log = self.server.log()
        self.assertIn("Editing works", log)
        self.assertRegex(log, rf"Editor link .*: http://127\.0\.0\.1:{self.server.port}/\?key=[\w-]{{20,}}")
        self.assertIn("--fetch-offline-assets", log)
        self.assertIn(f"http://127.0.0.1:{self.server.port}/", log)

    def test_size_controls_are_on_both_kinds_of_page(self):
        wanted = [("small", "S"), ("medium", "M"), ("large", "L"), ("xl", "XL")]
        self.assertEqual(size_toggle(self.server.text("/day/2026-05-01"), "thumb-grid"), ("tripThumbSize", wanted))
        self.assertEqual(size_toggle(self.server.text("/"), "day-list"), ("tripCardSize", wanted))
        self.assertIn('id="day-list"', self.server.text("/"))

    def test_esc_leads_back_to_the_homepage(self):
        prev, nxt, home = day_nav_args(self.server.text("/day/2026-05-02"))
        self.assertEqual(home, "/")
        self.assertEqual(prev, "/day/2026-05-01")
        self.assertIn("Esc returns to all days", self.server.text("/day/2026-05-02"))

    def test_the_startup_message_says_exactly_what_is_written_to_disk(self):
        log = self.server.log()
        self.assertIn("It writes to disk only", log)
        self.assertIn(str(self.server.content), log)
        self.assertNotIn("tile cache", log, "no tile cache was asked for")
        self.assertNotIn("Nothing has been written", log, "the old claim, which stopped being true")

    # ---- images and files
    def test_thumbnails_are_small_jpegs(self):
        status, headers, body = self.server.get(f"/thumb/{self.ids1[0]}")
        self.assertEqual((status, headers["content-type"]), (200, "image/jpeg"))
        with Image.open(io.BytesIO(body)) as im:
            self.assertEqual(im.format, "JPEG")
            self.assertLessEqual(max(im.size), 400)

    def test_the_original_is_streamed_unchanged(self):
        status, headers, body = self.server.get(f"/media/{self.ids1[0]}")
        self.assertEqual(status, 200)
        self.assertEqual(headers["content-type"], "image/jpeg")
        self.assertEqual(headers["accept-ranges"], "bytes")
        self.assertEqual(body, (self.photos / "a1.jpg").read_bytes())

    def test_versioned_urls_may_be_cached_forever_and_others_must_revalidate(self):
        html = self.server.text("/day/2026-05-01")
        url = re.search(r'src="(/thumb/[0-9a-f]+\?v=[^"]+)"', html).group(1)
        self.assertIn("immutable", self.server.get(url)[1]["cache-control"])
        self.assertNotIn("immutable", self.server.get(url.split("?")[0] + "?v=stale")[1]["cache-control"])

    def test_byte_ranges(self):
        size = (self.photos / "a1.jpg").stat().st_size
        data = (self.photos / "a1.jpg").read_bytes()
        path = f"/media/{self.ids1[0]}"
        status, headers, body = self.server.get(path, {"Range": "bytes=0-99"})
        self.assertEqual((status, body), (206, data[:100]))
        self.assertEqual(headers["content-range"], f"bytes 0-99/{size}")
        status, _, body = self.server.get(path, {"Range": "bytes=100-"})
        self.assertEqual((status, body), (206, data[100:]))
        status, _, body = self.server.get(path, {"Range": "bytes=-50"})
        self.assertEqual((status, body), (206, data[-50:]))
        status, _, body = self.server.get(path, {"Range": f"bytes=0-{size * 10}"})
        self.assertEqual((status, body), (206, data))

    def test_an_unsatisfiable_range_is_416(self):
        size = (self.photos / "a1.jpg").stat().st_size
        status, headers, _ = self.server.get(f"/media/{self.ids1[0]}", {"Range": f"bytes={size + 10}-"})
        self.assertEqual(status, 416)
        self.assertEqual(headers["content-range"], f"bytes */{size}")

    def test_a_garbled_range_header_just_gets_the_whole_file(self):
        status, _, body = self.server.get(f"/media/{self.ids1[0]}", {"Range": "pages=3"})
        self.assertEqual((status, len(body)), (200, (self.photos / "a1.jpg").stat().st_size))

    def test_head_requests_have_headers_and_no_body(self):
        status, headers, body = self.server.request("HEAD", f"/media/{self.ids1[0]}")
        self.assertEqual((status, body), (200, b""))
        self.assertEqual(int(headers["content-length"]), (self.photos / "a1.jpg").stat().st_size)

    def test_download_has_a_safe_attachment_header_even_for_unusual_names(self):
        ids = item_ids(self.server, "2026-05-02")
        names = [s["detail"]["filename"] for s in slides_of(self.server.text("/day/2026-05-02"))]
        cafe = ids[names.index("Caf\u00e9 \u2603.jpg")]
        status, headers, body = self.server.get(f"/download/{cafe}")
        self.assertEqual(status, 200)
        disp = headers["content-disposition"]
        self.assertTrue(disp.startswith("attachment;"))
        self.assertIn("filename*=UTF-8''Caf%C3%A9%20%E2%98%83.jpg", disp)
        disp.encode("latin-1")                                   # a header must survive http's encoding

    def test_bad_ids_are_404_not_errors(self):
        for path in ("/thumb/abc", "/thumb/-1", "/thumb/99999", "/media/1.5", "/media/", "/download/x",
                     "/live/0"):
            with self.subTest(path=path):
                self.assertEqual(self.server.get(path)[0], 404)

    # ---- the Host check
    def test_requests_for_a_foreign_host_name_are_refused(self):
        for host in ("evil.example", "evil.example:80", "attacker.test:8000"):
            with self.subTest(host=host):
                self.assertEqual(self.server.get("/", {"Host": host})[0], 403)

    def test_ip_addresses_and_localhost_are_accepted(self):
        for host in ("localhost", f"localhost:{self.server.port}", f"127.0.0.1:{self.server.port}", "192.168.1.20:8000"):
            with self.subTest(host=host):
                self.assertEqual(self.server.get("/", {"Host": host})[0], 200)

    def test_the_host_check_also_protects_images(self):
        self.assertEqual(self.server.get(f"/media/{self.ids1[0]}", {"Host": "evil.example"})[0], 403)

    # ---- many requests at once
    def test_a_burst_of_simultaneous_requests_is_answered_in_full(self):
        # 60 connections at the same instant, e.g. several devices opening a day together.
        # Every one must get an answer: a dropped connection counts as a failure.
        outcomes, gate = [], threading.Barrier(60)

        def fetch(i):
            gate.wait()
            try:
                outcomes.append(self.server.get(f"/thumb/{self.ids1[i % len(self.ids1)]}")[0])
            except OSError as e:
                outcomes.append(f"dropped: {e}")

        threads = [threading.Thread(target=fetch, args=(i,)) for i in range(60)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(sorted(map(str, outcomes)), ["200"] * 60)


class ServerHotReloadTests(TempDirMixin, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = cls.make_tmp()
        cls.app = install_app(cls.tmp / "app")
        cls.photos = cls.tmp / "photos"
        standard_trip(cls.photos)
        cls.server = AlbumMaker(cls.app, cls.photos, cls.tmp).start()
        cls.addClassCleanup(cls.server.stop)

    def test_edits_made_in_a_text_editor_show_up_on_refresh(self):
        write_json(self.server.content, {"title": "First title"})
        self.assertIn("First title", self.server.text("/"))
        write_json(self.server.content, {"title": "Second title"})
        self.assertIn("Second title", self.server.text("/"))

    def test_a_half_finished_edit_keeps_the_last_good_version_and_recovers(self):
        write_json(self.server.content, {"title": "Good title"})
        self.assertIn("Good title", self.server.text("/"))
        self.server.content.write_text('{"title": "oops",}', encoding="utf-8")
        self.assertEqual(self.server.get("/")[0], 200)
        self.assertIn("Good title", self.server.text("/"))
        self.assertIn("is not valid JSON", self.server.log())
        self.assertIn("Still showing the last version", self.server.log())
        write_json(self.server.content, {"title": "Fixed title"})
        self.assertIn("Fixed title", self.server.text("/"))

    def test_the_file_is_not_modified_by_merely_viewing(self):
        write_json(self.server.content, {"title": "Stable"})
        before = self.server.content.read_bytes()
        for path in ("/", "/day/2026-05-01"):
            self.server.get(path)
        self.assertEqual(self.server.content.read_bytes(), before)


class ServerDateCheckTests(TempDirMixin, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = cls.make_tmp()
        cls.app = install_app(cls.tmp / "app")
        cls.photos = cls.tmp / "photos"
        standard_trip(cls.photos)
        make_jpeg(cls.photos / "ancient.jpg", "1904:01:01 00:00:00", color=(1, 1, 1))
        make_jpeg(cls.photos / "reset.jpg", "2000:01:01 00:00:07", color=(2, 2, 2))

    def test_wrong_dates_go_to_unsorted_with_an_explanation(self):
        server = AlbumMaker(self.app, self.photos, self.tmp / "w1").start()
        self.addCleanup(server.stop)
        self.assertEqual(server.get("/day/1904-01-01")[0], 404)
        self.assertEqual(server.get("/day/2000-01-01")[0], 404)
        html = server.text("/day/unknown")
        self.assertIn("Embedded date 1904-01-01", slide_for(html, "ancient.jpg")["detail"]["date_note"])
        self.assertIn("Date check: 2 file(s)", server.log())

    def test_the_check_can_be_disabled_and_ranged(self):
        off = AlbumMaker(self.app, self.photos, self.tmp / "w2", "--no-date-check").start()
        self.addCleanup(off.stop)
        self.assertEqual(off.get("/day/1904-01-01")[0], 200)
        ranged = AlbumMaker(self.app, self.photos, self.tmp / "w3", "--date-range", "1900-01-01", "2030-01-01").start()
        self.addCleanup(ranged.stop)
        self.assertEqual(ranged.get("/day/2000-01-01")[0], 200)

    def test_a_bad_range_is_rejected_before_the_server_starts(self):
        r = run_script(self.app, "album_server.py", "-i", self.photos, "--date-range", "2026-05-10", "2026-05-01")
        self.assertEqual(r.returncode, 2)


@needs_ffmpeg
class ServerVideoTests(TempDirMixin, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = cls.make_tmp()
        cls.app = install_app(cls.tmp / "app")
        cls.photos = cls.tmp / "photos"
        standard_trip(cls.photos)
        make_video(cls.photos / "clip.mp4", "2026-05-02T12:00:00Z", seconds=6)
        make_jpeg(cls.photos / "live.jpg", "2026:05:01 14:00:00", color=(70, 70, 70))
        make_video(cls.photos / "live.mov", "2026-05-01T14:00:00Z", seconds=2)
        cls.server = AlbumMaker(cls.app, cls.photos, cls.tmp, "--tz-offset", "0").start()
        cls.addClassCleanup(cls.server.stop)

    def id_of(self, day, filename):
        names = [s["detail"]["filename"] for s in slides_of(self.server.text(f"/day/{day}"))]
        return item_ids(self.server, day)[names.index(filename)]

    def test_a_video_streams_with_ranges_and_has_a_preview_frame(self):
        vid = self.id_of("2026-05-02", "clip.mp4")
        data = (self.photos / "clip.mp4").read_bytes()
        status, headers, body = self.server.get(f"/media/{vid}", {"Range": "bytes=0-999"})
        self.assertEqual((status, body), (206, data[:1000]))
        self.assertEqual(headers["content-type"], "video/mp4")
        status, headers, body = self.server.get(f"/thumb/{vid}")
        self.assertEqual((status, headers["content-type"]), (200, "image/jpeg"))
        with Image.open(io.BytesIO(body)) as im:
            self.assertGreater(im.size[0], 10)

    def test_a_live_photo_plays_its_clip_and_the_clip_is_not_listed_separately(self):
        day = self.server.text("/day/2026-05-01")
        names = [s["detail"]["filename"] for s in slides_of(day)]
        self.assertNotIn("live.mov", names)
        live_url = slide_for(day, "live.jpg")["live"]
        status, headers, body = self.server.get(live_url)
        self.assertEqual(status, 200)
        self.assertEqual(body, (self.photos / "live.mov").read_bytes())
        self.assertTrue(headers["content-type"].startswith("video/"))

    def test_a_clip_with_no_live_photo_url_is_404(self):
        self.assertEqual(self.server.get(f"/live/{self.id_of('2026-05-02', 'b1.jpg')}")[0], 404)


class ServerStartupTests(TempDirMixin, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = cls.make_tmp()
        cls.app = install_app(cls.tmp / "app")
        cls.photos = cls.tmp / "photos"
        standard_trip(cls.photos)

    def test_a_broken_content_file_stops_the_server_with_a_clear_message(self):
        bad = self.tmp / "bad.json"
        bad.write_text('{"title": }')
        r = run_script(self.app, "album_server.py", "-i", self.photos, "-c", bad, "--port", "0", timeout=30)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("bad.json", r.stdout + r.stderr)
        self.assertEqual(bad.read_text(), '{"title": }')

    def test_a_missing_photos_folder_is_a_clear_error(self):
        r = run_script(self.app, "album_server.py", "-i", self.tmp / "nope", timeout=30)
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn("Traceback", r.stderr)

    def test_the_lan_option_warns_that_there_is_no_login(self):
        server = AlbumMaker(self.app, self.photos, self.tmp / "lan", "--lan").start()
        self.addCleanup(server.stop)
        self.assertEqual(server.get("/")[0], 200)
        self.assertRegex(server.log().lower(), r"network|lan")

    def test_a_custom_host_name_can_be_allowed(self):
        server = AlbumMaker(self.app, self.photos, self.tmp / "host", "--allow-host", "trip.home").start()
        self.addCleanup(server.stop)
        self.assertEqual(server.get("/", {"Host": "trip.home"})[0], 200)
        self.assertEqual(server.get("/", {"Host": "other.home"})[0], 403)


if __name__ == "__main__":
    unittest.main()
