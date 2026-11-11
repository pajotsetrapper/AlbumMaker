# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Pieter Remi Coppens
"""Working without internet: downloading Leaflet and the fonts (against a fake
npm registry, so no real network is used), serving them, and the map-tile cache
(against a fake tile provider)."""
import base64
import hashlib
import http.server
import io
import os
import re
import socketserver
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

from albummaker import offline

from .helpers import (QuietHTTPServer, TempDirMixin, AlbumMaker, free_port, install_app, local_env, make_fake_vendor,
                      standard_trip, tc)

NO_PROXY = {"no_proxy": "127.0.0.1,localhost", "NO_PROXY": "127.0.0.1,localhost"}

SAMPLE_FONT_CSS = """/* {slug}-vietnamese-{w}-normal */
@font-face {{
  font-family: 'Sample';
  font-style: normal;
  font-display: swap;
  font-weight: {w};
  src: url(./files/{slug}-vietnamese-{w}-normal.woff2) format('woff2'), url(./files/{slug}-vietnamese-{w}-normal.woff) format('woff');
  unicode-range: U+0102-0103;
}}
/* {slug}-latin-ext-{w}-normal */
@font-face {{
  font-family: 'Sample';
  font-style: normal;
  font-display: swap;
  font-weight: {w};
  src: url(./files/{slug}-latin-ext-{w}-normal.woff2) format('woff2'), url(./files/{slug}-latin-ext-{w}-normal.woff) format('woff');
  unicode-range: U+0100-02AF;
}}
/* {slug}-latin-{w}-normal */
@font-face {{
  font-family: 'Sample';
  font-style: normal;
  font-display: swap;
  font-weight: {w};
  src: url(./files/{slug}-latin-{w}-normal.woff2) format('woff2'), url(./files/{slug}-latin-{w}-normal.woff) format('woff');
  unicode-range: U+0000-00FF;
}}
"""


def make_tgz(files: dict) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data in files.items():
            data = data if isinstance(data, bytes) else data.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def sri(data: bytes) -> str:
    return "sha512-" + base64.b64encode(hashlib.sha512(data).digest()).decode()


class FakeRegistry:
    """Serves the synthetic packages the way registry.npmjs.org serves tarballs."""

    def __init__(self):
        self.routes, self.port, self.served = {}, free_port(), []
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                outer.served.append(self.path)
                data = outer.routes.get(self.path)
                if data is None:
                    self.send_response(404); self.end_headers(); return
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.httpd = QuietHTTPServer(("127.0.0.1", self.port), Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}"

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def publish(self, name, version, files) -> tuple:
        tgz = make_tgz(files)
        self.routes[f"/{name}/-/{name.split('/')[-1]}-{version}.tgz"] = tgz
        return (name, version, sri(tgz))


def synthetic_packages(registry: FakeRegistry, extra_leaflet_members=None, tamper=None) -> dict:
    """The same four packages the real command downloads, in miniature."""
    leaflet_files = {"package/dist/leaflet.js": "/* leaflet */", "package/dist/leaflet.css": "/* css */",
                     "package/LICENSE": "BSD-2-Clause"}
    for img in ("layers.png", "layers-2x.png", "marker-icon.png", "marker-icon-2x.png", "marker-shadow.png"):
        leaflet_files[f"package/dist/images/{img}"] = "png"
    leaflet_files.update(extra_leaflet_members or {})
    packages = {"leaflet": registry.publish("leaflet", "1.9.4", leaflet_files)}
    for slug, weights in tc._VENDOR_FONT_WEIGHTS.items():
        files = {"package/LICENSE": "OFL-1.1"}
        for w in weights:
            files[f"package/{w}.css"] = SAMPLE_FONT_CSS.format(slug=slug, w=w)
            for subset in ("latin", "latin-ext", "vietnamese"):
                files[f"package/files/{slug}-{subset}-{w}-normal.woff2"] = f"{slug}{subset}{w}"
                files[f"package/files/{slug}-{subset}-{w}-normal.woff"] = "old format"
        packages[slug] = registry.publish(f"@fontsource/{slug}", "5.3.0", files)
    if tamper:                                                  # change a published file after its checksum was recorded
        name = packages[tamper][0]
        route = f"/{name}/-/{name.split('/')[-1]}-{packages[tamper][1]}.tgz"
        registry.routes[route] = registry.routes[route] + b"tampered"
    return packages


class RegistryTestCase(TempDirMixin, unittest.TestCase):
    def setUp(self):
        self.tmp = Path(__import__("tempfile").mkdtemp())
        self.addCleanup(__import__("shutil").rmtree, self.tmp, True)
        self.registry = FakeRegistry()
        self.addCleanup(self.registry.stop)
        env = mock.patch.dict(os.environ, NO_PROXY)
        env.start(); self.addCleanup(env.stop)

    def fetch(self, vendor, packages=None, **patches):
        packages = packages or synthetic_packages(self.registry)
        out, err = io.StringIO(), io.StringIO()
        # Patch the module that does the work.
        with mock.patch.object(offline, "_VENDOR_PACKAGES", packages), \
             mock.patch.object(offline, "_NPM_REGISTRY", self.registry.url), \
             mock.patch.object(offline, "_RETRY_PAUSE", 0), \
             mock.patch.multiple(offline, **patches) if patches else mock.patch.dict(os.environ, {}), \
             mock.patch("sys.stdout", out), mock.patch("sys.stderr", err):
            code = tc.fetch_offline_assets(vendor)
        return code, out.getvalue(), err.getvalue()


class FetchOfflineAssetsTests(RegistryTestCase):
    def test_a_successful_download_makes_a_complete_vendor_folder(self):
        vendor = self.tmp / "vendor"
        code, out, err = self.fetch(vendor)
        self.assertEqual(code, 0, err)
        # every package came from the fake registry: none from the real one
        self.assertEqual(len(self.registry.served), len(offline._VENDOR_PACKAGES))
        self.assertTrue(tc.vendor_ready(vendor))
        for rel in ("leaflet/leaflet.js", "leaflet/leaflet.css", "leaflet/images/layers.png",
                    "leaflet/images/marker-shadow.png", "fonts/fonts.css", "VERSIONS.txt",
                    "licenses/leaflet-LICENSE.txt", "licenses/fraunces-LICENSE.txt"):
            self.assertTrue((vendor / rel).is_file(), rel)
        self.assertIn("Saved", out)

    def test_fonts_css_has_only_the_wanted_character_sets_and_local_file_names(self):
        vendor = self.tmp / "vendor"
        self.fetch(vendor)
        css = (vendor / "fonts" / "fonts.css").read_text()
        weights = sum(len(w) for w in tc._VENDOR_FONT_WEIGHTS.values())
        self.assertEqual(css.count("@font-face"), weights * 2)              # latin + latin-ext for each weight
        self.assertNotIn("vietnamese", css)
        self.assertNotIn("./files/", css)
        self.assertNotIn(".woff)", css)                                     # only the modern format is kept
        files = set(re.findall(r"url\(([^)]+)\)", css))
        self.assertEqual(len(files), weights * 2)
        for name in files:
            self.assertTrue((vendor / "fonts" / name).is_file(), name)
        self.assertEqual(len(list((vendor / "fonts").glob("*.woff2"))), weights * 2)

    def test_only_named_files_are_taken_from_a_package(self):
        packages = synthetic_packages(self.registry, extra_leaflet_members={
            "package/../../evil.txt": "pwned", "package/dist/extra.js": "unwanted", "/abs/evil.txt": "pwned"})
        vendor = self.tmp / "sub" / "vendor"
        code, _, err = self.fetch(vendor, packages)
        self.assertEqual(code, 0, err)
        self.assertFalse((self.tmp / "evil.txt").exists())
        self.assertFalse((self.tmp / "sub" / "evil.txt").exists())
        self.assertFalse((vendor / "extra.js").exists() or (vendor / "leaflet" / "extra.js").exists())
        self.assertFalse(Path("/abs/evil.txt").exists())

    def test_a_download_that_does_not_match_its_checksum_is_refused_and_nothing_is_written(self):
        vendor = self.tmp / "vendor"
        packages = synthetic_packages(self.registry, tamper="fraunces")
        code, out, err = self.fetch(vendor, packages)
        self.assertEqual(code, 1)
        self.assertIn("checksum", err)
        self.assertIn("Nothing was changed", err)
        self.assertFalse(vendor.exists())

    def test_no_internet_gives_a_clear_message(self):
        vendor = self.tmp / "vendor"
        dead = free_port()
        code, out, err = self.fetch(vendor, _NPM_REGISTRY=f"http://127.0.0.1:{dead}")
        self.assertEqual(code, 1)
        self.assertIn("internet connection", err)
        self.assertFalse(vendor.exists())

    def test_a_package_missing_an_expected_file_is_refused(self):
        packages = synthetic_packages(self.registry)
        name, version, _ = packages["leaflet"]
        broken = {"package/dist/leaflet.js": "x", "package/LICENSE": "x"}            # no css, no images
        packages["leaflet"] = self.registry.publish(name, version, broken)
        code, _, err = self.fetch(self.tmp / "vendor", packages)
        self.assertEqual(code, 1)
        self.assertIn("doesn't contain", err)

    def test_an_interrupted_write_never_counts_as_ready_and_a_rerun_finishes_the_job(self):
        vendor = self.tmp / "vendor"
        real, calls = tc.atomic_write_bytes, {"n": 0}

        def flaky(path, data):
            calls["n"] += 1
            if calls["n"] == 12:
                raise OSError("disk full (simulated)")
            return real(path, data)

        code, _, err = self.fetch(vendor, atomic_write_bytes=flaky)
        self.assertEqual(code, 1)
        self.assertIn("disk full", err)
        self.assertGreater(len(list(vendor.rglob("*"))), 5)                 # part of it was written ...
        self.assertFalse(tc.vendor_ready(vendor))                           # ... but it must not be used
        code, _, err = self.fetch(vendor)
        self.assertEqual(code, 0, err)
        self.assertTrue(tc.vendor_ready(vendor))

    def test_a_failed_update_leaves_a_working_vendor_folder_untouched(self):
        vendor = self.tmp / "vendor"
        self.assertEqual(self.fetch(vendor)[0], 0)
        before = {p.relative_to(vendor): p.read_bytes() for p in vendor.rglob("*") if p.is_file()}
        code, _, _ = self.fetch(vendor, synthetic_packages(self.registry, tamper="leaflet"))
        self.assertEqual(code, 1)
        after = {p.relative_to(vendor): p.read_bytes() for p in vendor.rglob("*") if p.is_file()}
        self.assertEqual(before, after)
        self.assertTrue(tc.vendor_ready(vendor))

    def test_versions_file_records_what_was_saved(self):
        vendor = self.tmp / "vendor"
        self.fetch(vendor)
        text = (vendor / "VERSIONS.txt").read_text()
        for name in ("leaflet 1.9.4", "@fontsource/fraunces 5.3.0", "SHA-512", "Open Font License"):
            self.assertIn(name, text)

    def test_the_command_line_flag_works_without_an_input_folder_and_fails_cleanly_offline(self):
        app = install_app(self.tmp / "app")
        dead_proxy = f"http://127.0.0.1:{free_port()}"
        env = {**local_env(app / ".albummaker-data"), "https_proxy": dead_proxy, "HTTPS_PROXY": dead_proxy,
               "no_proxy": "", "NO_PROXY": ""}
        for script in ("generate_site.py", "album_server.py"):
            with self.subTest(script=script):
                r = subprocess.run([sys.executable, str(app / script), "--fetch-offline-assets"],
                                   capture_output=True, text=True, env=env, cwd=str(self.tmp), timeout=60)
                self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
                self.assertIn("Could not download", r.stderr)
                self.assertFalse((app / ".albummaker-data" / "offline-assets").exists())


@unittest.skipUnless(os.environ.get("TRIP_TEST_NETWORK") == "1",
                     "set TRIP_TEST_NETWORK=1 to check the real npm downloads against their pinned checksums")
class _Response:
    """A stand-in for urlopen's response that hands out `chunks` one by one."""
    def __init__(self, chunks, length=None):
        self.chunks = list(chunks)
        size = sum(len(c) for c in chunks) if length is None else length
        self.headers = {"Content-Length": str(size)}

    def read(self, _n=-1):
        return self.chunks.pop(0) if self.chunks else b""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class SlowNetworkTests(unittest.TestCase):
    """A stalling or trickling connection must not hang the download silently."""

    def run_download(self, responses, clock=None):
        out = io.StringIO()
        # (mock raises the exceptions in the list and returns everything else)
        with mock.patch.object(offline.urllib.request, "urlopen", side_effect=responses) as urlopen, \
                mock.patch.object(offline, "_RETRY_PAUSE", 0), mock.patch("sys.stdout", out):
            if clock is not None:
                with mock.patch.object(offline.time, "monotonic", side_effect=clock):
                    result = offline._download("https://registry.example/x.tgz")
            else:
                result = offline._download("https://registry.example/x.tgz")
        return result, out.getvalue(), urlopen

    def test_a_stalled_try_is_repeated_and_then_succeeds(self):
        data, out, urlopen = self.run_download([TimeoutError("timed out"), _Response([b"ab", b"cd"])])
        self.assertEqual(data, b"abcd")
        self.assertEqual(urlopen.call_count, 2)
        self.assertIn("trying again (2 of 3)", out)
        self.assertEqual(urlopen.call_args.kwargs["timeout"], offline._DOWNLOAD_TIMEOUT)

    def test_it_gives_up_after_the_last_try(self):
        with self.assertRaises(OSError):
            self.run_download([OSError("reset")] * offline._DOWNLOAD_ATTEMPTS)

    def test_a_trickling_download_is_stopped_at_the_deadline(self):
        # every chunk "takes" 50 seconds; the third crosses the 120-second deadline
        ticks = iter(range(0, 10_000, 50))
        clock = lambda: next(ticks)
        trickle = lambda: _Response([b"x"] * 10)
        with self.assertRaises(TimeoutError):
            self.run_download([trickle() for _ in range(offline._DOWNLOAD_ATTEMPTS)], clock=clock)

    def test_progress_is_reported_while_it_downloads(self):
        ticks = iter(range(0, 1000, 3))
        data, out, _ = self.run_download([_Response([b"x" * 2048] * 6)], clock=lambda: next(ticks))
        self.assertEqual(len(data), 6 * 2048)
        self.assertIn("KB", out)

    def test_a_download_cut_short_is_retried(self):
        data, out, urlopen = self.run_download([_Response([b"ab"], length=10), _Response([b"abcd"])])
        self.assertEqual(data, b"abcd")
        self.assertIn("connection closed", out)


class RealDownloadTests(TempDirMixin, unittest.TestCase):
    def test_the_pinned_packages_still_download_and_verify(self):
        vendor = self.make_tmp() / "vendor"
        self.assertEqual(tc.fetch_offline_assets(vendor), 0)
        self.assertTrue(tc.vendor_ready(vendor))
        self.assertEqual(len(list((vendor / "fonts").glob("*.woff2"))), 16)
        self.assertIn(b"Leaflet 1.9.4", (vendor / "leaflet" / "leaflet.js").read_bytes()[:400])


class FontFaceParsingTests(unittest.TestCase):
    def test_keeps_only_the_wanted_subsets_and_rewrites_the_source(self):
        css, files = tc._font_faces(SAMPLE_FONT_CSS.format(slug="demo", w=500), "demo", 500)
        self.assertEqual(files, ["demo-latin-ext-500-normal.woff2", "demo-latin-500-normal.woff2"])
        self.assertEqual(css.count("@font-face"), 2)
        self.assertIn("src: url(demo-latin-500-normal.woff2) format('woff2');", css)
        self.assertNotIn("vietnamese", css)

    def test_an_unexpected_stylesheet_is_an_error_not_a_silent_gap(self):
        with self.assertRaises(RuntimeError):
            tc._font_faces("/* demo-latin-500-normal */ @font-face { }\n", "demo", 500)        # latin-ext missing
        with self.assertRaises(RuntimeError):
            tc._font_faces("nothing useful", "demo", 500)

    def test_vendor_ready_needs_all_the_pieces(self):
        tmp = Path(__import__("tempfile").mkdtemp())
        self.addCleanup(__import__("shutil").rmtree, tmp, True)
        vendor = make_fake_vendor(tmp / "v")
        self.assertTrue(tc.vendor_ready(vendor))
        for rel in ("leaflet/leaflet.js", "leaflet/leaflet.css", "fonts/fonts.css", "VERSIONS.txt"):
            with self.subTest(missing=rel):
                (vendor / rel).rename(vendor / (rel + ".gone"))
                self.assertFalse(tc.vendor_ready(vendor))
                (vendor / (rel + ".gone")).rename(vendor / rel)
        self.assertFalse(tc.vendor_ready(tmp / "does-not-exist"))
class VendorServingTests(TempDirMixin, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = cls.make_tmp()
        cls.photos = cls.tmp / "photos"
        standard_trip(cls.photos)
        cls.vendor = make_fake_vendor(cls.tmp / "v")
        cls.app_with = install_app(cls.tmp / "with", with_vendor=cls.vendor)
        cls.app_without = install_app(cls.tmp / "without")
        cls.server = AlbumMaker(cls.app_with, cls.photos, cls.tmp / "w1").start()
        cls.bare = AlbumMaker(cls.app_without, cls.photos, cls.tmp / "w2").start()
        cls.addClassCleanup(cls.server.stop)
        cls.addClassCleanup(cls.bare.stop)

    EXTERNAL = re.compile(r"googleapis|gstatic|cdnjs|cloudflare|unpkg|jsdelivr")

    def test_pages_use_the_local_copies_and_ask_the_internet_for_nothing(self):
        for path in ("/", "/day/2026-05-01"):
            html = self.server.text(path)
            self.assertIsNone(self.EXTERNAL.search(html), path)
            for local in ("/vendor/fonts/fonts.css", "/vendor/leaflet/leaflet.css", "/vendor/leaflet/leaflet.js"):
                self.assertIn(local, html)

    def test_files_are_served_with_the_right_types(self):
        for path, mime in [("/vendor/leaflet/leaflet.js", "application/javascript"), ("/vendor/leaflet/leaflet.css", "text/css"),
                           ("/vendor/fonts/fonts.css", "text/css"), ("/vendor/fonts/f.woff2", "font/woff2"),
                           ("/vendor/leaflet/images/layers.png", "image/png"), ("/vendor/VERSIONS.txt", "text/plain; charset=utf-8")]:
            with self.subTest(path=path):
                status, headers, body = self.server.get(path)
                self.assertEqual((status, headers["content-type"]), (200, mime))
                self.assertEqual(body, (self.vendor / path[len("/vendor/"):]).read_bytes())

    def test_everything_the_stylesheets_refer_to_is_reachable(self):
        for css_path, base in (("/vendor/fonts/fonts.css", "/vendor/fonts/"), ("/vendor/leaflet/leaflet.css", "/vendor/leaflet/")):
            css = self.server.text(css_path)
            for ref in re.findall(r"url\(([^)]+)\)", css):
                self.assertEqual(self.server.get(base + ref)[0], 200, ref)

    def test_nothing_outside_the_vendor_folder_can_be_read(self):
        attacks = ["/vendor/../album_server.py", "/vendor/%2e%2e/album_server.py", "/vendor/%252e%252e/album_server.py",
                   "/vendor/leaflet/../../albummaker/server.py", "/vendor/..%2falbum_server.py", "/vendor/%2e%2e%2falbum_server.py",
                   "/vendor/leaflet/", "/vendor/", "/vendor", "/vendor/leaflet/leaflet.js/..", "/vendor/fonts/..\\..\\album_server.py",
                   "/vendor//etc/passwd", "/vendor/leaflet/nonexistent.js", "/vendor/%00", "/vendor/./leaflet/leaflet.js"]
        for attack in attacks:
            with self.subTest(attack=attack):
                status, _, body = self.server.get(attack)
                self.assertIn(status, (400, 404))
                self.assertNotIn(b"import", body[:300])

    def test_only_known_file_types_are_served(self):
        (self.vendor / "secret.py").write_text("print('x')")
        try:
            self.assertEqual(self.server.get("/vendor/secret.py")[0], 404)
        finally:
            (self.vendor / "secret.py").unlink()

    def test_without_a_vendor_folder_pages_use_the_internet_copies_and_the_route_is_closed(self):
        html = self.bare.text("/day/2026-05-01")
        self.assertIn("fonts.googleapis.com", html)
        self.assertIn("cdnjs.cloudflare.com", html)
        self.assertNotIn("/vendor/", html)
        self.assertEqual(self.bare.get("/vendor/leaflet/leaflet.js")[0], 404)

    def test_startup_messages_say_which_mode_is_active(self):
        self.assertIn("using the local copies", self.server.log())
        self.assertIn("--fetch-offline-assets", self.bare.log())


class FakeTileProvider:
    """A tile server we control: counts requests and can misbehave on demand."""
    PNG = None

    def __init__(self):
        if FakeTileProvider.PNG is None:
            buf = io.BytesIO()
            Image.new("RGB", (256, 256), (200, 190, 170)).save(buf, "PNG")
            FakeTileProvider.PNG = buf.getvalue()
        self.port, self.hits, self.agents, self.mode = free_port(), [], [], "ok"
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                outer.hits.append(self.path)
                outer.agents.append(self.headers.get("User-Agent"))
                if outer.mode == "hang":
                    time.sleep(15)
                if outer.mode == "forbid":
                    self.send_response(403); self.end_headers(); return
                body, ctype = ((b"<html>please log in to the wifi</html>", "text/html") if outer.mode == "html"
                               else (FakeTileProvider.PNG, "image/png"))
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.httpd = QuietHTTPServer(("127.0.0.1", self.port), Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    @property
    def template(self):
        return f"http://127.0.0.1:{self.port}/{{z}}/{{x}}/{{y}}.png"

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class TileCacheTestCase(TempDirMixin, unittest.TestCase):
    mode = "ok"

    @classmethod
    def setUpClass(cls):
        cls.tmp = cls.make_tmp()
        cls.app = install_app(cls.tmp / "app")
        cls.photos = cls.tmp / "photos"
        standard_trip(cls.photos)
        cls.cache = cls.tmp / "tile_cache"
        cls.upstream = FakeTileProvider()
        cls.upstream.mode = cls.mode
        cls.addClassCleanup(cls.upstream.stop)
        cls.server = AlbumMaker(cls.app, cls.photos, cls.tmp, "--tile-cache", cls.cache,
                                "--tile-url", cls.upstream.template).start()
        cls.addClassCleanup(cls.server.stop)

    def tile(self, z, x, y):
        return self.server.get(f"/tiles/{z}/{x}/{y}.png")


class TileCacheTests(TileCacheTestCase):
    def test_first_request_downloads_second_comes_from_disk(self):
        status, headers, body = self.tile(3, 4, 2)
        self.assertEqual((status, headers["content-type"], body), (200, "image/png", FakeTileProvider.PNG))
        self.assertEqual((self.cache / "3" / "4" / "2.png").read_bytes(), FakeTileProvider.PNG)
        hits = self.upstream.hits.count("/3/4/2.png")
        self.assertEqual(hits, 1)
        self.assertEqual(self.tile(3, 4, 2)[0], 200)
        self.assertEqual(self.upstream.hits.count("/3/4/2.png"), hits)

    def test_the_provider_is_told_who_is_asking(self):
        self.tile(2, 1, 1)
        self.assertTrue(all(a.startswith("AlbumMaker/") for a in self.upstream.agents))

    def test_simultaneous_requests_for_one_new_tile_download_it_once(self):
        results = []
        threads = [threading.Thread(target=lambda: results.append(self.tile(5, 10, 11)[0])) for _ in range(12)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(results, [200] * 12)
        self.assertEqual(self.upstream.hits.count("/5/10/11.png"), 1)

    def test_the_startup_message_admits_the_tile_cache_writes_to_disk(self):
        log = self.server.log()
        self.assertIn("It writes to disk only", log)
        self.assertIn(f"and the map tile cache in {self.cache}", log)

    def test_pages_ask_this_server_for_tiles(self):
        html = self.server.text("/day/2026-05-01")
        self.assertIn('window.TILE_URL = "/tiles/{z}/{x}/{y}.png"', html)

    def test_a_tile_older_than_a_week_is_refreshed(self):
        self.tile(4, 5, 5)
        path = self.cache / "4" / "5" / "5.png"
        old = time.time() - 8 * 86400
        os.utime(path, (old, old))
        hits = self.upstream.hits.count("/4/5/5.png")
        self.assertEqual(self.tile(4, 5, 5)[0], 200)
        self.assertEqual(self.upstream.hits.count("/4/5/5.png"), hits + 1)
        self.assertGreater(path.stat().st_mtime, old + 86400)

    def test_impossible_tiles_and_paths_are_404(self):
        for path in ("/tiles/25/0/0.png", "/tiles/3/9/0.png", "/tiles/3/0/9.png", "/tiles/3/0/0.jpg",
                     "/tiles/3/../../x.png", "/tiles/3/0/0.png/extra", "/tiles/-1/0/0.png", "/tiles/a/b/c.png", "/tiles/"):
            with self.subTest(path=path):
                self.assertEqual(self.server.get(path)[0], 404)
        self.assertFalse(any("x.png" in h for h in self.upstream.hits))

    def test_the_cache_stays_inside_its_folder(self):
        self.tile(6, 7, 7)
        files = [p for p in self.cache.rglob("*") if p.is_file()]
        self.assertTrue(files)
        for p in files:
            self.assertIn(self.cache, p.parents)


class TileCacheOfflineTests(TileCacheTestCase):
    def test_what_was_cached_still_works_with_no_internet_and_the_rest_fails_fast(self):
        self.assertEqual(self.tile(5, 1, 1)[0], 200)                     # cached while "online"
        old = time.time() - 9 * 86400
        os.utime(self.cache / "5" / "1" / "1.png", (old, old))           # even a stale copy is better than nothing
        self.upstream.stop()                                             # the internet goes away
        status, _, body = self.tile(5, 1, 1)
        self.assertEqual((status, body), (200, FakeTileProvider.PNG))
        started = time.time()
        codes = [self.tile(6, i, i)[0] for i in range(20)]
        elapsed = time.time() - started
        self.assertEqual(set(codes), {404})
        self.assertLess(elapsed, 3, f"20 uncached tiles took {elapsed:.1f}s: each one must not wait for a timeout")
        self.assertEqual(self.server.get("/day/2026-05-01")[0], 200)     # the pages themselves are unaffected


class TileProviderHtmlTests(TileCacheTestCase):
    mode = "html"

    def test_a_login_page_in_place_of_a_tile_is_never_cached(self):
        self.assertEqual(self.tile(4, 1, 1)[0], 404)
        self.assertFalse((self.cache / "4" / "1" / "1.png").exists())


class TileProviderRefusalTests(TileCacheTestCase):
    mode = "forbid"

    def test_after_a_refusal_the_server_stops_asking_for_a_while(self):
        self.assertEqual(self.tile(4, 2, 2)[0], 404)
        asked = len(self.upstream.hits)
        for i in range(5):
            self.tile(4, 3, i)
        self.assertEqual(asked, 1)
        self.assertEqual(len(self.upstream.hits), 1)


class TileProviderHangTests(TileCacheTestCase):
    mode = "hang"

    def test_a_network_that_hangs_makes_everything_give_up_together(self):
        results, started = [], time.time()
        threads = [threading.Thread(target=lambda i=i: results.append(self.tile(7, i, 1)[0])) for i in range(12)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        elapsed = time.time() - started
        self.assertEqual(set(results), {404})
        self.assertLess(elapsed, 10, f"took {elapsed:.1f}s: 12 tiles must not each wait for their own timeout")


class TileConfigurationTests(TempDirMixin, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = cls.make_tmp()
        cls.app = install_app(cls.tmp / "app")
        cls.photos = cls.tmp / "photos"
        standard_trip(cls.photos)

    def run_server_cli(self, *args):
        return subprocess.run([sys.executable, str(self.app / "album_server.py"), "-i", str(self.photos), *map(str, args)],
                              capture_output=True, text=True, timeout=30, cwd=str(self.tmp), env=local_env())

    def test_default_maps_use_openstreetmap_directly_and_the_tile_route_is_closed(self):
        server = AlbumMaker(self.app, self.photos, self.tmp / "d").start()
        self.addCleanup(server.stop)
        self.assertIn('window.TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"', server.text("/day/2026-05-01"))
        self.assertEqual(server.get("/tiles/3/4/2.png")[0], 404)

    def test_a_custom_provider_without_a_cache_is_used_directly_by_the_pages(self):
        server = AlbumMaker(self.app, self.photos, self.tmp / "c", "--tile-url", "https://tiles.example.com/{z}/{x}/{y}.png").start()
        self.addCleanup(server.stop)
        self.assertIn('window.TILE_URL = "https://tiles.example.com/{z}/{x}/{y}.png"', server.text("/day/2026-05-01"))
        self.assertEqual(server.get("/tiles/3/4/2.png")[0], 404)

    def test_bad_tile_urls_are_rejected_before_anything_starts(self):
        for bad in ("https://x.example/tiles.png", "https://x.example/{z}/{x}.png", "file:///etc/passwd?{z}{x}{y}",
                    "ftp://x.example/{z}/{x}/{y}.png", "tiles/{z}/{x}/{y}.png"):
            with self.subTest(bad=bad):
                r = self.run_server_cli("--tile-url", bad)
                self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
                self.assertIn("--tile-url", r.stderr)

    def test_an_unusable_cache_folder_is_reported(self):
        in_the_way = self.tmp / "a-file"
        in_the_way.write_text("not a folder")
        r = self.run_server_cli("--tile-cache", in_the_way / "cache")
        self.assertEqual(r.returncode, 2)
        self.assertIn("--tile-cache", r.stderr)


if __name__ == "__main__":
    unittest.main()
