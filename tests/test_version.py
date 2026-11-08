import contextlib
import io
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import albummaker
import albummaker.__main__ as entry
import albummaker.build as build
import albummaker.server as server
from tools import check_version

ROOT = Path(__file__).resolve().parent.parent
VERSION = albummaker.__version__


def run_script(*args):
    return subprocess.run([sys.executable, *args], cwd=ROOT, capture_output=True, text=True, timeout=60)


class VersionTests(unittest.TestCase):
    def test_version_is_a_plain_number(self):
        self.assertRegex(VERSION, r"^\d+\.\d+\.\d+$")

    def test_server_and_build_accept_version_flag(self):
        for module in (server, build):
            out = io.StringIO()
            with contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as caught:
                module.main(["--version"])
            self.assertEqual(caught.exception.code, 0)
            self.assertEqual(out.getvalue().strip(), f"AlbumMaker {VERSION}")

    def test_module_entry_point_accepts_version(self):
        for argv in (["--version"], ["-V"], ["gui", "--version"]):
            out = io.StringIO()
            with mock.patch.object(sys, "argv", ["albummaker", *argv]), contextlib.redirect_stdout(out):
                entry.main()
            self.assertEqual(out.getvalue().strip(), f"AlbumMaker {VERSION}")

    def test_scripts_print_version_when_run_directly(self):
        for script in ("album_server.py", "generate_site.py", "album_gui.py"):
            result = run_script(script, "--version")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), f"AlbumMaker {VERSION}")

    def test_site_footers_and_build_record_carry_the_version(self):
        self.assertIn(VERSION, build.StaticUrls.footer)
        self.assertIn(VERSION, str(server.LiveUrls.footer))
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            build.save_manifest(out, 400, {}, [])
            text = (out / build.SITE_MARKER).read_text(encoding="utf-8")
        self.assertIn(f'"app_version": "{VERSION}"', text)

    def test_check_version_tool(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(check_version.main([f"v{VERSION}"]), 0)
            self.assertEqual(check_version.main([f"refs/tags/v{VERSION}"]), 0)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(check_version.main(["v0.0.1"]), 1)


if __name__ == "__main__":
    unittest.main()
