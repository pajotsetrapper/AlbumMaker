"""A library of trips: one page linking every trip's site, from a list file.

    # trips.txt: one trip per line; '#' starts a comment
    /Volumes/Photos/2024 Japan
    ~/Pictures/Italy 2025 | italy
    /Volumes/NAS/Iceland | iceland | --tz-offset 0 --no-dedupe

Each line is a photos folder, then optionally (after "|") the name the
trip gets in URLs and in the output folder, and options for that trip only.
Relative paths are relative to the list file. Both scripts read it
(--library): generate_site.py builds every trip into its own folder under
-o plus a library page; trip_server.py serves them all.
"""

from __future__ import annotations

import argparse
import re
import shlex
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .cli import add_content_arg, add_date_check_args, add_location_args, resolve_date_range
from .content import trip_title, _text
from .model import DAY_COLORS
from .places import day_places
from .render import pick_cover
from .util import script_json

RESERVED_NAMES = {"assets", "vendor", "tiles", "api", "index.html"}   # used by the library itself
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
MAX_MAP_POINTS = 200


class TripListError(Exception):
    """The list file can't be used as written (the message says where)."""


@dataclass
class TripEntry:
    path: Path              # the photos folder
    name: str               # in URLs and as the output folder: "italy"
    options: list = field(default_factory=list)
    line: int = 0
    written: str = ""       # the folder as the list file has it


def _unquote(text: str) -> str:
    """'"/Volumes/Photos/Japan 2024"' -> '/Volumes/Photos/Japan 2024': quotes
    are what one types around a path with spaces in a terminal, so they're
    allowed (and not needed) in the list too."""
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        return text[1:-1].strip()
    return text


def _normalize_path_str(path_str: str) -> str:
    """Normalize Git Bash/WSL drive shorthands like /c/Users into Windows drive paths.

    Slash-prefixed paths are absolute in this app's list-file syntax, so we only translate
    the specific /c/... and /d/... Git Bash pattern into a real Windows drive path.
    """
    path_str = str(path_str).strip()
    if path_str.startswith("/") and len(path_str) >= 3 and path_str[1].isalpha() and path_str[2] in "/\\":
        drive = path_str[1].upper()
        return f"{drive}:{path_str[2:] or '/'}"
    return path_str


def missing_folder(entry: TripEntry, list_file: Path) -> Optional[str]:
    """Why a trip's folder can't be used, saying how its line was read, or None."""
    if entry.path.is_dir():
        return None
    where = f"line {entry.line} of {Path(list_file).expanduser()}"
    if entry.written and not _is_absolute_path(entry.written):
        return (f"Input folder not found: {entry.path}\n  ({where} says '{entry.written}'. That isn't a "
                f"full path, so it was taken as relative to the list file's folder. Write the full path, "
                f"starting with / (or a drive letter such as C:\\ on Windows), to use another place.)")
    return f"Input folder not found: {entry.path}  ({where})"


def slugify(text: str) -> str:
    """A URL- and folder-safe name: 'Ísland 2024!' -> 'island-2024'."""
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")[:48].strip("-")
    return slug or "trip"


class _OptionParser(argparse.ArgumentParser):
    def error(self, message):
        raise TripListError(message)


def _trip_option_parser() -> argparse.ArgumentParser:
    """The options a single trip may set in the list file. Every default is
    SUPPRESS, so only what a line actually says overrides the command line
    (the shared add_*_args helpers set their own defaults, hence the loop)."""
    p = _OptionParser(prog="trip options", add_help=False)
    add_content_arg(p)
    p.add_argument("--tz-offset", type=float)
    p.add_argument("--no-dedupe", action="store_true")
    p.add_argument("--no-live-photos", action="store_true")
    add_date_check_args(p)
    add_location_args(p)
    for action in p._actions:
        action.default = argparse.SUPPRESS
    return p


# set per trip on its line, never for the whole library: one content file
# or one date window can't be right for every trip
PER_TRIP_ONLY = {"content": "-c/--content", "date_range": "--date-range"}


def check_library_args(args) -> Optional[str]:
    """What's wrong with the command line for --library, or None."""
    for key, option in PER_TRIP_ONLY.items():
        if getattr(args, key, None):
            return f"{option} applies to one trip: with --library, put it on that trip's line in the list"
    return None


def read_trip_list(path: Path) -> list:
    """The trips in a list file, with unique names. Raises TripListError."""
    path = Path(path).expanduser()
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as e:
        raise TripListError(f"can't read the trip list {path}: {e}") from None
    base = path.resolve().parent
    entries, names, folders = [], set(), set()
    pending = []
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|")]
        parts[0] = _unquote(parts[0])
        folder = Path(_normalize_path_str(parts[0])).expanduser()
        if not _is_absolute_path(parts[0]):
            folder = base / folder
        name, options = "", []
        rest = parts[1:]
        if rest and not rest[0].startswith("-"):
            name = _unquote(rest.pop(0))    # (may be empty: "folder | | options")
        if len(rest) > 1:
            raise TripListError(f"{path}, line {n}: too many '|' (folder | name | options)")
        if rest:
            try:
                options = shlex.split(rest[0])
            except ValueError as e:
                raise TripListError(f"{path}, line {n}: {e}") from None
        if name and (not NAME_RE.match(name) or name in RESERVED_NAMES):
            raise TripListError(f"{path}, line {n}: '{name}' can't be a trip's name: use lower-case "
                                f"letters, digits, '-' and '_' (and not {', '.join(sorted(RESERVED_NAMES))})")
        if name in names:
            raise TripListError(f"{path}, line {n}: the name '{name}' is used twice")
        key = str(folder.resolve()) if folder.exists() else str(folder)
        if key in folders:
            raise TripListError(f"{path}, line {n}: {folder} is listed twice")
        folders.add(key)
        if name:
            names.add(name)
        pending.append((folder, name, options, n, parts[0]))
    # names for lines that didn't give one, after the given ones are known
    for folder, name, options, n, written in pending:
        if not name:
            stem = slugify(folder.name)
            if stem in RESERVED_NAMES:
                stem += "-trip"
            name, k = stem, 2
            while name in names:
                name, k = f"{stem}-{k}", k + 1
            names.add(name)
        entries.append(TripEntry(folder, name, options, n, written))
    if not entries:
        raise TripListError(f"{path} lists no trips")
    return entries


def trip_args(args, entry: TripEntry, list_file: Path) -> tuple:
    """(settings, date range) for one trip: the command line's settings with
    what the trip's line in the list file sets on top. Raises TripListError."""
    parser = _trip_option_parser()
    parsed = parser.parse_args(entry.options)
    base = Path(list_file).expanduser().resolve().parent
    out = argparse.Namespace(**vars(args))
    for key, value in vars(parsed).items():
        if key == "content" and value is not None:
            value = _normalize_path_str(value)
            value = value if _is_absolute_path(value) else base / Path(value).expanduser()
        elif key == "gpx":
            value = [_normalize_path_str(g) for g in value]
            value = [g if _is_absolute_path(g) else base / Path(g).expanduser() for g in value]
        setattr(out, key, value)
    out.input = entry.path
    return out, resolve_date_range(out, parser)        # (the parser's errors are TripListErrors)


# ---------------------------------------------------------------- the library page

def format_date_span(first, last) -> str:
    """'1 May 2026', '1 – 12 May 2026', '28 Apr – 3 May 2026', '28 Dec 2025 – 3 Jan 2026'."""
    if first is None:
        return "Date unknown"
    if first.date() == last.date():
        return f"{first.day} {first:%b %Y}"
    if (first.year, first.month) == (last.year, last.month):
        return f"{first.day} – {last.day} {last:%b %Y}"
    if first.year == last.year:
        return f"{first.day} {first:%b} – {last.day} {last:%b %Y}"
    return f"{first.day} {first:%b %Y} – {last.day} {last:%b %Y}"


def _map_points(days: list) -> list:
    """Up to MAX_MAP_POINTS points along the trip, for the library's map."""
    points = []
    for d in days:
        if d.track_overview:
            points += [p for stretch in d.track_overview for p in stretch]
        else:
            points += d.route
    if len(points) > MAX_MAP_POINTS:
        step = len(points) / MAX_MAP_POINTS
        points = [points[int(i * step)] for i in range(MAX_MAP_POINTS)] + [points[-1]]
    return [[round(lat, 4), round(lon, 4)] for lat, lon in points]


def trip_summary(name: str, days: list, content: dict, urls, href: str, base: str = "",
                 folder_title: str = "") -> dict:
    """One trip's card and map line on the library page. `days` must already
    be what visitors see; `base` goes before the trip's own (relative) URLs.
    A trip whose title was never changed from the default is shown under its
    folder's name: a page of trips all called "My Trip" says nothing."""
    title = trip_title(content)
    if title == trip_title({}) and folder_title:
        title = folder_title
    real = [d for d in days if not d.is_unknown]
    cover = None
    for d in real or days:
        if d.items:
            cover = pick_cover(d, content)
            break
    items = [it for d in days for it in d.items]
    return {
        "name": name,
        "title": title,
        "subtitle": _text(content.get("subtitle")),
        "href": href,
        "cover": base + urls.thumb(cover, from_index=True) if cover else None,
        "cover_hi": (base + urls.hi_thumb(cover, from_index=True)
                     if cover and urls.hi_thumb(cover, from_index=True) else None),
        "dates": format_date_span(real[0].date_obj, real[-1].date_obj) if real else "Date unknown",
        "first_date": real[0].date_key if real else "",
        "day_count": len(real),
        "photo_count": sum(d.photo_count for d in days),
        "video_count": sum(d.video_count for d in days),
        "distance_km": round(sum(d.distance_km for d in days)),
        "places": day_places(items),
        "points": _map_points(real),
        "has_places": any(it.place for it in items),
    }


def build_library_context(trips: list, urls, footer: str, places_credit: str) -> dict:
    """Template variables for LIBRARY_TEMPLATE: newest trip first."""
    trips = sorted(trips, key=lambda t: t["first_date"], reverse=True)
    for i, t in enumerate(trips):
        t["color"] = DAY_COLORS[i % len(DAY_COLORS)]
    return dict(
        page_title="Trips",
        assets_prefix=urls.assets_prefix(from_index=True),
        css_href=urls.asset("style.css", from_index=True),
        js_href=urls.asset("script.js", from_index=True),
        vendor=urls.vendor_url(from_index=True),
        tile_url_json=script_json(urls.tile_url),
        footer=footer,
        places_credit=places_credit if any(t["has_places"] for t in trips) else "",
        trips=trips,
        trips_json=script_json([{k: t[k] for k in ("title", "href", "color", "points", "dates")}
                                for t in trips]),
        trip_count=len(trips),
        day_count=sum(t["day_count"] for t in trips),
        photo_count=sum(t["photo_count"] for t in trips),
        distance_km=sum(t["distance_km"] for t in trips),
    )


def _is_absolute_path(path_str: str) -> bool:
    """Check if a path string is absolute in the list-file syntax used by the app."""
    raw = str(path_str).strip()
    if raw.startswith("/"):
        return True
    if re.match(r"^[a-zA-Z]:[\\/]", raw):
        return True
    from pathlib import Path
    return Path(raw).is_absolute()
