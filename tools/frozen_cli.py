#!/usr/bin/env python3
"""Console entry point embedded in the desktop app for its background tasks."""

import subprocess
import sys

from albummaker.__main__ import main
from albummaker.metadata import multimedia_tool


if __name__ == "__main__":
    if sys.argv[1:] == ["--packaging-smoke-test"]:
        for tool in ("ffmpeg", "ffprobe"):
            subprocess.run(
                [multimedia_tool(tool), "-version"],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
    else:
        main()
