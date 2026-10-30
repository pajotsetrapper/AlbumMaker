#!/usr/bin/env python3
"""
trip_server.py — serve your trip photos/videos as a live website, generated
on the fly, with in-page editing of titles, captions, favorites and covers.

    python trip_server.py -i /path/to/photos

Then open the URL it prints (defaults to http://127.0.0.1:8000/).

The code lives in the tripsite package next to this file (tripsite/server.py).
See README.md for the full walkthrough.
"""

import sys

try:
    import tripsite.server as _impl
except ModuleNotFoundError as e:
    if not (e.name or "").startswith("tripsite"):
        raise
    sys.exit("The tripsite folder is missing: keep it next to this script.")

# keep `import trip_server; trip_server.RequestHandler` etc. working
globals().update({k: v for k, v in vars(_impl).items() if not k.startswith("__")})

if __name__ == "__main__":
    _impl.main()
