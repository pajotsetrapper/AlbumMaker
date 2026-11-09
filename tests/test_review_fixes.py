# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Pieter Remi Coppens
"""Tests for the fixes from the architecture review: hidden photos and their
duplicates, output-name claims, the size-limited caches and render limiter,
editor authentication, the socket timeout, stable URL keys, the content-file
location and --read-only, and the zipapp layout.

    python -m unittest tests.test_review_fixes
"""

from __future__ import annotations

import http.client
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from argparse import Namespace
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PIL import Image  # noqa: E402

from albummaker import build, content, imaging, scan, server, util  # noqa: E402

HAS_FFMPEG = shutil.which("ffmpeg") is not None


def make_jpeg(path: Path, day: int, hour: int = 12, color=(200, 100, 50), size=(640, 480)):
    path.parent.mkdir(parents=True, exist_ok=True)
    im = Image.new("RGB", size, color)
    ex = Image.Exif()
    stamp = f"2026:05:{day:02d} {hour:02d}:00:00"
    ex[306] = stamp
    ex.get_ifd(0x8769)[36867] = stamp
    im.save(path, "JPEG", exif=ex.tobytes())


class TempDirTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="albummaker-test-"))
        self.photos = self.tmp / "photos"
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def write_content(self, data: dict, path: Path = None):
        path = path or self.photos / "trip_content.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return path


# --------------------------------------------------------------------------
# Static build
# --------------------------------------------------------------------------

class StaticHiddenDuplicateTests(TempDirTest):
    """Hiding a photo must never publish an identical copy of it."""

    def setUp(self):
        super().setUp()
        make_jpeg(self.photos / "a" / "secret.jpg", 1, color=(255, 0, 0))
        (self.photos / "b").mkdir()
        shutil.copy(self.photos / "a" / "secret.jpg", self.photos / "b" / "copy.jpg")
        make_jpeg(self.photos / "a" / "ok.jpg", 1, color=(0, 255, 0))
        self.out = self.tmp / "site"

    def run_build(self):
        with mock.patch("sys.stdout"), mock.patch("sys.stderr"):
            build.main(["-i", str(self.photos), "-o", str(self.out), "--workers", "1"])

    def media(self):
        return sorted(p.name for p in (self.out / "media").iterdir())

    def test_hiding_the_kept_file_drops_its_copy(self):
        self.write_content({"photos": {"a/secret.jpg": {"hidden": True}}})
        self.run_build()
        self.assertEqual(self.media(), ["ok.jpg"])

    def test_hiding_the_skipped_copy_drops_the_kept_file(self):
        self.write_content({"photos": {"b/copy.jpg": {"hidden": True}}})
        self.run_build()
        self.assertEqual(self.media(), ["ok.jpg"])

    def test_unhiding_brings_it_back_and_rerun_is_incremental(self):
        self.write_content({"photos": {"a/secret.jpg": {"hidden": True}}})
        self.run_build()
        self.write_content({"photos": {}})
        self.run_build()
        self.assertEqual(self.media(), ["ok.jpg", "secret.jpg"])
        manifest = json.loads((self.out / scan.SITE_MARKER).read_text())
        self.assertIn("meta", manifest["files"]["a/ok.jpg"])   # metadata cached for the next run


class RebuildCleanupTests(TempDirTest):
    """What a rebuild deletes, and what it must not."""

    def setUp(self):
        super().setUp()
        make_jpeg(self.photos / "IMG_1.JPG", 1, color=(10, 20, 30))
        make_jpeg(self.photos / "IMG_2.jpg", 1, hour=13, color=(40, 50, 60))
        self.out = self.tmp / "site"

    def run_build(self, *extra):
        err = __import__("io").StringIO()
        with mock.patch("sys.stdout"), mock.patch("sys.stderr", err):
            build.main(["-i", str(self.photos), "-o", str(self.out), "--workers", "1", *extra])
        return err.getvalue()

    def media(self):
        return sorted(p.name for p in (self.out / "media").iterdir())

    def test_renaming_only_the_letter_case_keeps_the_output(self):
        self.run_build()
        os.rename(self.photos / "IMG_1.JPG", self.photos / "IMG_1.jpg")
        self.run_build()
        self.assertEqual(self.media(), ["IMG_1.jpg", "IMG_2.jpg"])          # present, with the new spelling
        page = (self.out / "days" / "2026-05-01.html").read_text(encoding="utf-8")
        self.assertIn("../media/IMG_1.jpg", page)

    def test_without_a_build_record_leftovers_are_reported(self):
        self.run_build()
        (self.out / scan.SITE_MARKER).unlink()
        self.write_content({"photos": {"IMG_2.jpg": {"hidden": True}}})
        err = self.run_build()
        self.assertIn("not part of this build", err)
        self.assertIn("IMG_2", err)
        self.assertIn("IMG_2.jpg", self.media(), "nothing is deleted without a record to go by")
        err = self.run_build()                                              # with a record again: quiet
        self.assertNotIn("not part of this build", err)

    def test_a_file_that_cannot_be_read_this_time_keeps_its_outputs(self):
        # e.g. a network or USB drive that drops out for a moment during the build
        self.run_build()
        before = self.media()
        real = build.drop_hidden_groups

        def then_unreadable(*a, **kw):
            groups = real(*a, **kw)
            os.rename(self.photos / "IMG_2.jpg", self.tmp / "away.jpg")     # gone right after the scan
            return groups
        with mock.patch.object(build, "drop_hidden_groups", then_unreadable):
            self.run_build()
        self.assertEqual(self.media(), before, "its copy was not deleted")
        self.assertTrue(any(p.name.startswith("IMG_2") for p in (self.out / "thumbs").iterdir()))
        record = json.loads((self.out / scan.SITE_MARKER).read_text())
        self.assertIn("IMG_2.jpg", record["files"], "still recorded, so a later run can clean up or reuse")
        os.rename(self.tmp / "away.jpg", self.photos / "IMG_2.jpg")          # back again: nothing to redo
        self.run_build()
        self.assertEqual(self.media(), before)

    def test_a_damaged_record_for_an_unreadable_file_does_not_crash_the_build(self):
        self.run_build()
        path = self.out / scan.SITE_MARKER
        record = json.loads(path.read_text())
        del record["files"]["IMG_2.jpg"]["outputs"]                         # hand-edited, say
        path.write_text(json.dumps(record))
        real = build.drop_hidden_groups

        def then_unreadable(*a, **kw):
            groups = real(*a, **kw)
            os.rename(self.photos / "IMG_2.jpg", self.tmp / "away.jpg")
            return groups
        with mock.patch.object(build, "drop_hidden_groups", then_unreadable):
            self.run_build()                                                 # no KeyError
        self.assertIn("IMG_1.JPG", json.loads(path.read_text())["files"])

    def test_files_written_by_this_run_are_not_reported_as_leftovers(self):
        # e.g. the thumbnail of a file whose processing failed later on
        (self.out / "thumbs").mkdir(parents=True)
        old = self.out / "thumbs" / "old.jpg"
        old.write_bytes(b"x")
        time.sleep(0.05)
        since = time.time()
        (self.out / "thumbs" / "new.jpg").write_bytes(b"x")
        err = __import__("io").StringIO()
        with mock.patch("sys.stderr", err):
            build.report_unrecorded_outputs(self.out, set(), since)
        self.assertIn("1 file(s)", err.getvalue())
        self.assertIn("thumbs/old.jpg", err.getvalue())

    @unittest.skipUnless(sys.platform in ("darwin", "win32"), "needs a disk that ignores letter case")
    def test_a_differently_spelled_output_is_not_reported_as_a_leftover(self):
        self.run_build()
        (self.out / scan.SITE_MARKER).unlink()
        os.rename(self.photos / "IMG_1.JPG", self.photos / "IMG_1.jpg")      # the output keeps its old spelling
        err = self.run_build()
        self.assertNotIn("not part of this build", err)

    def test_a_crashed_worker_stops_the_build_before_anything_is_deleted(self):
        from concurrent.futures import Future
        from concurrent.futures.process import BrokenProcessPool
        self.run_build()
        record = (self.out / scan.SITE_MARKER).read_bytes()
        before = self.media()

        class CrashedPool:
            def __init__(self, *a, **kw):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def submit(self, *a, **kw):
                f = Future()
                f.set_exception(BrokenProcessPool("a worker died"))
                return f
        class CrashedWhileSubmitting(CrashedPool):
            calls = 0

            def submit(self, *a, **kw):
                type(self).calls += 1
                if type(self).calls > 1:                # the first job killed its worker
                    raise BrokenProcessPool("a worker died")
                return Future()
        for pool in (CrashedPool, CrashedWhileSubmitting):
            with self.subTest(pool=pool.__name__):
                with mock.patch.object(build, "ProcessPoolExecutor", pool):
                    with self.assertRaises(SystemExit) as stopped:
                        self.run_build("--force")
                self.assertIn("worker process stopped", str(stopped.exception.code))
                self.assertEqual(self.media(), before)
                self.assertEqual((self.out / scan.SITE_MARKER).read_bytes(), record)


class OutputNameTests(unittest.TestCase):
    def test_display_name_of_a_jpeg_is_reserved(self):
        a, b = Path("/p/IMG_1.jpg"), Path("/p/x/IMG_1_display.jpg")
        bases = build.compute_output_bases({a: "IMG_1.jpg", b: "x/IMG_1_display.jpg"}, {})
        self.assertEqual(bases[a], "IMG_1")
        self.assertNotEqual(bases[b].lower(), "img_1_display")

    def test_previous_names_that_now_clash_are_not_reused(self):
        a, b = Path("/p/IMG_1.jpg"), Path("/p/IMG_1_display.jpg")
        previous = {"IMG_1_display.jpg": {"base": "IMG_1_display"}, "IMG_1.jpg": {"base": "IMG_1"}}
        bases = build.compute_output_bases({a: "IMG_1.jpg", b: "IMG_1_display.jpg"}, previous)
        claims = [build._claims(p, bases[p]) for p in (a, b)]
        self.assertFalse(claims[0] & claims[1])


class HiddenLivePhotoRuleTests(TempDirTest):
    """The static build drops a hidden photo's clip only when it really is a
    Live Photo clip, the same rule the live server's pairing uses."""

    def groups(self):
        return scan.no_dedupe_groups(scan.collect_candidates(self.photos))

    def test_long_video_with_the_same_name_is_kept(self):
        make_jpeg(self.photos / "IMG_9.jpg", 1)
        (self.photos / "IMG_9.mov").write_bytes(b"x")
        kept = scan.drop_hidden_groups(self.groups(), self.photos, {"IMG_9.jpg"}, True,
                                       lambda p: {"duration_s": 60.0})
        self.assertEqual([p.name for p, _, _ in kept], ["IMG_9.mov"])

    def test_live_clip_goes_with_its_photo(self):
        make_jpeg(self.photos / "IMG_9.jpg", 1)
        (self.photos / "IMG_9.mov").write_bytes(b"x")
        kept = scan.drop_hidden_groups(self.groups(), self.photos, {"IMG_9.jpg"}, True,
                                       lambda p: {"duration_s": 2.0})
        self.assertEqual(kept, [])

    def test_the_clip_goes_even_when_the_copy_kept_is_in_another_folder(self):
        make_jpeg(self.photos / "A" / "IMG_6.jpg", 1)
        (self.photos / "0").mkdir()
        for folder in ("A", "0"):                      # identical clips; dedupe keeps the one in 0/
            (self.photos / folder / "IMG_6.MOV").write_bytes(b"same clip")
        with mock.patch("sys.stdout"), mock.patch("sys.stderr"):
            groups = scan.dedupe_groups(scan.collect_candidates(self.photos))
            kept = scan.drop_hidden_groups(groups, self.photos, {"A/IMG_6.jpg"}, True,
                                           lambda p: {"duration_s": 2.0})
        self.assertEqual(kept, [])

    def test_a_clip_that_cannot_be_read_is_left_out_rather_than_crashing_the_build(self):
        make_jpeg(self.photos / "IMG_9.jpg", 1)
        (self.photos / "IMG_9.mov").write_bytes(b"x")

        def unreadable(path):
            raise ValueError("ffprobe could not read this file")
        with mock.patch("sys.stdout"):
            kept = scan.drop_hidden_groups(self.groups(), self.photos, {"IMG_9.jpg"}, True, unreadable)
        self.assertEqual(kept, [])

    @staticmethod
    def item(path, kind, duplicates=()):
        from datetime import datetime
        from albummaker.model import MediaItem
        return MediaItem(path=Path(path), media_type=kind, dt=datetime(2026, 5, 1), dt_is_fallback=False,
                         lat=None, lon=None, is_live_clip=(kind == "video"), duplicates=[Path(d) for d in duplicates])

    def pair(self, items):
        with mock.patch("sys.stdout"):
            return scan.pair_live_photos(items)

    def test_pairing_finds_a_clip_whose_kept_copy_is_in_another_folder(self):
        # the live server pairs this way too, so it hides such a clip along with
        # its hidden photo, just as the static build leaves it out
        photo = self.item("/p/a/IMG_5.jpg", "image")
        clip = self.item("/p/b/IMG_5.MOV", "video", duplicates=["/p/a/IMG_5.MOV"])
        self.assertEqual(self.pair([photo, clip]), [photo])
        self.assertIs(photo.live_clip, clip)

    def test_pairing_finds_a_photo_whose_kept_copy_is_in_another_folder(self):
        photo = self.item("/p/a/IMG_7.jpg", "image", duplicates=["/p/b/IMG_7.jpg"])
        clip = self.item("/p/b/IMG_7.MOV", "video")
        self.assertEqual(self.pair([photo, clip]), [photo])
        self.assertIs(photo.live_clip, clip)

    def test_a_photo_claims_its_own_folder_before_a_copy_of_another_photo_does(self):
        # A/IMG_1.JPG has a copy in B, but B has its own IMG_1.HEIC: each clip belongs in its own folder
        jpg = self.item("/p/A/IMG_1.JPG", "image", duplicates=["/p/B/IMG_1.JPG"])
        heic = self.item("/p/B/IMG_1.HEIC", "image")
        clip_a, clip_b = self.item("/p/A/IMG_1.MOV", "video"), self.item("/p/B/IMG_1.MOV", "video")
        self.assertEqual(self.pair([jpg, heic, clip_a, clip_b]), [jpg, heic])
        self.assertIs(jpg.live_clip, clip_a)
        self.assertIs(heic.live_clip, clip_b)

    def test_a_photo_gets_one_clip_and_a_second_stays_a_video(self):
        photo = self.item("/p/a/IMG_8.jpg", "image", duplicates=["/p/b/IMG_8.jpg"])
        first, second = self.item("/p/a/IMG_8.MOV", "video"), self.item("/p/b/IMG_8.MOV", "video")
        self.assertEqual(self.pair([photo, first, second]), [photo, second])
        self.assertIs(photo.live_clip, first)

    def test_no_live_photos_keeps_the_clip(self):
        make_jpeg(self.photos / "IMG_9.jpg", 1)
        (self.photos / "IMG_9.mov").write_bytes(b"x")
        kept = scan.drop_hidden_groups(self.groups(), self.photos, {"IMG_9.jpg"}, False,
                                       lambda p: {"duration_s": 2.0})
        self.assertEqual([p.name for p, _, _ in kept], ["IMG_9.mov"])


class ImagingTests(TempDirTest):
    def test_thumbnails_shrink_in_place_largest_first(self):
        make_jpeg(self.photos / "big.jpg", 1, size=(3000, 2000))
        out = imaging.image_thumbnails(self.photos / "big.jpg", [400, 1600])
        sizes = {k: Image.open(__import__("io").BytesIO(v)).size for k, v in out.items()}
        self.assertEqual(sizes, {1600: (1600, 1067), 400: (400, 267)})

    @unittest.skipUnless(HAS_FFMPEG, "needs ffmpeg")
    def test_video_name_starting_with_dash(self):
        self.photos.mkdir(parents=True)
        clip = self.photos / "-clip.mp4"
        subprocess.run(["ffmpeg", "-v", "quiet", "-f", "lavfi", "-i", "testsrc=duration=1:size=160x120",
                        "file:" + str(clip)], check=True)
        cwd = os.getcwd()
        os.chdir(self.photos)
        try:
            thumbs, placeholder = imaging.video_thumbnails(Path("-clip.mp4"), [100])
        finally:
            os.chdir(cwd)
        self.assertFalse(placeholder)


class AtomicWriteTests(TempDirTest):
    def test_symlink_is_kept_and_target_updated(self):
        self.photos.mkdir()
        real = self.tmp / "real.json"
        real.write_text("{}")
        link = self.photos / "trip_content.json"
        try:
            link.symlink_to(real)
        except OSError as e:
            if sys.platform == "win32" and getattr(e, "winerror", None) == 1314:
                self.skipTest("creating symlinks requires Windows Developer Mode or elevated privileges")
            raise
        util.atomic_write_text(link, '{"a": 1}')
        self.assertTrue(link.is_symlink())
        self.assertEqual(real.read_text(), '{"a": 1}')

    def test_parallel_writes_to_one_file_never_collide(self):
        self.photos.mkdir()
        target = self.photos / "f.bin"
        errors = []

        def write(n):
            try:
                for _ in range(20):
                    util.atomic_write_bytes(target, bytes([n]) * 1000)
            except Exception as e:   # pragma: no cover
                errors.append(e)
        threads = [threading.Thread(target=write, args=(n,)) for n in range(8)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(errors, [])
        self.assertEqual(len(target.read_bytes()), 1000)
        self.assertEqual([p.name for p in self.photos.iterdir()], ["f.bin"])


# --------------------------------------------------------------------------
# Content file
# --------------------------------------------------------------------------

class ContentPathTests(TempDirTest):
    def setUp(self):
        super().setUp()
        self.photos.mkdir()
        self.cwd = self.tmp / "cwd"
        self.cwd.mkdir()
        old = os.getcwd()
        os.chdir(self.cwd)
        self.addCleanup(os.chdir, old)

    def resolve(self, given=None):
        with mock.patch("sys.stderr"):
            return content.resolve_content_path(given, self.photos)

    def test_default_is_the_photos_folder(self):
        self.assertEqual(self.resolve(), self.photos / "trip_content.json")

    def test_old_location_is_still_found(self):
        (self.cwd / "trip_content.json").write_text("{}")
        self.assertEqual(self.resolve().resolve(), (self.cwd / "trip_content.json").resolve())

    def test_photos_folder_wins_when_both_exist(self):
        (self.cwd / "trip_content.json").write_text("{}")
        (self.photos / "trip_content.json").write_text("{}")
        self.assertEqual(self.resolve(), self.photos / "trip_content.json")

    def test_explicit_path_wins(self):
        self.assertEqual(self.resolve(Path("x.json")), Path("x.json"))


class AliasEditTests(unittest.TestCase):
    def test_unhide_clears_the_flag_on_duplicates_too(self):
        c = {"photos": {"b/copy.jpg": {"hidden": True, "caption": "keep me"}}}
        entry = content.set_photo_fields(c, "a/orig.jpg", hidden=False, aliases=["b/copy.jpg"])
        self.assertFalse(entry["hidden"])
        self.assertEqual(c["photos"], {"b/copy.jpg": {"caption": "keep me"}})


# --------------------------------------------------------------------------
# Live server
# --------------------------------------------------------------------------

class CacheTests(unittest.TestCase):
    def test_byte_limit(self):
        c = server.ByteLRU(1000)
        for i in range(10):
            c.set(i, b"x" * 200)
        self.assertLessEqual(c.size, 1000)
        self.assertEqual(len(c), 5)
        c.set("huge", b"x" * 400)   # > a quarter of the budget: not kept
        self.assertIsNone(c.get("huge"))

    def test_single_flight_and_render_limit(self):
        renderer = server.Renderer(limit=2)
        cache = server.ByteLRU(10_000_000)
        calls, running, peak = [], [0], [0]
        lock = threading.Lock()

        def render(n):
            def go():
                with lock:
                    running[0] += 1
                    peak[0] = max(peak[0], running[0])
                calls.append(n)
                time.sleep(0.05)
                with lock:
                    running[0] -= 1
                return b"data"
            return go
        threads = [threading.Thread(target=renderer.get, args=(cache, ("same",), render(i))) for i in range(10)]
        threads += [threading.Thread(target=renderer.get, args=(cache, ("k", i), render(i))) for i in range(6)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(len(calls), 1 + 6)   # one render for the 10 identical requests
        self.assertLessEqual(peak[0], 2)


def server_args(**kw):
    defaults = dict(port=0, read_only=False, editor_link_only=False, verbose=False, thumb_size=200,
                    large_thumb_size=0, memory_cache_mb=64, render_threads=2)
    defaults.update(kw)
    return Namespace(**defaults)


class LiveServerTests(TempDirTest):
    def start(self, **kw):
        make_jpeg(self.photos / "a" / "one.jpg", 1, color=(1, 2, 3))
        make_jpeg(self.photos / "a" / "two.jpg", 2, color=(4, 5, 6))
        shutil.copy(self.photos / "a" / "one.jpg", self.photos / "dup.jpg")
        with mock.patch("sys.stdout"), mock.patch("sys.stderr"):
            items = server.build_index(self.photos, None, dedupe=True)
            days = scan.group_by_day(items)
            path = self.photos / "trip_content.json"
            store = server.ContentStore(path, [d.date_key for d in days], write=not kw.get("read_only"))
        args = server_args(**kw)
        self.app = server.App(args, items, days, store)
        self.httpd = server.TripHTTPServer(("127.0.0.1", 0), server.RequestHandler, self.app)
        args.port = self.httpd.server_address[1]
        t = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        t.start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        return items

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.app.args.port, timeout=10)
        h = {"Host": f"127.0.0.1:{self.app.args.port}"}
        h.update(headers or {})
        if body is not None:
            body = json.dumps(body)
            h["Content-Type"] = "application/json"
        conn.request(method, path, body=body, headers=h)
        r = conn.getresponse()
        data = r.read()
        conn.close()
        return r, data

    def test_stable_keys_from_relative_path(self):
        items = self.start()
        one = next(it for it in items if it.rel == "a/one.jpg")
        import hashlib
        self.assertEqual(one.key, hashlib.sha1(b"a/one.jpg").hexdigest()[:10])
        self.assertEqual(one.aliases, ["dup.jpg"])
        r, _ = self.request("GET", f"/thumb/{one.key}")
        self.assertEqual(r.status, 200)
        r, _ = self.request("GET", "/thumb/0")
        self.assertEqual(r.status, 404)

    def test_proxy_requests_are_not_editors(self):
        items = self.start()
        one = next(it for it in items if it.rel == "a/one.jpg")
        r, _ = self.request("POST", "/api/photo", {"rel": "a/one.jpg", "hidden": True})
        self.assertEqual(r.status, 200)
        # straight from this machine: still visible (editor)
        r, _ = self.request("GET", f"/thumb/{one.key}")
        self.assertEqual(r.status, 200)
        # the same, but through a reverse proxy: a visitor
        proxied = {"X-Forwarded-For": "203.0.113.9"}
        r, _ = self.request("GET", f"/thumb/{one.key}", headers=proxied)
        self.assertEqual(r.status, 404)
        r, _ = self.request("POST", "/api/photo", {"rel": "a/one.jpg", "hidden": False}, headers=proxied)
        self.assertEqual(r.status, 403)

    def test_hidden_through_a_duplicate(self):
        items = self.start()
        one = next(it for it in items if it.rel == "a/one.jpg")
        self.app.content.update(lambda c: c["photos"].update({"dup.jpg": {"hidden": True}}))
        r, _ = self.request("GET", f"/thumb/{one.key}", headers={"Via": "1.1 proxy"})
        self.assertEqual(r.status, 404)

    def test_editor_link_sets_a_cookie(self):
        self.start(editor_link_only=True)
        r, _ = self.request("POST", "/api/day", {"date": "2026-05-01", "title": "x"})
        self.assertEqual(r.status, 403)   # loopback alone isn't enough with --editor-link-only
        r, _ = self.request("GET", "/?key=wrong")
        self.assertEqual(r.status, 403)
        r, _ = self.request("GET", f"/day/2026-05-01?key={self.app.editor_token}&preview=1")
        self.assertEqual(r.status, 303)
        self.assertEqual(r.getheader("Location"), "/day/2026-05-01?preview=1")
        cookie = r.getheader("Set-Cookie").split(";")[0]
        self.assertIn("HttpOnly", r.getheader("Set-Cookie"))
        r, _ = self.request("POST", "/api/day", {"date": "2026-05-01", "title": "x"},
                            headers={"Cookie": cookie, "X-Forwarded-For": "198.51.100.1"})
        self.assertEqual(r.status, 200)

    def test_cover_must_belong_to_the_day(self):
        self.start()
        r, data = self.request("POST", "/api/cover", {"date": "2026-05-02", "rel": "a/one.jpg"})
        self.assertEqual(r.status, 400)
        r, _ = self.request("POST", "/api/cover", {"date": "2026-05-01", "rel": "a/one.jpg"})
        self.assertEqual(r.status, 200)

    def test_read_only_writes_nothing(self):
        self.start(read_only=True)
        self.assertFalse((self.photos / "trip_content.json").exists())
        r, _ = self.request("POST", "/api/day", {"date": "2026-05-01", "title": "x"})
        self.assertEqual(r.status, 403)

    def test_assets_are_versioned_and_cacheable(self):
        self.start()
        r, html = self.request("GET", "/")
        self.assertIn(f"/assets/script.js?v={server.ASSET_VERSION}".encode(), html)
        r, _ = self.request("GET", f"/assets/script.js?v={server.ASSET_VERSION}")
        self.assertEqual(r.getheader("Cache-Control"), server.CACHE_IMMUTABLE)
        r, _ = self.request("GET", "/assets/script.js")
        self.assertEqual(r.getheader("Cache-Control"), server.CACHE_REVALIDATE)

    def test_the_editor_cookie_survives_other_odd_cookies_on_the_same_host(self):
        # browsers share localhost cookies across ports, so another app's cookies arrive too;
        # one outside SimpleCookie's grammar used to make it drop the editor cookie silently
        self.start(editor_link_only=True)
        r, _ = self.request("GET", f"/?key={self.app.editor_token}")
        cookie = r.getheader("Set-Cookie").split(";")[0]
        for others in ('prefs={"theme":"dark"}', "a=b c", 'q="unbalanced', "flag"):
            with self.subTest(others=others):
                r, _ = self.request("POST", "/api/day", {"date": "2026-05-01", "title": "x"},
                                    headers={"Cookie": f"{others}; {cookie}; z=1"})
                self.assertEqual(r.status, 200)
        r, _ = self.request("POST", "/api/day", {"date": "2026-05-01", "title": "x"},
                            headers={"Cookie": f"{self.app.editor_cookie}=wrong"})
        self.assertEqual(r.status, 403)

    def test_a_non_ascii_key_or_cookie_is_refused_not_a_crash(self):
        items = self.start(editor_link_only=True)
        r, _ = self.request("GET", "/?key=%C3%A9t%C3%A9")
        self.assertEqual(r.status, 403)
        one = next(it for it in items if it.rel == "a/one.jpg")
        self.app.content.update(lambda c: c["photos"].update({"a/one.jpg": {"hidden": True}}))
        odd = {"Cookie": f"{self.app.editor_cookie}=été"}          # sent as Latin-1
        with mock.patch("sys.stderr"):
            r, _ = self.request("GET", f"/thumb/{one.key}", headers=odd)
            self.assertEqual(r.status, 404)                                  # a visitor, not a 500
            r, _ = self.request("POST", "/api/day", {"date": "2026-05-01", "title": "x"}, headers=odd)
            self.assertEqual(r.status, 403)

    def test_malformed_edits_are_a_400_not_a_500(self):
        self.start()
        for path, body in (("/api/cover", {"date": [], "rel": None}), ("/api/day", {"date": {"a": 1}, "title": "x"}),
                           ("/api/cover", {"date": None})):
            with self.subTest(path=path, body=body):
                r, data = self.request("POST", path, body)
                self.assertEqual(r.status, 400, data)
                self.assertEqual(json.loads(data)["error"], "Unknown day")

    def test_a_save_that_fails_on_disk_says_so_and_leaves_the_file_alone(self):
        self.start()
        path = self.photos / "trip_content.json"
        before = path.read_bytes()
        full = OSError(28, "No space left on device")
        with mock.patch.object(server, "write_content", side_effect=full), mock.patch("sys.stderr"):
            r, data = self.request("POST", "/api/day", {"date": "2026-05-01", "title": "x"})
        self.assertEqual(r.status, 500)
        self.assertIn("Nothing was saved", json.loads(data)["error"])
        self.assertIn("No space left", json.loads(data)["error"])
        self.assertEqual(path.read_bytes(), before)
        r, _ = self.request("POST", "/api/day", {"date": "2026-05-01", "title": "x"})   # and it recovers
        self.assertEqual(r.status, 200)

    def test_only_a_single_byte_range_is_answered_with_206(self):
        items = self.start()
        one = next(it for it in items if it.rel == "a/one.jpg")
        size = (self.photos / "a" / "one.jpg").stat().st_size
        r, data = self.request("GET", f"/media/{one.key}", headers={"Range": "bytes=0-1,5-9"})
        self.assertEqual((r.status, len(data)), (200, size))          # not just the first range
        r, data = self.request("GET", f"/media/{one.key}", headers={"Range": "bytes=2-5"})
        self.assertEqual((r.status, len(data)), (206, 4))

    def test_long_lived_responses_are_never_kept_by_a_shared_cache(self):
        # hiding a photo doesn't change its URL: a proxy or tunnel that kept a copy
        # would go on serving it, so only browsers may keep anything
        items = self.start()
        one = next(it for it in items if it.rel == "a/one.jpg")
        r, html = self.request("GET", "/day/2026-05-01")
        import re
        url = re.search(rf'/thumb/{one.key}\?v=[^"]+'.encode(), html).group(0).decode()
        visitor = {"Via": "1.1 proxy"}
        for headers in (visitor, {}):                                       # a visitor, and the editor
            r, _ = self.request("GET", url, headers=headers)
            cache = r.getheader("Cache-Control")
            self.assertEqual((r.status, cache), (200, server.CACHE_IMMUTABLE))
            self.assertIn("private", cache)
            self.assertNotIn("public", cache)
        self.app.content.update(lambda c: c["photos"].update({"a/one.jpg": {"hidden": True}}))
        self.assertEqual(self.request("GET", url, headers=visitor)[0].status, 404)

    def test_idle_connection_is_closed(self):
        self.start()
        with mock.patch.object(server.RequestHandler, "timeout", 0.5):
            import socket
            s = socket.create_connection(("127.0.0.1", self.app.args.port))
            s.settimeout(5)
            start = time.monotonic()
            self.assertEqual(s.recv(1), b"")   # the server hangs up on a silent client
            self.assertLess(time.monotonic() - start, 4)
            s.close()


class TileProviderGarbageTests(unittest.TestCase):
    def test_a_garbled_answer_is_a_failed_download_not_a_crash(self):
        # http.client raises BadStatusLine (an HTTPException, not an OSError) for this
        import socket
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(4)
        self.addCleanup(listener.close)

        def answer():
            for _ in range(2):
                try:
                    conn, _ = listener.accept()
                except OSError:
                    return
                conn.recv(4096)
                conn.sendall(b"NOT HTTP AT ALL\r\n\r\n")
                conn.close()
        threading.Thread(target=answer, daemon=True).start()
        port = listener.getsockname()[1]
        with tempfile.TemporaryDirectory() as tmp:
            tiles = server.TileCache(Path(tmp), f"http://127.0.0.1:{port}/{{z}}/{{x}}/{{y}}.png")
            with mock.patch.dict(os.environ, {"no_proxy": "127.0.0.1", "NO_PROXY": "127.0.0.1"}):
                self.assertIsNone(tiles.get(1, 0, 0))
            self.assertTrue(tiles._blocked(), "backs off like for any other network failure")


class ZipappTests(unittest.TestCase):
    def test_zipapp_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run([sys.executable, str(ROOT / "tools" / "build_zipapp.py")], check=True,
                           capture_output=True, cwd=ROOT)
            pyz = ROOT / "dist" / "albummaker.pyz"
            r = subprocess.run([sys.executable, str(pyz), "build", "--help"], capture_output=True, text=True,
                               cwd=tmp)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("--large-thumb-size", r.stdout)
            # data files are read from inside the archive too (the place names)
            code = ("import sys; sys.path.insert(0, sys.argv[1]); from albummaker import places; "
                    "print(places.describe(places.Gazetteer().nearest(34.9858, 135.7588)))")
            r = subprocess.run([sys.executable, "-c", code, str(pyz)], capture_output=True, text=True, cwd=tmp)
            self.assertEqual((r.returncode, r.stdout.strip()), (0, "Kyoto, Japan"), r.stderr)


if __name__ == "__main__":
    unittest.main()
