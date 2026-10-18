"""Finding files, skipping duplicates, pairing Live Photos, naming the
static site's output files, and the build record that makes re-runs cheap."""
import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from .helpers import TempDirMixin, make_jpeg, tc

import generate_site as gs  # noqa: E402  (helpers puts the project on sys.path)


def quiet():
    return contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO())


class FolderTestCase(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)

    def touch(self, rel, data=b"x"):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return p


class ListMediaFilesTests(FolderTestCase):
    def rels(self, **kw):
        out, err = quiet()
        with out, err:
            return [p.relative_to(self.root).as_posix() for p in tc.list_media_files(self.root, **kw)]

    def test_finds_nested_files_sorted(self):
        self.touch("b.jpg"); self.touch("a/z.jpg"); self.touch("a/c.jpg")
        self.assertEqual(self.rels(), ["a/c.jpg", "a/z.jpg", "b.jpg"])

    def test_skips_hidden_apple_double_and_nas_folders(self):
        for rel in ("ok.jpg", ".hidden.jpg", "._IMG_1.JPG", ".git/x.jpg", "@eaDir/t.jpg", "#recycle/t.jpg"):
            self.touch(rel)
        self.assertEqual(self.rels(), ["ok.jpg"])

    def test_skips_a_generated_site_inside_the_photos_folder(self):
        self.touch("a.jpg")
        self.touch("site/media/a.jpg")
        self.touch(f"site/{tc.SITE_MARKER}", b"{}")
        self.assertEqual(self.rels(), ["a.jpg"])

    def test_skips_explicitly_excluded_folders(self):
        self.touch("a.jpg"); self.touch("out/a.jpg")
        self.assertEqual(self.rels(exclude_dirs=(self.root / "out",)), ["a.jpg"])

    def test_collect_candidates_sorts_images_from_videos_and_ignores_the_rest(self):
        for rel in ("a.jpg", "b.PNG", "c.mp4", "d.MOV", "notes.txt", "e.pdf", "f.gif"):
            self.touch(rel)
        out, err = quiet()
        with out, err:
            got = {p.name: kind for p, kind in tc.collect_candidates(self.root)}
        self.assertEqual(got, {"a.jpg": "image", "b.PNG": "image", "c.mp4": "video", "d.MOV": "video"})


class DedupeTests(FolderTestCase):
    def dedupe(self, names):
        cands = [(self.root / n, "image") for n in names]
        out, err = quiet()
        with out, err:
            return [p.name for p, _ in tc.dedupe_candidates(cands)]

    def test_identical_copies_are_dropped_keeping_the_first(self):
        self.touch("a.jpg", b"same bytes"); self.touch("b.jpg", b"same bytes"); self.touch("c.jpg", b"other")
        self.assertEqual(self.dedupe(["a.jpg", "b.jpg", "c.jpg"]), ["a.jpg", "c.jpg"])

    def test_files_of_the_same_size_but_different_content_are_both_kept(self):
        self.touch("a.jpg", b"aaaa"); self.touch("b.jpg", b"bbbb")
        self.assertEqual(self.dedupe(["a.jpg", "b.jpg"]), ["a.jpg", "b.jpg"])

    def test_a_matching_fingerprint_is_confirmed_byte_for_byte(self):
        # the fingerprint samples only the first and last 64 KB, so two big files that
        # differ only in the middle look the same to it: the full comparison must save them
        size, chunk = 300_000, 65536
        base = bytearray(b"A" * size)
        other = bytearray(base)
        other[size // 2] = ord("B")
        self.assertEqual(tc.fast_fingerprint(self.touch("a.jpg", bytes(base))),
                         tc.fast_fingerprint(self.touch("b.jpg", bytes(other))))
        self.assertEqual(self.dedupe(["a.jpg", "b.jpg"]), ["a.jpg", "b.jpg"])

    def test_empty_files_do_not_crash(self):
        self.touch("a.jpg", b""); self.touch("b.jpg", b"")
        self.assertEqual(self.dedupe(["a.jpg", "b.jpg"]), ["a.jpg"])

    def test_fingerprint_of_a_missing_file_is_none(self):
        self.assertIsNone(tc.fast_fingerprint(self.root / "nope.jpg"))


class LivePhotoTests(unittest.TestCase):
    def media(self, rel, kind="image", live=False):
        return tc.MediaItem(path=Path("/p") / rel, media_type=kind, dt=datetime(2026, 5, 1, 12),
                            dt_is_fallback=False, lat=None, lon=None, is_live_clip=live, rel=rel)

    def pair(self, items):
        out, err = quiet()
        with out, err:
            return tc.pair_live_photos(items)

    def test_clip_is_attached_to_its_photo_and_removed_from_the_list(self):
        photo, clip = self.media("IMG_1.HEIC"), self.media("IMG_1.MOV", "video", live=True)
        kept = self.pair([clip, photo])
        self.assertEqual(kept, [photo])
        self.assertIs(photo.live_clip, clip)

    def test_names_are_compared_ignoring_case(self):
        photo, clip = self.media("img_1.jpg"), self.media("IMG_1.mov", "video", live=True)
        self.pair([photo, clip])
        self.assertIs(photo.live_clip, clip)

    def test_a_real_video_with_the_same_name_is_left_alone(self):
        photo, video = self.media("IMG_1.jpg"), self.media("IMG_1.mp4", "video", live=False)
        kept = self.pair([photo, video])
        self.assertEqual(len(kept), 2)
        self.assertIsNone(photo.live_clip)

    def test_same_name_in_another_folder_is_not_paired(self):
        photo, clip = self.media("a/IMG_1.jpg"), self.media("b/IMG_1.mov", "video", live=True)
        self.assertEqual(len(self.pair([photo, clip])), 2)

    def test_a_clip_without_a_photo_stays_a_video(self):
        clip = self.media("IMG_9.mov", "video", live=True)
        self.assertEqual(self.pair([clip]), [clip])

    def test_one_photo_takes_only_one_clip(self):
        photo = self.media("IMG_1.jpg")
        c1, c2 = self.media("IMG_1.mov", "video", live=True), self.media("IMG_1.MP4", "video", live=True)
        kept = self.pair([photo, c1, c2])
        self.assertEqual(len(kept), 2)

    def test_clip_detection_rules(self):
        self.assertTrue(tc.is_live_photo_clip({"live_photo_id": "ABC"}))
        self.assertTrue(tc.is_live_photo_clip({"duration_s": 3.0}))
        self.assertTrue(tc.is_live_photo_clip({"duration_s": tc.LIVE_CLIP_MAX_SECONDS}))
        self.assertFalse(tc.is_live_photo_clip({"duration_s": 12.0}))
        self.assertFalse(tc.is_live_photo_clip({}))


class OutputNameTests(unittest.TestCase):
    def names(self, files, previous=None):
        rel = {Path("/in") / f: f for f in files}
        bases = gs.compute_output_bases(rel, previous or {})
        return {rel[p]: b for p, b in bases.items()}

    def test_plain_names_are_kept_as_they_are(self):
        self.assertEqual(self.names(["IMG_1234.HEIC", "My Photo.jpg"]),
                         {"IMG_1234.HEIC": "IMG_1234", "My Photo.jpg": "My Photo"})

    def test_collisions_get_numbered_suffixes_in_path_order(self):
        got = self.names(["a/IMG_1.jpg", "b/IMG_1.jpg", "c/IMG_1.jpg"])
        self.assertEqual(got, {"a/IMG_1.jpg": "IMG_1", "b/IMG_1.jpg": "IMG_1_2", "c/IMG_1.jpg": "IMG_1_3"})

    def test_collisions_ignore_case_and_extension(self):
        got = self.names(["IMG_1.jpg", "IMG_1.HEIC", "img_1.png"])
        # three files that differ only by case/extension must still get three distinct
        # names, since every thumbnail is a .jpg and some file systems ignore case
        self.assertEqual(len({b.lower() for b in got.values()}), 3)
        self.assertEqual(got["IMG_1.HEIC"], "IMG_1")      # photos sort by path: "IMG_1.HEIC" < "IMG_1.jpg"

    def test_a_photo_keeps_the_plain_name_over_its_live_clip(self):
        got = self.names(["IMG_1.MOV", "IMG_1.HEIC"])
        self.assertEqual(got["IMG_1.HEIC"], "IMG_1")
        self.assertEqual(got["IMG_1.MOV"], "IMG_1_2")

    def test_characters_illegal_on_windows_are_replaced(self):
        got = self.names(['a:b?c*d.jpg'])
        self.assertEqual(got["a:b?c*d.jpg"], "a_b_c_d")

    def test_names_cannot_end_in_dots_or_spaces_or_be_empty(self):
        got = self.names(["trailing .jpg", "...jpg"])
        self.assertFalse(got["trailing .jpg"].endswith((" ", ".")))
        self.assertTrue(got["...jpg"])

    def test_very_long_names_are_capped(self):
        self.assertLessEqual(len(self.names(["x" * 400 + ".jpg"])["x" * 400 + ".jpg"]), 180)

    def test_a_name_given_in_an_earlier_run_is_kept_even_if_a_new_file_sorts_first(self):
        previous = {"z/IMG_1.jpg": {"base": "IMG_1"}}
        got = self.names(["a/IMG_1.jpg", "z/IMG_1.jpg"], previous)
        self.assertEqual(got["z/IMG_1.jpg"], "IMG_1")          # unchanged
        self.assertEqual(got["a/IMG_1.jpg"], "IMG_1_2")        # the newcomer steps aside

    def test_a_suffix_that_is_a_real_file_name_is_skipped(self):
        got = self.names(["a/IMG_1.jpg", "b/IMG_1.jpg", "IMG_1_2.jpg"])
        self.assertEqual(got["IMG_1_2.jpg"], "IMG_1_2")
        self.assertNotIn(got["b/IMG_1.jpg"], ("IMG_1", "IMG_1_2"))

    def test_a_remembered_name_with_bad_characters_is_not_trusted(self):
        got = self.names(["a.jpg"], {"a.jpg": {"base": "../escape"}})
        self.assertEqual(got["a.jpg"], "a")


class BuildRecordTests(TempDirMixin, unittest.TestCase):
    def setUp(self):
        self.out = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.out, True)

    def test_save_and_load_round_trip(self):
        gs.save_manifest(self.out, 400, {"a.jpg": {"outputs": ["media/a.jpg"], "base": "a"}}, ["days/x.html"])
        data = gs.load_manifest(self.out)
        self.assertEqual(data["thumb_size"], 400)
        self.assertEqual(data["files"]["a.jpg"]["base"], "a")
        self.assertEqual(data["pages"], ["days/x.html"])

    def test_no_record_means_a_fresh_start(self):
        self.assertEqual(gs.load_manifest(self.out), {})

    def test_an_unreadable_record_is_ignored_with_a_warning(self):
        (self.out / tc.SITE_MARKER).write_text("{not json")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(gs.load_manifest(self.out), {})
        self.assertIn("ignoring unreadable build record", err.getvalue())

    def test_a_record_from_another_version_is_ignored(self):
        (self.out / tc.SITE_MARKER).write_text(json.dumps({"version": 999, "files": {}}))
        self.assertEqual(gs.load_manifest(self.out), {})
        (self.out / tc.SITE_MARKER).write_text(json.dumps({"version": gs.MANIFEST_VERSION, "files": []}))
        self.assertEqual(gs.load_manifest(self.out), {})


class StaleOutputTests(unittest.TestCase):
    def setUp(self):
        self.out = Path(tempfile.mkdtemp())
        self.outside = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.out, True)
        self.addCleanup(shutil.rmtree, self.outside, True)

    def make(self, rel):
        p = self.out / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x")
        return p

    def run_cleanup(self, previous, keep):
        o, e = quiet()
        with o, e:
            gs.remove_stale_outputs(self.out, previous, keep)

    def test_removes_only_what_is_listed_and_no_longer_kept(self):
        old, kept, unlisted = self.make("media/old.jpg"), self.make("media/kept.jpg"), self.make("media/mine.jpg")
        previous = {"files": {"old.jpg": {"outputs": ["media/old.jpg"]}, "kept.jpg": {"outputs": ["media/kept.jpg"]}},
                    "pages": []}
        self.run_cleanup(previous, {"media/kept.jpg"})
        self.assertFalse(old.exists())
        self.assertTrue(kept.exists())
        self.assertTrue(unlisted.exists())        # not in the build record: never touched

    def test_removes_pages_of_days_that_no_longer_exist(self):
        page = self.make("days/2026-05-02.html")
        self.run_cleanup({"files": {}, "pages": ["days/2026-05-02.html"]}, set())
        self.assertFalse(page.exists())

    def test_never_leaves_the_output_folder(self):
        victim = self.outside / "precious.txt"
        victim.write_text("keep me")
        rel = os.path.relpath(victim, self.out)           # something like ../tmpabc/precious.txt
        self.run_cleanup({"files": {"x": {"outputs": [rel]}}, "pages": [rel, "../../../etc/hostname"]}, set())
        self.assertTrue(victim.exists())

    def test_ignores_missing_files_folders_and_odd_records(self):
        (self.out / "media").mkdir()
        previous = {"files": {"a": {"outputs": ["media", "gone.jpg", 5, None]}, "b": "bad", "c": {"outputs": "no"}},
                    "pages": [None, 3]}
        self.run_cleanup(previous, set())
        self.assertTrue((self.out / "media").is_dir())


class CopyIsCurrentTests(FolderTestCase):
    def test_same_size_and_time_is_current(self):
        a = self.touch("a.bin", b"1234")
        b = self.root / "b.bin"
        shutil.copy2(a, b)
        self.assertTrue(gs._copy_is_current(a, b))

    def test_different_size_is_not(self):
        a, b = self.touch("a.bin", b"1234"), self.touch("b.bin", b"12345")
        os.utime(b, (a.stat().st_mtime,) * 2)
        self.assertFalse(gs._copy_is_current(a, b))

    def test_two_seconds_of_clock_difference_is_tolerated_but_not_more(self):
        a, b = self.touch("a.bin", b"1234"), self.touch("b.bin", b"abcd")
        t = a.stat().st_mtime
        os.utime(b, (t + 1.9,) * 2)
        self.assertTrue(gs._copy_is_current(a, b))
        os.utime(b, (t + 5,) * 2)
        self.assertFalse(gs._copy_is_current(a, b))

    def test_missing_copy_is_not_current(self):
        self.assertFalse(gs._copy_is_current(self.touch("a.bin"), self.root / "nope.bin"))


if __name__ == "__main__":
    unittest.main()
