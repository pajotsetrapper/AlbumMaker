#!/usr/bin/env python3
"""Package the tripsite folder into one runnable file, dist/tripsite.pyz:

    python tools/build_zipapp.py
    python dist/tripsite.pyz serve -i /path/to/photos
    python dist/tripsite.pyz build -i /path/to/photos -o site

The .pyz contains only this project's code, templates and static files; the
requirements (Pillow, pillow-heif, Jinja2) still need to be installed. A
`vendor` folder (from --fetch-offline-assets) is looked for next to the .pyz.
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
    target = dist / "tripsite.pyz"
    with tempfile.TemporaryDirectory() as tmp:
        staging = Path(tmp) / "app"
        shutil.copytree(ROOT / "tripsite", staging / "tripsite",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        (staging / "__main__.py").write_text("from tripsite.__main__ import main\nmain()\n", encoding="utf-8")
        zipapp.create_archive(staging, target, interpreter="/usr/bin/env python3", compressed=True)
    print(f"Wrote {target} ({target.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
