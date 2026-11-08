"""In-page editing: what the API saves into trip_content.json, what it
refuses, who is allowed to use it, and how hidden photos are treated."""
import json
import re
import threading
import unittest
from pathlib import Path

from .helpers import (TempDirMixin, AlbumMaker, install_app, item_ids, make_jpeg, read_json, slides_of,
                      standard_trip, tc, write_json)

A1 = "a1.jpg"
A3 = "sub/a3.jpg"


class EditingTestCase(TempDirMixin, unittest.TestCase):
    """A running server per class; the content file is reset before each test."""
    server_args = ()

    @classmethod
    def setUpClass(cls):
        cls.tmp = cls.make_tmp()
        cls.app = install_app(cls.tmp / "app")
        cls.photos = cls.tmp / "photos"
        standard_trip(cls.photos)
        make_jpeg(cls.photos / "nodate.jpg", None, color=(1, 2, 3))          # gives the Unsorted day something to hold
        write_json(cls.tmp / "content.json", {})
        cls.server = AlbumMaker(cls.app, cls.photos, cls.tmp, *cls.server_args).start()
        cls.addClassCleanup(cls.server.stop)
        cls._resets = 0

    def reset(self, content=None):
        type(self)._resets += 1
        data = dict(content or {})
        data["_test_reset"] = self._resets        # a changing value, so the server always sees a new file
        write_json(self.server.content, data)
        return data

    def setUp(self):
        self.reset()

    def post(self, path, body, **kw):
        return self.server.post_json(path, body, **kw)

    def saved(self):
        return read_json(self.server.content)


class EditApiSavingTests(EditingTestCase):
    def test_caption_and_favorite_are_saved_and_echoed_back(self):
        status, reply = self.post("/api/photo", {"rel": A1, "caption": "  Sunrise  ", "favorite": True})
        self.assertEqual(status, 200)
        self.assertEqual(reply, {"ok": True, "photo": {"caption": "Sunrise", "favorite": True, "hidden": False}})
        self.assertEqual(self.saved()["photos"][A1], {"caption": "Sunrise", "favorite": True})

    def test_photos_in_subfolders_use_their_relative_path(self):
        self.assertEqual(self.post("/api/photo", {"rel": A3, "hidden": True})[0], 200)
        self.assertEqual(self.saved()["photos"][A3], {"hidden": True})

    def test_clearing_everything_removes_the_entry(self):
        self.post("/api/photo", {"rel": A1, "caption": "x", "favorite": True})
        self.post("/api/photo", {"rel": A1, "caption": "", "favorite": False})
        self.assertNotIn(A1, self.saved()["photos"])

    def test_a_partial_edit_leaves_the_other_fields(self):
        self.post("/api/photo", {"rel": A1, "caption": "keep", "hidden": True})
        self.post("/api/photo", {"rel": A1, "favorite": True})
        self.assertEqual(self.saved()["photos"][A1], {"caption": "keep", "hidden": True, "favorite": True})

    def test_cover_can_be_set_and_cleared(self):
        self.assertEqual(self.post("/api/cover", {"date": "2026-05-01", "rel": "a2.jpg"})[0], 200)
        self.assertEqual(self.saved()["days"]["2026-05-01"]["cover"], "a2.jpg")
        self.assertEqual(self.post("/api/cover", {"date": "2026-05-01", "rel": None})[0], 200)
        self.assertNotIn("cover", self.saved()["days"]["2026-05-01"])

    def test_day_title_and_description_with_windows_line_endings(self):
        status, _ = self.post("/api/day", {"date": "2026-05-01", "title": " Canals ", "description": "One\r\n\r\nTwo\r\n"})
        self.assertEqual(status, 200)
        day = self.saved()["days"]["2026-05-01"]
        self.assertEqual((day["title"], day["description"]), ("Canals", "One\n\nTwo"))

    def test_the_unsorted_day_can_be_edited_too(self):
        self.assertEqual(self.post("/api/day", {"date": "unknown", "title": "Odds and ends"})[0], 200)
        self.assertEqual(self.saved()["days"]["unknown"]["title"], "Odds and ends")
        self.assertIn("Odds and ends", self.server.text("/day/unknown"))

    def test_cover_and_text_edits_do_not_disturb_each_other(self):
        self.post("/api/cover", {"date": "2026-05-01", "rel": "a2.jpg"})
        self.post("/api/day", {"date": "2026-05-01", "title": "T"})
        self.assertEqual(self.saved()["days"]["2026-05-01"], {"cover": "a2.jpg", "title": "T"})

    def test_text_is_kept_as_typed_including_non_ascii(self):
        self.post("/api/photo", {"rel": A1, "caption": "Caf\u00e9 \u2603 \u65e5\u672c\u8a9e"})
        raw = self.server.content.read_text(encoding="utf-8")
        self.assertIn("Caf\u00e9 \u2603 \u65e5\u672c\u8a9e", raw)          # not turned into \\u escapes
        self.assertEqual(self.saved()["photos"][A1]["caption"], "Caf\u00e9 \u2603 \u65e5\u672c\u8a9e")

    def test_the_file_stays_tidy(self):
        self.post("/api/photo", {"rel": A1, "favorite": True})
        raw = self.server.content.read_text(encoding="utf-8")
        self.assertTrue(raw.endswith("\n"))
        self.assertIn('\n  "', raw)                                           # two-space indentation

    def test_no_temporary_files_are_left_next_to_the_content_file(self):
        for i in range(5):
            self.post("/api/photo", {"rel": A1, "caption": f"c{i}"})
        self.assertEqual(sorted(p.name for p in self.tmp.iterdir() if "content" in p.name and p.suffix != ".json"), [])
        self.assertEqual([p.name for p in self.tmp.glob("*.tmp*")], [])

    def test_hand_made_changes_and_unknown_keys_survive_an_edit(self):
        self.reset({"title": "Hand title", "future_key": {"a": [1, 2]},
                    "photos": {"b1.jpg": {"caption": "typed by hand", "rating": 5}}})
        self.post("/api/photo", {"rel": A1, "favorite": True})
        c = self.saved()
        self.assertEqual(c["title"], "Hand title")
        self.assertEqual(c["future_key"], {"a": [1, 2]})
        self.assertEqual(c["photos"]["b1.jpg"], {"caption": "typed by hand", "rating": 5})

    def test_an_edit_shows_up_in_the_editor_view_immediately(self):
        self.post("/api/photo", {"rel": A1, "caption": "Visible now", "favorite": True})
        slide = [s for s in slides_of(self.server.text("/day/2026-05-01")) if s["detail"]["filename"] == A1][0]
        self.assertEqual((slide["caption"], slide["favorite"]), ("Visible now", True))

    def test_a_missing_content_file_is_recreated_by_the_first_edit(self):
        self.server.content.unlink()
        self.assertEqual(self.post("/api/photo", {"rel": A1, "favorite": True})[0], 200)
        self.assertEqual(self.saved()["photos"][A1], {"favorite": True})


class EditApiValidationTests(EditingTestCase):
    def assert_refused(self, path, body, status=400, **kw):
        before = self.server.content.read_bytes()
        got, reply = self.post(path, body, **kw)
        self.assertEqual(got, status, reply)
        self.assertEqual(self.server.content.read_bytes(), before, "a refused edit must not change the file")
        return reply

    def test_only_real_photos_can_be_edited(self):
        for rel in ("nope.jpg", "../../etc/passwd", "/etc/passwd", "", "a1.JPG", "sub", "sub/", None, 5, ["a1.jpg"]):
            with self.subTest(rel=rel):
                self.assert_refused("/api/photo", {"rel": rel, "favorite": True})

    def test_wrong_types_are_refused(self):
        for body in ({"favorite": "yes"}, {"favorite": 1}, {"hidden": "true"}, {"hidden": None},
                     {"caption": 5}, {"caption": ["x"]}, {"caption": None}):
            with self.subTest(body=body):
                self.assert_refused("/api/photo", {"rel": A1, **body})

    def test_an_edit_that_changes_nothing_is_refused(self):
        self.assert_refused("/api/photo", {"rel": A1})

    def test_length_limits(self):
        self.assert_refused("/api/photo", {"rel": A1, "caption": "x" * (tc.CAPTION_MAX + 1)})
        self.assert_refused("/api/day", {"date": "2026-05-01", "title": "x" * 201})
        self.assert_refused("/api/day", {"date": "2026-05-01", "description": "x" * 20001})
        self.assertEqual(self.post("/api/photo", {"rel": A1, "caption": "x" * tc.CAPTION_MAX})[0], 200)

    def test_days_and_covers_must_exist(self):
        self.assert_refused("/api/day", {"date": "1999-01-01", "title": "x"})
        self.assert_refused("/api/day", {"date": None, "title": "x"})
        self.assert_refused("/api/cover", {"date": "1999-01-01", "rel": A1})
        self.assert_refused("/api/cover", {"date": "2026-05-01", "rel": "nope.jpg"})
        self.assert_refused("/api/day", {"date": "2026-05-01"})
        self.assert_refused("/api/day", {"date": "2026-05-01", "title": 5})

    def test_bodies_that_are_not_json_objects(self):
        for raw in (b"{bad", b"[1,2]", b'"text"', b"null", b"\xff\xfe"):
            with self.subTest(raw=raw):
                before = self.server.content.read_bytes()
                status, _, _ = self.server.request("POST", "/api/photo", {"Content-Type": "application/json"}, raw)
                self.assertEqual(status, 400)
                self.assertEqual(self.server.content.read_bytes(), before)

    def test_the_wrong_content_type_is_refused(self):
        for ctype in ("text/plain", "application/x-www-form-urlencoded", "multipart/form-data"):
            with self.subTest(ctype=ctype):
                status, _, _ = self.server.request("POST", "/api/photo", {"Content-Type": ctype},
                                                   json.dumps({"rel": A1, "favorite": True}).encode())
                self.assertEqual(status, 415)

    def test_content_type_parameters_are_allowed(self):
        status, _, _ = self.server.request("POST", "/api/photo", {"Content-Type": "application/json; charset=utf-8"},
                                           json.dumps({"rel": A1, "favorite": True}).encode())
        self.assertEqual(status, 200)

    def test_oversized_requests_are_refused(self):
        status, _, _ = self.server.request("POST", "/api/photo", {"Content-Type": "application/json"}, b"x" * 70000)
        self.assertEqual(status, 413)

    def test_unknown_api_paths_and_methods(self):
        self.assertEqual(self.server.request("POST", "/api/other", {"Content-Type": "application/json"}, b"{}")[0], 404)
        self.assertEqual(self.server.get("/api/photo")[0], 404)
        self.assertEqual(self.server.request("PUT", "/api/photo", {"Content-Type": "application/json"}, b"{}")[0], 501)

    def test_requests_from_another_web_page_are_refused(self):
        body = {"rel": A1, "favorite": True}
        for origin in ("http://evil.example", "https://127.0.0.1", f"http://127.0.0.1:{self.server.port + 1}", "null"):
            with self.subTest(origin=origin):
                self.assert_refused("/api/photo", body, status=403, headers={"Origin": origin})

    def test_the_servers_own_pages_may_edit(self):
        origin = f"http://127.0.0.1:{self.server.port}"
        self.assertEqual(self.post("/api/photo", {"rel": A1, "favorite": True}, headers={"Origin": origin})[0], 200)

    def test_a_foreign_host_header_is_refused(self):
        self.assert_refused("/api/photo", {"rel": A1, "favorite": True}, status=403, headers={"Host": "evil.example"})


class EditApiSafetyTests(EditingTestCase):
    def test_a_file_with_a_syntax_error_is_never_overwritten(self):
        broken = '{"title": "half done",}'
        self.server.content.write_text(broken, encoding="utf-8")
        status, reply = self.post("/api/photo", {"rel": A1, "favorite": True})
        self.assertEqual(status, 409)
        self.assertIn("Nothing was saved", reply["error"])
        self.assertEqual(self.server.content.read_text(encoding="utf-8"), broken)

    def test_editing_works_again_once_the_file_is_fixed(self):
        self.server.content.write_text("{", encoding="utf-8")
        self.assertEqual(self.post("/api/photo", {"rel": A1, "favorite": True})[0], 409)
        self.reset({"title": "fixed"})
        self.assertEqual(self.post("/api/photo", {"rel": A1, "favorite": True})[0], 200)
        self.assertEqual(self.saved()["title"], "fixed")

    def test_a_wrongly_shaped_file_is_refused_too(self):
        for shape in ('{"photos": []}', '{"days": 5}', "[]"):
            with self.subTest(shape=shape):
                self.server.content.write_text(shape, encoding="utf-8")
                self.assertEqual(self.post("/api/photo", {"rel": A1, "favorite": True})[0], 409)
                self.assertEqual(self.server.content.read_text(encoding="utf-8"), shape)

    def test_many_simultaneous_edits_lose_nothing(self):
        photos = ["a1.jpg", "a2.jpg", "sub/a3.jpg", "b1.jpg", "b2.jpg"]
        outcomes = []

        def favorite(rel):
            try:
                outcomes.append(self.post("/api/photo", {"rel": rel, "favorite": True})[0])
            except OSError as e:
                outcomes.append(f"dropped: {e}")

        def title(i):
            try:
                outcomes.append(self.post("/api/day", {"date": "2026-05-0" + str(1 + i % 2), "title": f"t{i}"})[0])
            except OSError as e:
                outcomes.append(f"dropped: {e}")

        threads = [threading.Thread(target=favorite, args=(rel,)) for rel in photos for _ in range(4)]
        threads += [threading.Thread(target=title, args=(i,)) for i in range(20)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(outcomes, [200] * 40)            # every edit answered (none dropped, none refused)
        c = self.saved()                                  # still valid JSON, and every photo got its star
        self.assertEqual({rel for rel, e in c["photos"].items() if e.get("favorite")}, set(photos))
        self.assertTrue(c["days"]["2026-05-01"]["title"].startswith("t"))


class EditorAndVisitorViewTests(EditingTestCase):
    def setUp(self):
        self.reset({"photos": {A3: {"hidden": True}}})

    def thumbs(self, path):
        return len(re.findall(r'class="thumb', self.server.text(path)))

    def test_the_editor_sees_controls_and_everything_including_hidden_photos(self):
        html = self.server.text("/day/2026-05-01")
        for needle in ('id="day-editor"', 'id="edit-caption"', "editable: true"):
            self.assertIn(needle, html)
        self.assertEqual(self.thumbs("/day/2026-05-01"), 3)
        self.assertIn("is-hidden", html)
        self.assertIn(A3, [s["rel"] for s in slides_of(html)])

    def test_the_visitor_preview_has_no_controls_no_paths_and_no_hidden_photos(self):
        html = self.server.text("/day/2026-05-01?preview=1")
        for needle in ("day-editor", "edit-caption", '"rel"', "a3.jpg"):
            self.assertNotIn(needle, html)
        self.assertIn("editable: false", html)
        self.assertEqual(self.thumbs("/day/2026-05-01?preview=1"), 2)

    def test_the_editor_can_still_open_hidden_files(self):
        ids = item_ids(self.server, "2026-05-01")
        self.assertEqual(len(ids), 3)                       # a1, a2 and the hidden sub/a3
        for kind in ("thumb", "media", "download"):
            self.assertEqual(self.server.get(f"/{kind}/{ids[-1]}")[0], 200, kind)

    def test_a_day_with_only_hidden_photos_vanishes_for_visitors_only(self):
        self.reset({"photos": {"b1.jpg": {"hidden": True}, "b2.jpg": {"hidden": True}}})
        self.assertEqual(self.server.get("/day/2026-05-02")[0], 200)                 # editor
        self.assertEqual(self.server.get("/day/2026-05-02?preview=1")[0], 404)       # visitor
        self.assertIn("/day/2026-05-02", self.server.text("/"))                      # editor's homepage lists it
        self.assertNotIn("/day/2026-05-02", self.server.text("/?preview=1"))         # visitor's does not

    def test_hiding_takes_effect_without_a_restart(self):
        self.assertEqual(self.thumbs("/day/2026-05-02?preview=1"), 2)
        self.post("/api/photo", {"rel": "b2.jpg", "hidden": True})
        self.assertEqual(self.thumbs("/day/2026-05-02?preview=1"), 1)
        self.post("/api/photo", {"rel": "b2.jpg", "hidden": False})
        self.assertEqual(self.thumbs("/day/2026-05-02?preview=1"), 2)

    def test_the_cover_chosen_in_the_editor_is_used_on_the_homepage(self):
        self.post("/api/cover", {"date": "2026-05-01", "rel": "a2.jpg"})
        covers = {}
        html = self.server.text("/")
        for m in re.finditer(r'href="/day/([^"]+)">\s*<div class="cover" style="background-image:url\(\'([^\']+)\'\)', html):
            covers[m.group(1)] = m.group(2)
        ids = item_ids(self.server, "2026-05-01")
        self.assertTrue(covers["2026-05-01"].startswith(f"/thumb/{ids[1]}?"), covers)       # a2 is the second photo


class ReadOnlyServerTests(EditingTestCase):
    server_args = ("--read-only",)

    def setUp(self):
        self.reset({"photos": {A3: {"hidden": True}}})

    def test_editing_is_switched_off_for_everyone(self):
        before = self.server.content.read_bytes()
        status, reply = self.post("/api/photo", {"rel": A1, "favorite": True})
        self.assertEqual(status, 403)
        self.assertIn("read-only", reply["error"])
        self.assertEqual(self.server.content.read_bytes(), before)

    def test_pages_have_no_editor_and_no_hidden_photos(self):
        html = self.server.text("/day/2026-05-01")
        for needle in ("day-editor", "edit-caption", '"rel"', "a3.jpg"):
            self.assertNotIn(needle, html)

    def test_hidden_files_cannot_be_fetched_even_by_working_out_their_key(self):
        # a URL key is a hash of the file's path, so anyone who can guess the
        # path can compute the key: being hidden must be enforced on every fetch
        from types import SimpleNamespace
        from albummaker.server import assign_keys
        names = ("a1.jpg", "a2.jpg", "b1.jpg", "b2.jpg", "nodate.jpg", A3)
        items = [SimpleNamespace(rel=n) for n in names]
        assign_keys(items)
        keys = {it.rel: it.key for it in items}
        for kind in ("thumb", "media", "download"):
            for name in names[:-1]:
                self.assertEqual(self.server.get(f"/{kind}/{keys[name]}")[0], 200, (kind, name))
            self.assertEqual(self.server.get(f"/{kind}/{keys[A3]}")[0], 404, kind)
            self.assertEqual(self.server.get(f"/{kind}/0")[0], 404, kind)

    def test_the_startup_message_does_not_offer_editing(self):
        log = self.server.log()
        self.assertIn("Serving at", log)                # the startup message was written ...
        self.assertNotIn("Editing works", log)          # ... and doesn't offer editing
        self.assertNotIn("Editor link", log)


class LanModeEditingTests(TempDirMixin, unittest.TestCase):
    def test_the_computer_running_the_server_can_still_edit_in_lan_mode(self):
        tmp = self.make_tmp()
        app = install_app(tmp / "app")
        standard_trip(tmp / "photos")
        server = AlbumMaker(app, tmp / "photos", tmp, "--lan").start()
        self.addCleanup(server.stop)
        status, _ = server.post_json("/api/photo", {"rel": A1, "favorite": True})
        self.assertEqual(status, 200)
        self.assertEqual(read_json(server.content)["photos"][A1], {"favorite": True})
        self.assertIn("day-editor", server.text("/day/2026-05-01"))


if __name__ == "__main__":
    unittest.main()
