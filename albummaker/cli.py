# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Pieter Remi Coppens
"""Command-line pieces shared by generate_site.py and album_server.py."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from .offline import add_offline_args  # noqa: F401  (re-exported for the scripts)
from .timezones import get_timezone


def large_thumb_size_arg(value: str) -> int:
    """--large-thumb-size: 0 (off), or a size like --thumb-size accepts."""
    if str(value).strip() == "0":
        return 0
    return positive_thumb_size(value)


def positive_thumb_size(value: str) -> int:
    try:
        n = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a whole number: {value!r}")
    if not 16 <= n <= 4096:
        raise argparse.ArgumentTypeError("must be between 16 and 4096")
    return n


def _parse_ymd(s: str) -> datetime:
    try:
        return datetime.strptime(s, "%Y-%m-%d")
    except ValueError:
        raise argparse.ArgumentTypeError(f"'{s}' is not a date like 2026-05-01")


def add_date_check_args(parser):
    parser.add_argument("--date-range", nargs=2, type=_parse_ymd, metavar=("START", "END"),
                        help="The dates your files should fall between, e.g. --date-range "
                             "2026-04-28 2026-05-10 (both days included). Files dated outside it "
                             "are filed under Unsorted. Without this, a date counts as wrong if it "
                             "is before 1995, in the future, or more than a year away from the "
                             "rest of your files.")
    parser.add_argument("--no-date-check", action="store_true",
                        help="Trust every embedded date, even ones that look wrong.")


def resolve_date_range(args, parser) -> Optional[tuple]:
    """(first moment, first moment after) of --date-range, or None."""
    if not args.date_range:
        return None
    start, end = args.date_range
    if end < start:
        parser.error("--date-range: the end date is before the start date")
    return start, end + timedelta(days=1)



def add_content_arg(parser):
    parser.add_argument("-c", "--content", type=Path, default=None,
                        help="Where titles, descriptions and photo notes are stored (default: "
                             "trip_content.json in your photos folder). See README.md.")


def add_location_args(parser):
    parser.add_argument("--gpx", type=Path, action="append", default=[], metavar="FILE",
                        help="A GPS track (.gpx file, or a folder of them) recorded during the trip. "
                             "Repeatable. .gpx files inside the photos folder are used anyway. Files "
                             "without a location are placed on the track, and the maps show the way "
                             "you went. See README.md.")
    parser.add_argument("--no-gpx", action="store_true",
                        help="Ignore GPS tracks, including .gpx files in the photos folder.")
    parser.add_argument("--no-places", action="store_true",
                        help="Don't show place names (the nearest town to each photo, looked up "
                             "offline).")


def add_timezone_arg(parser):
    def valid_timezone(value):
        try:
            get_timezone(value)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(str(exc)) from exc
        return value

    parser.add_argument("--timezone", type=valid_timezone, default=None, metavar="IANA_NAME",
                        help="IANA time zone for video UTC timestamps and GPS-track matching, "
                             "for example Europe/Paris. It accounts for daylight-saving changes.")
