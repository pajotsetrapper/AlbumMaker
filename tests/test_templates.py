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

from tests.helpers import AlbumMaker, TempDirMixin, install_app, make_jpeg, size_toggle, standard_trip, write_json

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
    def test_the_default_is_cinema_and_the_journal_style_exists(self):
        self.assertEqual(site_templates.DEFAULT_TEMPLATE, "cinema")
        self.assertIn("journal", site_templates.template_names())

    def test_names_are_case_insensitive_and_unknown_ones_list_the_choices(self):
        self.assertEqual(site_templates.template_arg(" Journal "), "journal")
        with self.assertRaisesRegex(argparse.ArgumentTypeError, "classic, journal, scrapbook, cinema"):
            site_templates.template_arg("fancy")

    def test_the_command_line_takes_the_option(self):
        self.assertEqual(build_parser().parse_args(["-i", "x"]).template, "cinema")
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

    def test_journal_day_pages_have_the_thumbnail_size_control(self):
        day = self.page("journal", "days/2026-05-01.html")
        self.assertEqual(size_toggle(day, "thumb-grid"), ("tripThumbSize", [("small", "S"), ("medium", "M"), ("large", "L"), ("xl", "XL")]))
        self.assertIn("initSizeControls();", day)
        css = self.page("journal", "assets/style.css")
        for size in ("small", "large", "xl"):
            self.assertIn(f".jr-photos.size-{size}", css)

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
        self.assertEqual(size_toggle(day, "thumb-grid"), ("tripThumbSize", [("small", "S"), ("medium", "M"), ("large", "L"), ("xl", "XL")]))
        self.assertIn("initSizeControls();", day)
        css = (tmp / "site" / "assets" / "style.css").read_text(encoding="utf-8")
        for size in ("small", "large", "xl"):
            self.assertIn(f".sb-photos.size-{size}", css)
        listing = tmp / "trips.txt"
        listing.write_text(f"{photos} | trip\n", encoding="utf-8")
        build("--library", str(listing), "-o", str(tmp / "lib"), "--template", "scrapbook")
        self.assertIn("sb-trips", (tmp / "lib" / "index.html").read_text(encoding="utf-8"))


class CinemaTests(TempDirMixin, unittest.TestCase):
    def test_a_static_site_and_a_library_use_the_cinema_pages(self):
        tmp = self.make_tmp()
        photos = tmp / "trip"
        standard_trip(photos)
        build("-i", str(photos), "-o", str(tmp / "site"), "--template", "cinema")
        index = (tmp / "site" / "index.html").read_text(encoding="utf-8")
        for part in ("tpl-cinema", "cn-intro", 'class="cn-scene cn-day"', "cn-mini", "initCinemaOverview"):
            self.assertIn(part, index)
        day = (tmp / "site" / "days" / "2026-05-01.html").read_text(encoding="utf-8")
        for part in ('id="slideshow"', 'id="thumb-grid"', 'data-slide-index="0"', 'style="--r:', "cn-gallery"):
            self.assertIn(part, day)
        self.assertIn(".cn-scene", (tmp / "site" / "assets" / "style.css").read_text(encoding="utf-8"))
        listing = tmp / "trips.txt"
        listing.write_text(f"{photos} | trip\n", encoding="utf-8")
        build("--library", str(listing), "-o", str(tmp / "lib"), "--template", "cinema")
        self.assertIn("cn-posters", (tmp / "lib" / "index.html").read_text(encoding="utf-8"))

    def test_the_day_banner_is_not_a_small_thumbnail(self):
        tmp = self.make_tmp()
        photos = tmp / "trip"
        standard_trip(photos)
        build("-i", str(photos), "-o", str(tmp / "site"), "--template", "cinema")
        index = (tmp / "site" / "index.html").read_text(encoding="utf-8")
        self.assertRegex(index, r"cn-bg cover\" style=\"background-image:url\('media/")

    def test_the_library_intro_is_visible_without_a_scroll_observer(self):
        # the library page has no initCinemaOverview, so nothing else would reveal the intro
        tmp = self.make_tmp()
        photos = tmp / "trip"
        standard_trip(photos)
        listing = tmp / "trips.txt"
        listing.write_text(f"{photos} | trip\n", encoding="utf-8")
        build("--library", str(listing), "-o", str(tmp / "lib"), "--template", "cinema")
        index = (tmp / "lib" / "index.html").read_text(encoding="utf-8")
        self.assertRegex(index, r'class="cn-scene cn-intro cn-intro-plain in-view"')

    def test_the_library_page_of_every_template_has_the_card_size_control(self):
        wanted = [("small", "S"), ("medium", "M"), ("large", "L"), ("xl", "XL")]
        tmp = self.make_tmp()
        photos = tmp / "trip"
        standard_trip(photos)
        listing = tmp / "trips.txt"
        listing.write_text(f"{photos} | trip\n", encoding="utf-8")
        for name, grid in (("journal", ".jr-trips"), ("scrapbook", ".sb-trips"), ("cinema", ".cn-posters")):
            with self.subTest(template=name):
                build("--library", str(listing), "-o", str(tmp / name), "--template", name)
                index = (tmp / name / "index.html").read_text(encoding="utf-8")
                self.assertEqual(size_toggle(index, "trip-list"), ("tripLibrarySize", wanted))
                self.assertIn("initSizeControls();", index)
                self.assertIn('id="trip-list"', index)
                css = (tmp / name / "assets" / "style.css").read_text(encoding="utf-8")
                for size in ("small", "large", "xl"):
                    self.assertIn(f"{grid}.size-{size}", css)

    def test_cinema_day_pages_have_the_thumbnail_size_control(self):
        tmp = self.make_tmp()
        photos = tmp / "trip"
        standard_trip(photos)
        build("-i", str(photos), "-o", str(tmp / "site"), "--template", "cinema")
        day = (tmp / "site" / "days" / "2026-05-01.html").read_text(encoding="utf-8")
        self.assertEqual(size_toggle(day, "thumb-grid"), ("tripThumbSize", [("small", "S"), ("medium", "M"), ("large", "L"), ("xl", "XL")]))
        self.assertIn("initSizeControls();", day)
        css = (tmp / "site" / "assets" / "style.css").read_text(encoding="utf-8")
        for size in ("small", "large", "xl"):
            self.assertIn(f".cn-gallery.size-{size}", css)

    def test_every_template_has_the_ken_burns_switch_in_its_viewer(self):
        tmp = self.make_tmp()
        photos = tmp / "trip"
        standard_trip(photos)
        for name in ("classic", "journal", "scrapbook", "cinema"):
            with self.subTest(template=name):
                build("-i", str(photos), "-o", str(tmp / name), "--template", name)
                day = (tmp / name / "days" / "2026-05-01.html").read_text(encoding="utf-8")
                self.assertIn('id="slideshow-kb"', day)
                css = (tmp / name / "assets" / "style.css").read_text(encoding="utf-8")
                self.assertIn("@keyframes kb-move", css)
                self.assertIn("prefers-reduced-motion", css)
                self.assertIn("applyKenBurns", (tmp / name / "assets" / "script.js").read_text(encoding="utf-8"))

    def test_a_scene_can_animate_in_every_time_it_is_reached(self):
        js = (Path(__file__).resolve().parent.parent / "albummaker" / "static" / "cinema.js").read_text(encoding="utf-8")
        self.assertIn("classList.remove('in-view')", js)


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


class LinkPreviewTests(TempDirMixin, unittest.TestCase):
    """The preview tags sit in the one shared partial, so every template has them. A static
    site does not know its own address: it gets the title and dates, but no picture or url."""

    def test_every_template_has_the_tags_and_a_static_site_has_no_picture(self):
        tmp = self.make_tmp()
        photos = tmp / "photos"
        standard_trip(photos)
        for name in site_templates.template_names():
            with self.subTest(template=name):
                build("-i", str(photos), "-o", str(tmp / name), "--template", name)
                for page in ("index.html", next((tmp / name / "days").glob("*.html")).relative_to(tmp / name)):
                    html = (tmp / name / page).read_text(encoding="utf-8")
                    self.assertIn('property="og:title" content="', html)
                    self.assertIn('name="twitter:title" content="', html)
                    self.assertNotIn("og:image", html)
                    self.assertNotIn("og:url", html)
                    self.assertIn('name="twitter:card" content="summary"', html)
                index = (tmp / name / "index.html").read_text(encoding="utf-8")
                self.assertIn('og:description" content="1 \u2013 2 May 2026"', index)

    def test_text_in_the_tags_is_escaped(self):
        from albummaker import render
        html = render.get_template("cinema").index.render(
            page_title="t", css_href="x.css", tile_url_json="null", js_href="x.js", vendor=None,
            social={"title": 'A "quoted" <trip>', "description": "", "url": None, "image": None})
        self.assertIn('og:title" content="A &#34;quoted&#34; &lt;trip&gt;"', html)


class LiveServerTests(TempDirMixin, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tmp = cls.make_tmp()
        cls.app = install_app(tmp / "app")
        photos = tmp / "photos"
        standard_trip(photos)
        cls.server = AlbumMaker(cls.app, photos, tmp / "work", "--template", "journal",
                                "--allow-host", "trips.example.org")
        cls.server.start()
        cls.addClassCleanup(cls.server.stop)

    def test_a_pasted_link_shows_the_cover_title_and_dates(self):
        """What WhatsApp / iMessage read when the link is pasted: Open Graph and Twitter tags."""
        seen_by_the_app = {"Host": "trips.example.org", "X-Forwarded-Proto": "https"}
        html = self.server.text("/", seen_by_the_app)
        meta = dict(re.findall(r'<meta (?:property|name)="((?:og|twitter):[a-z_]+)" content="([^"]*)"', html))
        self.assertEqual(meta["og:title"], "My Trip")
        self.assertEqual(meta["og:description"], "1 \u2013 2 May 2026")          # the date range
        self.assertEqual(meta["og:url"], "https://trips.example.org/")
        self.assertEqual(meta["twitter:card"], "summary_large_image")
        self.assertEqual(meta["twitter:title"], "My Trip")
        image = meta["og:image"].replace("&amp;", "&")
        self.assertRegex(image, r"^https://trips\.example\.org/thumb/[0-9a-f]+\?v=\w+&w=1200$")
        self.assertEqual(meta["twitter:image"], meta["og:image"])
        # ... and the picture is really served (the app fetches it as a visitor)
        status, headers, body = self.server.get(image[len("https://trips.example.org"):], seen_by_the_app)
        self.assertEqual((status, headers["content-type"]), (200, "image/jpeg"))

    def test_a_day_link_has_the_day_title_and_date(self):
        seen_by_the_app = {"Host": "trips.example.org", "X-Forwarded-Proto": "https"}
        day = re.search(r'href="(/day/[^"]+)"', self.server.text("/")).group(1)
        html = self.server.text(day, seen_by_the_app)
        self.assertRegex(html, r'og:title" content="[^"]+ \u2014 My Trip"')
        self.assertIn('og:description" content="Friday, May 1, 2026"', html)
        self.assertIn(f'og:url" content="https://trips.example.org{day}"', html)
        self.assertIn('og:image" content="https://trips.example.org/thumb/', html)

    def test_the_address_comes_from_the_host_the_visitor_used(self):
        self.assertIn('og:url" content="http://127.0.0.1:%d/"' % self.server.port, self.server.text("/"))
        # a made-up Host is refused as before, and an odd one never reaches a tag
        status, _, _ = self.server.get("/", {"Host": "evil.example.net"})
        self.assertEqual(status, 403)

    def test_only_the_two_known_large_sizes_are_served(self):
        key = re.search(r"/thumb/([0-9a-f]+)\?v=", self.server.text("/")).group(1)
        self.assertEqual(self.server.get(f"/thumb/{key}?w=1200")[0], 200)
        self.assertEqual(self.server.get(f"/thumb/{key}?w=1234")[0], 404)

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


class LibraryPickerTests(TempDirMixin, unittest.TestCase):
    """The template is picked on the live library page and remembered in library_settings.json."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = cls.make_tmp()
        cls.app = install_app(cls.tmp / "app")
        cls.trip = cls.tmp / "photos"
        standard_trip(cls.trip)
        cls.listing = cls.tmp / "lists" / "trips.txt"
        cls.listing.parent.mkdir()
        cls.listing.write_text(f"{cls.trip} | trip\n", encoding="utf-8")
        cls.settings = cls.listing.parent / "library_settings.json"

    def serve(self, *extra):
        server = AlbumMaker(self.app, None, self.tmp / "work", "--library", self.listing, *extra,
                           default_template=True)
        server.start()
        self.addCleanup(server.stop)
        return server

    def test_pick_apply_remember_and_override(self):
        server = self.serve()
        page = server.text("/")
        self.assertIn('id="template-select"', page)                # we are the owner: localhost
        for name in site_templates.template_names():
            self.assertIn(f'value="{name}"', page)
        self.assertIn("tpl-cinema", page)                          # the default: cinema
        self.assertNotIn("tpl-journal", page)
        status, reply = server.post_json("/api/library-template", {"template": "journal"})
        self.assertEqual((status, reply["template"]), (200, "journal"))
        page = server.text("/")
        self.assertIn("tpl-journal", page)
        self.assertRegex(page, r'value="journal" [^>]*selected')
        self.assertIn(b".jr-hero", server.get("/assets/style.css")[2])
        self.assertIn("tpl-journal", server.text("/trip/"))        # the trip's own pages too
        import json
        self.assertEqual(json.loads(self.settings.read_text())["template"], "journal")
        server.stop()
        # a new start uses what was remembered ...
        again = self.serve()
        self.assertIn("tpl-journal", again.text("/"))
        again.stop()
        # ... unless --template says otherwise
        forced = self.serve("--template", "cinema")
        self.assertIn("tpl-cinema", forced.text("/"))

    def test_bad_requests_change_nothing(self):
        server = self.serve()
        before = self.settings.read_text() if self.settings.exists() else None
        for body in ({"template": "nope"}, {"template": 3}, {}):
            status, reply = server.post_json("/api/library-template", body)
            self.assertEqual(status, 400, body)
            self.assertFalse(reply["ok"])
        self.assertIn("tpl-journal" if before and "journal" in before else "tpl-cinema", server.text("/"))
        self.assertEqual(self.settings.read_text() if self.settings.exists() else None, before)

    def test_visitors_and_read_only_servers_see_the_picker_but_cannot_switch(self):
        owner = self.serve()
        self.assertNotRegex(owner.text("/"), r'id="template-select"[^>]*disabled')   # the owner can use it
        owner.stop()
        server = self.serve("--read-only")
        page = server.text("/")
        self.assertIn('id="template-select"', page)          # easy to find from any device ...
        self.assertRegex(page, r'id="template-select"[^>]*disabled')   # ... but locked
        self.assertIn("Only the owner can change this", page)
        status, reply = server.post_json("/api/library-template", {"template": "journal"})
        self.assertEqual(status, 403)

    def test_a_single_trip_has_no_template_to_pick(self):
        server = AlbumMaker(self.app, self.trip, self.tmp / "single", default_template=True)
        server.start()
        self.addCleanup(server.stop)
        status, reply = server.post_json("/api/library-template", {"template": "journal"})
        self.assertEqual(status, 400)


class DesktopAppSettingsTests(unittest.TestCase):
    def settings(self, template=None):
        s = default_settings()
        if template is not None:
            s["library"]["template"] = template
        return s

    def test_the_default_is_cinema_and_unknown_names_are_refused(self):
        self.assertEqual(default_settings()["library"]["template"], "cinema")
        with self.assertRaisesRegex(SettingsError, "library.template"):
            validate_settings(self.settings("fancy"))
        self.assertEqual(validate_settings(self.settings("journal"))["library"]["template"], "journal")

    def test_the_commands_pass_the_template_on(self):
        listing = Path(__import__("tempfile").mkdtemp()) / "trips.txt"
        listing.write_text("/tmp/x\n", encoding="utf-8")
        s = self.settings("journal")
        s["advanced"]["tile_cache"] = "/tmp/tiles"
        command = build_command("python", listing, "/tmp/out", s)
        self.assertEqual(command[command.index("--template") + 1], "journal")
        # the live server remembers the template picked on its library page instead
        self.assertNotIn("--template", server_command("python", listing, s))


if __name__ == "__main__":
    unittest.main()
