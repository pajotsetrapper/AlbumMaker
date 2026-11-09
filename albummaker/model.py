# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Pieter Remi Coppens
"""The data model: one MediaItem per photo/video, grouped into DayGroups."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from math import asin, cos, radians, sin, sqrt
from pathlib import Path
from typing import Optional

from .places import day_places

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".tif", ".tiff", ".bmp", ".webp"}
VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".3gp"}

# Formats a normal browser can display directly via <img>. These are used
# completely untouched (a byte-for-byte copy of the original file, or the
# file itself) as the full-screen "display" version. Other formats
# (HEIC/HEIF, TIFF), and multi-image JPEGs, get a full-resolution,
# near-lossless JPEG for display instead; the original stays downloadable.
BROWSER_NATIVE_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}

DAY_COLORS = ["#E3A857", "#4FA5A0", "#C97B84", "#7C9473", "#8C7BC9", "#D9B24C"]
UNKNOWN_DAY_KEY = "unknown"
UNKNOWN_DAY_COLOR = "#6B7280"  # muted neutral gray, visually distinct from real trip-day colors


@dataclass
class StaticOutputs:
    """What generate_site made for one source file (names inside the output's
    thumbs/, thumbs_large/ and media/ folders)."""
    thumb_file: str
    display_file: str
    original_file: Optional[str] = None  # untouched copy kept next to a converted display_file
    thumb_is_placeholder: bool = False   # the video preview couldn't be extracted


@dataclass
class MediaItem:
    """One photo or video."""
    path: Path                 # the original file
    media_type: str            # 'image' | 'video'
    dt: datetime
    dt_is_fallback: bool       # True if there is no usable capture date (filed under Unsorted)
    lat: Optional[float]
    lon: Optional[float]
    width: Optional[int] = None
    height: Optional[int] = None
    detail: dict = field(default_factory=dict)   # human-readable info-panel rows
    is_mpo: bool = False       # multi-image JPEG (portrait/panorama/HDR modes)
    is_live_clip: bool = False  # video that looks like a Live Photo's motion clip
    live_clip: Optional["MediaItem"] = None      # a photo's paired Live Photo clip
    rel: str = ""              # path relative to the photos folder: the key used in trip_content.json
    dt_rejected: str = ""      # why the embedded capture date was ignored (see the date check)
    # byte-identical files that were skipped in favour of this one, and their
    # relative paths: a caption or "hidden" flag on any of them applies here too
    duplicates: list = field(default_factory=list)
    aliases: list = field(default_factory=list)
    # stable identifier used in URLs and #p=... links. album_server: a short
    # hash of `rel`; generate_site: the display file's name
    key: str = ""
    outputs: Optional[StaticOutputs] = None      # generate_site only
    # minutes east of UTC of `dt`, and where that came from: "exif", "gps",
    # "apple" (the file says so) or "utc" (a video's exact UTC time converted
    # with an assumed offset). None: unknown. See albummaker.gpx.
    utc_offset: Optional[int] = None
    utc_offset_src: str = ""
    place: str = ""               # nearest town, e.g. "Kyoto" (see albummaker.places)

    @property
    def has_gps(self) -> bool:
        return self.lat is not None and self.lon is not None

    @property
    def content_keys(self) -> list:
        """Every trip_content.json key that refers to this file."""
        return [self.rel] + [a for a in self.aliases if a != self.rel]

    @property
    def needs_jpeg_fallback(self) -> bool:
        """Shown to browsers as a converted JPEG rather than as-is."""
        return self.media_type == "image" and (
            self.path.suffix.lower() not in BROWSER_NATIVE_IMAGE_EXTS or self.is_mpo)


@dataclass
class DayGroup:
    date_key: str                       # 'YYYY-MM-DD', or UNKNOWN_DAY_KEY
    date_obj: Optional[datetime]        # None for the unknown-date bucket
    is_unknown: bool = False
    items: list = field(default_factory=list)
    # the day's part of a GPX track (see albummaker.gpx.attach_day_tracks): for the
    # day map ([[[lat, lon], ...], ...], one list per stretch), a coarser copy for
    # the overview map, and its length (measured before simplifying for the maps)
    track: list = field(default_factory=list)
    track_overview: list = field(default_factory=list)
    track_km: float = 0.0

    @property
    def route(self):
        return [[it.lat, it.lon] for it in self.items if it.has_gps]

    @property
    def photo_count(self):
        return sum(1 for it in self.items if it.media_type == "image")

    @property
    def video_count(self):
        return sum(1 for it in self.items if it.media_type == "video")

    @property
    def distance_km(self) -> float:
        """Along the GPX track, or from photo to photo, whichever is longer: a
        track may cover only part of the day (a hike, but not the drive there)."""
        return max(self.track_km, route_distance_km(self.route))

    @property
    def places(self) -> list:
        """The day's main towns, in visiting order (see albummaker.places)."""
        return [] if self.is_unknown else day_places(self.items)


def route_distance_km(points) -> float:
    """Straight-line (great-circle) distance along consecutive points."""
    if len(points) < 2:
        return 0.0
    total = 0.0
    for (lat1, lon1), (lat2, lon2) in zip(points, points[1:]):
        lat1r, lon1r, lat2r, lon2r = map(radians, (lat1, lon1, lat2, lon2))
        a = sin((lat2r - lat1r) / 2) ** 2 + cos(lat1r) * cos(lat2r) * sin((lon2r - lon1r) / 2) ** 2
        total += 2 * 6371 * asin(sqrt(a))
    return total
