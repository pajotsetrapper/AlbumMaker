from io import BytesIO
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from albummaker.metadata import find_multimedia_tool
from tools import build_executable


class ExecutableLicenseNoticeTests(unittest.TestCase):
    def test_ffmpeg_license_text_urls_do_not_depend_on_ffmpeg_tag(self):
        license_output = (
            "ffmpeg is free software; you can redistribute it and/or modify\n"
            "it under the terms of the GNU General Public License as published by\n"
            "the Free Software Foundation; either version 2 of the License, or\n"
            "(at your option) any later version."
        )
        version_output = "ffmpeg version 8.0\nconfiguration: --enable-gpl"
        run_results = [
            mock.Mock(stdout=license_output, stderr=""),
            mock.Mock(stdout=license_output, stderr=""),
            mock.Mock(stdout=version_output, stderr=""),
        ]
        gpl_text = "GPL license text for test"
        lgpl_text = "LGPL license text for test"

        def fake_urlopen(url, timeout):
            text = lgpl_text if url == build_executable.LGPL_V3_LICENSE_URL else gpl_text
            return BytesIO(text.encode("utf-8"))

        with mock.patch.object(
            build_executable.subprocess, "run", side_effect=run_results
        ):
            with mock.patch.object(
                build_executable.urllib.request, "urlopen", side_effect=fake_urlopen
            ) as urlopen:
                with mock.patch.object(
                    build_executable, "_python_package_notices", return_value="Python notices"
                ):
                    notice = build_executable._ffmpeg_notice(
                        Path("ffmpeg"), Path("ffprobe")
                    )

        self.assertIn("FFmpeg source code: https://github.com/FFmpeg/FFmpeg/tree/n8.0", notice)
        self.assertEqual(
            [call.args[0] for call in urlopen.call_args_list],
            [
                build_executable.GPL_LICENSE_URLS["2"],
                build_executable.LGPL_V3_LICENSE_URL,
            ],
        )
        self.assertIn(gpl_text, notice)
        self.assertIn(lgpl_text, notice)

    def test_license_download_failure_identifies_the_source_url(self):
        url = "https://www.gnu.org/licenses/gpl-3.0.txt"
        with mock.patch.object(
            build_executable.urllib.request,
            "urlopen",
            side_effect=build_executable.urllib.error.HTTPError(
                url, 404, "Not Found", {}, None
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, url):
                build_executable._download_license_text(url, "GNU GPL version 3")


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
