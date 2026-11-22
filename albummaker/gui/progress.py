# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Pieter Remi Coppens
"""What a running task is doing, in plain words for the window's progress pop-up.

The server and the site builder only print log lines. RunProgress reads those lines
as they arrive and keeps a heading, a one-line detail and (when the task says how far
it is) a fraction. It has no Qt in it, so it is easy to test."""

from __future__ import annotations

import re
from typing import Optional

# the heading of the progress pop-up, by the kind of task the window starts
HEADINGS = {
    "server": "Preparing your trips\u2026",
    "build": "Creating your site\u2026",
    "offline assets": "Getting ready\u2026",
}
FIRST_DETAIL = {
    "server": "Starting the server\u2026",
    "build": "Starting\u2026",
    "offline assets": "Downloading the map and font files (this is only needed once)\u2026",
}
# what the user is told to expect, under the progress bar
HINTS = {
    "server": ("Your photos and videos are being read and your pages are being made. For a large "
               "album this can take a few minutes, especially the first time.\n\n"
               "Please keep AlbumMaker open. A message will appear here the moment your trips "
               "are ready."),
    "build": ("Your photos are being resized and your pages are being made. For a large album "
              "this can take a few minutes.\n\nPlease keep AlbumMaker open. A message will "
              "appear here the moment your site is ready."),
    "offline assets": ("AlbumMaker downloads the map and font files once, so the server does "
                       "not need the internet for them afterwards. The server starts by itself "
                       "when this is done."),
}

_TRIP = re.compile(r"^=== Trip (\d+) of (\d+): (.+?)\s+->\s+")
_INDEXING = re.compile(r"^(?:Indexing|Scanning) (.+?) \.\.\.$")
_FOUND = re.compile(r"^Found (\d+) media files")
_PROCESSING = re.compile(r"^Processing (\d+) file\(s\)")
_STEP = re.compile(r"^\[(\d+)/(\d+)\]")
_NOTHING_TO_DO = re.compile(r"^(?:Nothing to \(re\)generate|\d+ file\(s\) unchanged)")


def _folder_name(path: str) -> str:
    """The last part of a folder path: what people call the trip."""
    parts = [part for part in re.split(r"[\\/]+", path.strip().strip('"')) if part]
    return parts[-1] if parts else path


def _files(count: int) -> str:
    return f"{count} photo or video" if count == 1 else f"{count} photos and videos"


class RunProgress:
    """Follows one task. feed() takes each output line; heading, detail and fraction
    (None while it cannot tell how far along it is) say what to show."""

    def __init__(self, kind: str):
        self.kind = kind
        self.heading = HEADINGS.get(kind, "Working\u2026")
        self.detail = FIRST_DETAIL.get(kind, "Starting\u2026")
        self.hint = HINTS.get(kind, "")
        self.fraction: Optional[float] = None
        self._trip = 0                  # which trip of a library is being worked on
        self._trips = 0
        self._trip_name = ""
        self._files = 0                 # how many files the builder has to process
        self._done = 0

    def _where(self) -> str:
        return f"Trip {self._trip} of {self._trips}: {self._trip_name}\n" if self._trips > 1 else ""

    def _update_fraction(self) -> None:
        inner = self._done / self._files if self._files else None
        if self._trips > 1:
            base = (self._trip - 1) / self._trips
            self.fraction = base + (inner or 0) / self._trips if inner is not None or self._trip > 1 else None
        else:
            self.fraction = inner

    def feed(self, line: str) -> bool:
        """Read one output line; True when what to show has changed."""
        line = line.strip()
        before = (self.detail, self.fraction)
        if m := _TRIP.match(line):
            self._trip, self._trips, self._trip_name = int(m[1]), int(m[2]), _folder_name(m[3])
            self._files = self._done = 0
            self.detail = self._where() + "Looking at the photos\u2026"
            self._update_fraction()
        elif m := _INDEXING.match(line):
            what = "Reading" if line.startswith("Indexing") else "Looking at"
            self.detail = self._where() + f"{what} the photos and videos in {_folder_name(m[1])}\u2026"
        elif m := _FOUND.match(line):
            self.detail = self._where() + f"Found {_files(int(m[1]))}. Sorting them into days\u2026"
        elif line.startswith("Grouped into"):
            self.detail = self._where() + "Making the pages\u2026"
        elif m := _PROCESSING.match(line):
            self._files, self._done = int(m[1]), 0
            self.detail = self._where() + f"Preparing {_files(self._files)}\u2026"
            self._update_fraction()
        elif m := _STEP.match(line):
            self._done, total = int(m[1]), int(m[2])
            self._files = total or self._files
            self.detail = self._where() + f"Preparing photo {self._done} of {self._files}"
            self._update_fraction()
        elif _NOTHING_TO_DO.match(line):
            self.detail = self._where() + "Everything is already up to date. Finishing\u2026"
        return (self.detail, self.fraction) != before
