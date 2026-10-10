#!/usr/bin/env python3
"""
generate_site.py — build a static, browsable website from a folder of trip
photos and videos.

Reads EXIF (and, for video, container metadata) to find when and where each
file was captured, groups everything into day "chapters", and renders:

  - index.html        overview map of the whole route + trip title + day list
  - days/<date>.html   one page per day: description, map of that day's route,
                       thumbnail grid, and a full-screen keyboard slideshow

Descriptions/titles live in a separate JSON file (trip_content.json by
default) that this script only ever *adds to*, never overwrites, so you can
fill it in over time and re-run the generator safely.

Usage:
    python generate_site.py -i /path/to/photos -o /path/to/site_output

See README.md for the full walkthrough.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from PIL import Image, ImageOps
from PIL.ExifTags import GPSTAGS
from jinja2 import Environment
from markupsafe import Markup

try:
    import pillow_heif
    pillow_heif.register_heif_opener()
    HEIF_SUPPORT = True
except ImportError:
    HEIF_SUPPORT = False


# --------------------------------------------------------------------------
# Shared safety helpers: which files to scan, reading/writing
# trip_content.json without ever losing the user's text, and emitting data
# into HTML/<script> safely.
# --------------------------------------------------------------------------

# Folders that hold system/NAS/recycle-bin clutter rather than trip media.
IGNORED_DIR_NAMES = {"@eaDir", "#recycle", "$RECYCLE.BIN", "System Volume Information"}


def is_ignored_path(path: Path, root: Path) -> bool:
    """Skip hidden files/folders (anything starting with '.') and known
    system folders. Most importantly this skips macOS '._IMG_1234.JPG'
    AppleDouble files, which carry a photo extension but aren't images."""
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        parts = path.parts
    return any(p.startswith(".") or p in IGNORED_DIR_NAMES for p in parts)


class ContentFileError(Exception):
    """trip_content.json exists but can't be used as-is. Never write to the
    file when this is raised; the user's text must stay untouched."""


def default_content() -> dict:
    return {"title": "My Trip", "subtitle": "", "days": {}}


def parse_content_file(path: Path) -> dict:
    """Read and validate trip_content.json. Raises ContentFileError with a
    human-readable message (including line/column for JSON syntax errors)
    instead of silently falling back to defaults."""
    try:
        # utf-8-sig: tolerate the BOM that Windows Notepad adds on save
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as e:
        raise ContentFileError(f"could not read {path}: {e}") from e
    try:
        loaded = json.loads(text)
    except json.JSONDecodeError as e:
        raise ContentFileError(
            f"{path} is not valid JSON: {e.msg} at line {e.lineno}, column {e.colno}. "
            f"(A trailing comma after the last entry, or a missing comma between "
            f"entries, are the usual suspects.)"
        ) from e
    if not isinstance(loaded, dict):
        raise ContentFileError(f"{path}: the top level must be an object, i.e. {{ ... }}")
    days = loaded.get("days")
    if days is None:
        days = {}
    if not isinstance(days, dict):
        raise ContentFileError(f'{path}: "days" must be an object mapping dates to entries')
    content = default_content()
    content.update({k: v for k, v in loaded.items() if k != "days"})
    content["days"] = days
    return content


def atomic_write_text(path: Path, text: str):
    """Write via a temp file + rename, so a crash or full disk can never
    leave a half-written trip_content.json behind."""
    path = Path(path)
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def _text(v) -> str:
    """Coerce a value from the hand-edited JSON into display text, so a
    null or a number in the file can't crash page rendering."""
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    if isinstance(v, list):  # tolerate a list of paragraphs
        return "\n\n".join(_text(x) for x in v)
    return str(v)


def trip_title(content: dict) -> str:
    return _text(content.get("title")).strip() or "My Trip"


def day_text(content: dict, date_key: str) -> tuple:
    """(title, description) for a day, both plain strings."""
    entry = content.get("days", {}).get(date_key)
    if not isinstance(entry, dict):
        entry = {}
    return _text(entry.get("title")).strip(), _text(entry.get("description")).strip()


def script_json(obj) -> Markup:
    """JSON that's safe to drop inside a <script> block: escapes the
    characters that could otherwise close the script tag or start an HTML
    comment/entity. json.dumps alone is NOT safe there."""
    s = (json.dumps(obj)
         .replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026"))
    return Markup(s)


# Autoescaping on: every {{ value }} is HTML-escaped unless explicitly
# marked safe (script_json does that, after escaping for script context).
_JINJA = Environment(autoescape=True)


# generate_site.py keeps a build record with this name in its output folder.
# Its presence marks a folder as a generated site, which both scripts skip
# when scanning for photos (otherwise a site built inside the photos folder
# would have its own thumbnails and copies picked up as "photos").
SITE_MARKER = ".trip_site_manifest.json"


def list_media_files(root: Path, exclude_dirs=()) -> list:
    """Every file under root worth considering, sorted. Skips hidden and
    system paths (see is_ignored_path), the given folders, and any folder
    holding a site generated by generate_site.py."""
    files = [p for p in root.rglob("*") if p.is_file()]
    excluded = set()
    for d in exclude_dirs:
        try:
            excluded.add(Path(d).resolve())
        except OSError:
            pass
    for p in files:
        if p.name == SITE_MARKER:
            site = p.parent.resolve()
            if site not in excluded:
                print(f"Skipping {p.parent}: it holds a site generated by generate_site.py")
                excluded.add(site)

    def is_excluded(p: Path) -> bool:
        if not excluded:
            return False
        rp = p.resolve()
        return any(d == rp or d in rp.parents for d in excluded)

    return sorted(p for p in files if not is_ignored_path(p, root) and not is_excluded(p))


# iPhone Live Photos are exported as IMG_1234.HEIC (or .JPG) plus a short
# IMG_1234.MOV. A video counts as such a clip only if it has a photo with the
# same name in the same folder AND either carries Apple's Live Photo content
# identifier or is no longer than this, so a real video that merely shares
# a name with a photo is left alone.
LIVE_CLIP_MAX_SECONDS = 4.0


def is_live_photo_clip(meta: dict) -> bool:
    if meta.get("live_photo_id"):
        return True
    duration = meta.get("duration_s")
    return duration is not None and duration <= LIVE_CLIP_MAX_SECONDS


IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".tif", ".tiff", ".bmp", ".webp"}
VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".3gp"}

# Formats a normal browser can display directly via <img> — these get used
# completely untouched (a byte-for-byte copy of the original file) as the
# full-screen "display" version. Formats outside this set (HEIC/HEIF, TIFF)
# can't be shown inline in Chrome/Firefox/Edge at all, so there's no way to
# view them in the generated site without converting them to something a
# browser understands; that conversion is done at full original resolution
# and near-lossless quality to stay as close to "unchanged" as possible.
BROWSER_NATIVE_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}

DAY_COLORS = ["#E3A857", "#4FA5A0", "#C97B84", "#7C9473", "#8C7BC9", "#D9B24C"]


# --------------------------------------------------------------------------
# Data model
# --------------------------------------------------------------------------

@dataclass
class MediaItem:
    original_path: Path
    media_type: str            # 'image' | 'video'
    dt: datetime
    dt_is_fallback: bool       # True if we had to fall back to file mtime
    lat: Optional[float]
    lon: Optional[float]
    thumb_file: str            # filename inside output/thumbs/
    display_file: str          # filename inside output/media/ (always browser-viewable)
    original_file: Optional[str] = None  # set only when a genuine original copy is kept
                                          # alongside a converted display_file (HEIC/HEIF, TIFF)
    width: Optional[int] = None
    height: Optional[int] = None
    detail: dict = field(default_factory=dict)   # human-readable info-panel rows
    is_live_clip: bool = False         # video that looks like a Live Photo's motion clip
    live_file: Optional[str] = None    # paired Live Photo clip, inside output/media/

    @property
    def has_gps(self) -> bool:
        return self.lat is not None and self.lon is not None


UNKNOWN_DAY_KEY = "unknown"
UNKNOWN_DAY_COLOR = "#6B7280"  # muted neutral gray — visually distinct from real trip-day colors


@dataclass
class DayGroup:
    date_key: str                       # 'YYYY-MM-DD', or UNKNOWN_DAY_KEY
    date_obj: Optional[datetime]        # None for the unknown-date bucket
    is_unknown: bool = False
    items: list = field(default_factory=list)

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
        pts = self.route
        return _route_distance_km(pts)


# --------------------------------------------------------------------------
# EXIF / metadata extraction
# --------------------------------------------------------------------------

def _to_deg(value) -> float:
    d, m, s = value
    return float(d) + float(m) / 60.0 + float(s) / 3600.0


def _gps_from_exif(gps_ifd: dict) -> Optional[tuple]:
    if not gps_ifd or "GPSLatitude" not in gps_ifd or "GPSLongitude" not in gps_ifd:
        return None
    try:
        lat = _to_deg(gps_ifd["GPSLatitude"])
        if str(gps_ifd.get("GPSLatitudeRef", "N")).upper().startswith("S"):
            lat = -lat
        lon = _to_deg(gps_ifd["GPSLongitude"])
        if str(gps_ifd.get("GPSLongitudeRef", "E")).upper().startswith("W"):
            lon = -lon
        if lat == 0 and lon == 0:
            return None
        return (round(lat, 6), round(lon, 6))
    except Exception:
        return None


# ---- human-readable formatting helpers for the slideshow info panel ------

def clean_str(v) -> Optional[str]:
    if v is None:
        return None
    s = str(v).replace("\x00", "").strip()
    return s or None


def format_camera(make, model) -> Optional[str]:
    make, model = clean_str(make), clean_str(model)
    if not make and not model:
        return None
    if not make:
        return model
    if not model:
        return make
    return model if model.lower().startswith(make.lower()) else f"{make} {model}"


def format_fnumber(v) -> Optional[str]:
    try:
        f = float(v)
        return f"f/{f:g}" if f > 0 else None
    except Exception:
        return None


def format_exposure(v) -> Optional[str]:
    try:
        f = float(v)
        if f <= 0:
            return None
        return f"1/{round(1 / f)}s" if f < 1 else f"{f:g}s"
    except Exception:
        return None


def format_iso(v) -> Optional[str]:
    try:
        if isinstance(v, (tuple, list)):
            v = v[0]
        return f"ISO {int(v)}"
    except Exception:
        return None


def format_focal(v, v35=None) -> Optional[str]:
    try:
        s = f"{float(v):g}mm"
    except Exception:
        return None
    try:
        if v35:
            s += f" (35mm: {int(v35)}mm)"
    except Exception:
        pass
    return s


def format_altitude(alt, ref) -> Optional[str]:
    try:
        a = float(alt)
        if ref in (1, "1", b"\x01"):
            a = -a
        return f"{a:.0f} m"
    except Exception:
        return None


def format_gps_display(lat: float, lon: float) -> str:
    ns = "N" if lat >= 0 else "S"
    ew = "E" if lon >= 0 else "W"
    return f"{abs(lat):.5f}\u00b0 {ns}, {abs(lon):.5f}\u00b0 {ew}"


def human_filesize(n) -> Optional[str]:
    try:
        n = float(n)
    except Exception:
        return None
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def format_duration(seconds) -> Optional[str]:
    try:
        s = int(round(float(seconds)))
    except Exception:
        return None
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def read_image_metadata(path: Path) -> dict:
    """Pull everything useful out of a photo's EXIF: capture time, GPS
    (+ altitude), dimensions, camera/lens, and exposure settings. Any field
    that isn't present in the file is simply absent from the returned dict."""
    meta = {}
    # Opening the file is deliberately NOT inside the try below: if Pillow
    # can't open it at all, it isn't a usable image, and the exception lets
    # the caller skip it instead of indexing a broken tile with a guessed date.
    with Image.open(path) as img:
        meta["width"], meta["height"] = img.size
        # Pillow reports multi-image JPEGs (portrait/depth, panorama and HDR
        # modes write these) as "MPO" from the MPF header it reads anyway
        # while opening, which is far cheaper than scanning the whole file.
        meta["is_mpo"] = (img.format == "MPO")
        try:
            exif = img.getexif()
            if exif:
                exif_ifd = {}
                try:
                    exif_ifd = dict(exif.get_ifd(0x8769))
                except Exception:
                    pass

                dt_str = exif_ifd.get(36867) or exif_ifd.get(36868) or exif.get(306)
                if dt_str:
                    try:
                        meta["dt"] = datetime.strptime(str(dt_str).strip(), "%Y:%m:%d %H:%M:%S")
                    except Exception:
                        pass

                try:
                    gps_ifd_raw = exif.get_ifd(0x8825)
                    gps_ifd = {GPSTAGS.get(k, k): v for k, v in gps_ifd_raw.items()}
                    gps = _gps_from_exif(gps_ifd)
                    if gps:
                        meta["gps"] = gps
                    if "GPSAltitude" in gps_ifd:
                        alt = format_altitude(gps_ifd["GPSAltitude"], gps_ifd.get("GPSAltitudeRef"))
                        if alt:
                            meta["altitude"] = alt
                except Exception:
                    pass

                camera = format_camera(exif.get(271), exif.get(272))  # Make, Model
                if camera:
                    meta["camera"] = camera

                lens = clean_str(exif_ifd.get(42036))  # LensModel
                if lens:
                    meta["lens"] = lens

                if 33437 in exif_ifd:  # FNumber
                    ap = format_fnumber(exif_ifd[33437])
                    if ap:
                        meta["aperture"] = ap
                if 33434 in exif_ifd:  # ExposureTime
                    sh = format_exposure(exif_ifd[33434])
                    if sh:
                        meta["shutter"] = sh
                if 34855 in exif_ifd:  # ISOSpeedRatings / PhotographicSensitivity
                    iso = format_iso(exif_ifd[34855])
                    if iso:
                        meta["iso"] = iso
                if 37386 in exif_ifd:  # FocalLength
                    fl = format_focal(exif_ifd[37386], exif_ifd.get(41989))
                    if fl:
                        meta["focal_length"] = fl
        except Exception as e:
            print(f"  ! could not read EXIF from {path.name}: {e}", file=sys.stderr)
    return meta


def read_video_metadata(path: Path, tz_offset_hours: Optional[float] = None) -> dict:
    """Pull capture time, GPS, dimensions, codec, frame rate and duration out
    of a video via ffprobe. Missing fields are simply absent.

    Video containers store `creation_time` as absolute UTC. If tz_offset_hours
    is given, that fixed offset is applied to convert it to the trip's local
    time. Otherwise it falls back to whatever timezone this script happens to
    be running in, which is only correct if you're generating the site from
    the same timezone the trip was in. Photos aren't affected by this at all:
    camera EXIF timestamps are recorded in local time already, with no
    timezone conversion involved."""
    meta = {}
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_format", "-show_streams", str(path)],
            capture_output=True, text=True, errors="replace", timeout=30,
        )
    except FileNotFoundError:
        # ffprobe isn't installed: keep the video, just without metadata
        print(f"  ! ffprobe not found, so {path.name} has no date/GPS metadata "
              f"(install ffmpeg to fix this)", file=sys.stderr)
        return meta
    except subprocess.TimeoutExpired:
        print(f"  ! ffprobe timed out on {path.name}", file=sys.stderr)
        return meta
    if result.returncode != 0:
        # ffprobe ran but couldn't parse the file: not a usable video, so
        # let the caller skip it rather than show a broken tile
        raise ValueError("ffprobe could not read this file (not a valid video?)")
    try:
        data = json.loads(result.stdout or "{}")
    except ValueError as e:
        print(f"  ! unexpected ffprobe output for {path.name}: {e}", file=sys.stderr)
        return meta

    fmt = data.get("format", {}) or {}
    tags = dict(fmt.get("tags", {}) or {})
    video_stream = None
    for s in data.get("streams", []):
        tags.update(s.get("tags", {}) or {})
        if s.get("codec_type") == "video" and video_stream is None:
            video_stream = s

    ct = tags.get("creation_time")
    if ct:
        try:
            ct_clean = ct.replace("Z", "+00:00")
            parsed = datetime.fromisoformat(ct_clean)
            if parsed.tzinfo is not None:
                if tz_offset_hours is not None:
                    parsed = (parsed.astimezone(timezone.utc)
                              + timedelta(hours=tz_offset_hours)).replace(tzinfo=None)
                else:
                    parsed = parsed.astimezone().replace(tzinfo=None)
            meta["dt"] = parsed
        except Exception:
            pass

    loc = tags.get("location") or tags.get("location-eng") or tags.get(
        "com.apple.quicktime.location.ISO6709"
    )
    if loc:
        m = re.match(r"([+-]\d+\.?\d*)([+-]\d+\.?\d*)", loc)
        if m:
            try:
                meta["gps"] = (round(float(m.group(1)), 6), round(float(m.group(2)), 6))
            except Exception:
                pass

    if video_stream:
        meta["width"] = video_stream.get("width")
        meta["height"] = video_stream.get("height")
        codec = video_stream.get("codec_name")
        if codec:
            meta["codec"] = codec.upper()
        rate = video_stream.get("avg_frame_rate") or video_stream.get("r_frame_rate")
        if rate and "/" in rate:
            try:
                num, den = rate.split("/")
                num, den = float(num), float(den)
                if den:
                    meta["fps"] = f"{num / den:g} fps"
            except Exception:
                pass

    if fmt.get("duration"):
        d = format_duration(fmt["duration"])
        if d:
            meta["duration"] = d
        try:
            meta["duration_s"] = float(fmt["duration"])
        except (TypeError, ValueError):
            pass

    live_id = tags.get("com.apple.quicktime.content.identifier")
    if live_id:
        meta["live_photo_id"] = str(live_id)

    return meta


# --------------------------------------------------------------------------
# Derivative generation (thumbnails + web-friendly display copies)
# --------------------------------------------------------------------------

ILLEGAL_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


# Build record (SITE_MARKER) kept in the output folder. Per source file it
# remembers the output name it was given, the outputs made for it, and the
# source's size and modification time when they were made. That lets a
# re-run keep every file's output name stable, regenerate exactly the files
# that changed, and delete the outputs of files that were removed.
MANIFEST_VERSION = 1


def _natural_stem(path: Path) -> str:
    stem = ILLEGAL_FILENAME_CHARS.sub("_", path.stem).rstrip(" .") or "file"
    return stem[:180]  # defensive cap well under filesystem limits


def compute_output_bases(rel_paths: dict, previous: dict) -> dict:
    """Work out the output filename stem for every source file.

    rel_paths maps each source path to its path relative to the input
    folder; previous is the "files" section of the last build record.
    Names are compared ignoring case and extension, since every thumbnail
    is .jpg (and so is a HEIC's display copy). In order:

    1. A file that already got a name in an earlier run keeps it, so its
       existing outputs stay valid and no other file can take that name.
    2. Every other file gets its own name ('IMG_1234') when that's free.
       Only characters that are illegal in filenames are replaced. Photos
       claim names before videos, so in a Live Photo pair (IMG_1234.HEIC +
       IMG_1234.MOV) it's the photo that keeps the plain name.
    3. Genuine collisions (two phones that both started at IMG_0001.jpg)
       get '_2', '_3', ... appended, skipping suffixes already taken by a
       real file's own name."""
    taken = set()
    bases = {}
    for path, rel in rel_paths.items():
        prev = previous.get(rel)
        base = prev.get("base") if isinstance(prev, dict) else None
        if (isinstance(base, str) and base and base.lower() not in taken
                and not ILLEGAL_FILENAME_CHARS.search(base)):
            bases[path] = base
            taken.add(base.lower())

    pending = []
    photos_first = sorted(rel_paths, key=lambda p: (p.suffix.lower() in VIDEO_EXTS, rel_paths[p]))
    for path in photos_first:
        if path in bases:
            continue
        stem = _natural_stem(path)
        if stem.lower() in taken:
            pending.append(path)
        else:
            bases[path] = stem
            taken.add(stem.lower())

    for path in pending:
        stem = _natural_stem(path)
        n = 2
        while f"{stem}_{n}".lower() in taken:
            n += 1
        bases[path] = f"{stem}_{n}"
        taken.add(bases[path].lower())
    return bases


def load_manifest(output_dir: Path) -> dict:
    """The previous build record, or {} if there is none (first run, or a
    site built by an older version of this script)."""
    path = output_dir / SITE_MARKER
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"! ignoring unreadable build record {path} ({e}); "
              f"everything will be regenerated", file=sys.stderr)
        return {}
    if (not isinstance(data, dict) or data.get("version") != MANIFEST_VERSION
            or not isinstance(data.get("files"), dict)):
        return {}
    return data


def save_manifest(output_dir: Path, thumb_size: int, records: dict, pages: list):
    data = {
        "version": MANIFEST_VERSION,
        "note": "Build record for generate_site.py. Safe to delete: the next run "
                "then regenerates everything.",
        "thumb_size": thumb_size,
        "pages": sorted(pages),
        "files": records,
    }
    atomic_write_text(output_dir / SITE_MARKER, json.dumps(data, indent=1, ensure_ascii=False) + "\n")


def remove_stale_outputs(output_dir: Path, previous: dict, keep: set):
    """Delete outputs listed in the previous build record that the current
    site no longer uses (files removed from the input folder, duplicates,
    days that no longer exist). Only files the record itself lists are ever
    touched, and only inside the output folder."""
    old = set()
    for rec in (previous.get("files") or {}).values():
        if isinstance(rec, dict):
            old.update(o for o in rec.get("outputs", []) if isinstance(o, str))
    old.update(p for p in previous.get("pages", []) if isinstance(p, str))

    root = output_dir.resolve()
    removed = 0
    for rel in sorted(old - keep):
        try:
            target = (output_dir / rel).resolve()
        except OSError:
            continue
        if root not in target.parents or not target.is_file():
            continue
        try:
            target.unlink()
            removed += 1
        except OSError as e:
            print(f"  ! could not remove {target}: {e}", file=sys.stderr)
    if removed:
        print(f"Removed {removed} output file(s) the site no longer uses.")


def fast_fingerprint(path: Path, sample_size: int = 65536) -> Optional[tuple]:
    """A cheap-but-reliable fingerprint for spotting exact duplicate files:
    file size plus a hash of the first and last chunk of bytes. Reads at
    most ~2x sample_size regardless of file size, so it stays fast even for
    large videos, instead of hashing entire multi-GB files. This catches
    byte-identical duplicates (the same photo imported from two devices, a
    folder synced twice, etc.) — it will not catch a photo re-exported or
    re-compressed differently, which is a much harder "perceptually similar"
    problem and out of scope here."""
    try:
        size = path.stat().st_size
        with open(path, "rb") as f:
            head = f.read(sample_size)
            tail = b""
            if size > sample_size:
                f.seek(max(0, size - sample_size))
                tail = f.read(sample_size)
        return (size, hashlib.sha1(head + tail).hexdigest())
    except Exception:
        return None


def _copy_is_current(src: Path, dst: Path) -> bool:
    """shutil.copy2 preserves size and modification time, so a copy whose
    size and mtime still match the source is up to date. (2 s tolerance for
    FAT/exFAT drives, which store mtimes at 2-second resolution.)"""
    try:
        s, d = src.stat(), dst.stat()
    except OSError:
        return False
    return s.st_size == d.st_size and abs(s.st_mtime - d.st_mtime) <= 2


def make_image_derivatives(src: Path, thumb_path: Path, media_dir: Path, base: str,
                            thumb_size: int, is_mpo: bool,
                            regen_thumb: bool, regen_converted: bool, force_copy: bool):
    """Create the small day-grid thumbnail, and the full-screen 'display'
    version:
      - formats a browser can show directly (jpg/png/webp/bmp) are used
        unchanged: a byte-for-byte copy under the original name.
      - formats browsers can't show reliably (HEIC/HEIF, TIFF, and
        multi-image/MPO JPEGs) get a full-resolution, near-lossless JPEG
        version for display, plus an untouched copy of the original.

    regen_thumb / regen_converted: rebuild those even if they exist (the
    source changed, or --thumb-size did). force_copy: re-copy originals
    even if the existing copy looks current (--force).

    Returns (display_filename, original_filename_or_None)."""
    ext = src.suffix                 # preserve exact original case for output filenames
    ext_lower = ext.lower()          # lowercased only for format-matching checks below
    native = ext_lower in BROWSER_NATIVE_IMAGE_EXTS and not is_mpo

    if native:
        display_file = f"{base}{ext}"
    elif ext_lower in (".jpg", ".jpeg"):
        # an MPO with a .jpg extension: the display version needs a name
        # distinct from the kept original, which uses that same extension
        display_file = f"{base}_display.jpg"
    else:
        display_file = f"{base}.jpg"
    display_path = media_dir / display_file
    original_file = None

    thumb_needed = regen_thumb or not thumb_path.exists()
    if native:
        display_needed = force_copy or not _copy_is_current(src, display_path)
    else:
        display_needed = regen_converted or not display_path.exists()
    convert = display_needed and not native

    # only decode with Pillow when there's actually a reason to
    if thumb_needed or convert:
        with Image.open(src) as img:
            if not convert:
                # Only the thumbnail is needed, so let libjpeg decode at 1/2,
                # 1/4 or 1/8 scale instead of decoding all the pixels and
                # throwing most away (a no-op for non-JPEG formats). It has to
                # happen before exif_transpose, which loads the image.
                img.draft("RGB", (thumb_size * 2, thumb_size * 2))
            exif = img.getexif()  # grab before transpose: has the original orientation tag
            img = ImageOps.exif_transpose(img)
            if img.mode != "RGB":
                img = img.convert("RGB")
            if convert:
                # carry over the original EXIF (camera, GPS, timestamp, ...).
                # The pixels are already rotated upright by exif_transpose,
                # so Orientation is reset to "normal" to stop EXIF-aware
                # viewers rotating the image a second time.
                exif_bytes = None
                try:
                    exif[0x0112] = 1  # Orientation
                    exif_bytes = exif.tobytes()
                except Exception:
                    pass
                save_kwargs = {"quality": 97, "optimize": True}
                if exif_bytes:
                    save_kwargs["exif"] = exif_bytes
                img.save(display_path, "JPEG", **save_kwargs)
            if thumb_needed:
                thumb_img = img.copy()
                thumb_img.thumbnail((thumb_size, thumb_size), Image.LANCZOS)
                thumb_img.save(thumb_path, "JPEG", quality=82, optimize=True)

    if native and display_needed:
        shutil.copy2(src, display_path)

    if not native:
        # also keep an untouched copy of the original file itself
        original_file = f"{base}{ext}"
        original_path = media_dir / original_file
        if force_copy or not _copy_is_current(src, original_path):
            shutil.copy2(src, original_path)

    return display_file, original_file


def make_video_derivatives(src: Path, thumb_path: Path, display_path: Path,
                            thumb_size: int, regen_thumb: bool, force_copy: bool):
    if force_copy or not _copy_is_current(src, display_path):
        shutil.copy2(src, display_path)

    if regen_thumb or not thumb_path.exists():
        # don't leave an outdated frame behind if extraction fails below
        try:
            thumb_path.unlink()
        except OSError:
            pass
        for seek in ("00:00:01", "00:00:00"):
            try:
                subprocess.run(
                    ["ffmpeg", "-y", "-ss", seek, "-i", str(src),
                     "-frames:v", "1", "-vf", f"scale={thumb_size}:-1",
                     str(thumb_path)],
                    capture_output=True, timeout=30,
                )
                if thumb_path.exists() and thumb_path.stat().st_size > 0:
                    break
            except Exception:
                continue


# --------------------------------------------------------------------------
# Scanning
# --------------------------------------------------------------------------

def _process_one_file(path: Path, media_type: str, thumbs_dir: Path, media_dir: Path,
                       thumb_size: int, tz_offset: Optional[float], base: str,
                       regen_thumb: bool, regen_converted: bool, force_copy: bool) -> MediaItem:
    """Extract metadata and generate the thumbnail/display derivatives for a
    single file. This is the unit of work handed to each worker process, so
    it must stay a plain top-level function (not a closure/method): that's
    what lets it be pickled and sent across the process boundary, and it's
    required for it to work at all under Windows' 'spawn' process model.
    `base` and the regen flags are worked out up front in scan_media, since
    they depend on every file at once and on the previous build record."""
    original_file = None
    thumb_file = f"{base}.jpg"

    if media_type == "image":
        meta = read_image_metadata(path)
        display_file, original_file = make_image_derivatives(
            path, thumbs_dir / thumb_file, media_dir, base, thumb_size,
            bool(meta.get("is_mpo")), regen_thumb, regen_converted, force_copy,
        )
    else:
        meta = read_video_metadata(path, tz_offset)
        display_file = f"{base}{path.suffix}"  # preserve original extension case (e.g. .MOV)
        make_video_derivatives(
            path, thumbs_dir / thumb_file, media_dir / display_file,
            thumb_size, regen_thumb, force_copy,
        )

    dt = meta.get("dt")
    gps = meta.get("gps")
    w, h = meta.get("width"), meta.get("height")
    dt_is_fallback = False
    if dt is None:
        dt = datetime.fromtimestamp(path.stat().st_mtime)
        dt_is_fallback = True

    detail = {"filename": path.name}
    if w and h:
        detail["dimensions"] = f"{w} \u00d7 {h}"
    size_str = human_filesize(path.stat().st_size)
    if size_str:
        detail["filesize"] = size_str
    if media_type == "image":
        for key in ("camera", "lens", "aperture", "shutter", "iso", "focal_length"):
            if meta.get(key):
                detail[key] = meta[key]
    else:
        for key in ("codec", "fps", "duration"):
            if meta.get(key):
                detail[key] = meta[key]
    if gps:
        detail["gps_display"] = format_gps_display(gps[0], gps[1])
    if meta.get("altitude"):
        detail["altitude"] = meta["altitude"]

    return MediaItem(
        original_path=path,
        media_type=media_type,
        dt=dt,
        dt_is_fallback=dt_is_fallback,
        lat=gps[0] if gps else None,
        lon=gps[1] if gps else None,
        thumb_file=thumb_file,
        display_file=display_file,
        original_file=original_file,
        width=w, height=h,
        detail=detail,
        is_live_clip=(media_type == "video" and is_live_photo_clip(meta)),
    )


def scan_media(input_dir: Path, output_dir: Path, thumb_size: int,
                force: bool, tz_offset: Optional[float] = None,
                workers: Optional[int] = None, dedupe: bool = True,
                manifest: Optional[dict] = None) -> tuple:
    """Returns (items, records): the processed media, and the build-record
    entry for every processed file (see save_manifest)."""
    manifest = manifest or {}
    thumbs_dir = output_dir / "thumbs"
    media_dir = output_dir / "media"
    thumbs_dir.mkdir(parents=True, exist_ok=True)
    media_dir.mkdir(parents=True, exist_ok=True)

    candidates = []
    # the output folder is excluded explicitly too: on the very first run
    # it doesn't hold a build record yet
    for path in list_media_files(input_dir, exclude_dirs=(output_dir,)):
        ext = path.suffix.lower()
        if ext in IMAGE_EXTS:
            if ext in (".heic", ".heif") and not HEIF_SUPPORT:
                print(f"  ! skipping {path.name}: install pillow-heif for HEIC support "
                      f"(pip install pillow-heif)", file=sys.stderr)
                continue
            candidates.append((path, "image"))
        elif ext in VIDEO_EXTS:
            candidates.append((path, "video"))

    if dedupe and candidates:
        seen = {}
        deduped = []
        skipped = 0
        for path, media_type in candidates:
            fp = fast_fingerprint(path)
            original = seen.get(fp) if fp is not None else None
            if original is not None:
                print(f"  ~ skipping {path.name}: duplicate of {original.name}", file=sys.stderr)
                skipped += 1
                continue
            if fp is not None:
                seen[fp] = path
            deduped.append((path, media_type))
        if skipped:
            print(f"Skipped {skipped} duplicate file(s) (identical content to a file already found).")
        candidates = deduped

    total = len(candidates)
    if total == 0:
        return [], {}

    rel_paths = {path: path.relative_to(input_dir).as_posix() for path, _ in candidates}
    previous = manifest.get("files", {})
    bases = compute_output_bases(rel_paths, previous)
    thumb_size_changed = manifest.get("thumb_size") != thumb_size

    # decide per file what needs rebuilding. "Unchanged" means the build
    # record has the file with the same output name, size and mtime.
    plan = {}
    stats = {}
    changed = 0
    for path, _ in candidates:
        st = path.stat()
        stats[path] = (st.st_size, st.st_mtime_ns)
        prev = previous.get(rel_paths[path])
        unchanged = (isinstance(prev, dict) and prev.get("base") == bases[path]
                     and prev.get("size") == st.st_size and prev.get("mtime_ns") == st.st_mtime_ns)
        regen_converted = force or not unchanged
        regen_thumb = regen_converted or thumb_size_changed
        plan[path] = (regen_thumb, regen_converted, force)
        changed += not unchanged
    if manifest and not force:
        print(f"{total - changed} file(s) unchanged since the last run, "
              f"{changed} new or changed."
              + (" Thumbnail size changed, so all thumbnails will be rebuilt." if thumb_size_changed else ""))

    workers = workers or min(os.cpu_count() or 1, total)
    workers = max(1, workers)
    items = []
    records = {}
    done = 0

    print(f"Processing {total} file(s) with {workers} worker process(es)...")
    with ProcessPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(_process_one_file, path, media_type, thumbs_dir, media_dir,
                             thumb_size, tz_offset, bases[path], *plan[path]): (path, media_type)
            for path, media_type in candidates
        }
        for future in as_completed(futures):
            path, media_type = futures[future]
            done += 1
            try:
                item = future.result()
            except Exception as e:
                print(f"[{done}/{total}] {media_type:5s} {path.name}  ! failed: {e}", file=sys.stderr)
                continue
            items.append(item)
            print(f"[{done}/{total}] {media_type:5s} {path.name}")
            outputs = [f"thumbs/{item.thumb_file}", f"media/{item.display_file}"]
            if item.original_file:
                outputs.append(f"media/{item.original_file}")
            size, mtime_ns = stats[path]
            records[rel_paths[path]] = {
                "base": bases[path], "size": size, "mtime_ns": mtime_ns, "outputs": outputs,
            }

    return items, records


def pair_live_photos(items: list) -> list:
    """Attach each Live Photo clip to its photo (same folder, same name,
    see is_live_photo_clip) and drop the clip from the list, so it plays
    from the photo instead of cluttering the grid as a separate video."""
    items = sorted(items, key=lambda it: str(it.original_path))
    photos = {}
    for it in items:
        if it.media_type == "image":
            photos.setdefault((it.original_path.parent, it.original_path.stem.lower()), it)
    kept = []
    paired = 0
    for it in items:
        if it.media_type == "video" and it.is_live_clip:
            photo = photos.get((it.original_path.parent, it.original_path.stem.lower()))
            if photo is not None and photo.live_file is None:
                photo.live_file = it.display_file
                paired += 1
                continue
        kept.append(it)
    if paired:
        print(f"Paired {paired} Live Photo clip(s) with their photos "
              f"(use --no-live-photos to show them as separate videos).")
    return kept


def _route_distance_km(points) -> float:
    from math import radians, sin, cos, asin, sqrt
    if len(points) < 2:
        return 0.0
    total = 0.0
    for (lat1, lon1), (lat2, lon2) in zip(points, points[1:]):
        lat1r, lon1r, lat2r, lon2r = map(radians, (lat1, lon1, lat2, lon2))
        dlat = lat2r - lat1r
        dlon = lon2r - lon1r
        a = sin(dlat / 2) ** 2 + cos(lat1r) * cos(lat2r) * sin(dlon / 2) ** 2
        total += 2 * 6371 * asin(sqrt(a))
    return total


def format_long_date(dt) -> str:
    """'Friday, May 1, 2026' — cross-platform (avoids %-d / %#d, which differ
    between Linux/macOS and Windows and aren't portable)."""
    return f"{dt.strftime('%A, %B')} {dt.day}, {dt.year}"


def group_by_day(items: list) -> list:
    """Group media by the day they were taken. Items with no real capture-date
    metadata (dt_is_fallback=True — we only had the file's mtime to go on)
    are deliberately kept out of the normal date-based grouping: a file's
    modified/copied time often has nothing to do with when it was actually
    taken, so trusting it to place a photo on a specific day could silently
    invent a day that never happened on the trip, or misfile a photo into
    the wrong one. They all go into a single dedicated 'unknown date' group
    instead, which always sorts after every real day."""
    days = {}
    for it in items:
        if it.dt_is_fallback:
            key = UNKNOWN_DAY_KEY
        else:
            key = it.dt.strftime("%Y-%m-%d")
        if key not in days:
            days[key] = DayGroup(
                date_key=key,
                date_obj=None if key == UNKNOWN_DAY_KEY else it.dt,
                is_unknown=(key == UNKNOWN_DAY_KEY),
            )
        days[key].items.append(it)
    ordered = sorted(days.values(), key=lambda d: d.date_key)
    for d in ordered:
        d.items.sort(key=lambda it: it.dt)
    return ordered


# --------------------------------------------------------------------------
# Content file (titles & descriptions) — additive, never overwritten
# --------------------------------------------------------------------------

def load_or_init_content(content_path: Path, day_keys: list) -> dict:
    """Load trip_content.json and add blank entries for any new days. Only
    ever adds; never overwrites. If the existing file can't be parsed this
    raises ContentFileError WITHOUT writing anything, since falling back to
    defaults and saving would wipe every title and description."""
    exists = content_path.exists()
    content = parse_content_file(content_path) if exists else default_content()

    changed = not exists
    for key in day_keys:
        if key not in content["days"]:
            content["days"][key] = {"title": "", "description": ""}
            changed = True

    if changed:
        atomic_write_text(content_path, json.dumps(content, indent=2, ensure_ascii=False) + "\n")
        print(f"-> wrote {content_path} (fill in titles/descriptions any time and re-run)")

    return content


# --------------------------------------------------------------------------
# Templates
# --------------------------------------------------------------------------

CSS = """
:root{
  --navy:#16233B; --navy-deep:#0E1729; --navy-light:#223252;
  --parchment:#F1E9D8; --parchment-dim:#E4D9C0;
  --ink:#1F2A3D; --cream:#EDE6D6;
  --gold:#E3A857; --gold-dim:#B9863F; --teal:#4FA5A0;
  --radius:14px;
  --wide:min(96vw,1700px);
  --serif:'Fraunces',Georgia,serif;
  --sans:'IBM Plex Sans',-apple-system,Segoe UI,sans-serif;
  --mono:'IBM Plex Mono',ui-monospace,Menlo,monospace;
}
*{box-sizing:border-box;}
html{scroll-behavior:smooth;}
body{
  margin:0; background:var(--navy); color:var(--cream);
  font-family:var(--sans); line-height:1.55;
}
a{color:var(--gold); text-decoration:none;}
a:hover{text-decoration:underline;}
img,video{max-width:100%; display:block;}
:focus-visible{outline:2px solid var(--gold); outline-offset:2px;}

.site-header{
  padding:1.1rem 1.5rem; display:flex; align-items:baseline; gap:.75rem;
  border-bottom:1px solid rgba(237,230,214,.12);
}
.site-header .brand{font-family:var(--serif); font-weight:600; font-size:1.15rem; letter-spacing:.02em;}
.site-header .brand a{color:var(--cream);}
.site-header .crumb{font-family:var(--mono); font-size:.78rem; color:var(--gold-dim); text-transform:uppercase; letter-spacing:.08em;}

.hero{padding:4.5rem 1.5rem 3rem; text-align:center;}
.hero .eyebrow{font-family:var(--mono); font-size:.8rem; letter-spacing:.18em; text-transform:uppercase; color:var(--teal);}
.hero h1{font-family:var(--serif); font-weight:600; font-size:clamp(2.2rem,6vw,4rem); margin:.4rem 0 .6rem; letter-spacing:-.01em;}
.hero .subtitle{color:var(--cream); opacity:.75; font-size:1.05rem; max-width:38em; margin:0 auto;}

.stats{display:flex; justify-content:center; flex-wrap:wrap; gap:2.2rem; margin:2.2rem 0 0;}
.stat{text-align:center;}
.stat .n{font-family:var(--mono); font-size:1.7rem; color:var(--gold);}
.stat .l{font-family:var(--mono); font-size:.7rem; letter-spacing:.1em; text-transform:uppercase; opacity:.65;}

.map-wrap{margin:2.5rem auto; max-width:var(--wide); padding:0 1.5rem;}
.map{width:100%; height:min(64vh,640px); border-radius:var(--radius); overflow:hidden; border:1px solid rgba(237,230,214,.15);}
.map .dark-tiles{filter:invert(1) hue-rotate(180deg) brightness(0.92) contrast(0.88) saturate(0.7);}

/* marker popups (photo preview when clicking a point on a day map) —
   restyled to match the site's dark theme instead of Leaflet's default
   white popup box */
.leaflet-popup-content-wrapper{background:var(--navy-light); color:var(--cream); border-radius:8px;}
.leaflet-popup-tip{background:var(--navy-light);}
.leaflet-popup-close-button{color:var(--cream) !important;}
.map-popup-thumb{width:240px; max-width:60vw; display:block; border-radius:6px; cursor:zoom-in; transition:opacity .15s ease;}
.map-popup-thumb:hover{opacity:.85;}
.map-popup-hint{font-family:var(--mono); font-size:.65rem; color:var(--cream); opacity:.6; margin-top:.4rem; text-align:center;}
.map-legend{display:flex; flex-wrap:wrap; gap:.9rem; margin-top:.8rem; font-family:var(--mono); font-size:.75rem;}
.map-legend .sw{display:inline-block; width:.7em; height:.7em; border-radius:2px; margin-right:.4em; vertical-align:middle;}

.day-list{max-width:var(--wide); margin:1rem auto 4rem; padding:0 1.5rem; display:grid; gap:1rem; grid-template-columns:repeat(auto-fill,minmax(280px,1fr));}
.day-card{
  background:var(--navy-light); border-radius:var(--radius); overflow:hidden;
  border:1px solid rgba(237,230,214,.1); transition:transform .15s ease, border-color .15s ease;
  display:flex; flex-direction:column;
}
.day-card:hover{transform:translateY(-3px); border-color:var(--gold-dim);}
.day-card .cover{aspect-ratio:16/10; background:var(--navy-deep) center/cover no-repeat;}
.day-card .body{padding:1rem 1.1rem 1.2rem; position:relative;}
.stamp{
  position:absolute; top:-1.4rem; right:1rem; width:3.1rem; height:3.1rem; border-radius:50%;
  background:var(--gold); color:var(--navy-deep); display:flex; align-items:center; justify-content:center;
  flex-direction:column; font-family:var(--mono); font-weight:700; transform:rotate(-8deg);
  box-shadow:0 3px 10px rgba(0,0,0,.35); border:2px dashed var(--navy-deep);
}
.stamp .num{font-size:1.05rem; line-height:1;}
.stamp .of{font-size:.5rem; opacity:.8;}
.day-card h3{font-family:var(--serif); margin:.2rem 0 .2rem; font-size:1.25rem;}
.day-card .date{font-family:var(--mono); font-size:.72rem; color:var(--teal); letter-spacing:.05em;}
.day-card p{font-size:.88rem; opacity:.8; margin:.5rem 0 0;}
.day-card .meta{font-family:var(--mono); font-size:.7rem; opacity:.6; margin-top:.6rem;}

.day-header{max-width:1100px; margin:0 auto; padding:3.5rem 1.5rem 1rem; position:relative;}
.day-header .stamp{position:static; transform:rotate(-6deg); display:inline-flex; margin-bottom:1rem;}
.day-header h1{font-family:var(--serif); font-size:clamp(1.8rem,4.5vw,2.8rem); margin:.2rem 0;}
.day-header .date{font-family:var(--mono); color:var(--teal); font-size:.85rem;}
.day-nav{display:flex; justify-content:space-between; gap:1rem; font-family:var(--mono); font-size:.8rem; margin-top:1.2rem;}
.day-nav a{
  display:inline-flex; align-items:center; gap:.4rem; padding:.5rem .9rem;
  border:1px solid rgba(237,230,214,.25); border-radius:8px;
  transition:border-color .15s ease, background .15s ease;
}
.day-nav a:hover{border-color:var(--gold); background:rgba(227,168,87,.08); text-decoration:none;}
.day-nav-hint{text-align:center; font-family:var(--mono); font-size:.68rem; opacity:.4; margin-top:.6rem;}

.description{
  max-width:1100px; margin:1rem auto 2rem; padding:1.6rem 1.8rem; background:var(--parchment);
  color:var(--ink); border-radius:var(--radius);
  white-space: pre-line;
}
.description p{margin:0 0 .8rem;}
.description p:last-child{margin-bottom:0;}
.description.empty{color:rgba(31,42,61,.55); font-style:italic;}

.grid-controls{max-width:var(--wide); margin:0 auto .8rem; padding:0 1.5rem; display:flex; align-items:center; justify-content:flex-end; gap:.6rem;}
.grid-controls-label{font-family:var(--mono); font-size:.7rem; letter-spacing:.06em; text-transform:uppercase; opacity:.55;}
.size-toggle{display:flex; border:1px solid rgba(237,230,214,.25); border-radius:6px; overflow:hidden;}
.size-btn{
  background:none; border:none; color:var(--cream); opacity:.6; font-family:var(--mono); font-size:.75rem;
  padding:.3rem .7rem; cursor:pointer; border-left:1px solid rgba(237,230,214,.25);
}
.size-btn:first-child{border-left:none;}
.size-btn:hover{opacity:1;}
.size-btn.active{background:var(--gold); color:var(--navy-deep); opacity:1; font-weight:600;}

.grid{max-width:var(--wide); margin:0 auto 3rem; padding:0 1.5rem; display:grid; gap:.7rem; grid-template-columns:repeat(auto-fill,minmax(clamp(130px,11vw,220px),1fr));}
.grid.size-small{grid-template-columns:repeat(auto-fill,minmax(clamp(90px,8vw,140px),1fr));}
.grid.size-large{grid-template-columns:repeat(auto-fill,minmax(clamp(220px,18vw,380px),1fr));}
.thumb{position:relative; cursor:pointer; border-radius:8px; overflow:hidden; aspect-ratio:1/1; background:var(--navy-deep);}
.thumb img{width:100%; height:100%; object-fit:contain; display:block; transition:transform .2s ease;}
.thumb:hover img{transform:scale(1.06);}
.thumb .badge{position:absolute; bottom:.35rem; right:.35rem; background:rgba(14,23,41,.75); color:var(--cream);
  font-family:var(--mono); font-size:.65rem; padding:.15rem .4rem; border-radius:4px;}
.thumb .play{position:absolute; inset:0; display:flex; align-items:center; justify-content:center; pointer-events:none;}
.thumb .play svg{width:2rem; height:2rem; opacity:.9; filter:drop-shadow(0 1px 4px rgba(0,0,0,.6));}
.thumb .live-badge{position:absolute; top:.35rem; left:.35rem; background:rgba(14,23,41,.75); color:var(--cream);
  font-family:var(--mono); font-size:.6rem; letter-spacing:.1em; padding:.1rem .35rem; border-radius:4px; pointer-events:none;}
.slideshow-btn[hidden]{display:none;}

.site-footer{text-align:center; padding:2.5rem 1.5rem 3.5rem; font-family:var(--mono); font-size:.72rem; opacity:.5;}

/* Slideshow / lightbox */
.slideshow{position:fixed; inset:0; background:rgba(10,15,26,.97); display:none; z-index:1000; flex-direction:column;}
.slideshow.open{display:flex;}
.slideshow-stage{flex:1; display:flex; align-items:center; justify-content:center; padding:2.5rem; min-height:0; position:relative;}
.slideshow-media{width:100%; height:100%; display:flex; align-items:center; justify-content:center; position:relative;}
.slideshow-media.loading::after{
  content:''; position:absolute; top:50%; left:50%; width:2.4rem; height:2.4rem;
  margin:-1.2rem 0 0 -1.2rem; border-radius:50%; pointer-events:none;
  border:3px solid rgba(237,230,214,.25); border-top-color:var(--gold);
  animation:spin .8s linear infinite;
}
@keyframes spin{ to { transform:rotate(360deg); } }
.slideshow-media img, .slideshow-media video{max-width:100%; max-height:100%; object-fit:contain; border-radius:4px;}
.slideshow-media img{transition:transform .15s ease-out; cursor:zoom-in;}
.slideshow-media img.zoomed{cursor:grab; transition:none;}
.slideshow-media img.zoomed:active{cursor:grabbing;}
.slideshow-bar{display:flex; align-items:center; justify-content:space-between; padding:.9rem 1.4rem; font-family:var(--mono); font-size:.75rem; color:var(--cream); opacity:.85;}
.slideshow-hint{opacity:.6;}
.slideshow-btn{
  background:none; border:1px solid rgba(237,230,214,.3); color:var(--cream); font-family:var(--mono);
  border-radius:6px; padding:.4rem .7rem; cursor:pointer; font-size:.8rem;
}
.slideshow-btn:hover{border-color:var(--gold); color:var(--gold);}
.slideshow-nav{position:absolute; top:0; bottom:0; width:18%; display:flex; align-items:center; cursor:pointer; opacity:.35; transition:opacity .15s ease, background .15s ease;}
.slideshow-nav:hover{opacity:1; background:linear-gradient(to var(--edge,left), rgba(0,0,0,.25), transparent);}
.slideshow-nav.prev{left:0; justify-content:flex-start; padding-left:1rem; --edge:right;}
.slideshow-nav.next{right:0; justify-content:flex-end; padding-right:1rem; --edge:left;}
.slideshow-nav svg{width:2.2rem; height:2.2rem; stroke:var(--cream);}
.slideshow-caption{position:absolute; left:0; right:0; bottom:.6rem; text-align:center; font-family:var(--mono); font-size:.72rem; color:var(--cream); opacity:.65; pointer-events:none;}

/* True (browser) fullscreen: the photo should fill the entire screen with
   nothing else visible — no bar, no caption, no nav hint chrome, no padding.
   Keyboard/swipe/wheel navigation and zoom all keep working exactly the
   same; this only hides the visual chrome. The info panel is deliberately
   exempt since opening it is an explicit action, not passive on-screen clutter. */
#slideshow:fullscreen .slideshow-bar,
#slideshow:-webkit-full-screen .slideshow-bar,
#slideshow:fullscreen .slideshow-caption,
#slideshow:-webkit-full-screen .slideshow-caption,
#slideshow:fullscreen .slideshow-nav,
#slideshow:-webkit-full-screen .slideshow-nav{
  display:none;
}
#slideshow:fullscreen .slideshow-stage,
#slideshow:-webkit-full-screen .slideshow-stage{
  padding:0;
}

/* Info panel — slides in from the right (bottom sheet on mobile) */
.info-panel{
  position:fixed; top:0; right:0; bottom:0; width:min(380px,92vw);
  background:var(--parchment); color:var(--ink); z-index:1001;
  transform:translateX(100%); transition:transform .25s ease;
  overflow-y:auto; box-shadow:-10px 0 28px rgba(0,0,0,.4);
}
.info-panel.open{transform:translateX(0);}
.info-panel-inner{padding:1.7rem 1.6rem 2.2rem; position:relative;}
.info-close{
  position:absolute; top:1rem; right:1.1rem; width:2rem; height:2rem; border-radius:50%;
  background:rgba(31,42,61,.08); border:none; font-size:1.3rem; color:var(--ink);
  cursor:pointer; line-height:1; display:flex; align-items:center; justify-content:center;
}
.info-close:hover{background:rgba(31,42,61,.16);}
.info-eyebrow{font-family:var(--mono); font-size:.7rem; letter-spacing:.14em; text-transform:uppercase; color:var(--gold-dim); margin-bottom:.3rem;}
.info-panel h2{font-family:var(--serif); font-size:1.35rem; margin:0 0 1.3rem; padding-right:2rem;}
.info-rows{display:flex; flex-direction:column; gap:0; font-family:var(--mono); font-size:.82rem; margin-bottom:1.4rem; border-top:1px dashed rgba(31,42,61,.2);}
.info-row{display:flex; justify-content:space-between; gap:1rem; padding:.55rem 0; border-bottom:1px dashed rgba(31,42,61,.2);}
.info-row .k{opacity:.6; text-transform:uppercase; letter-spacing:.06em; font-size:.68rem; white-space:nowrap;}
.info-row .v{text-align:right; font-weight:500;}
.info-map-wrap{border-radius:10px; overflow:hidden; border:1px solid rgba(31,42,61,.18); height:200px;}
.info-map{width:100%; height:100%;}
.info-map .dark-tiles{filter:invert(1) hue-rotate(180deg) brightness(0.92) contrast(0.88) saturate(0.7);}
.info-no-gps{font-family:var(--mono); font-size:.78rem; opacity:.55; font-style:italic; padding:.5rem 0 0;}
@media (max-width:640px){
  .info-panel{width:100%; height:75vh; top:auto; left:0; right:0; bottom:0; transform:translateY(100%); border-radius:16px 16px 0 0;}
  .info-panel.open{transform:translateY(0);}
}

@media (prefers-reduced-motion: reduce){
  .day-card, .thumb img{transition:none !important;}
  html{scroll-behavior:auto;}
}
@media (max-width:640px){
  .slideshow-nav{width:24%; opacity:.5;}
  .slideshow-bar{flex-direction:column; align-items:flex-start; gap:.5rem;}
  .slideshow-hint{font-size:.68rem;}
}
"""

JS = """
// ---------- shared map helpers ----------
function fmtTime(iso){
  const d = new Date(iso);
  return d.toLocaleString(undefined, {month:'short', day:'numeric', hour:'2-digit', minute:'2-digit'});
}

// ---------- thumbnail size control (day pages) ----------
function applyThumbSize(size){
  const grid = document.getElementById('thumb-grid');
  if(!grid) return;
  grid.classList.remove('size-small', 'size-large');
  if(size === 'small' || size === 'large') grid.classList.add('size-' + size);
  document.querySelectorAll('.size-btn').forEach(b => {
    b.classList.toggle('active', b.dataset.size === size);
  });
  try { localStorage.setItem('tripThumbSize', size); } catch(e) { /* private browsing, etc. */ }
}

function initThumbSizeControl(){
  const buttons = document.querySelectorAll('.size-btn');
  if(!buttons.length) return;
  let saved = 'medium';
  try { saved = localStorage.getItem('tripThumbSize') || 'medium'; } catch(e) {}
  applyThumbSize(saved);
  buttons.forEach(b => b.addEventListener('click', () => applyThumbSize(b.dataset.size)));
}

// ---------- prev/next day navigation (keyboard + swipe) ----------
function initDayNav(prevHref, nextHref){
  function slideshowOpen(){
    const el = document.getElementById('slideshow');
    return !!(el && el.classList.contains('open'));
  }
  function inMap(el){
    return !!(el && el.closest && el.closest('.leaflet-container'));
  }

  document.addEventListener('keydown', (e) => {
    if(slideshowOpen()) return;                  // the slideshow has its own arrow-key handling
    if(inMap(document.activeElement)) return;     // let Leaflet's own keyboard map-panning work
    if(e.key === 'ArrowLeft' && prevHref) window.location.href = prevHref;
    else if(e.key === 'ArrowRight' && nextHref) window.location.href = nextHref;
  });

  let touchStartX = null, touchStartY = null, touchInMap = false;
  document.addEventListener('touchstart', (e) => {
    if(slideshowOpen() || e.touches.length !== 1) return;
    touchInMap = inMap(e.target);
    touchStartX = e.touches[0].clientX;
    touchStartY = e.touches[0].clientY;
  }, {passive: true});

  document.addEventListener('touchend', (e) => {
    if(slideshowOpen() || touchInMap || touchStartX === null) return;
    const t = e.changedTouches[0];
    const dx = t.clientX - touchStartX;
    const dy = t.clientY - touchStartY;
    touchStartX = null;
    // a map drag can also be a long horizontal touch, so this only fires for
    // touches that didn't originate inside the map (checked above) and are
    // clearly horizontal, deliberate swipes rather than a scroll or a tap
    if(Math.abs(dx) > 80 && Math.abs(dx) > Math.abs(dy) * 1.8){
      if(dx < 0 && nextHref) window.location.href = nextHref;
      else if(dx > 0 && prevHref) window.location.href = prevHref;
    }
  }, {passive: true});
}

function initOverviewMap(elId, days){
  const map = L.map(elId, {scrollWheelZoom:false});
  L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
    attribution: '&copy; OpenStreetMap contributors',
    maxZoom: 19,
    className: 'dark-tiles'
  }).addTo(map);

  const allPts = [];
  days.forEach(day => {
    day.route.forEach(p => {
      allPts.push(p);
      L.circleMarker(p, {radius:4, color: day.color, fillColor: day.color, fillOpacity:1, weight:1})
        .addTo(map)
        .bindTooltip(day.label, {sticky:true})
        .on('click', () => { window.location.href = day.href; });
    });
  });
  if(allPts.length){
    map.fitBounds(allPts, {padding:[30,30]});
  } else {
    map.setView([20,0], 2);
  }
}

function initDayMap(elId, route, thumbs, slideIndices){
  const map = L.map(elId, {scrollWheelZoom:false});
  L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
    attribution: '&copy; OpenStreetMap contributors',
    maxZoom: 19,
    className: 'dark-tiles'
  }).addTo(map);

  if(route.length){
    route.forEach((p, i) => {
      const marker = L.circleMarker(p, {radius:6, color:'#E3A857', fillColor:'#16233B', fillOpacity:1, weight:2}).addTo(map);
      if(thumbs[i]){
        // built as DOM nodes rather than an HTML string, so nothing in the
        // URL can ever be interpreted as markup
        const popup = document.createElement('div');
        const img = document.createElement('img');
        img.src = thumbs[i];
        img.className = 'map-popup-thumb';
        img.alt = '';
        img.addEventListener('click', () => openSlideshow(slideIndices[i]));
        const hint = document.createElement('div');
        hint.className = 'map-popup-hint';
        hint.textContent = 'Click photo to view full-size';
        popup.append(img, hint);
        marker.bindPopup(popup);
      }
    });
    map.fitBounds(route, {padding:[30,30]});
  } else {
    map.setView([20,0], 2);
  }
}

// ---------- slideshow ----------
let SLIDES = [];
let currentSlide = 0;
let infoPanelOpen = false;
let infoMap = null;
let infoMarker = null;

const INFO_LABELS = {
  filename: 'File', dimensions: 'Dimensions', filesize: 'File size',
  camera: 'Camera', lens: 'Lens', aperture: 'Aperture', shutter: 'Shutter',
  iso: 'ISO', focal_length: 'Focal length', codec: 'Codec', fps: 'Frame rate',
  duration: 'Duration', gps_display: 'Location', altitude: 'Altitude'
};
const INFO_ORDER = ['filename','dimensions','filesize','camera','lens','aperture','shutter',
                    'iso','focal_length','codec','fps','duration','gps_display','altitude'];

function buildSlideStage(item){
  // Returns a DOM element (not an HTML string): URLs and filenames are set
  // as properties, so they can never be interpreted as markup.
  if(item.type === 'video'){
    // muted is required for autoplay to work under browser autoplay
    // policies; poster gives a real starting frame either way, and native
    // controls let people unmute.
    const v = document.createElement('video');
    v.src = item.display;
    v.controls = true;
    v.autoplay = true;
    v.muted = true;
    v.setAttribute('muted', '');
    v.playsInline = true;
    v.setAttribute('playsinline', '');
    if(item.poster) v.poster = item.poster;
    return v;
  }
  // always the browser-safe display version (item.display), even for HEIC;
  // the true original is available via the download link where offered.
  const img = document.createElement('img');
  img.src = item.display;
  img.alt = '';
  return img;
}

// ---------- zoom / pan (images only) ----------
let ZOOM = {scale: 1, x: 0, y: 0};
const ZOOM_MIN = 1, ZOOM_MAX = 5;

function resetZoom(){
  ZOOM = {scale: 1, x: 0, y: 0};
  const img = document.querySelector('#slideshow-media img');
  if(img) img.style.transform = '';
  document.querySelectorAll('.slideshow-nav').forEach(el => el.style.pointerEvents = '');
}

function applyZoomTransform(){
  const img = document.querySelector('#slideshow-media img');
  const stage = document.getElementById('slideshow-stage');
  if(!img || !stage) return;

  // apply scale alone first so we can measure the actual on-screen size at
  // this zoom level, then clamp the pan so the image can't be dragged
  // further than its own edge reaching the edge of the viewport
  img.style.transform = `scale(${ZOOM.scale})`;
  const imgRect = img.getBoundingClientRect();
  const stageRect = stage.getBoundingClientRect();
  const maxX = Math.max(0, (imgRect.width - stageRect.width) / 2);
  const maxY = Math.max(0, (imgRect.height - stageRect.height) / 2);
  ZOOM.x = Math.max(-maxX, Math.min(maxX, ZOOM.x));
  ZOOM.y = Math.max(-maxY, Math.min(maxY, ZOOM.y));
  img.style.transform = `translate(${ZOOM.x}px, ${ZOOM.y}px) scale(${ZOOM.scale})`;
  img.classList.toggle('zoomed', ZOOM.scale > 1.01);

  // once zoomed in, dragging near the screen edge should pan the photo, not
  // jump to the next/previous one — the nav click-zones step aside while zoomed
  document.querySelectorAll('.slideshow-nav').forEach(el => {
    el.style.pointerEvents = ZOOM.scale > 1.01 ? 'none' : '';
  });
}

function zoomBy(delta){
  if(!document.querySelector('#slideshow-media img')) return; // videos aren't zoomable
  ZOOM.scale = Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, ZOOM.scale + delta));
  if(ZOOM.scale <= 1.01){ ZOOM.scale = 1; ZOOM.x = 0; ZOOM.y = 0; }
  applyZoomTransform();
}

function toggleZoomAt(){
  if(!document.querySelector('#slideshow-media img')) return;
  if(ZOOM.scale > 1){
    ZOOM = {scale: 1, x: 0, y: 0};
  } else {
    ZOOM.scale = 2.5;
  }
  applyZoomTransform();
}

function openSlideshow(index){
  currentSlide = index;
  const overlay = document.getElementById('slideshow');
  overlay.classList.add('open');
  renderSlide();
  document.body.style.overflow = 'hidden';
}

function closeSlideshow(){
  const overlay = document.getElementById('slideshow');
  overlay.classList.remove('open');
  document.getElementById('slideshow-media').innerHTML = '';
  document.body.style.overflow = '';
  closeInfoPanel();
  if(document.fullscreenElement) document.exitFullscreen();
}

function preloadSlide(index){
  // warm the browser cache for the neighboring slide so arrow/swipe
  // navigation feels instant instead of popping in. Images only — video
  // preloading is heavier on bandwidth (especially mobile data) for a
  // benefit people notice far less than instant photo navigation.
  const item = SLIDES[index];
  if(!item || item.type !== 'image') return;
  const img = new Image();
  img.src = item.display;
}

function renderSlide(){
  resetZoom();
  const item = SLIDES[currentSlide];
  const mediaEl = document.getElementById('slideshow-media');
  mediaEl.classList.add('loading');
  mediaEl.textContent = '';
  mediaEl.appendChild(buildSlideStage(item));

  const clearLoading = () => mediaEl.classList.remove('loading');
  const mediaTag = mediaEl.querySelector('img, video');
  if(mediaTag){
    if(item.type === 'video'){
      mediaTag.addEventListener('loadeddata', clearLoading, {once: true});
    } else if(mediaTag.complete){
      clearLoading();
    } else {
      mediaTag.addEventListener('load', clearLoading, {once: true});
      mediaTag.addEventListener('error', clearLoading, {once: true});
    }
  } else {
    clearLoading();
  }

  document.getElementById('slideshow-caption').textContent =
    (item.time ? fmtTime(item.time) + ' — ' : '') + (currentSlide+1) + ' / ' + SLIDES.length;
  if(infoPanelOpen) renderInfoPanel();

  const liveBtn = document.getElementById('slideshow-live');
  if(liveBtn) liveBtn.hidden = !item.live;

  preloadSlide((currentSlide + 1) % SLIDES.length);
  preloadSlide((currentSlide - 1 + SLIDES.length) % SLIDES.length);
}

function nextSlide(){ currentSlide = (currentSlide + 1) % SLIDES.length; renderSlide(); }
function prevSlide(){ currentSlide = (currentSlide - 1 + SLIDES.length) % SLIDES.length; renderSlide(); }

function toggleFullscreen(){
  const overlay = document.getElementById('slideshow');
  if(!document.fullscreenElement){
    overlay.requestFullscreen?.().catch(()=>{});
  } else {
    document.exitFullscreen();
  }
}

// ---------- Live Photos ----------
function playLive(){
  // Plays the photo's motion clip in place of the photo, then returns to
  // the photo when it ends. Started from a click or key press, so browsers
  // allow it to play with sound; if they don't, it retries muted.
  const item = SLIDES[currentSlide];
  if(!item || !item.live) return;
  resetZoom();
  const mediaEl = document.getElementById('slideshow-media');
  const v = document.createElement('video');
  v.src = item.live;
  v.poster = item.display;   // keep showing the photo until the clip starts
  v.playsInline = true;
  v.setAttribute('playsinline', '');
  const slideAtStart = currentSlide;
  const backToPhoto = () => {
    if(currentSlide === slideAtStart && v.isConnected) renderSlide();
  };
  v.addEventListener('ended', backToPhoto, {once: true});
  v.addEventListener('error', backToPhoto, {once: true});
  mediaEl.classList.remove('loading');
  mediaEl.textContent = '';
  mediaEl.appendChild(v);
  v.play().catch(() => { v.muted = true; v.play().catch(backToPhoto); });
}

// ---------- info panel ----------
function fillInfoRows(container, detail){
  // textContent, never innerHTML: EXIF strings and filenames come from the
  // files themselves and must not be able to inject markup or script.
  container.textContent = '';
  INFO_ORDER.filter(key => detail[key]).forEach(key => {
    const row = document.createElement('div');
    row.className = 'info-row';
    const k = document.createElement('span');
    k.className = 'k';
    k.textContent = INFO_LABELS[key];
    const v = document.createElement('span');
    v.className = 'v';
    v.textContent = String(detail[key]);
    row.append(k, v);
    container.appendChild(row);
  });
}

function renderInfoPanel(){
  const item = SLIDES[currentSlide];
  const detail = item.detail || {};
  document.getElementById('info-title').textContent = item.time ? fmtTime(item.time) : 'Details';
  fillInfoRows(document.getElementById('info-rows'), detail);

  const mapWrap = document.getElementById('info-map-wrap');
  const noGps = document.getElementById('info-no-gps');
  if(item.lat != null && item.lon != null){
    mapWrap.style.display = '';
    noGps.style.display = 'none';
    try {
      if(!infoMap){
        infoMap = L.map('info-map', {zoomControl:false, attributionControl:false, scrollWheelZoom:false});
        L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {maxZoom:19, className:'dark-tiles'}).addTo(infoMap);
      }
      const latlng = [item.lat, item.lon];
      infoMap.setView(latlng, 15);
      if(infoMarker){
        infoMarker.setLatLng(latlng);
      } else {
        infoMarker = L.circleMarker(latlng, {radius:7, color:'#E3A857', fillColor:'#E3A857', fillOpacity:1, weight:2}).addTo(infoMap);
      }
      // panel slide-in transition + map container becoming visible both need a resize nudge
      setTimeout(() => { if(infoMap) infoMap.invalidateSize(); }, 260);
    } catch(e){
      console.error('Map failed to load (check your internet connection):', e);
      mapWrap.style.display = 'none';
    }
  } else {
    mapWrap.style.display = 'none';
    noGps.style.display = '';
  }
}

function toggleInfoPanel(){
  infoPanelOpen = !infoPanelOpen;
  document.getElementById('info-panel').classList.toggle('open', infoPanelOpen);
  if(infoPanelOpen) renderInfoPanel();
}

function closeInfoPanel(){
  infoPanelOpen = false;
  document.getElementById('info-panel').classList.remove('open');
}

function initSlideshow(items){
  SLIDES = items;
  document.querySelectorAll('[data-slide-index]').forEach(el => {
    el.addEventListener('click', () => openSlideshow(parseInt(el.dataset.slideIndex, 10)));
  });
  document.getElementById('slideshow-close').addEventListener('click', closeSlideshow);
  document.getElementById('slideshow-next').addEventListener('click', nextSlide);
  document.getElementById('slideshow-prev').addEventListener('click', prevSlide);
  document.getElementById('slideshow-fs').addEventListener('click', toggleFullscreen);
  document.getElementById('slideshow-info').addEventListener('click', toggleInfoPanel);
  document.getElementById('slideshow-live').addEventListener('click', playLive);
  document.getElementById('info-close').addEventListener('click', closeInfoPanel);
  document.getElementById('slideshow').addEventListener('click', (e) => {
    if(e.target.id === 'slideshow') closeSlideshow();
  });

  document.addEventListener('keydown', (e) => {
    if(!document.getElementById('slideshow').classList.contains('open')) return;
    if(e.key === 'ArrowRight') nextSlide();
    else if(e.key === 'ArrowLeft') prevSlide();
    else if(e.key === 'Escape'){ infoPanelOpen ? closeInfoPanel() : closeSlideshow(); }
    else if(e.key.toLowerCase() === 'f') toggleFullscreen();
    else if(e.key.toLowerCase() === 'i') toggleInfoPanel();
    else if(e.key.toLowerCase() === 'l') playLive();
    else if(e.key === '+' || e.key === '=') zoomBy(0.4);
    else if(e.key === '-' || e.key === '_') zoomBy(-0.4);
    else if(e.key === '0') resetZoom();
  });

  const stage = document.getElementById('slideshow-stage');
  const mediaEl = document.getElementById('slideshow-media');

  // mouse wheel to zoom in/out, centered on the stage
  stage.addEventListener('wheel', (e) => {
    if(!document.querySelector('#slideshow-media img')) return;
    e.preventDefault();
    zoomBy(e.deltaY < 0 ? 0.25 : -0.25);
  }, {passive: false});

  // double-click to toggle zoom
  stage.addEventListener('dblclick', (e) => {
    if(!document.querySelector('#slideshow-media img')) return;
    e.preventDefault();
    toggleZoomAt();
  });

  // mouse-drag to pan once zoomed in
  let dragging = false, dragStartX = 0, dragStartY = 0, dragOrigX = 0, dragOrigY = 0;
  mediaEl.addEventListener('mousedown', (e) => {
    if(ZOOM.scale <= 1) return;
    dragging = true;
    dragStartX = e.clientX; dragStartY = e.clientY;
    dragOrigX = ZOOM.x; dragOrigY = ZOOM.y;
    e.preventDefault();
  });
  document.addEventListener('mousemove', (e) => {
    if(!dragging) return;
    ZOOM.x = dragOrigX + (e.clientX - dragStartX);
    ZOOM.y = dragOrigY + (e.clientY - dragStartY);
    applyZoomTransform();
  });
  document.addEventListener('mouseup', () => { dragging = false; });

  // touch: one finger swipes to navigate (only while not zoomed in), one
  // finger pans once zoomed in, two fingers pinch to zoom
  let touchStartX = 0, touchStartY = 0, panOrigX = 0, panOrigY = 0;
  let pinchStartDist = 0, pinchStartScale = 1;

  function touchDist(a, b){
    return Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY);
  }

  stage.addEventListener('touchstart', (e) => {
    if(e.touches.length === 2){
      pinchStartDist = touchDist(e.touches[0], e.touches[1]);
      pinchStartScale = ZOOM.scale;
    } else if(e.touches.length === 1){
      const t = e.touches[0];
      touchStartX = t.clientX; touchStartY = t.clientY;
      panOrigX = ZOOM.x; panOrigY = ZOOM.y;
    }
  }, {passive: true});

  stage.addEventListener('touchmove', (e) => {
    if(!document.querySelector('#slideshow-media img')) return;
    if(e.touches.length === 2){
      e.preventDefault();
      const dist = touchDist(e.touches[0], e.touches[1]);
      if(pinchStartDist > 0){
        ZOOM.scale = Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, pinchStartScale * (dist / pinchStartDist)));
        applyZoomTransform();
      }
    } else if(e.touches.length === 1 && ZOOM.scale > 1){
      e.preventDefault();
      const t = e.touches[0];
      ZOOM.x = panOrigX + (t.clientX - touchStartX);
      ZOOM.y = panOrigY + (t.clientY - touchStartY);
      applyZoomTransform();
    }
  }, {passive: false});

  stage.addEventListener('touchend', (e) => {
    if(ZOOM.scale > 1 || e.changedTouches.length !== 1) return;
    const t = e.changedTouches[0];
    const dx = t.clientX - touchStartX;
    const dy = t.clientY - touchStartY;
    // require a clearly horizontal, deliberate swipe so it doesn't fire on
    // small taps or on a vertical scroll/drag inside the info panel area
    if(Math.abs(dx) > 60 && Math.abs(dx) > Math.abs(dy) * 1.5){
      dx < 0 ? nextSlide() : prevSlide();
    }
  }, {passive: true});
}
"""


SLIDESHOW_HTML = """
<div class="slideshow" id="slideshow">
  <div class="slideshow-bar">
    <span class="slideshow-hint">&larr; &rarr; navigate &middot; Scroll/pinch to zoom &middot; I info &middot; F fullscreen &middot; Esc close</span>
    <div>
      <button class="slideshow-btn" id="slideshow-live" type="button" hidden title="Play Live Photo (L)">Live</button>
      <button class="slideshow-btn" id="slideshow-info" type="button">Info</button>
      <button class="slideshow-btn" id="slideshow-fs" type="button">Fullscreen</button>
      <button class="slideshow-btn" id="slideshow-close" type="button">Close &times;</button>
    </div>
  </div>
  <div class="slideshow-stage" id="slideshow-stage">
    <div class="slideshow-media" id="slideshow-media"></div>
    <div class="slideshow-nav prev" id="slideshow-prev"><svg viewBox="0 0 24 24" fill="none" stroke-width="2"><path d="M15 5l-7 7 7 7"/></svg></div>
    <div class="slideshow-nav next" id="slideshow-next"><svg viewBox="0 0 24 24" fill="none" stroke-width="2"><path d="M9 5l7 7-7 7"/></svg></div>
    <div class="slideshow-caption" id="slideshow-caption"></div>
  </div>
  <div class="info-panel" id="info-panel">
    <div class="info-panel-inner">
      <button class="info-close" id="info-close" type="button" aria-label="Close details">&times;</button>
      <div class="info-eyebrow">Details</div>
      <h2 id="info-title"></h2>
      <div class="info-rows" id="info-rows"></div>
      <div class="info-map-wrap" id="info-map-wrap">
        <div class="info-map" id="info-map"></div>
      </div>
      <div class="info-no-gps" id="info-no-gps">No location data for this file.</div>
    </div>
  </div>
</div>
"""

HEAD = """<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ page_title }}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Fraunces:wght@500;600;700&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap" rel="stylesheet">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css">
<link rel="stylesheet" href="{{ assets_prefix }}assets/style.css">
</head>"""

INDEX_TEMPLATE = _JINJA.from_string("""<!doctype html>
<html lang="en">
""" + HEAD + """
<body>
<header class="site-header">
  <span class="brand"><a href="index.html">{{ title }}</a></span>
</header>

<section class="hero">
  <div class="eyebrow">{{ day_count }} day{{ 's' if day_count != 1 else '' }} on the road</div>
  <h1>{{ title }}</h1>
  {% if subtitle %}<p class="subtitle">{{ subtitle }}</p>{% endif %}
  <div class="stats">
    <div class="stat"><div class="n">{{ day_count }}</div><div class="l">Days</div></div>
    <div class="stat"><div class="n">{{ photo_count }}</div><div class="l">Photos</div></div>
    <div class="stat"><div class="n">{{ video_count }}</div><div class="l">Videos</div></div>
    <div class="stat"><div class="n">{{ distance_km }}</div><div class="l">Km tracked</div></div>
  </div>
</section>

<div class="map-wrap">
  <div class="map" id="overview-map"></div>
  <div class="map-legend">
    {% for d in days %}
    <span><span class="sw" style="background:{{ d.color }}"></span>{{ d.label }}</span>
    {% endfor %}
  </div>
</div>

<div class="day-list">
  {% for d in days %}
  <a class="day-card" href="{{ d.href }}">
    <div class="cover" {% if d.cover %}style="background-image:url('{{ d.cover }}')"{% endif %}></div>
    <div class="body">
      <div class="stamp"><span class="num">{{ d.day_number if d.day_number else '?' }}</span><span class="of">{{ 'N/A' if d.is_unknown else 'DAY' }}</span></div>
      <div class="date">{{ d.date_display }}</div>
      <h3>{{ d.title }}</h3>
      {% if d.excerpt %}<p>{{ d.excerpt }}</p>{% endif %}
      <div class="meta">{{ d.photo_count }} photo{{ 's' if d.photo_count != 1 else '' }}{% if d.video_count %} &middot; {{ d.video_count }} video{{ 's' if d.video_count != 1 else '' }}{% endif %}</div>
    </div>
  </a>
  {% endfor %}
</div>

<footer class="site-footer">Generated with generate_site.py</footer>

<script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.js"></script>
<script src="{{ assets_prefix }}assets/script.js"></script>
<script>
try { initOverviewMap('overview-map', {{ days_json }}); }
catch(e) { console.error('Map failed to load (check your internet connection):', e); }
</script>
</body>
</html>
""")

DAY_TEMPLATE = _JINJA.from_string("""<!doctype html>
<html lang="en">
""" + HEAD + """
<body>
<header class="site-header">
  <span class="brand"><a href="../index.html">{{ trip_title }}</a></span>
  <span class="crumb">{{ 'Unsorted' if is_unknown else 'Day ' ~ day_number ~ ' of ' ~ day_total }}</span>
</header>

<div class="day-header">
  <div class="stamp"><span class="num">{{ day_number if day_number else '?' }}</span><span class="of">{{ 'N/A' if is_unknown else 'DAY' }}</span></div>
  <h1>{{ day_title }}</h1>
  <div class="date">{{ date_display }}</div>
  <div class="day-nav">
    <span>{% if prev_href %}<a href="{{ prev_href }}">&larr; {{ prev_label }}</a>{% endif %}</span>
    <span>{% if next_href %}<a href="{{ next_href }}">{{ next_label }} &rarr;</a>{% endif %}</span>
  </div>
  {% if prev_href or next_href %}<div class="day-nav-hint">&larr; &rarr; keys or swipe also work</div>{% endif %}
</div>

<div class="description {{ 'empty' if not description else '' }}">
  {% if description %}
    {% for para in description %}<p>{{ para }}</p>{% endfor %}
  {% else %}
    <p>No description yet — add one for {{ date_key }} in trip_content.json and re-run the generator.</p>
  {% endif %}
</div>

{% if route %}
<div class="map-wrap">
  <div class="map" id="day-map"></div>
</div>
{% endif %}

{% if items %}
<div class="grid-controls">
  <span class="grid-controls-label">Thumbnail size</span>
  <div class="size-toggle" role="group" aria-label="Thumbnail size">
    <button type="button" class="size-btn" data-size="small">S</button>
    <button type="button" class="size-btn" data-size="medium">M</button>
    <button type="button" class="size-btn" data-size="large">L</button>
  </div>
</div>
{% endif %}

<div class="grid" id="thumb-grid">
  {% for item in items %}
  <div class="thumb" data-slide-index="{{ loop.index0 }}">
    <img src="{{ item.thumb_url }}" loading="lazy" alt="">
    {% if item.media_type == 'video' %}
    <div class="play"><svg viewBox="0 0 24 24" fill="white"><circle cx="12" cy="12" r="11" fill="rgba(14,23,41,.55)"/><path d="M10 8l6 4-6 4V8z"/></svg></div>
    {% endif %}
    {% if item.live %}<span class="live-badge" title="Live Photo">LIVE</span>{% endif %}
    {% if item.time_display %}<span class="badge">{{ item.time_display }}</span>{% endif %}
  </div>
  {% endfor %}
</div>

<footer class="site-footer">Generated with generate_site.py</footer>

""" + SLIDESHOW_HTML + """
<script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.js"></script>
<script src="{{ assets_prefix }}assets/script.js"></script>
<script>
// slideshow first: it must keep working even if the map/tiles can't load (no internet, blocked CDN, etc.)
initSlideshow({{ slides_json }});
initThumbSizeControl();
initDayNav({{ prev_href_json }}, {{ next_href_json }});
{% if route %}
try { initDayMap('day-map', {{ route_json }}, {{ route_thumbs_json }}, {{ route_slide_indices_json }}); }
catch(e) { console.error('Map failed to load (check your internet connection):', e); }
{% endif %}
</script>
</body>
</html>
""")


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------

def render_site(days: list, content: dict, output_dir: Path) -> list:
    """Write all pages. Returns the day pages written (relative paths)."""
    (output_dir / "days").mkdir(parents=True, exist_ok=True)
    pages = []

    total_photos = sum(d.photo_count for d in days)
    total_videos = sum(d.video_count for d in days)
    total_km = round(sum(d.distance_km for d in days), 1)
    real_day_count = sum(1 for d in days if not d.is_unknown)

    # 1-based rank among *real* days only, so "Day 3 of 5" always means
    # "day 3 of 5 actual trip days" — the unknown-date bucket (if any)
    # doesn't consume a number and isn't counted in that denominator
    real_rank = {}
    rank = 0
    for d in days:
        if not d.is_unknown:
            rank += 1
            real_rank[d.date_key] = rank

    day_summaries = []
    for d in days:
        c_title, c_desc = day_text(content, d.date_key)
        default_title = "Unsorted" if d.is_unknown else f"Day {real_rank[d.date_key]}"
        title = c_title or default_title
        desc = c_desc
        excerpt = (desc[:110] + "…") if len(desc) > 110 else desc
        cover = f"thumbs/{quote(d.items[0].thumb_file)}" if d.items else None
        date_display = "Date unknown" if d.is_unknown else format_long_date(d.date_obj)
        color = UNKNOWN_DAY_COLOR if d.is_unknown else DAY_COLORS[(real_rank[d.date_key] - 1) % len(DAY_COLORS)]
        day_summaries.append({
            "href": f"days/{d.date_key}.html",
            "date_display": date_display,
            "title": title,
            "excerpt": excerpt,
            "cover": cover,
            "photo_count": d.photo_count,
            "video_count": d.video_count,
            "color": color,
            "route": d.route,
            "label": ("Unsorted" if d.is_unknown else f"Day {real_rank[d.date_key]}") + f" — {d.date_key}",
            "is_unknown": d.is_unknown,
            "day_number": None if d.is_unknown else real_rank[d.date_key],
        })

    index_html = INDEX_TEMPLATE.render(
        page_title=trip_title(content),
        assets_prefix="",
        title=trip_title(content),
        subtitle=_text(content.get("subtitle")),
        day_count=real_day_count,
        photo_count=total_photos,
        video_count=total_videos,
        distance_km=total_km,
        days=day_summaries,
        days_json=script_json(day_summaries),
    )
    (output_dir / "index.html").write_text(index_html, encoding="utf-8")

    for i, d in enumerate(days):
        c_title, c_desc = day_text(content, d.date_key)
        default_title = "Unsorted" if d.is_unknown else f"Day {real_rank[d.date_key]}"
        title = c_title or default_title
        desc_raw = c_desc
        description = [p.strip() for p in desc_raw.split("\n\n") if p.strip()] if desc_raw else []
        date_display = "Date unknown" if d.is_unknown else format_long_date(d.date_obj)

        items_ctx = []
        slides = []
        route_thumbs = []
        route_slide_indices = []
        for idx, it in enumerate(d.items):
            item_ctx = {"thumb_url": f"../thumbs/{quote(it.thumb_file)}", "media_type": it.media_type,
                        "live": bool(it.live_file)}
            if not d.is_unknown:
                # only show a time badge when it's a real capture time — a
                # file-modified fallback time isn't worth presenting as fact
                item_ctx["time_display"] = it.dt.strftime("%H:%M")
            items_ctx.append(item_ctx)

            slide = {
                "type": it.media_type,
                "display": f"../media/{quote(it.display_file)}",
                "time": None if d.is_unknown else it.dt.isoformat(),
                "lat": it.lat,
                "lon": it.lon,
                "detail": it.detail,
            }
            if it.media_type == "video":
                slide["poster"] = f"../thumbs/{quote(it.thumb_file)}"
            if it.live_file:
                slide["live"] = f"../media/{quote(it.live_file)}"
            slides.append(slide)
            if it.has_gps:
                route_thumbs.append(f"../thumbs/{quote(it.thumb_file)}")
                # the day map's route only includes items with GPS, so its
                # index doesn't line up with the full slides list — this
                # records which slide each map point actually corresponds
                # to, so clicking a marker can open the right photo
                route_slide_indices.append(idx)

        prev_href = f"{days[i-1].date_key}.html" if i > 0 else None
        next_href = f"{days[i+1].date_key}.html" if i < len(days) - 1 else None

        day_html = DAY_TEMPLATE.render(
            page_title=f"{title} — {trip_title(content)}",
            assets_prefix="../",
            trip_title=trip_title(content),
            day_number=None if d.is_unknown else real_rank[d.date_key],
            day_total=real_day_count,
            is_unknown=d.is_unknown,
            day_title=title,
            date_display=date_display,
            date_key=d.date_key,
            description=description,
            route=d.route,
            route_json=script_json(d.route),
            route_thumbs_json=script_json(route_thumbs),
            route_slide_indices_json=script_json(route_slide_indices),
            items=items_ctx,
            slides_json=script_json(slides),
            prev_href=prev_href,
            prev_href_json=script_json(prev_href),
            prev_label=days[i-1].date_key if i > 0 else "",
            next_href=next_href,
            next_href_json=script_json(next_href),
            next_label=days[i+1].date_key if i < len(days) - 1 else "",
        )
        (output_dir / "days" / f"{d.date_key}.html").write_text(day_html, encoding="utf-8")
        pages.append(f"days/{d.date_key}.html")

    assets_dir = output_dir / "assets"
    assets_dir.mkdir(exist_ok=True)
    (assets_dir / "style.css").write_text(CSS, encoding="utf-8")
    (assets_dir / "script.js").write_text(JS, encoding="utf-8")
    return pages


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Build a static trip website from a photo/video folder.")
    parser.add_argument("-i", "--input", required=True, type=Path, help="Folder containing your photos/videos")
    parser.add_argument("-o", "--output", default=Path("site"), type=Path, help="Output folder for the generated site")
    parser.add_argument("-c", "--content", default=Path("trip_content.json"), type=Path,
                         help="JSON file holding the trip title and per-day descriptions (default: ./trip_content.json)")
    parser.add_argument("--thumb-size", type=int, default=400, help="Max thumbnail dimension in px")
    parser.add_argument("--force", action="store_true",
                         help="Regenerate all thumbnails and media, even for files that haven't changed")
    parser.add_argument("--tz-offset", type=float, default=None,
                         help="Fixed UTC offset in hours (e.g. 2 or -5.5) used to convert VIDEO "
                              "timestamps to your trip's local time. Video files store an absolute "
                              "UTC creation time; without this flag it's converted using whatever "
                              "timezone this script happens to run in, which is only correct if "
                              "you're generating the site from the same timezone the trip was in. "
                              "Photos are unaffected — camera EXIF timestamps are already local, "
                              "with no timezone conversion involved.")
    parser.add_argument("--workers", type=int, default=None,
                         help="Number of files to process in parallel (default: one per CPU core). "
                              "Lower this if you want to keep using your computer for other things "
                              "while it runs.")
    parser.add_argument("--no-dedupe", action="store_true",
                         help="Don't skip exact-duplicate files (same content found under two "
                              "different filenames/folders, e.g. the same photo imported from two "
                              "devices). Duplicate detection is on by default.")
    parser.add_argument("--no-live-photos", action="store_true",
                         help="Show iPhone Live Photo clips as separate videos instead of "
                              "attaching them to their photos.")
    args = parser.parse_args()

    if not args.input.is_dir():
        print(f"Input folder not found: {args.input}", file=sys.stderr)
        sys.exit(1)
    in_resolved, out_resolved = args.input.resolve(), args.output.resolve()
    if out_resolved == in_resolved or out_resolved in in_resolved.parents:
        print("The output folder can't be the photos folder itself or a folder that "
              "contains it. Pick a separate folder with -o.", file=sys.stderr)
        sys.exit(1)

    if not HEIF_SUPPORT:
        print("Note: pillow-heif not installed — .heic/.heif files will be skipped.\n"
              "      Install with: pip install pillow-heif", file=sys.stderr)

    if args.tz_offset is not None:
        print(f"Using a fixed UTC{args.tz_offset:+g}h offset for video timestamps.")

    # check the content file *before* the (possibly long) processing step, so
    # a typo is reported immediately and never leads to the file being rewritten
    if args.content.exists():
        try:
            parse_content_file(args.content)
        except ContentFileError as e:
            print(f"! {e}\n  Nothing was changed. Fix the file and run again.", file=sys.stderr)
            sys.exit(1)

    print(f"Scanning {args.input} ...")
    manifest = load_manifest(args.output)
    items, records = scan_media(args.input, args.output, args.thumb_size, args.force,
                                tz_offset=args.tz_offset, workers=args.workers,
                                dedupe=not args.no_dedupe, manifest=manifest)
    if not items:
        print("No supported photos or videos found.", file=sys.stderr)
        sys.exit(1)
    if not args.no_live_photos:
        items = pair_live_photos(items)

    fallback_count = sum(1 for it in items if it.dt_is_fallback)
    gps_count = sum(1 for it in items if it.has_gps)
    print(f"\nFound {len(items)} media files "
          f"({fallback_count} without EXIF date, using file date instead; "
          f"{gps_count} with GPS location).")

    days = group_by_day(items)
    print(f"Grouped into {len(days)} day(s).")

    try:
        content = load_or_init_content(args.content, [d.date_key for d in days])
    except ContentFileError as e:  # e.g. edited while the scan was running
        print(f"! {e}\n  Nothing was changed. Fix the file and run again.", file=sys.stderr)
        sys.exit(1)

    pages = render_site(days, content, args.output)

    # only after the site was written successfully: drop outputs nothing
    # uses any more, then record this build for the next run
    keep = {o for rec in records.values() for o in rec["outputs"]} | set(pages)
    remove_stale_outputs(args.output, manifest, keep)
    save_manifest(args.output, args.thumb_size, records, pages)
    print(f"\nSite generated at: {args.output.resolve()}")
    print(f"Open {args.output.resolve() / 'index.html'} in a browser to view it.")


if __name__ == "__main__":
    main()
