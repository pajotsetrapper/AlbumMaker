"""Run as a zipapp or with `python -m tripsite`:

    python tripsite.pyz serve -i /path/to/photos      (= trip_server.py)
    python tripsite.pyz build -i /path/to/photos -o site   (= generate_site.py)
"""

import sys


def main():
    commands = {"serve": "tripsite.server", "build": "tripsite.build"}
    if len(sys.argv) < 2 or sys.argv[1] not in commands:
        sys.exit("usage: tripsite {serve|build} [options]   (add --help after the command for its options)")
    import importlib
    module = importlib.import_module(commands[sys.argv[1]])
    sys.argv = [f"tripsite {sys.argv[1]}"] + sys.argv[2:]
    module.main()


if __name__ == "__main__":
    main()
