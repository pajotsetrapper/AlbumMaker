"""Run as a zipapp or with `python -m tripsite`:

    python tripsite.pyz serve -i /path/to/photos      (= trip_server.py)
    python tripsite.pyz build -i /path/to/photos -o site   (= generate_site.py)
    python -m tripsite gui                            (optional PySide6 GUI)
"""

import sys


def main():
    commands = {"serve": "tripsite.server", "build": "tripsite.build", "gui": "tripsite.gui.app"}
    if len(sys.argv) < 2 or sys.argv[1] not in commands:
        sys.exit("usage: tripsite {serve|build|gui} [options]   "
                 "(serve/build accept --help; gui opens the desktop app)")
    if sys.argv[1] == "gui" and "--help" in sys.argv[2:]:
        print("usage: tripsite gui\n\nOpen the optional TripServer desktop interface.")
        return
    import importlib
    module = importlib.import_module(commands[sys.argv[1]])
    sys.argv = [f"tripsite {sys.argv[1]}"] + sys.argv[2:]
    module.main()


if __name__ == "__main__":
    main()
