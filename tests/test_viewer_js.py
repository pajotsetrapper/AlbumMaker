"""The viewer's behaviour in a browser-like environment (Node + jsdom): editing
controls, links and the Back button, autoplay, and the maps with the real
Leaflet library.

These are optional: they are skipped, with a note, unless Node.js 18+ is installed
and `npm install` has been run once in tests/js. This module builds the photos,
starts the live server, builds and serves a static site, then runs each
JavaScript test file against them."""
import functools
import http.server
import json
import shutil
import subprocess
import threading
import unittest
from pathlib import Path

from .helpers import (HAVE_FFMPEG, QuietHTTPServer, ROOT, TempDirMixin, TripServer, install_app, make_gpx, make_jpeg,
                      make_video, run_script, standard_trip, write_json)

JS_DIR = Path(__file__).resolve().parent / "js"
NODE = shutil.which("node")
INITIAL_CONTENT = {"photos": {"a1.jpg": {"caption": "Sunrise over the canal", "favorite": True},
                              "sub/a3.jpg": {"hidden": True}}}


def _skip_reason():
    if not NODE:
        return "Node.js is not installed"
    if not (JS_DIR / "node_modules" / "jsdom").is_dir():
        return "run `npm install` in tests/js once to enable the browser tests"
    return None


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


class ViewerJsTests(TempDirMixin, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        reason = _skip_reason()
        if reason:
            raise unittest.SkipTest(reason)
        cls.tmp = cls.make_tmp()
        cls.photos = cls.tmp / "photos"
        standard_trip(cls.photos)
        # a GPS track for day 1 only, ending before sub/a3.jpg (11:00) so no photo moves
        make_gpx(cls.photos / "track.gpx", [[(f"2026-05-01T{8 + m // 60:02d}:{m % 60:02d}:00Z",
                                              round(50 + m / 150, 5), round(4 + (m % 7) / 1000, 5))
                                             for m in range(0, 151, 5)]])
        make_jpeg(cls.photos / "Caf\u00e9 #1 & more (final).jpg", "2026:05:02 13:00:00", color=(9, 9, 9))
        if HAVE_FFMPEG:
            make_video(cls.photos / "clip.mp4", "2026-05-02T12:00:00Z", seconds=4)

        # a vendor folder with the REAL Leaflet (from node_modules), so the map tests run real code
        leaflet = JS_DIR / "node_modules" / "leaflet" / "dist"
        cls.has_leaflet = (leaflet / "leaflet.js").is_file()
        vendor = None
        if cls.has_leaflet:
            vendor = cls.tmp / "vendor-src"
            shutil.copytree(leaflet, vendor / "leaflet", ignore=shutil.ignore_patterns("*.map", "leaflet-src*"))
            (vendor / "fonts").mkdir()
            (vendor / "fonts" / "fonts.css").write_text("/* fonts omitted in tests */")
            (vendor / "VERSIONS.txt").write_text("test copy")
        cls.app = install_app(cls.tmp / "app", with_vendor=vendor)

        write_json(cls.tmp / "content.json", INITIAL_CONTENT)
        cls.server = TripServer(cls.app, cls.photos, cls.tmp, "--tz-offset", "0").start()
        cls.addClassCleanup(cls.server.stop)

        static_content = write_json(cls.tmp / "static-content.json", INITIAL_CONTENT)
        site = cls.tmp / "site"
        built = run_script(cls.app, "generate_site.py", "-i", cls.photos, "-o", site, "-c", static_content,
                           "--tz-offset", "0")
        assert built.returncode == 0, built.stdout + built.stderr
        handler = functools.partial(_QuietHandler, directory=str(site))
        cls.static_httpd = QuietHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=cls.static_httpd.serve_forever, daemon=True).start()
        cls.addClassCleanup(cls.static_httpd.server_close)
        cls.addClassCleanup(cls.static_httpd.shutdown)

        # the same site again, built with the larger thumbnails
        large_site = cls.tmp / "site-large"
        built = run_script(cls.app, "generate_site.py", "-i", cls.photos, "-o", large_site,
                           "-c", cls.tmp / "static-content-large.json", "--tz-offset", "0",
                           "--large-thumb-size", "1200")
        assert built.returncode == 0, built.stdout + built.stderr
        cls.large_httpd = QuietHTTPServer(("127.0.0.1", 0), functools.partial(_QuietHandler, directory=str(large_site)))
        threading.Thread(target=cls.large_httpd.serve_forever, daemon=True).start()
        cls.addClassCleanup(cls.large_httpd.server_close)
        cls.addClassCleanup(cls.large_httpd.shutdown)

    def run_js(self, name):
        write_json(self.server.content, {**INITIAL_CONTENT, "_test_reset": name})     # a known starting point
        fixture = {"server": self.server.url,
                   "staticSite": f"http://127.0.0.1:{self.static_httpd.server_address[1]}",
                   "staticLargeSite": f"http://127.0.0.1:{self.large_httpd.server_address[1]}",
                   "contentFile": str(self.server.content), "initialContent": INITIAL_CONTENT,
                   "hasLeaflet": self.has_leaflet, "hasVideo": HAVE_FFMPEG,
                   "tileUrl": "https://tile.openstreetmap.org/{z}/{x}/{y}.png"}
        env = {"PATH": __import__("os").environ.get("PATH", ""), "TRIP_FIXTURE": json.dumps(fixture),
               "no_proxy": "127.0.0.1,localhost", "NO_PROXY": "127.0.0.1,localhost"}
        r = subprocess.run([NODE, "--test", str(JS_DIR / name)], capture_output=True, text=True,
                           env=env, cwd=str(JS_DIR), timeout=150)
        noise = ("Not implemented", "Could not load")
        shown = "\n".join(l for l in (r.stdout + r.stderr).splitlines() if not any(n in l for n in noise))
        self.assertEqual(r.returncode, 0, f"{name} failed:\n{shown[-6000:]}")

    def test_editing_controls(self):
        self.run_js("editing.test.js")

    def test_links_and_the_back_button(self):
        self.run_js("history.test.js")

    def test_autoplay(self):
        self.run_js("autoplay.test.js")

    def test_static_site_links_and_favorites(self):
        self.run_js("static_links.test.js")

    def test_escape_and_arrow_keys_on_day_pages(self):
        self.run_js("navigation.test.js")

    def test_size_controls(self):
        self.run_js("sizes.test.js")

    def test_sharper_thumbnails(self):
        self.run_js("sharp.test.js")

    def test_maps_with_real_leaflet(self):
        if not self.has_leaflet:
            self.skipTest("Leaflet is not installed (run npm install in tests/js)")
        self.run_js("maps.test.js")


if __name__ == "__main__":
    unittest.main()
