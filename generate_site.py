#!/usr/bin/env python3
"""
generate_site.py — build a static, browsable website from a folder of trip
photos and videos.

    python generate_site.py -i /path/to/photos -o /path/to/site_output

The code lives in the tripsite package next to this file (tripsite/build.py).
See README.md for the full walkthrough.
"""

import sys

try:
    import tripsite.build as _impl
except ModuleNotFoundError as e:
    if not (e.name or "").startswith("tripsite"):
        raise
    sys.exit("The tripsite folder is missing: keep it next to this script.")

# keep `import generate_site; generate_site.scan_media(...)` working
globals().update({k: v for k, v in vars(_impl).items() if not k.startswith("__")})

if __name__ == "__main__":
    _impl.main()
