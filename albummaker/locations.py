# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Pieter Remi Coppens
"""Where things were: GPS tracks (albummaker.gpx) and place names
(albummaker.places), applied the same way by both scripts."""

from __future__ import annotations

from .gpx import apply_gpx, attach_day_tracks, localize_utc_videos
from .places import apply_places


def localize_items(items: list, args) -> None:
    """Before dates are checked or grouped: put videos that record only a UTC time in the
    trip's own time zone (see albummaker.gpx.localize_utc_videos)."""
    moved = localize_utc_videos(items, args.tz_offset, getattr(args, "timezone", None))
    if moved:
        print(f"Placed {moved} video(s) that record only a UTC time in the time zone of the "
              f"trip's photos (set --timezone to choose it yourself).")


def locate_items(items: list, args, exclude_dirs=()) -> tuple:
    """Before the files are grouped into days: give files without a location
    their position on the GPX track. Returns what describe_days needs."""
    if args.no_gpx:
        return None, None
    return apply_gpx(items, args.input, args.gpx, args.tz_offset, exclude_dirs,
                     timezone_name=getattr(args, "timezone", None))


def describe_days(days: list, items: list, located: tuple, args, place_cache=None):
    """Once the days are known: each day's part of the track, and the place
    names of files and days."""
    track, zones = located
    if track is not None:
        attach_day_tracks(days, track, zones)
    if not args.no_places:
        apply_places(items, cache=place_cache)
