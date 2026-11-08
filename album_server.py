#!/usr/bin/env python3
"""
album_server.py — serve your trip photos/videos as a live website, generated
on the fly, with in-page editing of titles, captions, favorites and covers.

    python album_server.py -i /path/to/photos

Then open the URL it prints (defaults to http://127.0.0.1:8000/).

The code lives in the albummaker package next to this file (albummaker/server.py).
See README.md for the full walkthrough.
"""

import sys

try:
    import albummaker.server as _impl
except ModuleNotFoundError as e:
    if not (e.name or "").startswith("albummaker"):
        raise
    sys.exit("The albummaker folder is missing: keep it next to this script.")

# keep `import album_server; album_server.RequestHandler` etc. working
globals().update({k: v for k, v in vars(_impl).items() if not k.startswith("__")})

if __name__ == "__main__":
    _impl.main()
