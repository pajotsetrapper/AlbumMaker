import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from albummaker.metadata import find_multimedia_tool


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
