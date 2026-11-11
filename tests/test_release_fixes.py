# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Pieter Remi Coppens
"""Fixes made before the 3.0.0 release: one layout per page type, exact
cover matching, HEIC files kept without pi-heif, 16-bit greyscale photos,
retrying videos ffprobe couldn't read, the environment for outside tools,
the Windows worker limit and the finishing tip in the packaged app."""
from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import unittest
from concurrent.futures import Future
from datetime import datetime
from pathlib import Path
from unittest import mock

from PIL import Image

from .helpers import TempDirMixin, make_jpeg

from albummaker import build, content, imaging, library, metadata, render, scan, util  # noqa: E402
from albummaker.model import DayGroup, MediaItem  # noqa: E402


def item(rel, media="image", aliases=()):
    it = MediaItem(path=Path("/p") / rel, media_type=media, dt=datetime(2026, 5, 1, 12),
                   dt_is_fallback=False, lat=None, lon=None, rel=rel)
    it.aliases = list(aliases)
    return it


def day(key, *items):
    return DayGroup(date_key=key, date_obj=datetime.fromisoformat(key), items=list(items))


class OneLayoutPerPageTests(TempDirMixin, unittest.TestCase):
    def test_new_content_files_no_longer_name_a_template(self):
        self.assertNotIn("template", content.default_content())

    def test_the_removed_layouts_are_gone(self):
        names = {p.name for p in (Path(render.__file__).parent / "templates").iterdir()}
        self.assertNotIn("default.html", names)
        self.assertNotIn("modern.html", names)

    def test_an_old_template_key_is_ignored(self):
        tmp = self.make_tmp()
        path = tmp / "trip_content.json"
        path.write_text(json.dumps({"title": "T", "template": "modern",
                                    "templates": {"index": "modern", "day": "default"}}))
        loaded = content.parse_content_file(path)
        self.assertEqual(content.trip_title(loaded), "T")
        self.assertFalse(hasattr(render, "get_templates_for_content"))

        out = tmp / "site"
        photo = item("a.jpg")
        photo.outputs = build.planned_outputs(photo.path, "image", "a", False)
        days = [day("2026-05-01", photo)]
        build.render_site(days, loaded, out, build.site_urls())
        page = (out / "days" / "2026-05-01.html").read_text(encoding="utf-8")
        self.assertIn("2026-05-01", page)
        self.assertNotIn("modern-template", page)
        self.assertNotIn("modern-template", (out / "index.html").read_text(encoding="utf-8"))


class ExactCoverTests(unittest.TestCase):
    def test_a_cover_name_does_not_match_a_file_in_a_subfolder(self):
        d = day("2026-05-01", item("a.jpg"), item("phone/IMG_1.jpg"))
        chosen = {"days": {"2026-05-01": {"cover": "IMG_1.jpg"}}}
        self.assertEqual(render.pick_cover(d, chosen).rel, "a.jpg")
        self.assertEqual(render.pick_cover(d, {"cover": "IMG_1.jpg"}).rel, "a.jpg")

    def test_an_exact_path_or_a_duplicates_path_is_used(self):
        d = day("2026-05-01", item("a.jpg"), item("phone/IMG_1.jpg", aliases=["copy/IMG_1.jpg"]))
        self.assertEqual(render.pick_cover(d, {"days": {"2026-05-01": {"cover": "phone/IMG_1.jpg"}}}).rel,
                         "phone/IMG_1.jpg")
        self.assertEqual(render.pick_cover(d, {"cover": "copy/IMG_1.jpg"}).rel, "phone/IMG_1.jpg")


class TripCoverFallbackTests(unittest.TestCase):
    class Urls:
        def thumb(self, item, from_index):
            return f"thumbs/{item.path.name}"

        def hi_thumb(self, item, from_index):
            return None

    def test_a_trip_cover_that_is_not_shown_falls_back_to_the_first_days_cover(self):
        days = [day("2026-05-01", item("a.jpg"), item("b.jpg")), day("2026-05-02", item("c.jpg"))]
        summary = library.trip_summary("x", days, {"cover": "hidden.jpg"}, self.Urls(), "x/")
        self.assertEqual(summary["cover"], "thumbs/a.jpg")

    def test_a_trip_cover_that_is_shown_is_used(self):
        days = [day("2026-05-01", item("a.jpg")), day("2026-05-02", item("c.jpg"))]
        summary = library.trip_summary("x", days, {"cover": "c.jpg"}, self.Urls(), "x/")
        self.assertEqual(summary["cover"], "thumbs/c.jpg")


class HeicWithoutPiHeifTests(TempDirMixin, unittest.TestCase):
    """A HEIC built by an earlier run (with pi-heif) must not lose its
    thumbnail and copies when a later run has no pi-heif."""

    def setUp(self):
        self.tmp = self.make_tmp()
        self.photos, self.out = self.tmp / "photos", self.tmp / "site"
        self.photos.mkdir()
        self.heic = self.photos / "IMG_1.HEIC"
        self.heic.write_bytes(b"not really a heic, never decoded here")
        out = build.planned_outputs(self.heic, "image", "IMG_1", False)
        self.outputs = build.output_list(out, False)
        for rel in self.outputs:
            (self.out / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.out / rel).write_bytes(b"x")
        # the copy of the original matches the source, as shutil.copy2 leaves it
        st = self.heic.stat()
        copy = self.out / "media" / out.original_file
        copy.write_bytes(self.heic.read_bytes())
        os.utime(copy, ns=(st.st_atime_ns, st.st_mtime_ns))
        self.manifest = {"version": build.MANIFEST_VERSION, "thumb_size": 400, "large_thumb_size": 0,
                         "pages": [], "files": {"IMG_1.HEIC": {
                             "base": "IMG_1", "size": st.st_size, "mtime_ns": st.st_mtime_ns,
                             "outputs": self.outputs,
                             "meta": metadata.meta_to_json({"dt": datetime(2026, 5, 1, 9)})}}}
        patches = [mock.patch.object(scan, "HEIF_SUPPORT", False),
                   mock.patch.object(build, "HEIF_SUPPORT", False)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def scan(self):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return build.scan_media(self.photos, self.out, 400, False, manifest=self.manifest)

    def keep_and_clean(self, records):
        keep = {o for rec in records.values() for o in rec["outputs"]}
        with contextlib.redirect_stdout(io.StringIO()):
            build.remove_stale_outputs(self.out, self.manifest, keep)

    def test_an_unchanged_heic_is_still_shown_from_its_earlier_outputs(self):
        items, records = self.scan()
        self.assertEqual([it.path.name for it in items], ["IMG_1.HEIC"])
        self.assertIn("IMG_1.HEIC", records)
        self.keep_and_clean(records)
        for rel in self.outputs:
            self.assertTrue((self.out / rel).is_file(), rel)

    def test_a_changed_heic_keeps_its_outputs_until_it_can_be_read(self):
        os.utime(self.heic, ns=(0, 10 ** 18))   # changed since the last build
        items, records = self.scan()
        self.assertEqual(items, [])
        self.assertEqual(records["IMG_1.HEIC"]["outputs"], self.outputs)
        self.keep_and_clean(records)
        for rel in self.outputs:
            self.assertTrue((self.out / rel).is_file(), rel)

    def test_a_heic_never_built_is_still_left_out(self):
        (self.photos / "IMG_2.heic").write_bytes(b"new")
        with contextlib.redirect_stderr(io.StringIO()):
            got = [p.name for p, _ in scan.collect_candidates(self.photos, built_before={"IMG_1.HEIC"})]
        self.assertEqual(got, ["IMG_1.HEIC"])


class WideGreyscaleTests(TempDirMixin, unittest.TestCase):
    def test_16_bit_greyscale_keeps_its_shades(self):
        tmp = self.make_tmp()
        img = Image.new("I;16", (8, 8), 0)
        img.putpixel((1, 1), 32768)
        img.putpixel((2, 2), 65535)
        img.save(tmp / "grey.png")
        rgb, _ = imaging.open_upright(tmp / "grey.png")
        try:
            self.assertEqual(rgb.mode, "RGB")
            self.assertEqual(rgb.getpixel((0, 0)), (0, 0, 0))
            self.assertEqual(rgb.getpixel((1, 1)), (128, 128, 128))
            self.assertEqual(rgb.getpixel((2, 2)), (255, 255, 255))
        finally:
            rgb.close()

    def test_every_wide_mode_is_scaled(self):
        for mode in ("I;16", "I;16B", "I;16L", "I"):
            grey = imaging._integer_grey_to_8bit(Image.new(mode, (2, 2), 30000))
            self.assertEqual(grey.getpixel((0, 0)), 117, mode)

    def test_32_bit_values_beyond_16_bits_are_stretched(self):
        img = Image.new("I", (2, 1), 0)
        img.putpixel((1, 0), 10 ** 6)
        grey = imaging._integer_grey_to_8bit(img)
        self.assertEqual((grey.getpixel((0, 0)), grey.getpixel((1, 0))), (0, 255))


class _InlinePool:
    """Stands in for ProcessPoolExecutor: runs each job straight away."""
    made_with = []

    def __init__(self, max_workers):
        self.made_with.append(max_workers)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def submit(self, fn, *args):
        future = Future()
        try:
            future.set_result(fn(*args))
        except Exception as e:
            future.set_exception(e)
        return future


class FfprobeRetryTests(TempDirMixin, unittest.TestCase):
    def test_a_missing_or_slow_ffprobe_is_marked_and_never_cached(self):
        for problem in (FileNotFoundError(), subprocess.TimeoutExpired("ffprobe", 30)):
            with mock.patch.object(metadata.subprocess, "run", side_effect=problem), \
                    contextlib.redirect_stderr(io.StringIO()):
                meta = metadata.read_video_metadata(Path("clip.mp4"))
            self.assertTrue(meta.get("probe_failed"))
            self.assertIsNone(metadata.meta_from_json(metadata.meta_to_json(meta), media_type="video"))
        self.assertIsNotNone(metadata.meta_from_json(metadata.meta_to_json({}), media_type="video"))

    def test_the_build_record_asks_for_another_try(self):
        tmp = self.make_tmp()
        photos, out = tmp / "photos", tmp / "site"
        photos.mkdir()
        (photos / "clip.mp4").write_bytes(b"video")
        with mock.patch.object(build, "ProcessPoolExecutor", _InlinePool), \
                mock.patch.object(build, "read_metadata", return_value={"probe_failed": True}), \
                mock.patch.object(build, "make_video_derivatives", return_value=False), \
                contextlib.redirect_stdout(io.StringIO()):
            _, records = build.scan_media(photos, out, 400, False)
        self.assertTrue(records["clip.mp4"].get("retry"))


class ExternalToolEnvTests(unittest.TestCase):
    def env(self, frozen, platform, environ):
        with mock.patch.object(util.sys, "frozen", frozen, create=True), \
                mock.patch.object(util.sys, "platform", platform), \
                mock.patch.dict(os.environ, environ, clear=True):
            return util.external_tool_env()

    def test_inherited_unless_frozen_on_linux(self):
        self.assertIsNone(self.env(False, "linux", {"LD_LIBRARY_PATH": "/app"}))
        self.assertIsNone(self.env(True, "darwin", {"LD_LIBRARY_PATH": "/app"}))
        self.assertIsNone(self.env(True, "win32", {}))

    def test_the_original_library_path_is_put_back(self):
        env = self.env(True, "linux", {"LD_LIBRARY_PATH": "/app", "LD_LIBRARY_PATH_ORIG": "/usr/local/lib",
                                       "HOME": "/home/me"})
        self.assertEqual(env["LD_LIBRARY_PATH"], "/usr/local/lib")
        self.assertNotIn("LD_LIBRARY_PATH_ORIG", env)
        self.assertEqual(env["HOME"], "/home/me")

    def test_without_an_original_the_library_path_is_dropped(self):
        env = self.env(True, "linux", {"LD_LIBRARY_PATH": "/app", "PATH": "/usr/bin"})
        self.assertNotIn("LD_LIBRARY_PATH", env)
        self.assertEqual(env["PATH"], "/usr/bin")

    def test_ffprobe_and_ffmpeg_get_it(self):
        marker = {"MARK": "1"}
        with mock.patch.object(metadata, "external_tool_env", return_value=marker), \
                mock.patch.object(metadata.subprocess, "run", side_effect=FileNotFoundError) as run:
            metadata.read_video_metadata(Path("clip.mp4"))
        self.assertIs(run.call_args.kwargs["env"], marker)
        with mock.patch.object(imaging, "external_tool_env", return_value=marker), \
                mock.patch.object(imaging.subprocess, "run", side_effect=FileNotFoundError) as run:
            self.assertIsNone(imaging.video_frame(Path("clip.mp4"), 100))
        self.assertIs(run.call_args.kwargs["env"], marker)


class WorkerLimitTests(TempDirMixin, unittest.TestCase):
    def build_with(self, platform, workers):
        tmp = self.make_tmp()
        photos = tmp / "photos"
        make_jpeg(photos / "a.jpg")
        _InlinePool.made_with = []
        with mock.patch.object(build, "ProcessPoolExecutor", _InlinePool), \
                mock.patch.object(build.sys, "platform", platform), \
                contextlib.redirect_stdout(io.StringIO()):
            build.scan_media(photos, tmp / "site", 400, False, workers=workers)
        return _InlinePool.made_with[0]

    def test_windows_gets_at_most_61_workers(self):
        self.assertEqual(self.build_with("win32", 100), 61)
        self.assertEqual(self.build_with("win32", 8), 8)
        self.assertEqual(self.build_with("linux", 100), 100)


class FinishTipTests(TempDirMixin, unittest.TestCase):
    def tip(self, frozen):
        out = io.StringIO()
        with mock.patch.object(build, "vendor_ready", return_value=False), \
                mock.patch.object(build.sys, "frozen", frozen, create=True), \
                contextlib.redirect_stdout(out):
            build.print_finish(self.make_tmp())
        return out.getvalue()

    def test_the_packaged_app_is_not_told_to_run_a_script(self):
        text = self.tip(True)
        self.assertNotIn("generate_site.py", text)
        self.assertIn("--fetch-offline-assets", text)
        self.assertIn("Download offline assets", text)

    def test_from_source_the_script_is_named(self):
        self.assertIn("python generate_site.py --fetch-offline-assets", self.tip(False))


if __name__ == "__main__":
    unittest.main()
