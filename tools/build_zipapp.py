#!/usr/bin/env python3
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Pieter Remi Coppens
"""Package the albummaker folder into one runnable file, dist/albummaker.pyz:

    python tools/build_zipapp.py
    python dist/albummaker.pyz serve -i /path/to/photos
    python dist/albummaker.pyz build -i /path/to/photos -o site

The .pyz contains only this project's code, templates and static files; the
requirements (Pillow, pi-heif, Jinja2) still need to be installed.
Offline assets are stored in the user's AlbumMaker configuration directory.
"""

import shutil
import sys
import tempfile
import zipapp
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    dist = ROOT / "dist"
    dist.mkdir(exist_ok=True)
    target = dist / "albummaker.pyz"
    with tempfile.TemporaryDirectory() as tmp:
        staging = Path(tmp) / "app"
        shutil.copytree(ROOT / "albummaker", staging / "albummaker",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        (staging / "__main__.py").write_text("from albummaker.__main__ import main\nmain()\n", encoding="utf-8")
        zipapp.create_archive(staging, target, interpreter="/usr/bin/env python3", compressed=True)
    print(f"Wrote {target} ({target.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
