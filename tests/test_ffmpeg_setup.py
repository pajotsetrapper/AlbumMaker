# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Pieter Remi Coppens
import io
import unittest
from contextlib import redirect_stdout
from unittest import mock

from albummaker import ffmpeg_setup


class InstallPlanTests(unittest.TestCase):
    def test_windows_uses_winget_when_available(self):
        with mock.patch.object(ffmpeg_setup.sys, "platform", "win32"), mock.patch.object(
            ffmpeg_setup.shutil, "which", return_value="C:/winget.exe"
        ):
            plan = ffmpeg_setup.install_plan()
        self.assertEqual(plan[:4], ["C:/winget.exe", "install", "--id", "Gyan.FFmpeg"])

    def test_windows_without_winget_has_no_plan(self):
        with mock.patch.object(ffmpeg_setup.sys, "platform", "win32"), mock.patch.object(
            ffmpeg_setup.shutil, "which", return_value=None
        ):
            self.assertIsNone(ffmpeg_setup.install_plan())

    def test_mac_uses_homebrew(self):
        with mock.patch.object(ffmpeg_setup.sys, "platform", "darwin"), mock.patch.object(
            ffmpeg_setup, "_brew", return_value="/opt/homebrew/bin/brew"
        ):
            self.assertEqual(
                ffmpeg_setup.install_plan(), ["/opt/homebrew/bin/brew", "install", "ffmpeg"]
            )

    def test_linux_without_homebrew_never_runs_sudo(self):
        with mock.patch.object(ffmpeg_setup.sys, "platform", "linux"), mock.patch.object(
            ffmpeg_setup, "_brew", return_value=None
        ):
            self.assertIsNone(ffmpeg_setup.install_plan())
        self.assertIn("sudo apt install ffmpeg", self._manual("linux"))

    @staticmethod
    def _manual(platform):
        with mock.patch.object(ffmpeg_setup.sys, "platform", platform):
            return ffmpeg_setup.manual_instructions()


class SuggestedCommandTests(unittest.TestCase):
    def test_one_pasteable_command_per_platform(self):
        with mock.patch.object(ffmpeg_setup.sys, "platform", "win32"):
            self.assertEqual(ffmpeg_setup.suggested_command(), "winget install Gyan.FFmpeg")
        with mock.patch.object(ffmpeg_setup.sys, "platform", "darwin"):
            self.assertEqual(ffmpeg_setup.suggested_command(), "brew install ffmpeg")
        with mock.patch.object(ffmpeg_setup.sys, "platform", "linux"), mock.patch.object(
            ffmpeg_setup.shutil, "which", side_effect=lambda name: "/bin/dnf" if name == "dnf" else None
        ):
            self.assertEqual(ffmpeg_setup.suggested_command(), "sudo dnf install ffmpeg-free")
        with mock.patch.object(ffmpeg_setup.sys, "platform", "linux"), mock.patch.object(
            ffmpeg_setup.shutil, "which", return_value=None
        ):
            self.assertEqual(ffmpeg_setup.suggested_command(), "sudo apt install ffmpeg")


class InstallCommandTests(unittest.TestCase):
    def run_main(self, argv, **patches):
        out = io.StringIO()
        with redirect_stdout(out):
            code = ffmpeg_setup.main(argv)
        return code, out.getvalue()

    def test_does_nothing_when_ffmpeg_is_present(self):
        with mock.patch.object(ffmpeg_setup, "ffmpeg_missing", return_value=False), mock.patch(
            "albummaker.metadata.find_multimedia_tool", return_value="/usr/bin/ffmpeg"
        ), mock.patch.object(ffmpeg_setup.subprocess, "run") as run:
            code, out = self.run_main(["--yes"])
        self.assertEqual(code, 0)
        self.assertIn("already installed", out)
        run.assert_not_called()

    def test_refuses_without_consent_when_not_interactive(self):
        with mock.patch.object(ffmpeg_setup, "ffmpeg_missing", return_value=True), mock.patch.object(
            ffmpeg_setup, "install_plan", return_value=["brew", "install", "ffmpeg"]
        ), mock.patch.object(ffmpeg_setup.sys, "stdin", io.StringIO("")), mock.patch.object(
            ffmpeg_setup.subprocess, "run"
        ) as run:
            code, out = self.run_main([])
        self.assertEqual(code, 2)
        self.assertIn("--yes", out)
        run.assert_not_called()

    def test_print_only_shows_the_command_and_runs_nothing(self):
        with mock.patch.object(ffmpeg_setup, "ffmpeg_missing", return_value=True), mock.patch.object(
            ffmpeg_setup, "install_plan", return_value=["brew", "install", "ffmpeg"]
        ), mock.patch.object(ffmpeg_setup.subprocess, "run") as run:
            code, out = self.run_main(["--print-only"])
        self.assertEqual(code, 0)
        self.assertIn("brew install ffmpeg", out)
        self.assertIn("not part of AlbumMaker", out)
        run.assert_not_called()

    def test_runs_the_package_manager_after_yes(self):
        states = iter([True, False])
        with mock.patch.object(
            ffmpeg_setup, "ffmpeg_missing", side_effect=lambda: next(states)
        ), mock.patch.object(
            ffmpeg_setup, "install_plan", return_value=["brew", "install", "ffmpeg"]
        ), mock.patch.object(
            ffmpeg_setup.subprocess, "run", return_value=mock.Mock(returncode=0)
        ) as run:
            code, out = self.run_main(["--yes"])
        self.assertEqual(code, 0)
        run.assert_called_once_with(["brew", "install", "ffmpeg"])
        self.assertIn("FFmpeg is ready", out)

    def test_without_a_package_manager_it_prints_manual_steps(self):
        with mock.patch.object(ffmpeg_setup, "ffmpeg_missing", return_value=True), mock.patch.object(
            ffmpeg_setup, "install_plan", return_value=None
        ):
            code, out = self.run_main(["--yes"])
        self.assertEqual(code, 1)
        self.assertIn("ffmpeg.org/download.html", out)


class HeifDecoderTests(unittest.TestCase):
    def test_metadata_prefers_the_decode_only_plugin(self):
        with open(ffmpeg_setup.__file__.replace("ffmpeg_setup.py", "metadata.py"),
                  encoding="utf-8") as handle:
            source = handle.read()
        self.assertLess(source.index('"pi_heif"'), source.index('"pillow_heif"'))


if __name__ == "__main__":
    unittest.main()
