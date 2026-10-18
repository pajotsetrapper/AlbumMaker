"""trip_content.json: reading it, the per-photo and per-day edits, hiding,
covers, and the safety rules around the file."""
import contextlib
import copy
import io
import json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from .helpers import TempDirMixin, tc


def item(rel, media="image", dt=None):
    return tc.MediaItem(path=Path("/p") / rel, media_type=media, dt=dt or datetime(2026, 5, 1, 12),
                        dt_is_fallback=False, lat=None, lon=None, rel=rel)


class ParseContentFileTests(TempDirMixin, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = cls.make_tmp()

    def write(self, text, name="c.json"):
        p = self.tmp / name
        p.write_text(text, encoding="utf-8")
        return p

    def test_missing_keys_get_defaults(self):
        c = tc.parse_content_file(self.write("{}"))
        self.assertEqual(c["title"], "My Trip")
        self.assertEqual(c["days"], {})
        self.assertEqual(c["photos"], {})

    def test_values_and_unknown_keys_are_kept(self):
        c = tc.parse_content_file(self.write(json.dumps(
            {"title": "Belgium", "days": {"2026-05-01": {"title": "A"}},
             "photos": {"a.jpg": {"favorite": True}}, "future_key": [1, 2]})))
        self.assertEqual(c["title"], "Belgium")
        self.assertEqual(c["days"]["2026-05-01"]["title"], "A")
        self.assertEqual(c["future_key"], [1, 2])

    def test_syntax_error_names_the_line_and_column(self):
        with self.assertRaises(tc.ContentFileError) as cm:
            tc.parse_content_file(self.write('{\n  "title": "x",\n}\n'))
        self.assertIn("line 3", str(cm.exception))
        self.assertIn("column", str(cm.exception))

    def test_wrong_shapes_are_rejected(self):
        for text, mention in [('[1, 2]', "object"),
                              ('{"days": []}', '"days"'),
                              ('{"photos": []}', '"photos"'),
                              ('{"photos": "x"}', '"photos"')]:
            with self.subTest(text=text), self.assertRaises(tc.ContentFileError) as cm:
                tc.parse_content_file(self.write(text))
            self.assertIn(mention, str(cm.exception))

    def test_a_missing_or_empty_file_is_an_error_not_a_crash(self):
        with self.assertRaises(tc.ContentFileError):
            tc.parse_content_file(self.tmp / "does-not-exist.json")
        with self.assertRaises(tc.ContentFileError):
            tc.parse_content_file(self.write(""))

    def test_a_utf8_bom_from_windows_editors_is_accepted(self):
        p = self.tmp / "bom.json"
        p.write_bytes(b"\xef\xbb\xbf" + json.dumps({"title": "Caf\u00e9"}).encode("utf-8"))
        self.assertEqual(tc.parse_content_file(p)["title"], "Caf\u00e9")


class TextHelpersTests(unittest.TestCase):
    def test_trip_title_and_day_text(self):
        c = {"title": "  Belgium  ", "days": {"2026-05-01": {"title": " Day one ", "description": " Hello\n\nWorld "}}}
        self.assertEqual(tc.trip_title(c), "Belgium")
        self.assertEqual(tc.day_text(c, "2026-05-01"), ("Day one", "Hello\n\nWorld"))
        self.assertEqual(tc.day_text(c, "2030-01-01"), ("", ""))

    def test_blank_trip_title_falls_back(self):
        self.assertEqual(tc.trip_title({"title": "   "}), "My Trip")
        self.assertEqual(tc.trip_title({}), "My Trip")

    def test_non_text_values_do_not_crash(self):
        c = {"title": 5, "days": {"d": {"title": None, "description": ["x"]}, "e": "oops"}}
        tc.trip_title(c)
        self.assertEqual(tc.day_text(c, "e"), ("", ""))
        tc.day_text(c, "d")


class PhotoEntryTests(unittest.TestCase):
    def test_defaults(self):
        self.assertEqual(tc.photo_entry({}, "a.jpg"), {"caption": "", "favorite": False, "hidden": False})

    def test_only_a_real_true_counts(self):
        c = {"photos": {"a.jpg": {"favorite": "true", "hidden": 1, "caption": "  hi  "}}}
        self.assertEqual(tc.photo_entry(c, "a.jpg"), {"caption": "hi", "favorite": False, "hidden": False})
        c = {"photos": {"a.jpg": {"favorite": True, "hidden": True}}}
        e = tc.photo_entry(c, "a.jpg")
        self.assertTrue(e["favorite"] and e["hidden"])

    def test_odd_shapes_are_treated_as_empty(self):
        for photos in (None, [], "x", {"a.jpg": "oops"}, {"a.jpg": None}):
            with self.subTest(photos=photos):
                self.assertEqual(tc.photo_entry({"photos": photos}, "a.jpg"),
                                 {"caption": "", "favorite": False, "hidden": False})

    def test_hidden_rels(self):
        c = {"photos": {"a.jpg": {"hidden": True}, "b.jpg": {"hidden": False}, "c.jpg": {"favorite": True},
                        "d.jpg": "bad", "sub/e.jpg": {"hidden": True}}}
        self.assertEqual(tc.hidden_rels(c), {"a.jpg", "sub/e.jpg"})
        self.assertEqual(tc.hidden_rels({}), set())
        self.assertEqual(tc.hidden_rels({"photos": []}), set())


class SetPhotoFieldsTests(unittest.TestCase):
    def test_sets_and_returns_the_clean_entry(self):
        c = {}
        e = tc.set_photo_fields(c, "a.jpg", caption="  Golden hour ", favorite=True)
        self.assertEqual(e, {"caption": "Golden hour", "favorite": True, "hidden": False})
        self.assertEqual(c["photos"]["a.jpg"], {"caption": "Golden hour", "favorite": True})

    def test_only_non_default_values_are_stored(self):
        c = {}
        tc.set_photo_fields(c, "a.jpg", favorite=False, hidden=False, caption="")
        self.assertNotIn("a.jpg", c["photos"])

    def test_an_entry_disappears_when_cleared(self):
        c = {}
        tc.set_photo_fields(c, "a.jpg", caption="x", favorite=True)
        tc.set_photo_fields(c, "a.jpg", caption="")
        self.assertEqual(c["photos"]["a.jpg"], {"favorite": True})
        tc.set_photo_fields(c, "a.jpg", favorite=False)
        self.assertEqual(c["photos"], {})

    def test_partial_updates_leave_other_fields_alone(self):
        c = {"photos": {"a.jpg": {"caption": "keep me", "hidden": True}}}
        tc.set_photo_fields(c, "a.jpg", favorite=True)
        self.assertEqual(c["photos"]["a.jpg"], {"caption": "keep me", "hidden": True, "favorite": True})

    def test_unknown_keys_in_an_entry_survive(self):
        c = {"photos": {"a.jpg": {"favorite": True, "rating": 5}}}
        tc.set_photo_fields(c, "a.jpg", favorite=False)
        self.assertEqual(c["photos"]["a.jpg"], {"rating": 5})

    def test_caption_is_capped(self):
        c = {}
        e = tc.set_photo_fields(c, "a.jpg", caption="x" * (tc.CAPTION_MAX + 500))
        self.assertEqual(len(e["caption"]), tc.CAPTION_MAX)

    def test_a_broken_photos_value_is_replaced_rather_than_crashing(self):
        c = {"photos": []}
        tc.set_photo_fields(c, "a.jpg", favorite=True)
        self.assertEqual(c["photos"], {"a.jpg": {"favorite": True}})

    def test_does_not_touch_other_photos(self):
        c = {"photos": {"b.jpg": {"caption": "b"}}}
        before = copy.deepcopy(c["photos"]["b.jpg"])
        tc.set_photo_fields(c, "a.jpg", favorite=True)
        self.assertEqual(c["photos"]["b.jpg"], before)


class DayEditTests(unittest.TestCase):
    def test_cover_set_and_clear(self):
        c = {"days": {"2026-05-01": {"title": "T"}}}
        tc.set_cover(c, "2026-05-01", "a.jpg")
        self.assertEqual(c["days"]["2026-05-01"], {"title": "T", "cover": "a.jpg"})
        tc.set_cover(c, "2026-05-01", None)
        self.assertEqual(c["days"]["2026-05-01"], {"title": "T"})

    def test_day_text_normalises_line_endings_and_keeps_the_cover(self):
        c = {"days": {"2026-05-01": {"cover": "a.jpg", "extra": 1}}}
        tc.set_day_text(c, "2026-05-01", title="  Canals  ", description="One\r\n\r\nTwo\r\n")
        self.assertEqual(c["days"]["2026-05-01"],
                         {"cover": "a.jpg", "extra": 1, "title": "Canals", "description": "One\n\nTwo"})

    def test_partial_day_update(self):
        c = {"days": {"d": {"title": "T", "description": "D"}}}
        tc.set_day_text(c, "d", title="New")
        self.assertEqual(c["days"]["d"], {"title": "New", "description": "D"})

    def test_a_day_that_has_no_entry_yet_gets_one(self):
        c = {}
        tc.set_day_text(c, "2026-05-09", title="Late find")
        self.assertEqual(c["days"]["2026-05-09"]["title"], "Late find")


class VisibilityTests(unittest.TestCase):
    def days(self):
        a, b, c = item("a.jpg"), item("b.jpg"), item("c.jpg", dt=datetime(2026, 5, 2, 12))
        return tc.group_by_day([a, b, c])

    def test_visitors_do_not_see_hidden_photos(self):
        content = {"photos": {"a.jpg": {"hidden": True}}}
        out = tc.visible_days(self.days(), content)
        self.assertEqual([[i.rel for i in d.items] for d in out], [["b.jpg"], ["c.jpg"]])

    def test_a_day_with_nothing_left_disappears(self):
        content = {"photos": {"c.jpg": {"hidden": True}}}
        self.assertEqual([d.date_key for d in tc.visible_days(self.days(), content)], ["2026-05-01"])

    def test_the_editor_sees_everything(self):
        content = {"photos": {"a.jpg": {"hidden": True}, "c.jpg": {"hidden": True}}}
        days = self.days()
        self.assertIs(tc.visible_days(days, content, editable=True), days)

    def test_the_original_list_is_never_changed(self):
        days = self.days()
        tc.visible_days(days, {"photos": {"a.jpg": {"hidden": True}}})
        self.assertEqual([len(d.items) for d in days], [2, 1])

    def test_nothing_hidden_returns_the_same_days(self):
        days = self.days()
        self.assertIs(tc.visible_days(days, {}), days)


class CoverTests(unittest.TestCase):
    def day(self, *items):
        return tc.DayGroup(date_key="2026-05-01", date_obj=datetime(2026, 5, 1), items=list(items))

    def test_explicit_cover_wins(self):
        d = self.day(item("a.jpg"), item("b.jpg"))
        content = {"days": {"2026-05-01": {"cover": "b.jpg"}}}
        self.assertEqual(tc.pick_cover(d, content).rel, "b.jpg")

    def test_without_a_choice_a_photo_beats_a_video_frame(self):
        d = self.day(item("clip.mp4", media="video"), item("a.jpg"))
        self.assertEqual(tc.pick_cover(d, {}).rel, "a.jpg")

    def test_only_videos_uses_the_first(self):
        d = self.day(item("v1.mp4", media="video"), item("v2.mp4", media="video"))
        self.assertEqual(tc.pick_cover(d, {}).rel, "v1.mp4")

    def test_a_cover_that_is_not_in_this_day_is_ignored(self):
        d = self.day(item("a.jpg"), item("b.jpg"))
        content = {"days": {"2026-05-01": {"cover": "elsewhere.jpg"}}}
        self.assertEqual(tc.pick_cover(d, content).rel, "a.jpg")

    def test_a_non_text_cover_is_ignored(self):
        d = self.day(item("a.jpg"))
        self.assertEqual(tc.pick_cover(d, {"days": {"2026-05-01": {"cover": 5}}}).rel, "a.jpg")


class RelPathTests(unittest.TestCase):
    def test_relative_posix_paths(self):
        root = Path("/photos")
        items = [item("x"), item("y")]
        items[0].path, items[1].path = root / "sub" / "dir" / "a.jpg", root / "b.jpg"
        tc.assign_rel_paths(items, root)
        self.assertEqual([i.rel for i in items], ["sub/dir/a.jpg", "b.jpg"])

    def test_a_path_outside_the_root_falls_back_to_the_name(self):
        it = item("x")
        it.path = Path("/elsewhere/a.jpg")
        tc.assign_rel_paths([it], Path("/photos"))
        self.assertEqual(it.rel, "a.jpg")


class UnknownKeyReportTests(unittest.TestCase):
    def report(self, content, known):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            tc.report_unknown_photo_keys(content, known)
        return out.getvalue()

    def test_silent_when_everything_matches(self):
        self.assertEqual(self.report({"photos": {"a.jpg": {}}}, {"a.jpg"}), "")
        self.assertEqual(self.report({}, set()), "")

    def test_names_a_few_and_counts_the_rest(self):
        text = self.report({"photos": {f"gone{i}.jpg": {} for i in range(5)}}, set())
        self.assertIn("5 file(s)", text)
        self.assertIn("gone0.jpg", text)
        self.assertIn("...", text)


class AtomicWriteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(__import__("shutil").rmtree, self.tmp, True)

    def test_writes_and_leaves_no_temporary_files(self):
        p = self.tmp / "out.json"
        tc.atomic_write_text(p, "hello")
        self.assertEqual(p.read_text(), "hello")
        self.assertEqual([f.name for f in self.tmp.iterdir() if f.name != "out.json"], [])

    def test_replaces_an_existing_file_whole(self):
        p = self.tmp / "replace.json"
        p.write_text("old old old old")
        tc.atomic_write_text(p, "new")
        self.assertEqual(p.read_text(), "new")

    def test_bytes_version(self):
        p = self.tmp / "sub" / "data.bin"
        tc.atomic_write_bytes(p, b"\x00\x01")
        self.assertEqual(p.read_bytes(), b"\x00\x01")


if __name__ == "__main__":
    unittest.main()
