#!/usr/bin/env python3
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Pieter Remi Coppens
"""Launch the optional AlbumMaker desktop interface."""

import subprocess
import sys

if sys.argv[1:] == ["--version"]:      # works even where PySide6 is not installed
    from albummaker import __version__
    print(f"AlbumMaker {__version__}")
    raise SystemExit(0)

try:
    from albummaker.gui.app import MainWindow, main
except ModuleNotFoundError as exc:
    if exc.name == "PySide6":
        sys.exit("The AlbumMaker GUI needs PySide6. Install it with: python -m pip install -r requirements-gui.txt")
    raise


if __name__ == "__main__":
    if sys.argv[1:] == ["--packaging-smoke-test"]:
        from PySide6.QtWidgets import QApplication

        from albummaker.gui.commands import _command_prefix
        from albummaker.gui.settings import default_settings

        from albummaker.offline import bundled_assets_dir
        if bundled_assets_dir() is None:
            sys.exit("The map library and fonts are missing from this build.")

        app = QApplication([])
        window = MainWindow(default_settings())
        window._add_trip_item(".", "")
        window.hide()
        app.processEvents()
        cli = _command_prefix(sys.executable, "serve")[0]
        options = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
        subprocess.run(
            [cli, "--packaging-smoke-test"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **options,
        )
        raise SystemExit(0)
    raise SystemExit(main())
