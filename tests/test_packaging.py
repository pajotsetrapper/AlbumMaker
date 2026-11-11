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
    def test_notice_has_no_ffmpeg_or_gpl2_text_and_includes_lgpl(self):
        with mock.patch.object(
            build_executable, "_python_package_notices", return_value="Python notices"
        ), mock.patch.object(
            build_executable,
            "_read_license_text",
            wraps=build_executable._read_license_text,
        ) as read_license:
            notice = build_executable._notice()

        self.assertEqual(
            [call.args[0] for call in read_license.call_args_list],
            [
                build_executable.LGPL_V3_LICENSE_FILE,
                build_executable.GPL_V3_LICENSE_FILE,
            ],
        )
        self.assertIn("does not include FFmpeg", notice)
        self.assertIn("PolyForm Noncommercial", notice)
        self.assertIn("GNU LESSER GENERAL PUBLIC LICENSE", notice)
        self.assertIn("Version 3, 29 June 2007", notice)
        self.assertNotIn("Version 2, June 1991", notice)
        self.assertNotIn("x265", notice)

    def test_executable_build_does_not_bundle_ffmpeg(self):
        source = Path(build_executable.__file__).read_text(encoding="utf-8")
        self.assertNotIn("static_ffmpeg", source)
        self.assertNotIn("--add-binary\",\n                _binary_argument(ffmpeg", source)
        requirements = (build_executable.ROOT / "requirements-packaging.txt").read_text()
        self.assertNotIn("static-ffmpeg", requirements)
        base = (build_executable.ROOT / "requirements.txt").read_text()
        self.assertIn("pi-heif", base)
        self.assertNotIn("pillow-heif", base)

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

    def test_finds_a_tool_the_user_placed_in_albummakers_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            suffix = ".exe" if os.name == "nt" else ""
            tool = Path(tmp) / "ffmpeg" / f"ffmpeg{suffix}"
            tool.parent.mkdir()
            tool.touch()
            tool.chmod(0o755)
            with mock.patch.dict(os.environ, {"ALBUMMAKER_CONFIG_DIR": tmp}), mock.patch(
                "albummaker.metadata.sys._MEIPASS", None, create=True
            ):
                self.assertEqual(
                    os.path.normcase(find_multimedia_tool("ffmpeg")), os.path.normcase(str(tool))
                )

    def test_rejects_unknown_tool_names(self):
        with self.assertRaisesRegex(ValueError, "Unsupported multimedia tool"):
            find_multimedia_tool("ffplay")


if __name__ == "__main__":
    unittest.main()
