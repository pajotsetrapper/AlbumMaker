# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Pieter Remi Coppens
"""Site templates (--template): the registry, the command line, static builds,
the live server, libraries and the desktop app's settings."""
import argparse
import io
import re
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace

from tests.helpers import AlbumMaker, TempDirMixin, install_app, make_jpeg, standard_trip, write_json

from albummaker import render, site_templates
from albummaker.build import build_parser, main as build_main
from albummaker.gui.commands import build_command, server_command
from albummaker.gui.settings import SettingsError, default_settings, validate_settings


def build(*argv):
    out = io.StringIO()
    with redirect_stdout(out), redirect_stderr(out):
        try:
            build_main(list(argv))
        except SystemExit as e:
            if e.code not in (0, None):
                raise AssertionError(out.getvalue())


class RegistryTests(unittest.TestCase):
    def test_the_default_is_classic_and_the_journal_style_exists(self):
        self.assertEqual(site_templates.DEFAULT_TEMPLATE, "classic")
        self.assertIn("journal", site_templates.template_names())

    def test_names_are_case_insensitive_and_unknown_ones_list_the_choices(self):
        self.assertEqual(site_templates.template_arg(" Journal "), "journal")
        with self.assertRaisesRegex(argparse.ArgumentTypeError, "classic, journal, scrapbook"):
            site_templates.template_arg("fancy")

    def test_the_command_line_takes_the_option(self):
        self.assertEqual(build_parser().parse_args(["-i", "x"]).template, "classic")
        self.assertEqual(build_parser().parse_args(["-i", "x", "--template", "journal"]).template, "journal")

    def test_classic_is_exactly_what_it_was(self):
        classic = render.get_template("classic")
        self.assertIs(classic.css, render.CSS)
        self.assertIs(classic.js, render.JS)
        self.assertEqual(classic.asset_version, render.ASSET_VERSION)

    def test_a_template_adds_its_own_css_and_js_to_the_shared_ones(self):
        ps = render.get_template("journal")
        self.assertTrue(ps.css.startswith(render.CSS.rstrip("\n")))
        self.assertIn(".jr-hero", ps.css)
        self.assertIn("initJournalOverviewMap", ps.js)
        self.assertNotIn("initJournalOverviewMap", render.JS)
        self.assertNotEqual(ps.asset_version, render.ASSET_VERSION)


class StaticSiteTests(TempDirMixin, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = cls.make_tmp()
        cls.photos = cls.tmp / "photos"
        standard_trip(cls.photos)
        make_jpeg(cls.photos / "b3.jpg", "2026:05:02 11:00:00", (50, 5.01))
        write_json(cls.photos / "trip_content.json", {
            "title": "Template trip", "subtitle": "A subtitle",
            "days": {"2026-05-01": {"title": "First", "description": "One.\n\nTwo."}},
            "photos": {"a1.jpg": {"caption": "Hello", "favorite": True}}})
        for name in ("classic", "journal"):
            build("-i", str(cls.photos), "-o", str(cls.tmp / name), "--template", name, "--large-thumb-size", "800")

    def page(self, template, rel):
        return (self.tmp / template / rel).read_text(encoding="utf-8")

    def test_classic_pages_are_untouched(self):
        index = self.page("classic", "index.html")
        self.assertIn('class="day-card"', index)
        self.assertNotIn("tpl-journal", index)
        self.assertNotIn(".jr-hero", self.page("classic", "assets/style.css"))
        self.assertNotIn("initJournalOverviewMap", self.page("classic", "assets/script.js"))

    def test_the_journal_homepage_has_cover_stats_timeline_and_map(self):
        index = self.page("journal", "index.html")
        for part in ('tpl-journal', 'class="jr-hero"', 'jr-stats', 'class="jr-step"', 'jr-mosaic',
                     'id="overview-map"', "initJournalOverviewMap", "Template trip", "A subtitle"):
            self.assertIn(part, index)
        self.assertNotIn('class="day-card"', index)
        self.assertEqual(len(re.findall(r'class="jr-step"', index)), 2)
        # the cover is stretched over the screen, so it must not be a 400 px thumbnail
        self.assertRegex(index, r"background-image:url\('(thumbs_large|media)/")

    def test_journal_day_pages_keep_what_the_viewer_needs(self):
        day = self.page("journal", "days/2026-05-01.html")
        for part in ('id="slideshow"', 'id="thumb-grid"', 'data-slide-index="0"', "initSlideshow(",
                     "initJournalDayMap", "Hello", "<p>One.</p>", "<p>Two.</p>", "Next"):
            self.assertIn(part, day)
        self.assertNotIn("Edit title", day)            # a static site has no editor
        self.assertTrue((self.tmp / "journal" / "assets" / "style.css").read_text().count(".jr-hero"))

    def test_both_templates_share_the_same_media(self):
        a = sorted(p.name for p in (self.tmp / "classic" / "thumbs").iterdir())
        b = sorted(p.name for p in (self.tmp / "journal" / "thumbs").iterdir())
        self.assertEqual(a, b)


class ScrapbookTests(TempDirMixin, unittest.TestCase):
    def test_a_static_site_and_a_library_use_the_scrapbook_pages(self):
        tmp = self.make_tmp()
        photos = tmp / "trip"
        standard_trip(photos)
        build("-i", str(photos), "-o", str(tmp / "site"), "--template", "scrapbook")
        index = (tmp / "site" / "index.html").read_text(encoding="utf-8")
        for part in ("tpl-scrapbook", "sb-title", "sb-stamp", 'class="sb-page', "initScrapbookOverviewMap"):
            self.assertIn(part, index)
        day = (tmp / "site" / "days" / "2026-05-01.html").read_text(encoding="utf-8")
        for part in ('id="slideshow"', 'id="thumb-grid"', 'data-slide-index="0"', "sb-polaroid", "initSlideshow("):
            self.assertIn(part, day)
        self.assertIn(".sb-polaroid", (tmp / "site" / "assets" / "style.css").read_text(encoding="utf-8"))
        listing = tmp / "trips.txt"
        listing.write_text(f"{photos} | trip\n", encoding="utf-8")
        build("--library", str(listing), "-o", str(tmp / "lib"), "--template", "scrapbook")
        self.assertIn("sb-trips", (tmp / "lib" / "index.html").read_text(encoding="utf-8"))


class LibraryTests(TempDirMixin, unittest.TestCase):
    def test_a_library_uses_the_template_for_every_page(self):
        tmp = self.make_tmp()
        photos = tmp / "trip"
        standard_trip(photos)
        listing = tmp / "trips.txt"
        listing.write_text(f"{photos} | trip\n", encoding="utf-8")
        build("--library", str(listing), "-o", str(tmp / "out"), "--template", "journal")
        self.assertIn("jr-trips", (tmp / "out" / "index.html").read_text(encoding="utf-8"))
        self.assertIn("tpl-journal", (tmp / "out" / "trip" / "index.html").read_text(encoding="utf-8"))
        self.assertIn(".jr-hero", (tmp / "out" / "assets" / "style.css").read_text(encoding="utf-8"))


class LiveServerTests(TempDirMixin, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tmp = cls.make_tmp()
        cls.app = install_app(tmp / "app")
        photos = tmp / "photos"
        standard_trip(photos)
        cls.server = AlbumMaker(cls.app, photos, tmp / "work", "--template", "journal")
        cls.server.start()
        cls.addClassCleanup(cls.server.stop)

    def test_pages_and_assets_use_the_template(self):
        status, _, body = self.server.get("/")
        self.assertEqual(status, 200)
        html = body.decode("utf-8")
        self.assertIn("tpl-journal", html)
        version = re.search(r"/assets/style\.css\?v=(\w+)", html).group(1)
        self.assertEqual(version, render.get_template("journal").asset_version)
        status, headers, css = self.server.get("/assets/style.css")
        self.assertEqual(status, 200)
        self.assertIn(b".jr-hero", css)
        status, _, js = self.server.get("/assets/script.js")
        self.assertIn(b"initJournalDayMap", js)
        day = re.search(r'href="(/day/[^"]+)"', html).group(1)
        status, _, body = self.server.get(day)
        self.assertEqual(status, 200)

        self.assertIn("jr-hero-day", body.decode("utf-8"))


class DesktopAppSettingsTests(unittest.TestCase):
    def settings(self, template=None):
        s = default_settings()
        if template is not None:
            s["library"]["template"] = template
        return s

    def test_the_default_is_classic_and_unknown_names_are_refused(self):
        self.assertEqual(default_settings()["library"]["template"], "classic")
        with self.assertRaisesRegex(SettingsError, "library.template"):
            validate_settings(self.settings("fancy"))
        self.assertEqual(validate_settings(self.settings("journal"))["library"]["template"], "journal")

    def test_the_commands_pass_the_template_on(self):
        listing = Path(__import__("tempfile").mkdtemp()) / "trips.txt"
        listing.write_text("/tmp/x\n", encoding="utf-8")
        s = self.settings("journal")
        s["advanced"]["tile_cache"] = "/tmp/tiles"
        for command in (build_command("python", listing, "/tmp/out", s), server_command("python", listing, s)):
            self.assertEqual(command[command.index("--template") + 1], "journal")


if __name__ == "__main__":
    unittest.main()
