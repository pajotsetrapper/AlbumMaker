#!/usr/bin/env python3
"""Launch the optional TripServer desktop interface."""

import sys

try:
    from tripsite.gui.app import main
except ModuleNotFoundError as exc:
    if exc.name == "PySide6":
        sys.exit("The TripServer GUI needs PySide6. Install it with: python -m pip install -r requirements-gui.txt")
    raise


if __name__ == "__main__":
    raise SystemExit(main())
