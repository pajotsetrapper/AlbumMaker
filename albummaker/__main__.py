# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Pieter Remi Coppens
"""Run as a zipapp or with `python -m albummaker`:

    python albummaker.pyz serve -i /path/to/photos      (= album_server.py)
    python albummaker.pyz build -i /path/to/photos -o site   (= generate_site.py)
    python -m albummaker gui                            (optional PySide6 GUI)
    python -m albummaker --version
"""

import sys


def main():
    if sys.argv[1:] in (["--version"], ["-V"]):
        from albummaker import __version__
        print(f"AlbumMaker {__version__}")
        return
    commands = {"serve": "albummaker.server", "build": "albummaker.build", "gui": "albummaker.gui.app"}
    if len(sys.argv) < 2 or sys.argv[1] not in commands:
        sys.exit("usage: albummaker {serve|build|gui|--version} [options]   "
                 "(serve/build accept --help; gui opens the desktop app)")
    if sys.argv[1] == "gui" and "--version" in sys.argv[2:]:
        from albummaker import __version__
        print(f"AlbumMaker {__version__}")
        return
    if sys.argv[1] == "gui" and "--help" in sys.argv[2:]:
        print("usage: albummaker gui\n\nOpen the optional AlbumMaker desktop interface.")
        return
    import importlib
    module = importlib.import_module(commands[sys.argv[1]])
    sys.argv = [f"albummaker {sys.argv[1]}"] + sys.argv[2:]
    module.main()


if __name__ == "__main__":
    main()
