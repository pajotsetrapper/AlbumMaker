#!/usr/bin/env python3
"""Check that a release tag matches albummaker.__version__ (for CI).

    python tools/check_version.py v2.0.0     # exit 0 if it matches, 1 if not
    python tools/check_version.py            # just print the version
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from albummaker import __version__  # noqa: E402


def main(argv=None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if not args:
        print(__version__)
        return 0
    tag = args[0].rsplit("/", 1)[-1]          # also accepts refs/tags/v2.0.0
    if tag.lstrip("v") != __version__:
        print(f"Tag {args[0]!r} does not match albummaker.__version__ {__version__!r}. "
              f"Update albummaker/__init__.py or the tag.", file=sys.stderr)
        return 1
    print(f"Tag {args[0]} matches version {__version__}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
