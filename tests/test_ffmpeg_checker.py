# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Pieter Remi Coppens
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path

from albummaker.gui.ffmpeg_check import FfmpegChecker

ROOT = Path(__file__).resolve().parent.parent


def collect(checker, timeout=5.0):
    """Wait for the running check to finish and return its answer."""
    answer = None
    deadline = time.monotonic() + timeout
    while checker.busy and time.monotonic() < deadline:
        found = checker.poll()
        answer = answer if found is None else found
        time.sleep(0.01)
    found = checker.poll()
    return answer if found is None else found


class FfmpegCheckerTests(unittest.TestCase):
    def test_reports_the_answer_without_blocking_the_caller(self):
        release = threading.Event()

        def slow_check():
            release.wait(5)
            return True

        checker = FfmpegChecker(check=slow_check)
        started = time.monotonic()
        self.assertTrue(checker.request())
        self.assertLess(time.monotonic() - started, 1.0)     # request() returned while the check waits
        self.assertTrue(checker.busy)
        self.assertIsNone(checker.poll())
        release.set()
        self.assertIs(collect(checker), True)
        self.assertFalse(checker.busy)

    def test_a_recent_answer_is_reused_until_it_expires_or_is_forced(self):
        now = [100.0]
        calls = []
        checker = FfmpegChecker(check=lambda: calls.append(1) or False, max_age=5.0,
                                clock=lambda: now[0])
        checker.request()
        collect(checker)
        self.assertIs(checker.fresh(), False)
        self.assertFalse(checker.request())                  # recent: no new search
        now[0] += 6
        self.assertIsNone(checker.fresh())                   # expired
        self.assertTrue(checker.request())
        collect(checker)
        self.assertTrue(checker.request(force=True))         # forced even though recent
        collect(checker)
        self.assertEqual(len(calls), 3)

    def test_a_forced_request_during_a_check_makes_it_check_again(self):
        gate = threading.Event()
        answers = iter([True, False])           # FFmpeg gets installed while the first search runs

        def check():
            gate.wait(5)
            return next(answers)

        checker = FfmpegChecker(check=check)
        checker.request()
        self.assertFalse(checker.request(force=True))        # the running thread will repeat
        gate.set()
        self.assertIs(collect(checker), False)               # the newest answer wins
        self.assertIs(checker.fresh(), False)

    def test_a_failed_check_leaves_no_answer(self):
        def broken():
            raise OSError("PATH entry not readable")

        checker = FfmpegChecker(check=broken)
        checker.request()
        self.assertIsNone(collect(checker))
        self.assertIsNone(checker.fresh())
        self.assertTrue(checker.request())                   # and it can be asked again
        collect(checker)


class StartupImportTests(unittest.TestCase):
    def test_looking_for_ffmpeg_does_not_load_pillow(self):
        """The window asks ffmpeg_setup while it opens; Pillow and the HEIF plugin are only needed to build albums."""
        code = ("import sys; import albummaker.ffmpeg_setup as f; f.ffmpeg_missing(); "
                "print('PIL' in sys.modules, 'albummaker.metadata' in sys.modules)")
        result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True,
                                text=True, check=True)
        self.assertEqual(result.stdout.split(), ["False", "False"])

    def test_metadata_still_offers_the_tool_finder(self):
        from albummaker import ffmpeg_setup, metadata
        self.assertIs(metadata.find_multimedia_tool, ffmpeg_setup.find_multimedia_tool)


if __name__ == "__main__":
    unittest.main()
