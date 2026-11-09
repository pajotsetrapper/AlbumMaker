# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Pieter Remi Coppens
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from albummaker.metadata import find_multimedia_tool
from tools import build_executable


class ExecutableLicenseNoticeTests(unittest.TestCase):
    def test_ffmpeg_license_texts_are_read_from_the_repository(self):
        license_output = (
            "ffmpeg is free software; you can redistribute it and/or modify\n"
            "it under the terms of the GNU General Public License as published by\n"
            "the Free Software Foundation; either version 3 of the License, or\n"
            "(at your option) any later version."
        )
        version_output = "ffmpeg version 8.0\nconfiguration: --enable-gpl"
        run_results = [
            mock.Mock(stdout=license_output, stderr=""),
            mock.Mock(stdout=license_output, stderr=""),
            mock.Mock(stdout=version_output, stderr=""),
        ]
        with mock.patch.object(
            build_executable.subprocess, "run", side_effect=run_results
        ):
            with mock.patch.object(
                build_executable,
                "_python_package_notices",
                return_value="Python notices",
            ):
                with mock.patch.object(
                    build_executable,
                    "_read_license_text",
                    wraps=build_executable._read_license_text,
                ) as read_license:
                    notice = build_executable._ffmpeg_notice(
                        Path("ffmpeg"), Path("ffprobe")
                    )

        self.assertIn(
            "FFmpeg source code: https://github.com/FFmpeg/FFmpeg/tree/n8.0",
            notice,
        )
        self.assertEqual(
            [call.args[0] for call in read_license.call_args_list],
            [
                build_executable.GPL_LICENSE_FILES["3"],
                build_executable.GPL_LICENSE_FILES["2"],
                build_executable.LGPL_V3_LICENSE_FILE,
            ],
        )
        self.assertIn("GNU GENERAL PUBLIC LICENSE", notice)
        self.assertIn("GNU LESSER GENERAL PUBLIC LICENSE", notice)
        self.assertIn("Version 2, June 1991", notice)
        self.assertIn("Version 3, 29 June 2007", notice)

    def test_missing_repository_license_reports_its_path(self):
        path = Path("missing-license.txt")
        with mock.patch.object(Path, "read_text", side_effect=FileNotFoundError(path)):
            with self.assertRaisesRegex(RuntimeError, str(path)):
                build_executable._read_license_text(path, "GNU GPL version 3")


class BundledMultimediaToolTests(unittest.TestCase):
    def test_prefers_tools_bundled_with_a_frozen_executable(self):
        with tempfile.TemporaryDirectory() as tmp:
            tool_dir = Path(tmp) / "ffmpeg"
            tool_dir.mkdir()
            suffix = ".exe" if os.name == "nt" else ""
            ffprobe = tool_dir / f"ffprobe{suffix}"
            ffprobe.touch()
            with mock.patch("albummaker.metadata.sys._MEIPASS", tmp, create=True), mock.patch(
                "albummaker.metadata.shutil.which", return_value="/system/ffprobe"
            ):
                self.assertEqual(find_multimedia_tool("ffprobe"), str(ffprobe))

    def test_falls_back_to_system_tools_outside_the_app_bundle(self):
        with mock.patch("albummaker.metadata.sys._MEIPASS", None, create=True), mock.patch(
            "albummaker.metadata.shutil.which", return_value="/system/ffmpeg"
        ):
            self.assertEqual(find_multimedia_tool("ffmpeg"), "/system/ffmpeg")

    def test_reports_when_no_system_or_bundled_tool_exists(self):
        with mock.patch("albummaker.metadata.sys._MEIPASS", None, create=True), mock.patch(
            "albummaker.metadata.shutil.which", return_value=None
        ):
            self.assertIsNone(find_multimedia_tool("ffprobe"))

    def test_rejects_unknown_tool_names(self):
        with self.assertRaisesRegex(ValueError, "Unsupported multimedia tool"):
            find_multimedia_tool("ffplay")


if __name__ == "__main__":
    unittest.main()
