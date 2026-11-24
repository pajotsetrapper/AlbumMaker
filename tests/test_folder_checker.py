# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Pieter Remi Coppens
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from albummaker.gui.folders import FolderChecker


def collect(checker, timeout=5.0):
    answers = []
    deadline = time.monotonic() + timeout
    while checker.busy and time.monotonic() < deadline:
        answers += checker.poll()
        time.sleep(0.01)
    return answers + checker.poll()


class FolderCheckerTests(unittest.TestCase):
    def test_reports_which_folders_exist(self):
        with tempfile.TemporaryDirectory() as tmp:
            here, gone, a_file = str(Path(tmp)), str(Path(tmp) / "gone"), Path(tmp) / "f.txt"
            a_file.write_text("x", encoding="utf-8")
            checker = FolderChecker()
            checker.start([here, gone, str(a_file)])
            self.assertEqual(dict(collect(checker)), {here: True, gone: False, str(a_file): False})
            self.assertFalse(checker.busy)

    def test_nothing_to_check_is_not_busy(self):
        checker = FolderChecker()
        checker.start([])
        self.assertFalse(checker.busy)
        self.assertEqual(checker.poll(), [])

    def test_one_stuck_folder_does_not_hold_up_the_others(self):
        release = threading.Event()
        real_is_dir = Path.is_dir

        def is_dir(path):
            if path.name == "sleeping-nas":
                release.wait(10)          # a share that does not answer
            return real_is_dir(path)

        with tempfile.TemporaryDirectory() as tmp:
            fine = [str(Path(tmp))] * 5
            with mock.patch.object(Path, "is_dir", is_dir):
                checker = FolderChecker(workers=3)
                checker.start(["/sleeping-nas"] + fine)
                deadline = time.monotonic() + 5
                answers = []
                while len(answers) < 5 and time.monotonic() < deadline:
                    answers += checker.poll()
                    time.sleep(0.01)
                self.assertEqual(len(answers), 5)         # all the others answered ...
                self.assertTrue(checker.busy)             # ... while the stuck one is still pending
                release.set()
                answers += collect(checker)
        self.assertEqual(len(answers), 6)

    def test_an_error_while_asking_counts_as_missing(self):
        with mock.patch.object(Path, "is_dir", side_effect=PermissionError("denied")):
            checker = FolderChecker()
            checker.start(["/anything"])
            self.assertEqual(collect(checker), [("/anything", False)])


if __name__ == "__main__":
    unittest.main()
