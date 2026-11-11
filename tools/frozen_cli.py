#!/usr/bin/env python3
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Pieter Remi Coppens
"""Console entry point embedded in the desktop app for its background tasks."""

import multiprocessing
import sys

from albummaker.__main__ import main


if __name__ == "__main__":
    # Needed on Windows: the site builder starts worker processes, and without
    # this each worker would start another copy of the whole program.
    multiprocessing.freeze_support()
    if sys.argv[1:] == ["--packaging-smoke-test"]:
        # FFmpeg is not bundled; check that the bundled HEIC decoder works.
        from albummaker.metadata import HEIF_SUPPORT
        if not HEIF_SUPPORT:
            sys.exit("HEIC support (pi-heif) is missing from this build.")
    else:
        main()
