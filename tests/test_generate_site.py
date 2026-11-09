# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Pieter Remi Coppens
"""generate_site.py end to end: it is run exactly as a user would run it, on
small photos made on the fly, and the output folder is inspected."""
import hashlib
import json
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path

from PIL import Image

from .helpers import (HAVE_FFMPEG, TempDirMixin, card_covers, css_rule, day_nav_args, install_app, make_fake_vendor,
                      make_jpeg, make_video, needs_ffmpeg, read_json, run_script, size_toggle, slide_for,
                      slides_of, standard_trip, tc, write_json)


def md5(path):
    return hashlib.md5(Path(path).read_bytes()).hexdigest()


class StaticBuildTests(TempDirMixin, unittest.TestCase):
    """One build of a deliberately awkward folder, inspected from many angles."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = cls.make_tmp()
        cls.app = install_app(cls.tmp / "app")
        cls.photos = cls.tmp / "photos"
        standard_trip(cls.photos)
        shutil.copy(cls.photos / "a1.jpg", cls.photos / "copy_of_a1.jpg")           # exact duplicate
        make_jpeg(cls.photos / "nodate.jpg", None, color=(1, 2, 3))                  # no capture date
        make_jpeg(cls.photos / "x" / "IMG_1.jpg", "2026:05:01 12:00:00", color=(9, 9, 9))
        make_jpeg(cls.photos / "y" / "IMG_1.jpg", "2026:05:01 12:05:00", color=(8, 8, 8))   # same name
        try:                                                       # characters Windows forbids in file names
            if os.name != "nt":
                make_jpeg(cls.photos / "we:ird?.jpg", "2026:05:01 12:10:00", color=(7, 7, 7))
                cls.odd_name_made = True
            else:
                cls.odd_name_made = False
        except OSError:
            cls.odd_name_made = False
        Image.new("RGB", (200, 100), (5, 5, 5)).save(cls.photos / "scan.png")
        exif = Image.Exif(); exif[306] = "2026:05:02 11:00:00"; exif.get_ifd(0x8769)[36867] = "2026:05:02 11:00:00"
        Image.new("RGB", (200, 100), (50, 5, 5)).save(cls.photos / "doc.tif", exif=exif)   # browsers can't show TIFF
        (cls.photos / "notes.txt").write_text("not media")
        (cls.photos / ".hidden.jpg").write_bytes(b"x")
        cls.content = write_json(cls.tmp / "content.json", {
            "title": "Belgium 2026", "subtitle": "May",
            "days": {"2026-05-01": {"title": "Canals & <b>coffee</b>",
                                    "description": "Para one.\n\n<script>alert(1)</script>", "cover": "a2.jpg"},
                     "2026-05-02": {"cover": "b2.jpg"}},
            "photos": {"a1.jpg": {"caption": 'Sunrise & "canal"', "favorite": True},
                       "sub/a3.jpg": {"hidden": True}, "gone.jpg": {"caption": "orphan"}}})
        cls.site = cls.tmp / "site"
        cls.result = run_script(cls.app, "generate_site.py", "-i", cls.photos, "-o", cls.site, "-c", cls.content)
        cls.log = cls.result.stdout + cls.result.stderr

    def page(self, rel):
        return (self.site / rel).read_text(encoding="utf-8")

    # ---- the build itself
    def test_build_succeeds_and_lays_out_the_site(self):
        self.assertEqual(self.result.returncode, 0, self.log)
        for rel in ("index.html", "days/2026-05-01.html", "days/2026-05-02.html", "days/unknown.html",
                    "assets/style.css", "assets/script.js", tc.SITE_MARKER):
            self.assertTrue((self.site / rel).is_file(), rel)

    def test_one_page_per_day_and_nothing_else(self):
        self.assertEqual(sorted(p.name for p in (self.site / "days").iterdir()),
                         ["2026-05-01.html", "2026-05-02.html", "unknown.html"])

    def test_no_temporary_files_are_left_behind(self):
        leftovers = [p for p in self.site.rglob("*") if ".tmp" in p.name or p.name.endswith("~")]
        self.assertEqual(leftovers, [])

    def test_the_build_record_lists_what_was_made(self):
        record = read_json(self.site / tc.SITE_MARKER)
        self.assertEqual(record["thumb_size"], 400)
        self.assertIn("a1.jpg", record["files"])
        self.assertIn("media/a1.jpg", record["files"]["a1.jpg"]["outputs"])

    # ---- what is and isn't included
    def test_hidden_photos_are_left_out_entirely(self):
        self.assertFalse((self.site / "media" / "a3.jpg").exists())
        self.assertFalse((self.site / "thumbs" / "a3.jpg").exists())
        for page in self.site.rglob("*.html"):
            self.assertNotIn("a3.jpg", page.read_text(encoding="utf-8"), page.name)
        self.assertIn("Leaving out 1 file(s) marked hidden", self.log)

    def test_exact_duplicates_are_skipped_but_reported(self):
        self.assertFalse((self.site / "media" / "copy_of_a1.jpg").exists())
        self.assertIn("exact duplicate of a1.jpg", self.log)

    def test_hidden_files_and_non_media_are_ignored(self):
        names = {p.name for p in (self.site / "media").iterdir()}
        self.assertNotIn("notes.txt", names)
        self.assertFalse(any(n.startswith(".") for n in names))

    def test_name_collisions_and_odd_characters_get_safe_unique_names(self):
        names = {p.name for p in (self.site / "media").iterdir()}
        self.assertTrue({"IMG_1.jpg", "IMG_1_2.jpg"} <= names, names)
        if self.odd_name_made:                                     # (not possible on Windows file systems)
            self.assertIn("we_ird_.jpg", names)

    # ---- the files themselves
    def test_jpeg_and_png_are_byte_for_byte_copies(self):
        self.assertEqual(md5(self.site / "media" / "a1.jpg"), md5(self.photos / "a1.jpg"))
        self.assertEqual(md5(self.site / "media" / "scan.png"), md5(self.photos / "scan.png"))

    def test_a_tiff_is_converted_for_display_but_the_original_is_kept(self):
        media = self.site / "media"
        self.assertTrue((media / "doc.jpg").is_file() and (media / "doc.tif").is_file())
        self.assertEqual(md5(media / "doc.tif"), md5(self.photos / "doc.tif"))
        with Image.open(media / "doc.jpg") as im:
            self.assertEqual(im.format, "JPEG")
        slide = slide_for(self.page("days/2026-05-02.html"), "doc.tif")
        self.assertTrue(slide["display"].endswith("doc.jpg"))
        self.assertTrue(slide["downloadUrl"].endswith("doc.tif"))

    def test_thumbnails_are_small_jpegs(self):
        thumbs = list((self.site / "thumbs").iterdir())
        self.assertGreaterEqual(len(thumbs), 9 + self.odd_name_made)
        for t in thumbs:
            with Image.open(t) as im:
                self.assertEqual(im.format, "JPEG", t.name)
                self.assertLessEqual(max(im.size), 400, t.name)

    # ---- how the pages read the content file
    def test_titles_are_shown_but_never_interpreted_as_html(self):
        day = self.page("days/2026-05-01.html")
        self.assertIn("Canals &amp; &lt;b&gt;coffee&lt;/b&gt;", day)
        self.assertNotIn("<b>coffee</b>", day)

    def test_descriptions_are_escaped(self):
        day = self.page("days/2026-05-01.html")
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", day)
        self.assertNotIn("<script>alert(1)</script>", day)

    def test_trip_title_and_subtitle(self):
        index = self.page("index.html")
        self.assertIn("Belgium 2026", index)
        self.assertIn("May", index)

    def test_captions_and_favorites_reach_the_pages(self):
        day = self.page("days/2026-05-01.html")
        slide = slide_for(day, "a1.jpg")
        self.assertEqual(slide["caption"], 'Sunrise & "canal"')
        self.assertTrue(slide["favorite"])
        self.assertEqual(day.count("thumb is-fav"), 1)
        self.assertIn("Sunrise &amp; &#34;canal&#34;", day)           # as the image's alt text, escaped
        self.assertFalse(slide_for(day, "a2.jpg")["favorite"])

    def test_chosen_covers_are_used_on_the_homepage(self):
        covers = card_covers(self.page("index.html"))
        self.assertEqual(covers["2026-05-01"], "thumbs/a2.jpg")      # not the first photo
        self.assertEqual(covers["2026-05-02"], "thumbs/b2.jpg")

    def test_undated_files_are_listed_on_the_unsorted_page(self):
        unknown = self.page("days/unknown.html")
        self.assertEqual(sorted(s["detail"]["filename"] for s in slides_of(unknown)), ["nodate.jpg", "scan.png"])
        self.assertIn("no usable capture date", unknown)

    def test_the_unsorted_day_is_not_counted_as_a_day(self):
        index = self.page("index.html")
        self.assertIn("2 days on the road", index)
        self.assertIn("Unsorted", index)

    def test_the_static_site_has_no_editing_controls_or_private_paths(self):
        for page in self.site.rglob("*.html"):
            text = page.read_text(encoding="utf-8")
            for forbidden in ("day-editor", "edit-caption", '"rel"', "sub/a3"):
                self.assertNotIn(forbidden, text, f"{forbidden} in {page.name}")
        self.assertIn("editable: false", self.page("days/2026-05-01.html"))

    def test_captions_for_files_that_cannot_be_found_are_reported_not_lost(self):
        self.assertIn("gone.jpg", self.log)
        self.assertIn("gone.jpg", read_json(self.content)["photos"])

    def test_stats_count_what_is_shown(self):
        index = self.page("index.html")
        # 10 = a1 a2 b1 b2 (4) + IMG_1 IMG_1 we:ird doc.tif (4) + the 2 undated ones; the hidden
        # photo and the exact duplicate are not counted (9 where "we:ird?.jpg" can't exist)
        expected = 10 if self.odd_name_made else 9
        self.assertRegex(index, rf'<div class="n">{expected}</div><div class="l">Photos</div>')
        self.assertRegex(index, r'<div class="n">2</div><div class="l">Days</div>')
        self.assertRegex(index, r'<div class="n">0</div><div class="l">Videos</div>')

    # ---- size controls and Esc
    def test_day_pages_and_the_homepage_each_have_a_four_step_size_control(self):
        wanted = [("small", "S"), ("medium", "M"), ("large", "L"), ("xl", "XL")]
        day, index = self.page("days/2026-05-01.html"), self.page("index.html")
        self.assertEqual(size_toggle(day, "thumb-grid"), ("tripThumbSize", wanted))
        self.assertEqual(size_toggle(index, "day-list"), ("tripCardSize", wanted))
        self.assertIn('id="thumb-grid"', day)
        self.assertIn('id="day-list"', index)
        self.assertIsNone(size_toggle(day, "day-list"), "the day page must not carry the card control")
        self.assertIsNone(size_toggle(index, "thumb-grid"), "the homepage must not carry the thumbnail control")

    def test_every_size_has_a_layout_and_each_step_is_bigger_than_the_last(self):
        css = self.page("assets/style.css")
        import re
        # day-page thumbnails: the smallest width each size asks for, and the largest it may grow to
        def clamp(rule):
            lo, _, hi = re.search(r"clamp\((\d+)px,\s*(\d+)vw,\s*(\d+)px\)", rule).groups()
            return int(lo), int(hi)
        small, large, xl = (clamp(css_rule(css, f".grid.size-{n}")) for n in ("small", "large", "xl"))
        medium = clamp(css_rule(css, ".grid"))                       # the unchanged default layout
        self.assertEqual(medium, (130, 220), "Medium stays exactly the layout day pages always had")
        for position in (0, 1):                                      # smallest width, then largest width
            self.assertTrue(small[position] < medium[position] < large[position] < xl[position], (small, medium, large, xl))
        # homepage cards: S < M (the original 280px) < L < XL
        px = lambda selector: int(re.search(r"min\(100%,(\d+)px\)", css_rule(css, selector)).group(1))
        medium = int(re.search(r"\.day-list\{[^}]*minmax\((\d+)px", css).group(1))
        self.assertEqual(medium, 280, "Medium stays exactly the layout the homepage always had")
        self.assertTrue(px(".day-list.size-small") < medium < px(".day-list.size-large") < px(".day-list.size-xl"))

    def test_the_biggest_sizes_cannot_push_a_phone_screen_sideways(self):
        css = self.page("assets/style.css")
        for selector in (".grid.size-xl", ".day-list.size-large", ".day-list.size-xl", ".day-list.size-small"):
            self.assertIn("min(100%", css_rule(css, selector), selector)

    def test_day_pages_tell_the_script_where_esc_leads(self):
        first = day_nav_args(self.page("days/2026-05-01.html"))
        self.assertEqual((first[0], first[2]), (None, "../index.html"))     # nothing before the first day
        self.assertTrue(first[1].endswith(".html"))
        self.assertEqual(day_nav_args(self.page("days/unknown.html"))[2], "../index.html")

    def test_the_hint_under_a_day_page_mentions_esc(self):
        self.assertIn("Esc returns to all days", self.page("days/2026-05-01.html"))
        self.assertNotIn("Esc returns to all days", self.page("index.html"))

    def test_the_content_file_is_extended_not_overwritten(self):
        c = read_json(self.content)
        self.assertEqual(c["days"]["2026-05-01"]["title"], "Canals & <b>coffee</b>")   # yours, untouched
        self.assertIn("2026-05-02", c["days"])
        self.assertIn("unknown", c["days"])                                            # added for the Unsorted page


class StaticRebuildTests(TempDirMixin, unittest.TestCase):
    """Re-running over an existing site: only what changed is redone, and what
    is no longer wanted is removed."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = cls.make_tmp()
        cls.app = install_app(cls.tmp / "app")

    def setUp(self):
        self.work = Path(tempfile.mkdtemp(dir=self.tmp))
        self.photos = self.work / "photos"
        standard_trip(self.photos)
        self.content = self.work / "content.json"
        self.site = self.work / "site"

    def build(self, *extra):
        r = run_script(self.app, "generate_site.py", "-i", self.photos, "-o", self.site, "-c", self.content, *extra)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r.stdout + r.stderr

    def mtimes(self, folder):
        return {p.name: p.stat().st_mtime_ns for p in (self.site / folder).iterdir()}

    def test_an_unchanged_rebuild_touches_no_thumbnails_or_copies(self):
        self.build()
        thumbs, media = self.mtimes("thumbs"), self.mtimes("media")
        time.sleep(0.05)
        self.build()
        self.assertEqual(self.mtimes("thumbs"), thumbs)
        self.assertEqual(self.mtimes("media"), media)

    def test_a_new_photo_is_added_and_the_rest_is_left_alone(self):
        self.build()
        before = self.mtimes("thumbs")
        make_jpeg(self.photos / "new.jpg", "2026:05:02 15:00:00", color=(1, 1, 1))
        self.build()
        after = self.mtimes("thumbs")
        self.assertIn("new.jpg", after)
        self.assertTrue(all(after[k] == v for k, v in before.items()))

    def test_a_changed_photo_is_rebuilt(self):
        self.build()
        old = md5(self.site / "media" / "a1.jpg")
        make_jpeg(self.photos / "a1.jpg", "2026:05:01 09:00:00", (50, 4), color=(1, 2, 3), size=(640, 480))
        self.build()
        self.assertNotEqual(md5(self.site / "media" / "a1.jpg"), old)
        self.assertEqual(md5(self.site / "media" / "a1.jpg"), md5(self.photos / "a1.jpg"))

    def test_removed_photos_and_emptied_days_disappear_from_the_site(self):
        self.build()
        for name in ("b1.jpg", "b2.jpg"):
            (self.photos / name).unlink()
        log = self.build()
        self.assertFalse((self.site / "media" / "b1.jpg").exists())
        self.assertFalse((self.site / "thumbs" / "b2.jpg").exists())
        self.assertFalse((self.site / "days" / "2026-05-02.html").exists())
        self.assertTrue((self.site / "days" / "2026-05-01.html").exists())
        self.assertIn("Removed", log)

    def test_files_you_put_in_the_output_folder_yourself_are_never_deleted(self):
        self.build()
        mine = self.site / "media" / "my-own-file.txt"
        mine.write_text("mine")
        (self.photos / "b1.jpg").unlink()
        self.build()
        self.assertTrue(mine.exists())

    def test_hiding_a_photo_later_removes_its_files_and_unhiding_brings_them_back(self):
        self.build()
        self.assertTrue((self.site / "media" / "a2.jpg").exists())
        write_json(self.content, {"photos": {"a2.jpg": {"hidden": True}}})
        self.build()
        self.assertFalse((self.site / "media" / "a2.jpg").exists())
        self.assertFalse((self.site / "thumbs" / "a2.jpg").exists())
        write_json(self.content, {"photos": {}})
        self.build()
        self.assertTrue((self.site / "media" / "a2.jpg").exists())

    def test_a_different_thumb_size_rebuilds_the_thumbnails(self):
        self.build()
        with Image.open(self.site / "thumbs" / "a1.jpg") as im:
            self.assertEqual(max(im.size), 320)       # the 320x240 original is smaller than 400
        self.build("--thumb-size", "100")
        with Image.open(self.site / "thumbs" / "a1.jpg") as im:
            self.assertEqual(max(im.size), 100)

    def test_deleting_the_build_record_is_safe(self):
        self.build()
        (self.site / tc.SITE_MARKER).unlink()
        self.build()
        self.assertTrue((self.site / "media" / "a1.jpg").exists())
        self.assertTrue((self.site / tc.SITE_MARKER).exists())

    def test_force_regenerates_everything(self):
        self.build()
        before = self.mtimes("thumbs")
        time.sleep(0.05)
        self.build("--force")
        after = self.mtimes("thumbs")
        self.assertTrue(all(after[k] > before[k] for k in before))

    def test_a_site_built_inside_the_photos_folder_is_not_mistaken_for_photos(self):
        self.site = self.photos / "site"
        self.build()
        self.build()                                      # the second run must not pick up its own output
        self.assertEqual(sorted(p.name for p in (self.site / "media").iterdir()),
                         ["a1.jpg", "a2.jpg", "a3.jpg", "b1.jpg", "b2.jpg"])

    def test_the_content_file_keeps_user_text_across_rebuilds(self):
        self.build()
        c = read_json(self.content)
        c["days"]["2026-05-01"]["title"] = "My title"
        write_json(self.content, c)
        make_jpeg(self.photos / "later.jpg", "2026:05:09 10:00:00")
        self.build()
        c = read_json(self.content)
        self.assertEqual(c["days"]["2026-05-01"]["title"], "My title")
        self.assertIn("2026-05-09", c["days"])


class StaticCommandLineTests(TempDirMixin, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = cls.make_tmp()
        cls.app = install_app(cls.tmp / "app")
        cls.photos = cls.tmp / "photos"
        standard_trip(cls.photos)

    def run_gs(self, *args):
        return run_script(self.app, "generate_site.py", *args, cwd=self.tmp)

    def test_input_is_required(self):
        r = self.run_gs()
        self.assertEqual(r.returncode, 2)
        self.assertIn("-i/--input is required", r.stderr)

    def test_a_missing_input_folder_is_a_clear_error(self):
        r = self.run_gs("-i", self.tmp / "nope", "-o", self.tmp / "o1")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("nope", r.stdout + r.stderr)
        self.assertFalse((self.tmp / "o1").exists())

    def test_output_may_not_be_the_photos_folder_or_contain_it(self):
        for out in (self.photos, self.tmp):
            with self.subTest(output=out):
                before = sorted(p.name for p in self.photos.rglob("*"))
                r = self.run_gs("-i", self.photos, "-o", out, "-c", self.tmp / "c-unsafe.json")
                self.assertNotEqual(r.returncode, 0)
                self.assertEqual(sorted(p.name for p in self.photos.rglob("*")), before)

    def test_bad_thumb_sizes_are_rejected(self):
        for bad in ("5", "99999", "abc"):
            with self.subTest(bad=bad):
                r = self.run_gs("-i", self.photos, "-o", self.tmp / "o2", "--thumb-size", bad)
                self.assertEqual(r.returncode, 2)

    def test_a_reversed_date_range_is_rejected_before_any_work(self):
        r = self.run_gs("-i", self.photos, "-o", self.tmp / "o3", "--date-range", "2026-05-10", "2026-05-01")
        self.assertEqual(r.returncode, 2)
        self.assertFalse((self.tmp / "o3").exists())

    def test_a_broken_content_file_stops_the_build_and_is_left_untouched(self):
        bad = self.tmp / "broken.json"
        bad.write_text('{"title": "x",}')
        r = self.run_gs("-i", self.photos, "-o", self.tmp / "o4", "-c", bad)
        self.assertEqual(r.returncode, 1)
        self.assertIn("line 1", r.stdout + r.stderr)
        self.assertEqual(bad.read_text(), '{"title": "x",}')
        self.assertFalse((self.tmp / "o4" / "index.html").exists())

    def test_an_empty_folder_does_not_crash(self):
        empty = self.tmp / "empty"
        empty.mkdir()
        r = self.run_gs("-i", empty, "-o", self.tmp / "o5", "-c", self.tmp / "c5.json")
        self.assertIn(r.returncode, (0, 1))
        self.assertNotIn("Traceback", r.stderr)


class StaticDateCheckTests(TempDirMixin, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = cls.make_tmp()
        cls.app = install_app(cls.tmp / "app")
        cls.photos = cls.tmp / "photos"
        standard_trip(cls.photos)
        make_jpeg(cls.photos / "ancient.jpg", "1904:01:01 00:00:00", color=(1, 1, 1))
        make_jpeg(cls.photos / "reset.jpg", "2000:01:01 00:00:07", color=(2, 2, 2))
        cls._n = 0

    def build(self, *extra):
        type(self)._n += 1
        site = self.tmp / f"site{self._n}"
        r = run_script(self.app, "generate_site.py", "-i", self.photos, "-o", site,
                       "-c", self.tmp / f"c{self._n}.json", *extra)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return site, r.stdout + r.stderr

    def days(self, site):
        return sorted(p.stem for p in (site / "days").iterdir())

    def test_wrong_dates_go_to_unsorted_and_are_reported(self):
        site, log = self.build()
        self.assertEqual(self.days(site), ["2026-05-01", "2026-05-02", "unknown"])
        self.assertIn("Date check: 2 file(s)", log)
        self.assertIn("ancient.jpg", log)
        self.assertIn("reset.jpg", log)
        slide = slide_for((site / "days" / "unknown.html").read_text(encoding="utf-8"), "ancient.jpg")
        self.assertIn("Embedded date 1904-01-01", slide["detail"]["date_note"])

    def test_the_check_can_be_switched_off(self):
        site, log = self.build("--no-date-check")
        self.assertEqual(self.days(site), ["1904-01-01", "2000-01-01", "2026-05-01", "2026-05-02"])
        self.assertNotIn("Date check", log)

    def test_an_explicit_range_decides(self):
        site, log = self.build("--date-range", "2026-05-01", "2026-05-02")
        self.assertEqual(self.days(site), ["2026-05-01", "2026-05-02", "unknown"])
        self.assertIn("outside --date-range", log)

    def test_a_wide_explicit_range_accepts_old_dates(self):
        site, log = self.build("--date-range", "1900-01-01", "2030-01-01")
        self.assertEqual(self.days(site), ["1904-01-01", "2000-01-01", "2026-05-01", "2026-05-02"])


class StaticOfflineAssetsTests(TempDirMixin, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = cls.make_tmp()
        cls.photos = cls.tmp / "photos"
        standard_trip(cls.photos)

    def build(self, app, name):
        site = self.tmp / name
        r = run_script(app, "generate_site.py", "-i", self.photos, "-o", site, "-c", self.tmp / f"{name}.json")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return site, r.stdout

    def test_with_a_vendor_folder_the_site_carries_its_own_map_library_and_fonts(self):
        vendor = make_fake_vendor(self.tmp / "v")
        app = install_app(self.tmp / "app-with", with_vendor=vendor)
        site, log = self.build(app, "site-with")
        index = (site / "index.html").read_text(encoding="utf-8")
        day = (site / "days" / "2026-05-01.html").read_text(encoding="utf-8")
        self.assertIn('href="assets/vendor/fonts/fonts.css"', index)
        self.assertIn('src="assets/vendor/leaflet/leaflet.js"', index)
        self.assertIn('href="../assets/vendor/fonts/fonts.css"', day)
        self.assertIn('src="../assets/vendor/leaflet/leaflet.js"', day)
        for page in (index, day):
            for host in ("googleapis", "gstatic", "cdnjs", "unpkg", "jsdelivr"):
                self.assertNotIn(host, page)
        self.assertTrue((site / "assets/vendor/leaflet/leaflet.js").is_file())
        self.assertTrue((site / "assets/vendor/licenses/leaflet-LICENSE.txt").is_file())
        self.assertIn("bundled in the site", log)

    def test_rebuilding_over_an_existing_bundle_works(self):
        vendor = make_fake_vendor(self.tmp / "v2")
        app = install_app(self.tmp / "app-twice", with_vendor=vendor)
        self.build(app, "site-twice")
        site, _ = self.build(app, "site-twice")
        self.assertTrue((site / "assets/vendor/fonts/fonts.css").is_file())

    def test_without_a_vendor_folder_the_internet_copies_are_used_as_before(self):
        app = install_app(self.tmp / "app-without")
        site, log = self.build(app, "site-without")
        index = (site / "index.html").read_text(encoding="utf-8")
        self.assertIn("fonts.googleapis.com", index)
        self.assertIn("cdnjs.cloudflare.com", index)
        self.assertFalse((site / "assets" / "vendor").exists())
        self.assertIn("--fetch-offline-assets", log)

    def test_tiles_come_from_the_default_provider_in_a_static_site(self):
        app = install_app(self.tmp / "app-tiles")
        site, _ = self.build(app, "site-tiles")
        self.assertIn('window.TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"',
                      (site / "days" / "2026-05-01.html").read_text(encoding="utf-8"))


@needs_ffmpeg
class StaticVideoTests(TempDirMixin, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = cls.make_tmp()
        cls.app = install_app(cls.tmp / "app")
        cls.photos = cls.tmp / "photos"
        standard_trip(cls.photos)
        make_video(cls.photos / "clip.mp4", "2026-05-02T12:00:00Z", seconds=6)
        make_jpeg(cls.photos / "live.jpg", "2026:05:01 14:00:00", color=(70, 70, 70))
        make_video(cls.photos / "live.mov", "2026-05-01T14:00:00Z", seconds=2)       # short: a Live Photo's clip
        make_video(cls.photos / "reset2002.mp4", "2002-01-01T00:00:05Z", seconds=6)  # clock never set
        cls.site = cls.tmp / "site"
        cls.result = run_script(cls.app, "generate_site.py", "-i", cls.photos, "-o", cls.site,
                             "-c", cls.tmp / "c.json", "--tz-offset", "0")
        cls.log = cls.result.stdout + cls.result.stderr

    def day(self, key):
        return (self.site / "days" / f"{key}.html").read_text(encoding="utf-8")

    def test_build_succeeds(self):
        self.assertEqual(self.result.returncode, 0, self.log)

    def test_a_video_is_copied_untouched_and_gets_a_preview_frame(self):
        self.assertEqual(md5(self.site / "media" / "clip.mp4"), md5(self.photos / "clip.mp4"))
        slide = slide_for(self.day("2026-05-02"), "clip.mp4")
        self.assertEqual(slide["type"], "video")
        self.assertTrue(slide["poster"].endswith("clip.jpg"))
        self.assertIn("duration", slide["detail"])
        with Image.open(self.site / "thumbs" / "clip.jpg") as im:
            self.assertEqual(im.format, "JPEG")

    def test_videos_are_counted(self):
        self.assertRegex((self.site / "index.html").read_text(encoding="utf-8"),
                         r'<div class="n">2</div><div class="l">Videos</div>')

    def test_a_live_photo_clip_is_attached_to_its_photo_not_listed_separately(self):
        day = self.day("2026-05-01")
        self.assertNotIn("live.mov", [s["detail"]["filename"] for s in slides_of(day)])
        live = slide_for(day, "live.jpg")
        self.assertTrue(live["live"].endswith(".mov"))
        self.assertTrue((self.site / "media" / Path(live["live"]).name).is_file())
        self.assertIn("Paired 1 Live Photo clip", self.log)
        self.assertEqual(day.count("LIVE"), 1)

    def test_a_video_with_an_unset_clock_is_filed_under_unsorted(self):
        slide = slide_for(self.day("unknown"), "reset2002.mp4")
        self.assertIn("2002-01-01", slide["detail"]["date_note"])

    def test_no_live_photos_option_lists_the_clip_as_a_video(self):
        site = self.tmp / "site-nolive"
        r = run_script(self.app, "generate_site.py", "-i", self.photos, "-o", site,
                       "-c", self.tmp / "c2.json", "--tz-offset", "0", "--no-live-photos")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        day = (site / "days" / "2026-05-01.html").read_text(encoding="utf-8")
        self.assertIn("live.mov", [s["detail"]["filename"] for s in slides_of(day)])
        self.assertNotIn("live", slide_for(day, "live.jpg"))

    def test_without_ffmpeg_videos_still_appear_with_a_placeholder(self):
        env_path = self.tmp / "no-ffmpeg-bin"
        env_path.mkdir()
        site = self.tmp / "site-noffmpeg"
        import subprocess, sys
        r = subprocess.run([sys.executable, str(self.app / "generate_site.py"), "-i", str(self.photos), "-o", str(site),
                            "-c", str(self.tmp / "c3.json")],
                           capture_output=True, text=True, env={**os.environ, "PATH": str(env_path)})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("ffmpeg", r.stdout + r.stderr)
        self.assertTrue((site / "thumbs" / "clip.jpg").is_file())


if __name__ == "__main__":
    unittest.main()
