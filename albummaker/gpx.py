"""GPS tracks (.gpx files from a watch, phone app or GPS logger).

A track is a list of positions with their UTC time. It does two things:

  - Photos and videos without a location (most cameras; some phones' videos)
    get one: the track's position at the moment they were taken.
  - The day maps draw the way actually travelled, and a day's distance is
    measured along it instead of from photo to photo.

Matching needs each photo's moment in UTC, but cameras record local clock
time. The UTC offset comes from the file where it says (EXIF OffsetTime, a
phone's GPS time stamp, an iPhone video's local time), else from the other
files of that day, else --timezone/--tz-offset, else the rest of the trip (TimeZones).
"""

from __future__ import annotations

import re
import sys
from xml.parsers import expat
from bisect import bisect_left
from collections import Counter
from datetime import datetime, timedelta, timezone
from math import cos, radians
from pathlib import Path
from typing import Optional

from .metadata import format_gps_display, valid_gps
from .model import route_distance_km
from .scan import list_media_files
from .timezones import get_timezone

MAX_GAP_S = 10 * 60       # between two track points, a photo's position is interpolated ...
EDGE_S = 2 * 60           # ... and just before/after a stretch of track, its end point is used
BREAK_S = 15 * 60         # a longer pause in the recording starts a new line on the map
MAX_GPX_BYTES = 200_000_000

_TIME_RE = re.compile(r"^(\d{4})-(\d\d)-(\d\d)T(\d\d):(\d\d):(\d\d)(?:[.,](\d+))?"
                      r"(Z|[+-]\d\d:?\d\d)?$")


def parse_gpx_time(text: str) -> Optional[float]:
    """A GPX <time> as a UTC timestamp (seconds). GPX times are UTC; a time
    without a zone is taken as UTC too, as the format requires."""
    m = _TIME_RE.match((text or "").strip())
    if not m:
        return None
    y, mo, d, h, mi, s, frac, zone = m.groups()
    try:
        t = datetime(int(y), int(mo), int(d), int(h), int(mi), int(s), tzinfo=timezone.utc)
    except ValueError:
        return None
    ts = t.timestamp() + (float("0." + frac) if frac else 0.0)
    if zone and zone != "Z":
        sign = -1 if zone[0] == "-" else 1
        hh, mm = int(zone[1:3]), int(zone[-2:])
        ts -= sign * (hh * 3600 + mm * 60)
    return ts


def _refuse(*_):
    # GPX never needs a DTD, and this Python's XML parser would expand
    # entities defined in one (the "billion laughs" file)
    raise ValueError("it contains a DTD, which GPX files never need")


def read_gpx(path: Path) -> list:
    """The track segments in one GPX file: [[(utc seconds, lat, lon), ...], ...].
    Route and track points without a time are skipped (they can't be matched).
    Read as a stream (expat), so a long recording isn't held as a tree."""
    path = Path(path)
    if path.stat().st_size > MAX_GPX_BYTES:
        raise ValueError("it is too large")
    segments, current = [], []
    state = {"point": None, "text": None}

    def local(name):
        return name.rsplit(" ", 1)[-1]          # "namespace localname" -> localname

    def start(name, attrs):
        tag = local(name)
        if tag in ("trkpt", "rtept"):
            state["point"] = {"lat": attrs.get("lat"), "lon": attrs.get("lon")}
        elif tag == "time" and state["point"] is not None:
            state["text"] = []

    def text(data):
        if state["text"] is not None:
            state["text"].append(data)

    def end(name):
        nonlocal current
        tag, point = local(name), state["point"]
        if tag == "time" and state["text"] is not None:
            point["time"] = "".join(state["text"])
            state["text"] = None
        elif tag in ("trkpt", "rtept") and point is not None:
            try:
                lat, lon = float(point["lat"]), float(point["lon"])
            except (TypeError, ValueError):
                lat = lon = None
            t = parse_gpx_time(point.get("time"))
            if t is not None and lat is not None and valid_gps(lat, lon):
                current.append((t, lat, lon))
            state["point"] = None
        elif tag in ("trkseg", "rte"):
            if current:
                segments.append(current)
            current = []

    parser = expat.ParserCreate(namespace_separator=" ")
    parser.StartElementHandler, parser.EndElementHandler = start, end
    parser.CharacterDataHandler = text
    # wherever it is in the file, and whatever the file's encoding
    parser.StartDoctypeDeclHandler = parser.EntityDeclHandler = _refuse
    with open(path, "rb") as f:
        try:
            parser.ParseFile(f)
        except expat.ExpatError as e:
            raise ValueError(f"not a readable GPX file ({e})") from None
    if current:
        segments.append(current)
    return segments


def _is_gpx(name: str) -> bool:
    return name.lower().endswith(".gpx")


def find_gpx_files(input_dir: Path, extra=(), exclude_dirs=()) -> list:
    """.gpx files in the photos folder (found like the photos are, skipping
    hidden and system folders) plus any given with --gpx (a file or folder)."""
    found = list_media_files(input_dir, exclude_dirs, quiet=True, want=_is_gpx)
    for p in extra:
        p = Path(p)
        if p.is_dir():
            found += list_media_files(p, quiet=True, want=_is_gpx)
        else:
            found.append(p)
    unique = {}
    for p in found:
        try:
            unique.setdefault(p.resolve(), p)
        except OSError:
            unique.setdefault(p, p)
    return sorted(unique.values())


class _Recording:
    """One GPX segment: points in time order."""

    def __init__(self, points: list):
        points = sorted(points)
        self.t = [p[0] for p in points]
        self.pos = [(p[1], p[2]) for p in points]


class Track:
    """Every recorded segment of every GPX file. Each is used on its own, so
    two devices recording at the same time (or one file given twice) never
    get their points mixed, and no position is made up across a gap in a
    recording."""

    def __init__(self, segments: list):
        seen, self.recordings = set(), []
        for seg in segments:
            key = tuple(seg)
            if seg and key not in seen:          # the same track given twice counts once
                seen.add(key)
                self.recordings.append(_Recording(seg))

    def __len__(self):
        return sum(len(r.t) for r in self.recordings)

    def position_at(self, ts: float, max_gap: float = MAX_GAP_S, edge: float = EDGE_S) -> Optional[tuple]:
        """(lat, lon) at UTC time ts, or None when no recording says. Where
        several do, the one with a point closest in time wins."""
        best, best_gap = None, None
        for r in self.recordings:
            n = len(r.t)
            if ts < r.t[0] - edge or ts > r.t[-1] + edge:
                continue
            i = bisect_left(r.t, ts)
            if i < n and r.t[i] == ts:
                found, gap = r.pos[i], 0.0
            elif 0 < i < n and r.t[i] - r.t[i - 1] <= max_gap:
                f = (ts - r.t[i - 1]) / (r.t[i] - r.t[i - 1])
                (la, lo), (lb, lob) = r.pos[i - 1], r.pos[i]
                found, gap = (la + f * (lb - la), lo + f * (lob - lo)), min(ts - r.t[i - 1], r.t[i] - ts)
            else:
                gap, j = min((abs(ts - r.t[j]), j) for j in (i - 1, i) if 0 <= j < n)
                if gap > edge:
                    continue
                found = r.pos[j]
            if best_gap is None or gap < best_gap:
                best, best_gap = found, gap
        return best

    def stretches(self, start: float, end: float) -> list:
        """The points from start (inclusive) to end (exclusive), per recording,
        split where it paused for more than BREAK_S."""
        out = []
        for r in self.recordings:
            lo, hi = bisect_left(r.t, start), bisect_left(r.t, end)   # end itself is the next day
            current = []
            for i in range(lo, hi):
                if current and r.t[i] - r.t[i - 1] > BREAK_S:
                    out.append(current)
                    current = []
                current.append(r.pos[i])
            if current:
                out.append(current)
        return [s for s in out if len(s) >= 2]


# ---------------------------------------------------------------- time zones

class TimeZones:
    """The UTC offset (minutes) to assume for a file that doesn't record its
    own: what the files of the same day say most often, else --timezone or
    --tz-offset, else what the whole trip says most often."""

    VOTERS = ("exif", "gps", "apple")   # files that state their offset

    def __init__(self, items: list, tz_offset_hours: Optional[float] = None,
                 timezone_name: Optional[str] = None):
        by_day, overall = {}, Counter()
        for it in items:
            if it.utc_offset is not None and it.utc_offset_src in self.VOTERS and not it.dt_is_fallback:
                by_day.setdefault(it.dt.strftime("%Y-%m-%d"), Counter())[it.utc_offset] += 1
                overall[it.utc_offset] += 1
        self.by_day = {k: c.most_common(1)[0][0] for k, c in by_day.items()}
        self.fixed = None if tz_offset_hours is None else int(round(tz_offset_hours * 60))
        self.zone = get_timezone(timezone_name) if timezone_name else None
        self.overall = overall.most_common(1)[0][0] if overall else None

    def for_day(self, date_key: str) -> Optional[int]:
        found = self.by_day.get(date_key)
        if found is not None:
            return found
        if self.zone:
            date = datetime.strptime(date_key, "%Y-%m-%d").date()
            return int(datetime.combine(date, datetime.min.time(), self.zone).utcoffset().total_seconds() // 60)
        return self.fixed if self.fixed is not None else self.overall

    def utc_timestamp(self, item) -> Optional[float]:
        """The moment `item` was taken, as a UTC timestamp, or None."""
        if item.dt_is_fallback:
            return None
        offset = item.utc_offset
        if offset is None:
            offset = self.by_day.get(item.dt.strftime("%Y-%m-%d"))
        if offset is None and self.zone:
            return item.dt.replace(tzinfo=self.zone).timestamp()
        if offset is None:
            offset = self.fixed if self.fixed is not None else self.overall
        if offset is None:
            return None
        return (item.dt - timedelta(minutes=offset)).replace(tzinfo=timezone.utc).timestamp()


# ---------------------------------------------------------------- applying

def load_tracks(files: list) -> Optional[Track]:
    segments = []
    for f in files:
        try:
            got = read_gpx(f)
        except (OSError, ValueError) as e:
            print(f"  ! skipping GPX file {f.name}: {e}", file=sys.stderr)
            continue
        if not got:
            print(f"  ! {f.name} has no track points with a time; skipped", file=sys.stderr)
        segments += got
    track = Track(segments)
    if len(track):
        print(f"Read {len(track)} track point(s) from {len(files)} GPX file(s).")
    return track if len(track) else None


def locate_from_track(items: list, track: Track, zones: TimeZones) -> int:
    """Give files without a location their position on the track. Returns how
    many got one; says so when the time zone, not the track, was missing."""
    located = no_zone = 0
    for it in items:
        if it.has_gps or it.dt_is_fallback:
            continue
        ts = zones.utc_timestamp(it)
        if ts is None:
            no_zone += 1
            continue
        pos = track.position_at(ts)
        if pos is None:
            continue
        it.lat, it.lon = round(pos[0], 6), round(pos[1], 6)
        it.detail["gps_display"] = format_gps_display(it.lat, it.lon) + " (from GPX track)"
        located += 1
    if located:
        print(f"Placed {located} file(s) without a location on the GPX track.")
    if no_zone:
        print(f"  {no_zone} file(s) without a location couldn't be matched to the GPX track: their "
              f"time zone is unknown. Pass --timezone or --tz-offset to say what it was.")
    return located


def simplify(points: list, tolerance_m: float) -> list:
    """Ramer-Douglas-Peucker: drop points that sit within tolerance_m of the
    line through their neighbours (iterative, so long tracks don't recurse)."""
    if len(points) < 3:
        return list(points)
    k = cos(radians(sum(p[0] for p in points) / len(points)))   # metres per degree of longitude shrink

    def xy(p):
        return p[1] * 111_320 * k, p[0] * 110_540

    pts = [xy(p) for p in points]
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    tol2 = tolerance_m ** 2
    while stack:
        a, b = stack.pop()
        (ax, ay), (bx, by) = pts[a], pts[b]
        dx, dy = bx - ax, by - ay
        length2 = dx * dx + dy * dy
        worst, worst_d2 = None, tol2
        for i in range(a + 1, b):
            px, py = pts[i]
            if length2 == 0:
                d2 = (px - ax) ** 2 + (py - ay) ** 2
            else:
                u = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length2))
                d2 = (px - ax - u * dx) ** 2 + (py - ay - u * dy) ** 2
            if d2 > worst_d2:
                worst, worst_d2 = i, d2
        if worst is not None:
            keep[worst] = True
            stack += [(a, worst), (worst, b)]
    return [p for p, k_ in zip(points, keep) if k_]


def simplified_stretches(stretches: list, tolerance_m: float, max_points: int) -> list:
    """Each stretch simplified, coarser and coarser until all fit in max_points,
    with coordinates rounded to about a metre."""
    while True:
        out = [simplify(s, tolerance_m) for s in stretches]
        if sum(len(s) for s in out) <= max_points or tolerance_m > 5000:
            return [[[round(lat, 5), round(lon, 5)] for lat, lon in s] for s in out]
        tolerance_m *= 2


def attach_day_tracks(days: list, track: Track, zones: TimeZones,
                      tolerance_m: float = 8, max_points: int = 2000) -> int:
    """Set each real day's `track`: the part of the GPX track recorded on that
    local date. Returns how many days got one."""
    count = 0
    for d in days:
        if d.is_unknown:
            continue
        date = datetime.strptime(d.date_key, "%Y-%m-%d")
        if zones.zone:
            start = datetime.combine(date.date(), datetime.min.time(), zones.zone).timestamp()
            end = datetime.combine((date + timedelta(days=1)).date(), datetime.min.time(),
                                  zones.zone).timestamp()
        else:
            offset = zones.for_day(d.date_key)
            if offset is None:
                continue
            start = (date - timedelta(minutes=offset)).replace(tzinfo=timezone.utc).timestamp()
            end = start + 86400
        stretches = track.stretches(start, end)
        if stretches:
            fine = [simplify(s, tolerance_m) for s in stretches]   # GPS jitter would add phantom metres
            d.track_km = sum(route_distance_km(s) for s in fine)
            d.track = simplified_stretches(fine, tolerance_m, max_points)
            d.track_overview = simplified_stretches(d.track, 60, 300)   # for the homepage map
            count += 1
    return count


def apply_gpx(items: list, input_dir: Path, gpx_args=(), tz_offset: Optional[float] = None,
              exclude_dirs=(), timezone_name: Optional[str] = None) -> tuple:
    """Find and read the GPX files, place unlocated files on the track.
    Returns (track or None, TimeZones), for attach_day_tracks once the
    days are known."""
    zones = TimeZones(items, tz_offset, timezone_name)
    files = find_gpx_files(input_dir, gpx_args, exclude_dirs)
    if not files:
        return None, zones
    track = load_tracks(files)
    if track is not None:
        locate_from_track(items, track, zones)
    return track, zones
