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

    def test_notice_credits_qt_python_runtime_and_points_to_source(self):
        with mock.patch.object(
            build_executable, "_python_package_notices", return_value="Python notices"
        ):
            notice = build_executable._notice()
        from albummaker import __version__
        self.assertIn("PySide6, shiboken6 and Qt", notice)
        self.assertIn("runtime ---", notice)
        self.assertIn("PSF", notice)              # the Python license text itself
        self.assertIn("PyInstaller bootloader", notice)
        self.assertIn(f"/tree/v{__version__}", notice)
        self.assertIn("modify or replace these libraries", notice)

    def test_albummaker_is_importable_when_run_as_a_script(self):
        # "python build_executable.py" in tools/ (or python tools/...) only puts
        # tools/ on the import path, yet the notice imports albummaker.
        import subprocess
        import sys
        script = Path(build_executable.__file__).resolve()
        code = ("import runpy; runpy.run_path(%r, run_name='build'); "
                "import albummaker; print(albummaker.__version__)") % str(script)
        result = subprocess.run([sys.executable, "-I", "-c", code], cwd=script.parent,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_notice_includes_the_licenses_of_the_bundled_map_library_and_fonts(self):
        with tempfile.TemporaryDirectory() as folder:
            assets = Path(folder)
            (assets / "licenses").mkdir()
            (assets / "VERSIONS.txt").write_text("leaflet 1.9.4  (checked against its published SHA-512)")
            (assets / "licenses" / "leaflet-LICENSE.txt").write_text("BSD 2-Clause License")
            (assets / "licenses" / "fraunces-LICENSE.txt").write_text("SIL OPEN FONT LICENSE")
            with mock.patch.object(build_executable, "_python_package_notices", return_value="P"):
                notice = build_executable._notice(assets)
        self.assertIn("Map library and fonts", notice)
        self.assertIn("leaflet 1.9.4", notice)
        self.assertIn("BSD 2-Clause License", notice)
        self.assertIn("SIL OPEN FONT LICENSE", notice)

    def test_the_app_carries_the_offline_assets(self):
        source = Path(build_executable.__file__).read_text(encoding="utf-8")
        self.assertIn("_data_argument(assets_dir, assets_dir.name)", source)
        gui_entry = (build_executable.ROOT / "album_gui.py").read_text(encoding="utf-8")
        self.assertIn("bundled_assets_dir()", gui_entry)      # the smoke test fails without them

    def test_executable_build_does_not_bundle_ffmpeg(self):
        source = Path(build_executable.__file__).read_text(encoding="utf-8")
        self.assertNotIn("static_ffmpeg", source)
        self.assertNotIn("--add-binary\",\n                _binary_argument(ffmpeg", source)
        requirements = (build_executable.ROOT / "requirements-packaging.txt").read_text()
        self.assertNotIn("static-ffmpeg", requirements)
        base = (build_executable.ROOT / "requirements.txt").read_text()
        self.assertIn('pi-heif>=0.18; python_version < "3.15"', base)
        # pillow-heif (GPL x265 inside) is only a source-install fallback for 3.15+.
        self.assertIn('pillow-heif>=1.0; python_version >= "3.15"', base)
        self.assertIn("-r requirements.txt", requirements)

    def test_build_refuses_pillow_heif_and_needs_pi_heif(self):
        both = lambda name: object() if name in ("pi_heif", "pillow_heif") else None
        only_pi = lambda name: object() if name == "pi_heif" else None
        with mock.patch("importlib.util.find_spec", side_effect=both):
            with self.assertRaises(SystemExit):
                build_executable._require_decode_only_heif()
        with mock.patch("importlib.util.find_spec", side_effect=only_pi):
            build_executable._require_decode_only_heif()

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
