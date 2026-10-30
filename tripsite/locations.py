"""Where things were: GPS tracks (tripsite.gpx) and place names
(tripsite.places), applied the same way by both scripts."""

from __future__ import annotations

from .gpx import apply_gpx, attach_day_tracks
from .places import apply_places


def locate_items(items: list, args, exclude_dirs=()) -> tuple:
    """Before the files are grouped into days: give files without a location
    their position on the GPX track. Returns what describe_days needs."""
    if args.no_gpx:
        return None, None
    return apply_gpx(items, args.input, args.gpx, args.tz_offset, exclude_dirs)


def describe_days(days: list, items: list, located: tuple, args):
    """Once the days are known: each day's part of the track, and the place
    names of files and days."""
    track, zones = located
    if track is not None:
        attach_day_tracks(days, track, zones)
    if not args.no_places:
        apply_places(items)
