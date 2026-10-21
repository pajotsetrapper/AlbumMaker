"""
trip_common.py: code shared by generate_site.py and trip_server.py.

Both scripts build the same trip site, one as static files and one served
live, so everything they have in common lives here: reading photo and
video metadata, choosing which files to include, grouping them into days,
handling trip_content.json, and the page design (CSS, JavaScript and
templates). Keep this file next to the two scripts.
"""

from __future__ import annotations

import argparse
import base64
import filecmp
import hashlib
import io
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tarfile
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from PIL import Image
from PIL.ExifTags import GPSTAGS
from jinja2 import Environment
from markupsafe import Markup

try:
    import pillow_heif
    pillow_heif.register_heif_opener()
    HEIF_SUPPORT = True
except ImportError:
    HEIF_SUPPORT = False


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


# --------------------------------------------------------------------------
# Shared safety helpers: which files to scan, reading/writing
# trip_content.json without ever losing the user's text, and emitting data
# into HTML/<script> safely.
# --------------------------------------------------------------------------

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
    return {"title": "My Trip", "subtitle": "", "days": {}, "photos": {}}


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
    photos = loaded.get("photos")
    if photos is not None and not isinstance(photos, dict):
        raise ContentFileError(f'{path}: "photos" must be an object mapping file paths to entries')
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


# --------------------------------------------------------------------------
# Per-photo and per-day edits, stored in trip_content.json:
#
#   "photos": { "<path relative to the photos folder>":
#                 {"caption": "...", "favorite": true, "hidden": true} }
#   "days":   { "<date>": {"title": "...", "description": "...",
#                          "cover": "<same kind of relative path>"} }
#
# Only values that differ from the defaults are stored. Anything else in the
# file (including keys this code doesn't know) is left exactly as it was.
# --------------------------------------------------------------------------

CAPTION_MAX = 2000


def photo_entry(content: dict, rel: str) -> dict:
    """Caption / favorite / hidden for one photo, as clean plain values."""
    photos = content.get("photos")
    e = photos.get(rel) if isinstance(photos, dict) else None
    if not isinstance(e, dict):
        e = {}
    return {"caption": _text(e.get("caption")).strip(),
            "favorite": e.get("favorite") is True,
            "hidden": e.get("hidden") is True}


def hidden_rels(content: dict) -> set:
    photos = content.get("photos")
    if not isinstance(photos, dict):
        return set()
    return {rel for rel, e in photos.items() if isinstance(e, dict) and e.get("hidden") is True}


def _day_entry(content: dict, date_key: str) -> dict:
    days = content.get("days")
    if not isinstance(days, dict):
        days = content["days"] = {}
    entry = days.get(date_key)
    if not isinstance(entry, dict):
        entry = days[date_key] = {}
    return entry


def set_photo_fields(content: dict, rel: str, caption=None, favorite=None, hidden=None) -> dict:
    """Change a photo's fields in `content` (in place). Returns its new entry."""
    photos = content.get("photos")
    if not isinstance(photos, dict):
        photos = content["photos"] = {}
    entry = photos.get(rel)
    entry = dict(entry) if isinstance(entry, dict) else {}
    if caption is not None:
        entry["caption"] = caption.strip()[:CAPTION_MAX]
    if favorite is not None:
        entry["favorite"] = bool(favorite)
    if hidden is not None:
        entry["hidden"] = bool(hidden)
    for key in ("caption", "favorite", "hidden"):   # store only non-default values
        if not entry.get(key):
            entry.pop(key, None)
    if entry:
        photos[rel] = entry
    else:
        photos.pop(rel, None)
    return photo_entry(content, rel)


def set_cover(content: dict, date_key: str, rel: Optional[str]):
    entry = _day_entry(content, date_key)
    if rel:
        entry["cover"] = rel
    else:
        entry.pop("cover", None)


def set_day_text(content: dict, date_key: str, title=None, description=None):
    entry = _day_entry(content, date_key)
    if title is not None:
        entry["title"] = title.strip()
    if description is not None:
        entry["description"] = description.replace("\r\n", "\n").strip()


def report_unknown_photo_keys(content: dict, known_rels):
    """Captions/flags for files that weren't found (renamed or moved?) stay in
    the file untouched; this just says so."""
    photos = content.get("photos")
    if not isinstance(photos, dict):
        return
    missing = sorted(set(photos) - set(known_rels))
    if missing:
        shown = ", ".join(missing[:3]) + (", ..." if len(missing) > 3 else "")
        print(f"Note: trip_content.json has captions/flags for {len(missing)} file(s) that "
              f"weren't found ({shown}). They're kept, but only apply to that exact path.")


def script_json(obj) -> Markup:
    """JSON that's safe to drop inside a <script> block: escapes the
    characters that could otherwise close the script tag or start an HTML
    comment/entity. json.dumps alone is NOT safe there."""
    s = (json.dumps(obj)
         .replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026"))
    return Markup(s)


_JINJA = Environment(autoescape=True)


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


LIVE_CLIP_MAX_SECONDS = 4.0


def is_live_photo_clip(meta: dict) -> bool:
    if meta.get("live_photo_id"):
        return True
    duration = meta.get("duration_s")
    return duration is not None and duration <= LIVE_CLIP_MAX_SECONDS


def valid_gps(lat: float, lon: float) -> bool:
    """Reject coordinates that can't be real: NaN/inf (EXIF rationals with
    a zero denominator), out of range, or exactly 0,0 (the usual "no fix"
    placeholder). Bad values would otherwise break the maps."""
    return (math.isfinite(lat) and math.isfinite(lon)
            and -90 <= lat <= 90 and -180 <= lon <= 180
            and not (lat == 0 and lon == 0))


def _parse_iso_datetime(s: str) -> Optional[datetime]:
    """ISO 8601 timestamps as written by cameras and ffprobe, including
    offsets without a colon (+0200) that Python 3.9's parser rejects."""
    s = str(s).strip().replace("Z", "+00:00")
    s = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", s)
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def check_ffmpeg():
    missing = [tool for tool in ("ffmpeg", "ffprobe") if shutil.which(tool) is None]
    if missing:
        print(f"Note: {' and '.join(missing)} not found. Videos will still be included, but "
              f"without date, location or preview frame. Install ffmpeg to fix this.",
              file=sys.stderr)


def placeholder_thumbnail(thumb_size: int) -> Image.Image:
    """Plain dark tile used when a video's preview frame can't be extracted
    (no ffmpeg, or an undecodable stream), so the grid never shows a broken
    image. The grid draws its play icon on top, as for any video."""
    return Image.new("RGB", (thumb_size, thumb_size * 3 // 4), (34, 50, 82))


def same_file_content(a: Path, b: Path) -> bool:
    """Byte-for-byte comparison, used to confirm a fingerprint match."""
    try:
        return filecmp.cmp(a, b, shallow=False)
    except OSError:
        return False


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


# --------------------------------------------------------------------------
# Data model
# --------------------------------------------------------------------------

@dataclass
class MediaItem:
    """One photo or video. The first block of fields is shared; the rest is
    filled in by whichever script is running."""
    path: Path                 # the original file
    media_type: str            # 'image' | 'video'
    dt: datetime
    dt_is_fallback: bool       # True if we had to fall back to file mtime
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

    # trip_server: position in the item list, used in URLs (/thumb/42)
    id: int = -1
    # generate_site: output filenames (inside output/thumbs/ and output/media/)
    thumb_file: str = ""
    display_file: str = ""
    original_file: Optional[str] = None  # untouched copy kept next to a converted display_file
    thumb_is_placeholder: bool = False   # video preview couldn't be extracted

    @property
    def has_gps(self) -> bool:
        return self.lat is not None and self.lon is not None

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
        if not valid_gps(lat, lon):
            return None
        return (round(lat, 6), round(lon, 6))
    except Exception:
        return None


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
                # orientations 5-8 mean the camera stored the image sideways;
                # report the size as it's actually displayed
                if exif.get(0x0112) in (5, 6, 7, 8):
                    meta["width"], meta["height"] = meta["height"], meta["width"]
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

    Capture time: if tz_offset_hours is given, the container's UTC
    `creation_time` is converted with that fixed offset. Otherwise an
    iPhone's local `com.apple.quicktime.creationdate` is used when present,
    and plain `creation_time` is converted using the timezone this script
    runs in (only right if that's the trip's timezone). Photos aren't
    affected: camera EXIF timestamps are already local."""
    meta = {}
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_format", "-show_streams", str(path)],
            capture_output=True, text=True, errors="replace", timeout=30,
        )
    except FileNotFoundError:
        # ffprobe isn't installed: keep the video, just without metadata
        # (main() already explained this once; don't repeat it per file)
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

    # iPhones also store the local capture time with its UTC offset (e.g.
    # 2024-05-02T14:00:00+0200). Unless --tz-offset says otherwise, that's
    # the best source: the clock time where the video was shot, the same
    # thing a photo's EXIF time records, with no timezone guesswork.
    local_ct = tags.get("com.apple.quicktime.creationdate")
    if local_ct and tz_offset_hours is None:
        parsed = _parse_iso_datetime(local_ct)
        if parsed is not None:
            meta["dt"] = parsed.replace(tzinfo=None)

    ct = tags.get("creation_time")
    if ct and "dt" not in meta:
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
                lat, lon = float(m.group(1)), float(m.group(2))
                if valid_gps(lat, lon):
                    meta["gps"] = (round(lat, 6), round(lon, 6))
            except Exception:
                pass

    if video_stream:
        meta["width"] = video_stream.get("width")
        meta["height"] = video_stream.get("height")
        # phones record portrait video as landscape plus a rotation flag
        # (a "rotate" tag in older ffprobe, a display matrix in newer)
        rotation = 0
        try:
            rotation = int(float((video_stream.get("tags") or {}).get("rotate", 0)))
            for side_data in video_stream.get("side_data_list") or []:
                if "rotation" in side_data:
                    rotation = int(float(side_data["rotation"]))
        except (TypeError, ValueError):
            pass
        if rotation % 180 and meta["width"] and meta["height"]:
            meta["width"], meta["height"] = meta["height"], meta["width"]
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
# Scanning: choosing files, duplicates, Live Photos, grouping into days
# --------------------------------------------------------------------------

def fast_fingerprint(path: Path, sample_size: int = 65536) -> Optional[tuple]:
    """Cheap first-pass fingerprint for spotting duplicate files: file size
    plus a hash of the first and last chunk of bytes, so it stays fast even
    for multi-GB videos. Files that match are then compared byte-for-byte
    (same_file_content) before one is skipped. Re-exported or re-compressed
    copies of a photo are different files and are not detected."""
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


def collect_candidates(root: Path, exclude_dirs=()) -> list:
    """(path, 'image' | 'video') for every supported file under root."""
    candidates = []
    for path in list_media_files(root, exclude_dirs):
        ext = path.suffix.lower()
        if ext in IMAGE_EXTS:
            if ext in (".heic", ".heif") and not HEIF_SUPPORT:
                print(f"  ! skipping {path.name}: install pillow-heif for HEIC support "
                      f"(pip install pillow-heif)", file=sys.stderr)
                continue
            candidates.append((path, "image"))
        elif ext in VIDEO_EXTS:
            candidates.append((path, "video"))
    return candidates


def dedupe_candidates(candidates: list) -> list:
    """Drop files whose content is identical to one seen earlier."""
    seen = {}
    deduped = []
    skipped = 0
    for path, media_type in candidates:
        fp = fast_fingerprint(path)
        original = seen.get(fp) if fp is not None else None
        # the fingerprint only samples each file, so confirm a match
        # byte-for-byte before skipping anything
        if original is not None and same_file_content(original, path):
            print(f"  ~ skipping {path.name}: exact duplicate of {original.name}", file=sys.stderr)
            skipped += 1
            continue
        if fp is not None:
            seen[fp] = path
        deduped.append((path, media_type))
    if skipped:
        print(f"Skipped {skipped} duplicate file(s) (identical content to a file already found).")
    return deduped


def build_media_item(path: Path, media_type: str, meta: dict, **extra) -> MediaItem:
    """Turn a file's metadata (read_image_metadata / read_video_metadata)
    into a MediaItem, including the rows for the slideshow's info panel.
    `extra` sets script-specific fields (id, thumb_file, ...)."""
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
        path=path,
        media_type=media_type,
        dt=dt,
        dt_is_fallback=dt_is_fallback,
        lat=gps[0] if gps else None,
        lon=gps[1] if gps else None,
        width=w, height=h,
        detail=detail,
        is_mpo=bool(meta.get("is_mpo")),
        is_live_clip=(media_type == "video" and is_live_photo_clip(meta)),
        **extra,
    )


def pair_live_photos(items: list) -> list:
    """Attach each Live Photo clip to its photo (same folder, same name,
    see is_live_photo_clip) and drop the clip from the list, so it plays
    from the photo instead of cluttering the grid as a separate video.
    Returns the remaining items, sorted by path."""
    items = sorted(items, key=lambda it: it.path)
    photos = {}
    for it in items:
        if it.media_type == "image":
            photos.setdefault((it.path.parent, it.path.stem.lower()), it)
    kept = []
    paired = 0
    for it in items:
        if it.media_type == "video" and it.is_live_clip:
            photo = photos.get((it.path.parent, it.path.stem.lower()))
            if photo is not None and photo.live_clip is None:
                photo.live_clip = it
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
# Date sanity check
#
# Some files carry a capture date that is plainly wrong: the placeholder
# 1904-01-01 / 1970-01-01 / 1980-01-01 that editors and apps write when they
# don't know the time, a camera whose clock was never set (it counts up from
# 2000-01-01), or a clock set into the future. Left alone, each one invents a
# "day" that never happened, far from the real trip. Such files are filed
# under "Unsorted" instead, exactly like files that have no date at all, and
# their Details panel says which date was ignored and why.
# --------------------------------------------------------------------------

MIN_PLAUSIBLE_YEAR = 1995
CLUSTER_WINDOW_DAYS = 366   # how far from the typical date a file may be
CLUSTER_MIN_FILES = 5       # with fewer files there is no "typical" to judge by
CLUSTER_MIN_SHARE = 0.8     # the typical dates must cover at least this share of the files


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


def find_suspicious_dates(items: list, date_range=None, now=None) -> list:
    """[(item, reason)] for files whose embedded capture date looks wrong.
    Files that already have no date (dt_is_fallback) aren't judged."""
    now = now or datetime.now()
    dated = [it for it in items if not it.dt_is_fallback]
    flagged = []
    if date_range:
        start, end = date_range      # `end` is exclusive
        last = end - timedelta(days=1)
        for it in dated:
            if not start <= it.dt < end:
                flagged.append((it, f"outside --date-range {start:%Y-%m-%d} to {last:%Y-%m-%d}"))
        return flagged

    rest = []
    for it in dated:
        if it.dt.year < MIN_PLAUSIBLE_YEAR:
            flagged.append((it, f"before {MIN_PLAUSIBLE_YEAR}"))
        elif it.dt > now + timedelta(days=1):
            flagged.append((it, "in the future"))
        else:
            rest.append(it)

    # Dates that are possible but nowhere near the others (a camera that was
    # reset to 2000, say). Only judged when most files clearly agree with
    # each other; a folder that really spans several years is left alone.
    if len(rest) >= CLUSTER_MIN_FILES:
        typical = sorted(it.dt for it in rest)[len(rest) // 2]
        window = timedelta(days=CLUSTER_WINDOW_DAYS)
        near = [it for it in rest if abs(it.dt - typical) <= window]
        if len(near) / len(rest) >= CLUSTER_MIN_SHARE:
            for it in rest:
                if abs(it.dt - typical) > window:
                    flagged.append((it, f"more than a year from the rest of your files "
                                        f"(most are from about {typical:%Y-%m})"))
    return flagged


def apply_date_check(items: list, date_range=None, now=None, quiet: bool = False) -> int:
    """Move files with a wrong-looking date to the Unsorted group. Returns how many."""
    flagged = find_suspicious_dates(items, date_range, now)
    report = []
    for it, reason in flagged:
        original = it.dt
        try:
            it.dt = datetime.fromtimestamp(it.path.stat().st_mtime)
        except OSError:
            pass
        it.dt_is_fallback = True
        it.dt_rejected = reason
        it.detail["date_note"] = f"Embedded date {original:%Y-%m-%d %H:%M} ignored ({reason})"
        report.append((it.rel or it.path.name, original, reason))
    if report and not quiet:
        print(f"Date check: {len(report)} file(s) have a date that looks wrong, so they are listed "
              f"under Unsorted instead of getting a day of their own:")
        for name, original, reason in report[:8]:
            print(f"  {name}   dated {original:%Y-%m-%d} - {reason}")
        if len(report) > 8:
            print(f"  ... and {len(report) - 8} more")
        print("  If those dates are right, pass --date-range START END (or --no-date-check).")
    return len(report)


def assign_rel_paths(items: list, root: Path):
    """Give every item the key trip_content.json uses for it: its path
    relative to the photos folder, with forward slashes."""
    for it in items:
        try:
            it.rel = it.path.relative_to(root).as_posix()
        except ValueError:
            it.rel = it.path.name


def visible_days(days: list, content: dict, editable: bool = False) -> list:
    """The days a visitor sees: photos marked hidden are dropped, and so are
    days left with nothing. The editor sees everything (hidden ones dimmed)."""
    if editable:
        return days
    hidden = hidden_rels(content)
    if not hidden:
        return days
    out = []
    for d in days:
        items = [it for it in d.items if it.rel not in hidden]
        if items:
            out.append(DayGroup(date_key=d.date_key, date_obj=d.date_obj,
                                is_unknown=d.is_unknown, items=items))
    return out


def pick_cover(d: DayGroup, content: dict) -> MediaItem:
    """The day's cover: the one chosen in trip_content.json, else the first
    photo (not a video frame), else the first item."""
    entry = content.get("days", {}).get(d.date_key)
    want = entry.get("cover") if isinstance(entry, dict) else None
    if isinstance(want, str):
        for it in d.items:
            if it.rel == want:
                return it
    for it in d.items:
        if it.media_type == "image":
            return it
    return d.items[0]


# --------------------------------------------------------------------------
# Page design (CSS, JavaScript, templates) and the data that fills it
# --------------------------------------------------------------------------

CSS = r"""
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
.leaflet-container{background:#0d1626;}   /* the "sea" wherever a tile is missing */
.map-offline-badge{position:absolute; left:50%; top:.6rem; transform:translateX(-50%); z-index:1000; max-width:90%; text-align:center; background:rgba(14,23,41,.9); color:var(--cream); border:1px solid rgba(237,230,214,.25); border-radius:999px; padding:.3rem .8rem; font-family:var(--mono); font-size:.7rem; pointer-events:none;}
.info-map .map-offline-badge{font-size:.58rem; padding:.2rem .5rem; border-radius:10px;}

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
/* card size on the homepage (the default, medium, is the rule above); min(100%, ...) keeps a card from overflowing a narrow screen */
.day-list.size-small{grid-template-columns:repeat(auto-fill,minmax(min(100%,210px),1fr));}
.day-list.size-large{grid-template-columns:repeat(auto-fill,minmax(min(100%,400px),1fr));}
.day-list.size-xl{grid-template-columns:repeat(auto-fill,minmax(min(100%,560px),1fr));}
.grid-controls.cards{margin-top:1.6rem;}
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
.grid.size-xl{grid-template-columns:repeat(auto-fill,minmax(min(100%,clamp(300px,30vw,560px)),1fr));}
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
.info-download{display:block; margin-top:1rem; font-family:var(--mono); font-size:.78rem; text-align:center;
  padding:.6rem; border:1px solid rgba(31,42,61,.25); border-radius:8px; color:var(--ink);}
.info-download:hover{background:rgba(31,42,61,.06); text-decoration:none;}

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
#slideshow:-webkit-full-screen .slideshow-nav,
#slideshow:fullscreen .slideshow-edit,
#slideshow:-webkit-full-screen .slideshow-edit{
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

/* favorites, hidden photos, captions */
.thumb .fav-badge{position:absolute; top:.35rem; right:.35rem; display:none; color:var(--gold); font-size:1.05rem; line-height:1; text-shadow:0 1px 4px rgba(0,0,0,.8); pointer-events:none;}
.thumb.is-fav .fav-badge{display:block;}
.thumb .hidden-badge{position:absolute; bottom:.35rem; left:.35rem; display:none; background:rgba(14,23,41,.85); color:var(--cream); font-family:var(--mono); font-size:.6rem; letter-spacing:.1em; padding:.1rem .35rem; border-radius:4px; pointer-events:none;}
.thumb.is-hidden .hidden-badge{display:block;}
.thumb.is-hidden img{opacity:.35;}
.grid.fav-only .thumb:not(.is-fav){display:none;}
.fav-filter{margin-right:auto; background:none; border:1px solid rgba(237,230,214,.25); color:var(--cream); font-family:var(--mono); font-size:.75rem; padding:.3rem .7rem; border-radius:6px; cursor:pointer; opacity:.8;}
.fav-filter:hover{opacity:1;}
.fav-filter.active{background:var(--gold); color:var(--navy-deep); border-color:var(--gold); opacity:1; font-weight:600;}
.fav-filter[hidden]{display:none;}
.slideshow-caption .cap-text{font-family:var(--sans); font-size:.95rem; opacity:1; margin-bottom:.25rem; padding:0 3rem;}

/* editing (only rendered for the person running trip_server.py) */
.slideshow-edit{display:flex; gap:.6rem; align-items:center; padding:.2rem 1.4rem 1rem;}
.slideshow-edit[hidden]{display:none;}
.slideshow-edit input{flex:1; min-width:0; background:rgba(237,230,214,.08); border:1px solid rgba(237,230,214,.3); color:var(--cream); border-radius:6px; padding:.45rem .7rem; font-family:var(--sans); font-size:.9rem;}
.slideshow-edit input:focus{border-color:var(--gold); outline:none;}
.slideshow-btn.on{border-color:var(--gold); color:var(--gold);}
.slideshow-progress{position:absolute; top:0; left:0; right:0; height:3px; pointer-events:none; z-index:3;}
.slideshow-progress div{height:100%; width:0; background:var(--gold);}
@media (prefers-reduced-motion: reduce){ .slideshow-progress{display:none;} }
.day-edit-btn{display:inline-block; background:none; border:1px solid rgba(237,230,214,.3); color:var(--cream); font-family:var(--mono); font-size:.72rem; border-radius:6px; padding:.3rem .6rem; cursor:pointer; margin:.4rem .5rem 0 0;}
.day-edit-btn:hover{border-color:var(--gold); color:var(--gold); text-decoration:none;}
.day-edit-btn[hidden]{display:none;}
.day-editor{max-width:1100px; margin:1rem auto; padding:1.3rem 1.8rem; background:var(--parchment); color:var(--ink); border-radius:var(--radius);}
.day-editor[hidden]{display:none;}
.day-editor label{display:block; font-family:var(--mono); font-size:.7rem; letter-spacing:.08em; text-transform:uppercase; opacity:.7; margin:.2rem 0 .3rem;}
.day-editor label span{text-transform:none; letter-spacing:0; opacity:.8;}
.day-editor input, .day-editor textarea{width:100%; font-family:var(--sans); font-size:1rem; color:var(--ink); background:#fbf7ec; border:1px solid rgba(31,42,61,.25); border-radius:8px; padding:.55rem .75rem; margin-bottom:.9rem;}
.day-editor textarea{resize:vertical; line-height:1.5;}
.day-editor-actions{display:flex; gap:.6rem;}
.editor-btn{background:var(--navy); color:var(--cream); border:none; border-radius:6px; padding:.5rem 1rem; font-family:var(--mono); font-size:.8rem; cursor:pointer;}
.editor-btn.secondary{background:rgba(31,42,61,.1); color:var(--ink);}
.toast{position:fixed; left:50%; bottom:1.5rem; transform:translateX(-50%); display:none; background:var(--navy-light); color:var(--cream); border:1px solid var(--gold-dim); padding:.55rem 1rem; border-radius:8px; font-family:var(--mono); font-size:.78rem; z-index:2000; box-shadow:0 6px 20px rgba(0,0,0,.4); max-width:90vw;}
.toast.error{border-color:#d36a6a;}
@media (max-width:640px){ .slideshow-edit{flex-wrap:wrap;} .slideshow-edit input{flex-basis:100%; order:-1;} }

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


JS = r"""
// ---------- shared map helpers ----------
function fmtTime(iso){
  const d = new Date(iso);
  return d.toLocaleString(undefined, {month:'short', day:'numeric', hour:'2-digit', minute:'2-digit'});
}

// ---------- small shared helpers ----------
function isTyping(el){
  if(!el || !el.tagName) return false;
  const tag = el.tagName;
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || !!el.isContentEditable;
}

let toastTimer = null;
function showToast(msg, isError){
  let el = document.getElementById('toast');
  if(!el){
    el = document.createElement('div');
    el.id = 'toast';
    el.className = 'toast';
    el.setAttribute('role', 'status');
    document.body.appendChild(el);
  }
  el.textContent = msg;
  el.classList.toggle('error', !!isError);
  el.style.display = 'block';
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.style.display = 'none'; }, isError ? 5000 : 2200);
}

// Saves an edit through trip_server.py. Returns the parsed reply, or null
// (after showing why) if it didn't work.
async function apiPost(path, body){
  try {
    const r = await fetch(path, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body)
    });
    const data = await r.json().catch(() => ({}));
    if(!r.ok || !data.ok){
      showToast(data.error || ('Could not save (' + r.status + ')'), true);
      return null;
    }
    return data;
  } catch(e){
    showToast('Could not reach the server, so nothing was saved', true);
    return null;
  }
}

// ---------- size controls: thumbnails on day pages, cards on the homepage ----------
// Each .size-toggle names the element it resizes (data-target) and the browser-storage
// key that remembers the choice (data-store), so the two pages choose independently.
const SIZES = ['small', 'medium', 'large', 'xl'];

function applySize(toggle, size){
  const target = document.getElementById(toggle.dataset.target);
  if(!target) return;
  if(SIZES.indexOf(size) < 0) size = 'medium';
  SIZES.forEach(s => target.classList.remove('size-' + s));
  if(size !== 'medium') target.classList.add('size-' + size);
  toggle.querySelectorAll('.size-btn').forEach(b => {
    const on = b.dataset.size === size;
    b.classList.toggle('active', on);
    b.setAttribute('aria-pressed', on ? 'true' : 'false');
  });
  try { localStorage.setItem(toggle.dataset.store, size); } catch(e) { /* private browsing, etc. */ }
  sharpenVisible();          // a bigger size may want bigger thumbnails
}

function initSizeControls(){
  document.querySelectorAll('.size-toggle').forEach(toggle => {
    let saved = 'medium';
    try { saved = localStorage.getItem(toggle.dataset.store) || 'medium'; } catch(e) {}
    applySize(toggle, saved);
    toggle.querySelectorAll('.size-btn').forEach(b => {
      b.addEventListener('click', () => applySize(toggle, b.dataset.size));
    });
  });
}

// ---------- sharper thumbnails where the screen can use more pixels ----------
// A thumbnail is small (see --thumb-size). When the layout (L or XL, a card that crops a
// portrait photo) or a high-resolution screen would stretch one, the page swaps in the
// larger version named by data-hi. Only tiles near the viewport are checked, so a long day
// doesn't download hundreds of large images at once, and a failed download leaves the
// normal thumbnail in place.
const hiSeen = new Set();        // tiles near the viewport that have a larger version
const HI_SLACK = 1.05;           // a 5% stretch is not worth a bigger download

// How many times the screen would stretch the thumbnail along each side (> 1: it wants more pixels).
function stretchFor(el, nw, nh){
  const r = el.getBoundingClientRect();
  if(!r.width || !r.height || !nw || !nh) return 0;
  // a card's cover fills its box (cropping); a grid tile fits the whole image inside its box
  const cover = el.classList.contains('cover');
  const scale = cover ? Math.max(r.width / nw, r.height / nh) : Math.min(r.width / nw, r.height / nh);
  return scale * (window.devicePixelRatio || 1);
}

// The normal thumbnail's own size, measured once (before it is ever replaced).
function baseDims(el, done){
  if(el.dataset.bw){ done(+el.dataset.bw, +el.dataset.bh); return; }
  const finish = (w, h) => {
    if(!w || !h) return;
    el.dataset.bw = w;
    el.dataset.bh = h;
    done(w, h);
  };
  if(el.tagName === 'IMG'){
    if(el.complete && el.naturalWidth) finish(el.naturalWidth, el.naturalHeight);
    else el.addEventListener('load', () => finish(el.naturalWidth, el.naturalHeight), {once: true});
  } else {
    const probe = new Image();
    probe.onload = () => finish(probe.naturalWidth, probe.naturalHeight);
    probe.src = el.dataset.lo;
  }
}

function sharpen(el){
  if(!el.dataset.hi || el.dataset.tier === 'hi') return;
  baseDims(el, (nw, nh) => {
    if(el.dataset.tier === 'hi' || stretchFor(el, nw, nh) <= HI_SLACK) return;
    el.dataset.tier = 'hi';      // claimed now, so a second check can't start a second download
    const loader = new Image();
    loader.onload = () => {
      if(el.tagName === 'IMG') el.src = el.dataset.hi;
      else el.style.backgroundImage = "url('" + el.dataset.hi + "')";
    };
    loader.onerror = () => { el.dataset.tier = ''; };   // keep the normal thumbnail; try again later
    loader.src = el.dataset.hi;
  });
}

function sharpenVisible(){ hiSeen.forEach(sharpen); }

function initSharpThumbs(){
  const els = document.querySelectorAll('img[data-hi], .cover[data-hi]');
  if(!els.length || !('IntersectionObserver' in window)) return;   // older browsers keep the normal thumbnails
  const observer = new IntersectionObserver((entries) => {
    entries.forEach(e => {
      if(e.isIntersecting){ hiSeen.add(e.target); sharpen(e.target); }
      else hiSeen.delete(e.target);
    });
  }, {rootMargin: '300px'});
  els.forEach(el => observer.observe(el));
  let timer = null;     // resizing, zooming the page or moving to another screen changes what is needed
  window.addEventListener('resize', () => { clearTimeout(timer); timer = setTimeout(sharpenVisible, 250); });
}

// ---------- day pages: previous/next day (keys, swipe) and Esc back to the overview ----------
// Every move from one page to another goes through here.
function navigateTo(href){ window.location.href = href; }

function initDayNav(prevHref, nextHref, homeHref){
  function slideshowOpen(){
    const el = document.getElementById('slideshow');
    return !!(el && el.classList.contains('open'));
  }
  function inMap(el){
    return !!(el && el.closest && el.closest('.leaflet-container'));
  }

  // Esc backs out one level at a time: an open map popup, then the title/description
  // form, and only then the page itself.
  function onEscape(e){
    const popupClose = document.querySelector('.leaflet-popup-close-button');
    if(popupClose){ popupClose.click(); return; }
    const editor = document.getElementById('day-editor');
    if(editor && !editor.hidden){
      document.getElementById('day-cancel').click();
      if(isTyping(e.target)) e.target.blur();
      return;
    }
    if(isTyping(e.target)){ e.target.blur(); return; }     // leaving a text box isn't leaving the page
    if(homeHref) navigateTo(homeHref);
  }

  document.addEventListener('keydown', (e) => {
    if(e.defaultPrevented) return;                 // the viewer already used this key press
    if(e.ctrlKey || e.metaKey || e.altKey) return; // e.g. Alt+Left is the browser's own Back
    if(slideshowOpen()) return;                    // the slideshow has its own key handling
    if(e.key === 'Escape'){
      if(!e.repeat) onEscape(e);                   // holding Esc must not carry on past the first press
      return;
    }
    if(isTyping(e.target)) return;                 // typing in the title/caption boxes
    if(inMap(document.activeElement)) return;      // let Leaflet's own keyboard map-panning work
    if(e.key === 'ArrowLeft' && prevHref) navigateTo(prevHref);
    else if(e.key === 'ArrowRight' && nextHref) navigateTo(nextHref);
  });

  let touchStartX = null, touchStartY = null, touchInMap = false;
  document.addEventListener('touchstart', (e) => {
    if(slideshowOpen() || e.touches.length !== 1) return;
    touchInMap = inMap(e.target) || !!(e.target.closest && e.target.closest('.day-editor'));
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
      if(dx < 0 && nextHref) navigateTo(nextHref);
      else if(dx > 0 && prevHref) navigateTo(prevHref);
    }
  }, {passive: true});
}

// ---------- map basics shared by all three maps ----------
const DEFAULT_TILE_URL = 'https://tile.openstreetmap.org/{z}/{x}/{y}.png';
// a 1x1 transparent image, shown in place of a tile that can't be loaded
const BLANK_TILE = 'data:image/gif;base64,R0lGODlhAQABAAAAACH5BAEKAAEALAAAAAABAAEAAAICTAEAOw==';
// Rough outline of the continents (Natural Earth, public domain), drawn under
// the tiles: it only shows where no tile is available (no internet, or an
// area that was never cached), so the points still have something to sit on.
// Stored as integer tenths of a degree: [lon, lat, lon, lat, ...] per ring.
const WORLD_LAND = __WORLD_LAND__;
let worldOutlineCache = null;

function worldOutline(){
  if(!worldOutlineCache){
    worldOutlineCache = WORLD_LAND.map(poly => poly.map(flat => {
      const pts = [];
      for(let i = 0; i + 1 < flat.length; i += 2) pts.push([flat[i + 1] / 10, flat[i] / 10]);
      return pts;
    }));
  }
  return worldOutlineCache;
}

// Shows a small notice while NO tile has loaded but some have failed, i.e.
// the map is effectively offline. A few missing tiles among loaded ones are
// not worth announcing.
function watchTiles(map, layer){
  let loaded = 0, failed = 0, badge = null;
  function refresh(){
    const offline = failed > 0 && loaded === 0;
    if(offline && !badge){
      badge = document.createElement('div');
      badge.className = 'map-offline-badge';
      badge.textContent = 'Map tiles unavailable (offline?). Showing a rough outline.';
      map.getContainer().appendChild(badge);
    } else if(!offline && badge){
      badge.remove();
      badge = null;
    }
  }
  layer.on('tileload', () => { loaded++; refresh(); });
  layer.on('tileerror', () => { failed++; refresh(); });
}

function addBaseMap(map, options){
  const pane = map.createPane('outline');
  pane.style.zIndex = 150;   // under the tiles (200), which hide it wherever they load
  worldOutline().forEach(poly => {
    L.polygon(poly, {pane: 'outline', interactive: false, color: 'rgba(237,230,214,0.22)',
                     weight: 1, fillColor: '#1b2a45', fillOpacity: 1}).addTo(map);
  });
  const layer = L.tileLayer(window.TILE_URL || DEFAULT_TILE_URL, Object.assign({
    maxZoom: 19, className: 'dark-tiles', errorTileUrl: BLANK_TILE
  }, options || {})).addTo(map);
  watchTiles(map, layer);
  return layer;
}

function initOverviewMap(elId, days){
  const map = L.map(elId, {scrollWheelZoom:false});
  addBaseMap(map, {attribution: '&copy; OpenStreetMap contributors'});

  const allPts = [];
  days.forEach(day => {
    day.route.forEach(p => {
      allPts.push(p);
      L.circleMarker(p, {radius:4, color: day.color, fillColor: day.color, fillOpacity:1, weight:1})
        .addTo(map)
        .bindTooltip(day.label, {sticky:true})
        .on('click', () => { navigateTo(day.href); });
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
  addBaseMap(map, {attribution: '&copy; OpenStreetMap contributors'});

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
let EDITABLE = false;     // true only when trip_server.py says this viewer may edit
let DAY_KEY = '';
let FAV_ONLY = false;
let lastFocus = null;
let playing = false;      // slideshow autoplay
let playTimer = null;
let playMs = 5000;
let liveActive = false;   // a Live Photo clip is playing in place of the photo
let ignoreNextPop = false;
const PLAY_SPEEDS = [3000, 5000, 8000, 12000];

const INFO_LABELS = {
  filename: 'File', dimensions: 'Dimensions', filesize: 'File size',
  camera: 'Camera', lens: 'Lens', aperture: 'Aperture', shutter: 'Shutter',
  iso: 'ISO', focal_length: 'Focal length', codec: 'Codec', fps: 'Frame rate',
  duration: 'Duration', gps_display: 'Location', altitude: 'Altitude',
  date_note: 'Date'
};
const INFO_ORDER = ['filename','date_note','dimensions','filesize','camera','lens','aperture','shutter',
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

// ---------- links to a photo, and the Back button ----------
// Opening the viewer adds ONE history entry (#p=<key>); moving between photos
// only rewrites that hash. So Back, or a phone's back button, closes the viewer
// instead of leaving the page, and the address always links to the photo shown.
function slideHash(i){
  const s = SLIDES[i];
  return (s && s.key !== undefined) ? '#p=' + encodeURIComponent(String(s.key)) : '';
}

function slideIndexFromHash(){
  const m = /^#p=(.+)$/.exec(location.hash);
  if(!m) return -1;
  let key;
  try { key = decodeURIComponent(m[1]); } catch(e){ return -1; }
  return SLIDES.findIndex(s => String(s.key) === key);
}

// A link like day.html#p=IMG_0412.jpg opens straight onto that photo. The
// viewer then has no history entry of its own, so closing it only drops the hash.
function openSlideshowFromHash(){
  const idx = slideIndexFromHash();
  if(idx < 0) return;
  try { history.replaceState(null, '', location.href); } catch(e){}
  openSlideshow(idx, true);
}

function onPopState(){
  if(ignoreNextPop){ ignoreNextPop = false; return; }
  const idx = slideIndexFromHash();
  const open = document.getElementById('slideshow').classList.contains('open');
  if(idx >= 0){
    if(!open) openSlideshow(idx, true);
    else if(idx !== currentSlide){ currentSlide = idx; renderSlide(); }
  } else if(open){
    closeSlideshow(true);
  }
}

function openSlideshow(index, fromHistory){
  lastFocus = document.activeElement;
  currentSlide = index;
  const overlay = document.getElementById('slideshow');
  overlay.classList.add('open');
  if(fromHistory !== true && slideHash(index)){
    try { history.pushState({viewer: true}, '', slideHash(index)); } catch(e){}
  }
  renderSlide();
  document.body.style.overflow = 'hidden';
}

function closeSlideshow(fromHistory){
  const overlay = document.getElementById('slideshow');
  if(!overlay.classList.contains('open')) return;
  stopPlaying();
  overlay.classList.remove('open');
  document.getElementById('slideshow-media').innerHTML = '';
  document.body.style.overflow = '';
  closeInfoPanel();
  if(document.fullscreenElement) document.exitFullscreen();
  if(lastFocus && lastFocus.focus) lastFocus.focus();
  if(fromHistory !== true && location.hash.indexOf('#p=') === 0){
    if(history.state && history.state.viewer){
      ignoreNextPop = true;   // we opened it, so step back over our own entry
      history.back();
    } else {
      // arrived on a shared link: just drop the hash and stay on the page
      try { history.replaceState(null, '', location.pathname + location.search); } catch(e){}
    }
  }
}

// ---------- autoplay ----------
function autoBlocked(){
  // don't move on while someone is reading details, zoomed in, typing a
  // caption, watching a Live Photo clip, or has the tab in the background
  return infoPanelOpen || liveActive || ZOOM.scale > 1 || document.hidden ||
         isTyping(document.activeElement);
}

function resetProgress(){
  const bar = document.getElementById('slideshow-progress-bar');
  if(!bar) return;
  bar.style.transition = 'none';
  bar.style.width = '0';
}

function startProgress(ms){
  const bar = document.getElementById('slideshow-progress-bar');
  if(!bar) return;
  resetProgress();
  void bar.offsetWidth;   // restart the animation
  bar.style.transition = 'width ' + ms + 'ms linear';
  bar.style.width = '100%';
}

function scheduleAdvance(){
  clearTimeout(playTimer);
  resetProgress();
  if(!playing || !document.getElementById('slideshow').classList.contains('open')) return;
  const item = SLIDES[currentSlide];
  if(!item) return;
  if(item.type === 'video') return;   // moves on when the clip ends (see renderSlide)
  if(autoBlocked()){ playTimer = setTimeout(scheduleAdvance, 500); return; }
  startProgress(playMs);
  playTimer = setTimeout(() => {
    if(autoBlocked()){ scheduleAdvance(); return; }
    stepSlide(1);
  }, playMs);
}

function setPlaying(on){
  playing = !!on;
  const btn = document.getElementById('slideshow-play');
  const spd = document.getElementById('slideshow-speed');
  if(btn){
    btn.innerHTML = playing ? '&#10074;&#10074; Pause' : '&#9654; Play';
    btn.setAttribute('aria-pressed', playing ? 'true' : 'false');
    btn.classList.toggle('on', playing);
  }
  if(spd){ spd.hidden = !playing; spd.textContent = (playMs / 1000) + 's'; }
  scheduleAdvance();
}

function stopPlaying(){
  if(playing) setPlaying(false);
  else { clearTimeout(playTimer); resetProgress(); }
}

function togglePlaying(){ setPlaying(!playing); }

function cycleSpeed(){
  const i = PLAY_SPEEDS.indexOf(playMs);
  playMs = PLAY_SPEEDS[(i + 1) % PLAY_SPEEDS.length];
  try { localStorage.setItem('tripSlideMs', String(playMs)); } catch(e){}
  document.getElementById('slideshow-speed').textContent = (playMs / 1000) + 's';
  scheduleAdvance();
}

function loadPlayMs(){
  try {
    const v = parseInt(localStorage.getItem('tripSlideMs'), 10);
    if(PLAY_SPEEDS.indexOf(v) >= 0) return v;
  } catch(e){}
  return 5000;
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

function renderCaption(){
  const item = SLIDES[currentSlide];
  const capEl = document.getElementById('slideshow-caption');
  if(!item || !capEl) return;
  capEl.textContent = '';
  if(item.caption){
    const t = document.createElement('div');
    t.className = 'cap-text';
    t.textContent = item.caption;
    capEl.appendChild(t);
  }
  const meta = document.createElement('div');
  meta.textContent = (item.time ? fmtTime(item.time) + ' — ' : '') + (currentSlide + 1) + ' / ' + SLIDES.length;
  capEl.appendChild(meta);
}

function renderSlide(){
  liveActive = false;
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
      // while playing, a video holds the slide until it finishes
      mediaTag.addEventListener('ended', () => {
        if(playing && SLIDES[currentSlide] === item) stepSlide(1);
      }, {once: true});
      mediaTag.addEventListener('error', () => {
        if(playing && SLIDES[currentSlide] === item){
          clearTimeout(playTimer);
          playTimer = setTimeout(() => stepSlide(1), playMs);
        }
      }, {once: true});
    } else if(mediaTag.complete){
      clearLoading();
    } else {
      mediaTag.addEventListener('load', clearLoading, {once: true});
      mediaTag.addEventListener('error', clearLoading, {once: true});
    }
  } else {
    clearLoading();
  }

  renderCaption();
  if(infoPanelOpen) renderInfoPanel();
  syncEditBar();

  const liveBtn = document.getElementById('slideshow-live');
  if(liveBtn) liveBtn.hidden = !item.live;

  preloadSlide((currentSlide + 1) % SLIDES.length);
  preloadSlide((currentSlide - 1 + SLIDES.length) % SLIDES.length);

  const h = slideHash(currentSlide);
  if(h && document.getElementById('slideshow').classList.contains('open')){
    try { history.replaceState(history.state, '', h); } catch(e){}
  }
  scheduleAdvance();
}

// With "Favorites" switched on, arrows/swipes step through favorites only.
function stepSlide(dir){
  const n = SLIDES.length;
  for(let k = 1; k <= n; k++){
    const i = (((currentSlide + dir * k) % n) + n) % n;
    if(!FAV_ONLY || SLIDES[i].favorite){ currentSlide = i; renderSlide(); return; }
  }
}
function nextSlide(){ stepSlide(1); }
function prevSlide(){ stepSlide(-1); }

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
  liveActive = true;
  clearTimeout(playTimer);
  resetProgress();
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

  const downloadLink = document.getElementById('info-download');
  if(downloadLink){
    if(item.downloadUrl){
      downloadLink.href = item.downloadUrl;
      downloadLink.style.display = '';
    } else {
      downloadLink.style.display = 'none';
    }
  }

  const mapWrap = document.getElementById('info-map-wrap');
  const noGps = document.getElementById('info-no-gps');
  if(item.lat != null && item.lon != null){
    mapWrap.style.display = '';
    noGps.style.display = 'none';
    try {
      if(!infoMap){
        infoMap = L.map('info-map', {zoomControl:false, attributionControl:false, scrollWheelZoom:false});
        addBaseMap(infoMap);
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
  scheduleAdvance();
}

function closeInfoPanel(){
  infoPanelOpen = false;
  document.getElementById('info-panel').classList.remove('open');
  scheduleAdvance();
}

// ---------- favorites filter, and editing (editing only when trip_server.py allows it) ----------
function updateFavUi(){
  const btn = document.getElementById('fav-filter');
  if(!btn) return;
  const n = SLIDES.filter(s => s.favorite).length;
  btn.textContent = '★ Favorites (' + n + ')';
  btn.classList.toggle('active', FAV_ONLY);
  btn.hidden = (n === 0 && !FAV_ONLY && !EDITABLE);
}

function initFavFilter(){
  const btn = document.getElementById('fav-filter');
  if(btn){
    btn.addEventListener('click', () => {
      FAV_ONLY = !FAV_ONLY;
      const grid = document.getElementById('thumb-grid');
      if(grid) grid.classList.toggle('fav-only', FAV_ONLY);
      updateFavUi();
    });
  }
  updateFavUi();
}

function applyThumbState(i){
  const item = SLIDES[i];
  const el = document.querySelector('[data-slide-index="' + i + '"]');
  if(el && item){
    el.classList.toggle('is-fav', !!item.favorite);
    el.classList.toggle('is-hidden', !!item.hidden);
    const img = el.querySelector('img');
    if(img) img.alt = item.caption || '';
  }
  updateFavUi();
}

function syncEditBar(){
  if(!EDITABLE) return;
  const item = SLIDES[currentSlide];
  const fav = document.getElementById('edit-fav');
  const hide = document.getElementById('edit-hide');
  const cap = document.getElementById('edit-caption');
  if(!item || !fav || !hide || !cap) return;
  fav.classList.toggle('on', !!item.favorite);
  fav.textContent = item.favorite ? '★ Favorite' : '☆ Favorite';
  hide.classList.toggle('on', !!item.hidden);
  hide.textContent = item.hidden ? 'Unhide' : 'Hide';
  if(document.activeElement !== cap) cap.value = item.caption || '';
}

async function saveSlide(fields){
  const idx = currentSlide, item = SLIDES[idx];
  if(!item || !item.rel) return null;
  const res = await apiPost('/api/photo', Object.assign({rel: item.rel}, fields));
  if(res){
    Object.assign(item, res.photo);
    applyThumbState(idx);
    if(currentSlide === idx){ renderCaption(); }
  }
  syncEditBar();
  return res;
}

function toggleFavorite(){
  const item = SLIDES[currentSlide];
  if(item) saveSlide({favorite: !item.favorite});
}

async function toggleHidden(){
  const item = SLIDES[currentSlide];
  if(!item) return;
  const res = await saveSlide({hidden: !item.hidden});
  if(res && res.photo.hidden){
    showToast('Hidden from visitors. You still see it here, dimmed.');
  }
}

async function setCover(){
  const item = SLIDES[currentSlide];
  if(!item || !item.rel) return;
  const res = await apiPost('/api/cover', {date: DAY_KEY, rel: item.rel});
  if(res) showToast('Cover photo set for this day');
}

function initEditBar(){
  document.getElementById('edit-fav').addEventListener('click', toggleFavorite);
  document.getElementById('edit-hide').addEventListener('click', toggleHidden);
  document.getElementById('edit-cover').addEventListener('click', setCover);
  const cap = document.getElementById('edit-caption');
  // 'change' fires when the box loses focus (or Enter is pressed) with new text
  cap.addEventListener('change', () => saveSlide({caption: cap.value}));
  cap.addEventListener('keydown', (e) => { if(e.key === 'Enter') cap.blur(); });
}

function initDayEditor(dateKey){
  const openBtn = document.getElementById('day-edit');
  const box = document.getElementById('day-editor');
  if(!openBtn || !box) return;
  const title = document.getElementById('edit-title');
  const desc = document.getElementById('edit-desc');
  openBtn.addEventListener('click', () => { box.hidden = false; openBtn.hidden = true; title.focus(); });
  document.getElementById('day-cancel').addEventListener('click', () => {
    title.value = title.defaultValue;
    desc.value = desc.defaultValue;
    box.hidden = true;
    openBtn.hidden = false;
  });
  document.getElementById('day-save').addEventListener('click', async () => {
    const res = await apiPost('/api/day', {date: dateKey, title: title.value, description: desc.value});
    if(res) location.reload();
  });
}

function initSlideshow(items, opts){
  opts = opts || {};
  SLIDES = items;
  EDITABLE = !!opts.editable;
  DAY_KEY = opts.dayKey || '';
  document.querySelectorAll('[data-slide-index]').forEach(el => {
    el.addEventListener('click', () => openSlideshow(parseInt(el.dataset.slideIndex, 10)));
    el.addEventListener('keydown', (e) => {
      if(e.key === 'Enter' || e.key === ' '){
        e.preventDefault();
        e.stopPropagation();   // the viewer's own Space (play/pause) must not see this key press
        openSlideshow(parseInt(el.dataset.slideIndex, 10));
      }
    });
  });
  initFavFilter();
  if(EDITABLE) initEditBar();
  playMs = loadPlayMs();
  document.getElementById('slideshow-close').addEventListener('click', () => closeSlideshow());
  document.getElementById('slideshow-play').addEventListener('click', togglePlaying);
  document.getElementById('slideshow-speed').addEventListener('click', cycleSpeed);
  document.getElementById('slideshow-next').addEventListener('click', nextSlide);
  document.getElementById('slideshow-prev').addEventListener('click', prevSlide);
  document.getElementById('slideshow-fs').addEventListener('click', toggleFullscreen);
  document.getElementById('slideshow-info').addEventListener('click', toggleInfoPanel);
  document.getElementById('slideshow-live').addEventListener('click', playLive);
  document.getElementById('info-close').addEventListener('click', closeInfoPanel);
  document.getElementById('slideshow').addEventListener('click', (e) => {
    if(e.target.id === 'slideshow') closeSlideshow();
  });
  // after a mouse click, a button must not keep the keyboard focus: otherwise
  // the next Space press would "click" it again instead of play/pausing
  document.getElementById('slideshow').addEventListener('click', (e) => {
    const b = e.target.closest && e.target.closest('button');
    if(b && e.detail > 0) b.blur();
  });
  window.addEventListener('popstate', onPopState);

  document.addEventListener('keydown', (e) => {
    if(!document.getElementById('slideshow').classList.contains('open')) return;
    if(isTyping(e.target)){ if(e.key === 'Escape'){ e.preventDefault(); e.target.blur(); } return; }
    if(e.key === ' ' && e.target.tagName !== 'BUTTON'){ e.preventDefault(); togglePlaying(); }
    else if(e.key === 'ArrowRight') nextSlide();
    else if(e.key === 'ArrowLeft') prevSlide();
    else if(e.key === 'Escape'){ e.preventDefault(); infoPanelOpen ? closeInfoPanel() : closeSlideshow(); }
    else if(e.key.toLowerCase() === 'f') toggleFullscreen();
    else if(e.key.toLowerCase() === 'i') toggleInfoPanel();
    else if(e.key.toLowerCase() === 'l') playLive();
    else if(e.key.toLowerCase() === 's' && EDITABLE) toggleFavorite();
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
<div class="slideshow" id="slideshow" role="dialog" aria-modal="true" aria-label="Photo viewer">
  <div class="slideshow-progress" aria-hidden="true"><div id="slideshow-progress-bar"></div></div>
  <div class="slideshow-bar">
    <span class="slideshow-hint">&larr; &rarr; navigate &middot; Space play/pause &middot; Scroll/pinch to zoom &middot; I info &middot; F fullscreen &middot; Esc close</span>
    <div>
      <button class="slideshow-btn" id="slideshow-play" type="button" aria-pressed="false" title="Play slideshow (Space)">&#9654; Play</button>
      <button class="slideshow-btn" id="slideshow-speed" type="button" hidden title="Time per photo">5s</button>
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
  {% if editable %}
  <div class="slideshow-edit" id="slideshow-edit">
    <button class="slideshow-btn" id="edit-fav" type="button" title="Favorite (S)">&#9734; Favorite</button>
    <input id="edit-caption" type="text" maxlength="2000" placeholder="Add a caption (press Enter to save)" aria-label="Caption">
    <button class="slideshow-btn" id="edit-cover" type="button" title="Use this as the cover photo for the day">Set as cover</button>
    <button class="slideshow-btn" id="edit-hide" type="button" title="Hide from visitors">Hide</button>
  </div>
  {% endif %}
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
      <a class="info-download" id="info-download" href="#" download>Download original file</a>
    </div>
  </div>
</div>
"""


HEAD = """<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ page_title }}</title>
{% if vendor %}
<link rel="stylesheet" href="{{ vendor }}fonts/fonts.css">
<link rel="stylesheet" href="{{ vendor }}leaflet/leaflet.css">
{% else %}
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Fraunces:wght@500;600;700&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap" rel="stylesheet">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css">
{% endif %}
<link rel="stylesheet" href="{{ assets_prefix }}assets/style.css">
<script>window.TILE_URL = {{ tile_url_json }};</script>
</head>"""

INDEX_TEMPLATE = _JINJA.from_string("""<!doctype html>
<html lang="en">
""" + HEAD + """
<body>
<header class="site-header">
  <span class="brand"><a href="{{ home_href }}">{{ title }}</a></span>
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

{% if days %}
<div class="grid-controls cards">
  <span class="grid-controls-label">Card size</span>
  <div class="size-toggle" role="group" aria-label="Card size" data-target="day-list" data-store="tripCardSize">
    <button type="button" class="size-btn" data-size="small">S</button>
    <button type="button" class="size-btn" data-size="medium">M</button>
    <button type="button" class="size-btn" data-size="large">L</button>
    <button type="button" class="size-btn" data-size="xl">XL</button>
  </div>
</div>
{% endif %}

<div class="day-list" id="day-list">
  {% for d in days %}
  <a class="day-card" href="{{ d.href }}">
    <div class="cover" {% if d.cover %}style="background-image:url('{{ d.cover }}')" data-lo="{{ d.cover }}"{% if d.cover_hi %} data-hi="{{ d.cover_hi }}"{% endif %}{% endif %}></div>
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

<footer class="site-footer">{{ footer }}</footer>

{% if vendor %}<script src="{{ vendor }}leaflet/leaflet.js"></script>{% else %}<script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.js"></script>{% endif %}
<script src="{{ assets_prefix }}assets/script.js"></script>
<script>
initSizeControls();
initSharpThumbs();
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
  <span class="brand"><a href="{{ home_href }}">{{ trip_title }}</a></span>
  <span class="crumb">{{ 'Unsorted' if is_unknown else 'Day ' ~ day_number ~ ' of ' ~ day_total }}</span>
</header>

<div class="day-header">
  <div class="stamp"><span class="num">{{ day_number if day_number else '?' }}</span><span class="of">{{ 'N/A' if is_unknown else 'DAY' }}</span></div>
  <h1>{{ day_title }}</h1>
  <div class="date">{{ date_display }}</div>
  {% if editable %}
  <div><button type="button" class="day-edit-btn" id="day-edit">Edit title &amp; description</button><a class="day-edit-btn" href="?preview=1">Preview as visitor</a></div>
  {% endif %}
  <div class="day-nav">
    <span>{% if prev_href %}<a href="{{ prev_href }}">&larr; {{ prev_label }}</a>{% endif %}</span>
    <span>{% if next_href %}<a href="{{ next_href }}">{{ next_label }} &rarr;</a>{% endif %}</span>
  </div>
  <div class="day-nav-hint">{% if prev_href or next_href %}&larr; &rarr; keys or swipe change day &middot; {% endif %}Esc returns to all days</div>
</div>

{% if editable %}
<div class="day-editor" id="day-editor" hidden>
  <label for="edit-title">Title</label>
  <input id="edit-title" type="text" maxlength="200" value="{{ raw_title }}" placeholder="Day title">
  <label for="edit-desc">Description <span>(a blank line starts a new paragraph)</span></label>
  <textarea id="edit-desc" rows="8">{{ raw_description }}</textarea>
  <div class="day-editor-actions">
    <button type="button" class="editor-btn" id="day-save">Save</button>
    <button type="button" class="editor-btn secondary" id="day-cancel">Cancel</button>
  </div>
</div>
{% endif %}

<div class="description {{ 'empty' if not description else '' }}">
  {% if description %}
    {% for para in description %}<p>{{ para }}</p>{% endfor %}
  {% else %}
    <p>No description yet — add one for {{ date_key }} in trip_content.json{{ edit_hint }}</p>
  {% endif %}
</div>

{% if route %}
<div class="map-wrap">
  <div class="map" id="day-map"></div>
</div>
{% endif %}

{% if items %}
<div class="grid-controls">
  <button type="button" class="fav-filter" id="fav-filter" hidden></button>
  <span class="grid-controls-label">Thumbnail size</span>
  <div class="size-toggle" role="group" aria-label="Thumbnail size" data-target="thumb-grid" data-store="tripThumbSize">
    <button type="button" class="size-btn" data-size="small">S</button>
    <button type="button" class="size-btn" data-size="medium">M</button>
    <button type="button" class="size-btn" data-size="large">L</button>
    <button type="button" class="size-btn" data-size="xl">XL</button>
  </div>
</div>
{% endif %}

<div class="grid" id="thumb-grid">
  {% for item in items %}
  <div class="thumb{{ ' is-fav' if item.favorite else '' }}{{ ' is-hidden' if item.hidden else '' }}" data-slide-index="{{ loop.index0 }}" role="button" tabindex="0" aria-label="{{ item.caption or ('Open video' if item.media_type == 'video' else 'Open photo') }}">
    <img src="{{ item.thumb_url }}" loading="lazy" alt="{{ item.caption }}"{% if item.hi_url %} data-hi="{{ item.hi_url }}"{% endif %}>
    {% if item.media_type == 'video' %}
    <div class="play"><svg viewBox="0 0 24 24" fill="white"><circle cx="12" cy="12" r="11" fill="rgba(14,23,41,.55)"/><path d="M10 8l6 4-6 4V8z"/></svg></div>
    {% endif %}
    {% if item.live %}<span class="live-badge" title="Live Photo">LIVE</span>{% endif %}
    {% if item.time_display %}<span class="badge">{{ item.time_display }}</span>{% endif %}
    <span class="fav-badge" title="Favorite">&#9733;</span>
    <span class="hidden-badge">HIDDEN</span>
  </div>
  {% endfor %}
</div>

<footer class="site-footer">{{ footer }}</footer>

""" + SLIDESHOW_HTML + """
{% if vendor %}<script src="{{ vendor }}leaflet/leaflet.js"></script>{% else %}<script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.js"></script>{% endif %}
<script src="{{ assets_prefix }}assets/script.js"></script>
<script>
// slideshow first: it must keep working even if the map/tiles can't load (no internet, blocked CDN, etc.)
initSlideshow({{ slides_json }}, {editable: {{ 'true' if editable else 'false' }}, dayKey: {{ date_key_json }}});
openSlideshowFromHash();
initSizeControls();
initSharpThumbs();
initDayNav({{ prev_href_json }}, {{ next_href_json }}, {{ home_href_json }});
{% if editable %}initDayEditor({{ date_key_json }});{% endif %}
{% if route %}
try { initDayMap('day-map', {{ route_json }}, {{ route_thumbs_json }}, {{ route_slide_indices_json }}); }
catch(e) { console.error('Map failed to load (check your internet connection):', e); }
{% endif %}
</script>
</body>
</html>
""")


# --------------------------------------------------------------------------
# Working without internet
#
# The pages use Leaflet (the map library), three web fonts and map tiles. By
# default all of that comes from the internet. To make the pages independent
# of it, run this once while you're online:
#
#     python trip_server.py --fetch-offline-assets
#
# That saves Leaflet and the fonts in a `vendor` folder next to these scripts,
# and from then on both scripts use those local copies. Map tiles can be
# cached too (trip_server.py --tile-cache DIR), and wherever no tile is
# available the maps show a rough outline of the continents instead of
# nothing, so the points on them still make sense.
# --------------------------------------------------------------------------

VENDOR_DIR = Path(__file__).resolve().parent / "vendor"
DEFAULT_TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"

_NPM_REGISTRY = "https://registry.npmjs.org"

# Exact versions, each with the SHA-512 that npm publishes for it, so what
# ends up in `vendor` is exactly what was reviewed: a download that doesn't
# match is refused.
_VENDOR_PACKAGES = {
    "leaflet": ("leaflet", "1.9.4",
                "sha512-nxS1ynzJOmOlHp+iL3FyWqK89GtNL8U8rvlMOsQdTTssxZwCXh8N2NB3GDQOL+YR3XnWyZAxwQixURb+FA74PA=="),
    "fraunces": ("@fontsource/fraunces", "5.3.0",
                 "sha512-LEHTOBiZufQzAvnAe2rZ8ZknbDS28Q9kzwb0L+gZJEmjLGr8hhZ/5NuHXwDuHBWhVXYoiGGXmrb0H6Ko/RrHTg=="),
    "ibm-plex-sans": ("@fontsource/ibm-plex-sans", "5.3.0",
                      "sha512-CbE4CbbEEZJX860XyUiRpsksXIQR8Rp2XDva2VO53NJox9tVNtusrysd2x5YkUEY3ErQ66W1IiiQL8/wihhw5w=="),
    "ibm-plex-mono": ("@fontsource/ibm-plex-mono", "5.3.0",
                      "sha512-eTgnZjZEGk1QtD3ZstF+Vclo2HLAni8YMy34/DxllwZvyz1lR/1RF/xTiAquOBO7MvqBx8D2Ig2WCPMVfdZu7Q=="),
}

# the weights the pages use (see the Google Fonts link they replace), and the
# two character sets that cover Western and Central European languages
_VENDOR_FONT_WEIGHTS = {"fraunces": (500, 600, 700), "ibm-plex-sans": (400, 500, 600),
                        "ibm-plex-mono": (400, 500)}
_VENDOR_FONT_SUBSETS = ("latin", "latin-ext")

_LEAFLET_FILES = {   # path inside the package -> path inside `vendor`
    "dist/leaflet.js": "leaflet/leaflet.js",
    "dist/leaflet.css": "leaflet/leaflet.css",
    "dist/images/layers.png": "leaflet/images/layers.png",
    "dist/images/layers-2x.png": "leaflet/images/layers-2x.png",
    "dist/images/marker-icon.png": "leaflet/images/marker-icon.png",
    "dist/images/marker-icon-2x.png": "leaflet/images/marker-icon-2x.png",
    "dist/images/marker-shadow.png": "leaflet/images/marker-shadow.png",
    "LICENSE": "licenses/leaflet-LICENSE.txt",
}


def vendor_ready(vendor_dir: Optional[Path] = None) -> bool:
    """True when a complete set of local Leaflet + font files is present.
    VERSIONS.txt is written last, so a download that was interrupted
    part-way never counts as ready."""
    d = Path(vendor_dir or VENDOR_DIR)
    return all((d / f).is_file() for f in
               ("leaflet/leaflet.js", "leaflet/leaflet.css", "fonts/fonts.css", "VERSIONS.txt"))


def _download_package(key: str, wanted: set) -> dict:
    """Download one pinned npm package, check it against its published
    checksum, and return {path inside the package: bytes} for the wanted files."""
    name, version, integrity = _VENDOR_PACKAGES[key]
    url = f"{_NPM_REGISTRY}/{name}/-/{name.split('/')[-1]}-{version}.tgz"
    req = urllib.request.Request(url, headers={"User-Agent": "TripSite-offline-assets/1.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = resp.read(60_000_000)
    digest = "sha512-" + base64.b64encode(hashlib.sha512(data).digest()).decode("ascii")
    if digest != integrity:
        raise RuntimeError(f"{name} {version}: the download doesn't match its expected "
                           f"checksum, so it was not used")
    found = {}
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
        for member in tf.getmembers():   # only ever read named files, never extract paths
            rel = member.name[len("package/"):] if member.name.startswith("package/") else None
            if rel in wanted and member.isfile() and member.size <= 5_000_000:
                found[rel] = tf.extractfile(member).read()
    missing = wanted - set(found)
    if missing:
        raise RuntimeError(f"{name} {version} doesn't contain {sorted(missing)[0]}")
    return found


def _font_faces(css_text: str, slug: str, weight: int) -> tuple:
    """The @font-face rules for one weight, trimmed to the wanted character
    sets and to woff2 files named plainly. Returns (css, [woff2 file names])."""
    wanted = {f"{slug}-{subset}-{weight}-normal" for subset in _VENDOR_FONT_SUBSETS}
    rules, files = [], []
    for tag, rule in re.findall(r"/\* ([a-z0-9-]+) \*/\s*(@font-face\s*\{.*?\n\})", css_text, re.S):
        if tag in wanted:
            rules.append(re.sub(r"src:[^;]+;", f"src: url({tag}.woff2) format('woff2');", rule))
            files.append(f"{tag}.woff2")
    if len(files) != len(wanted):
        raise RuntimeError(f"unexpected font stylesheet layout for {slug} {weight}")
    return "\n".join(rules), files


def atomic_write_bytes(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def fetch_offline_assets(vendor_dir: Optional[Path] = None) -> int:
    """Download Leaflet and the fonts into `vendor_dir` (default: `vendor`
    next to these scripts). Returns a process exit code."""
    vendor_dir = Path(vendor_dir or VENDOR_DIR)
    files = {}   # path inside vendor -> bytes
    try:
        print("Downloading Leaflet ...")
        package = _download_package("leaflet", set(_LEAFLET_FILES))
        for source, target in _LEAFLET_FILES.items():
            files[target] = package[source]

        css = ["/* Local copies of the fonts, saved by --fetch-offline-assets. */"]
        for slug, weights in _VENDOR_FONT_WEIGHTS.items():
            print(f"Downloading the {slug} font ...")
            wanted = {"LICENSE"}
            for weight in weights:
                wanted.add(f"{weight}.css")
                wanted |= {f"files/{slug}-{subset}-{weight}-normal.woff2" for subset in _VENDOR_FONT_SUBSETS}
            package = _download_package(slug, wanted)
            files[f"licenses/{slug}-LICENSE.txt"] = package["LICENSE"]
            for weight in weights:
                rules, names = _font_faces(package[f"{weight}.css"].decode("utf-8"), slug, weight)
                css.append(rules)
                for name in names:
                    files[f"fonts/{name}"] = package[f"files/{name}"]
        files["fonts/fonts.css"] = ("\n".join(css) + "\n").encode("utf-8")
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        print(f"Could not download the files ({e}).", file=sys.stderr)
        print("Check your internet connection and run the command again. "
              "Nothing was changed.", file=sys.stderr)
        return 1
    except RuntimeError as e:
        print(f"Stopped: {e}. Nothing was changed.", file=sys.stderr)
        return 1

    try:
        for target, data in files.items():
            atomic_write_bytes(vendor_dir / target, data)
        lines = [f"Saved by --fetch-offline-assets on {datetime.now():%Y-%m-%d %H:%M}.", ""]
        for name, version, _ in _VENDOR_PACKAGES.values():
            lines.append(f"{name} {version}  (checked against its published SHA-512)")
        lines += ["", "Licenses are in the licenses folder: Leaflet is BSD-2-Clause, the fonts are",
                  "SIL Open Font License 1.1."]
        atomic_write_bytes(vendor_dir / "VERSIONS.txt", ("\n".join(lines) + "\n").encode("utf-8"))
    except OSError as e:
        print(f"Could not write to {vendor_dir}: {e}", file=sys.stderr)
        return 1
    total = sum(len(d) for d in files.values())
    print(f"Saved {len(files)} files ({total / 1024 / 1024:.1f} MB) in {vendor_dir}")
    print("From now on the pages load Leaflet and the fonts from there, with no internet needed.")
    return 0


def add_offline_args(parser):
    parser.add_argument("--fetch-offline-assets", action="store_true",
                        help="Download Leaflet and the fonts into a 'vendor' folder next to these "
                             "scripts (needs internet, once), then exit. Afterwards the pages load "
                             "them from there instead of from the internet.")


class SiteUrls:
    """Where the pages link to. Each script subclasses this: generate_site
    links to files by relative path, trip_server to its own routes.
    `from_index` is True for links on the homepage rather than a day page,
    which matters for relative paths."""
    footer = ""     # the line at the bottom of every page
    edit_hint = ""  # completes "add one for <date> in trip_content.json..."
    tile_url = DEFAULT_TILE_URL   # what the maps ask for; {z}/{x}/{y} are filled in by Leaflet

    def vendor_url(self, from_index: bool) -> Optional[str]:
        """Folder (ending in /) holding local copies of Leaflet and the fonts,
        or None to use the copies on the internet."""
        return None

    def hi_thumb(self, item: MediaItem, from_index: bool) -> Optional[str]:
        """The larger thumbnail the page may swap in where a screen can use
        more pixels (L/XL sizes, high-resolution displays), or None when
        there is no larger version."""
        return None

    def home(self, from_index: bool) -> str:
        raise NotImplementedError

    def assets_prefix(self, from_index: bool) -> str:
        raise NotImplementedError

    def day(self, date_key: str, from_index: bool) -> str:
        raise NotImplementedError

    def thumb(self, item: MediaItem, from_index: bool) -> str:
        raise NotImplementedError

    def display(self, item: MediaItem) -> str:
        raise NotImplementedError

    def download(self, item: MediaItem) -> str:
        raise NotImplementedError

    def live(self, item: MediaItem) -> str:
        """URL of item.live_clip (only called when there is one)."""
        raise NotImplementedError


def _day_ranks(days: list) -> dict:
    """1-based rank among *real* days only, so "Day 3 of 5" always means
    "day 3 of 5 actual trip days": the unknown-date bucket (if any)
    doesn't consume a number and isn't counted in that denominator."""
    ranks = {}
    for d in days:
        if not d.is_unknown:
            ranks[d.date_key] = len(ranks) + 1
    return ranks


def _day_title(d, content: dict, ranks: dict) -> tuple:
    """(title, description) for a day, with the default title filled in."""
    title, description = day_text(content, d.date_key)
    if d.is_unknown and not description:
        description = ("Photos and videos with no usable capture date, or with one that looked "
                       "wrong (the Details panel of each says which). They are kept together "
                       "here rather than being given a day they weren't taken on.")
    return title or ("Unsorted" if d.is_unknown else f"Day {ranks[d.date_key]}"), description


def build_index_context(days: list, content: dict, urls: SiteUrls) -> dict:
    """Template variables for INDEX_TEMPLATE."""
    ranks = _day_ranks(days)
    summaries = []
    for d in days:
        title, desc = _day_title(d, content, ranks)
        rank = ranks.get(d.date_key)
        summaries.append({
            "href": urls.day(d.date_key, from_index=True),
            "date_display": "Date unknown" if d.is_unknown else format_long_date(d.date_obj),
            "title": title,
            "excerpt": (desc[:110] + "\u2026") if len(desc) > 110 else desc,
            "cover": urls.thumb(pick_cover(d, content), from_index=True) if d.items else None,
            "cover_hi": urls.hi_thumb(pick_cover(d, content), from_index=True) if d.items else None,
            "photo_count": d.photo_count,
            "video_count": d.video_count,
            "color": UNKNOWN_DAY_COLOR if d.is_unknown else DAY_COLORS[(rank - 1) % len(DAY_COLORS)],
            "route": d.route,
            "label": ("Unsorted" if d.is_unknown else f"Day {rank}") + f" \u2014 {d.date_key}",
            "is_unknown": d.is_unknown,
            "day_number": rank,
        })
    return dict(
        page_title=trip_title(content),
        home_href=urls.home(from_index=True),
        assets_prefix=urls.assets_prefix(from_index=True),
        vendor=urls.vendor_url(from_index=True),
        tile_url_json=script_json(urls.tile_url),
        footer=urls.footer,
        title=trip_title(content),
        subtitle=_text(content.get("subtitle")),
        day_count=len(ranks),
        photo_count=sum(d.photo_count for d in days),
        video_count=sum(d.video_count for d in days),
        distance_km=round(sum(d.distance_km for d in days), 1),
        days=summaries,
        days_json=script_json(summaries),
    )


def build_day_context(days: list, i: int, content: dict, urls: SiteUrls,
                      editable: bool = False) -> dict:
    """Template variables for DAY_TEMPLATE, for days[i]."""
    ranks = _day_ranks(days)
    d = days[i]
    title, desc = _day_title(d, content, ranks)

    items_ctx, slides, route_thumbs, route_slide_indices = [], [], [], []
    for idx, it in enumerate(d.items):
        pe = photo_entry(content, it.rel)
        item_ctx = {"thumb_url": urls.thumb(it, from_index=False), "media_type": it.media_type,
                    "live": it.live_clip is not None, "caption": pe["caption"],
                    "favorite": pe["favorite"], "hidden": pe["hidden"],
                    "hi_url": urls.hi_thumb(it, from_index=False)}
        if not d.is_unknown:
            # only show a time badge when it's a real capture time: a
            # file-modified fallback time isn't worth presenting as fact
            item_ctx["time_display"] = it.dt.strftime("%H:%M")
        items_ctx.append(item_ctx)

        slide = {
            "type": it.media_type,
            "display": urls.display(it),
            "time": None if d.is_unknown else it.dt.isoformat(),
            "lat": it.lat,
            "lon": it.lon,
            "detail": it.detail,
            "downloadUrl": urls.download(it),
        }
        slide["key"] = it.display_file or str(it.id)   # what #p=... in a link points at
        slide["caption"] = pe["caption"]
        slide["favorite"] = pe["favorite"]
        slide["hidden"] = pe["hidden"]
        if editable:
            slide["rel"] = it.rel   # what edits are saved against; visitors never get it
        if it.media_type == "video":
            slide["poster"] = urls.thumb(it, from_index=False)
        if it.live_clip is not None:
            slide["live"] = urls.live(it)
        slides.append(slide)
        if it.has_gps:
            route_thumbs.append(urls.thumb(it, from_index=False))
            # the day map's route only includes items with GPS, so its
            # index doesn't line up with the full slides list: this records
            # which slide each map point corresponds to, so clicking a
            # marker can open the right photo
            route_slide_indices.append(idx)

    prev_day = days[i - 1] if i > 0 else None
    next_day = days[i + 1] if i < len(days) - 1 else None
    prev_href = urls.day(prev_day.date_key, from_index=False) if prev_day else None
    next_href = urls.day(next_day.date_key, from_index=False) if next_day else None

    return dict(
        page_title=f"{title} \u2014 {trip_title(content)}",
        home_href=urls.home(from_index=False),
        home_href_json=script_json(urls.home(from_index=False)),
        assets_prefix=urls.assets_prefix(from_index=False),
        vendor=urls.vendor_url(from_index=False),
        tile_url_json=script_json(urls.tile_url),
        footer=urls.footer,
        edit_hint=urls.edit_hint,
        trip_title=trip_title(content),
        day_number=ranks.get(d.date_key),
        day_total=len(ranks),
        is_unknown=d.is_unknown,
        day_title=title,
        date_display="Date unknown" if d.is_unknown else format_long_date(d.date_obj),
        date_key=d.date_key,
        date_key_json=script_json(d.date_key),
        editable=editable,
        raw_title=day_text(content, d.date_key)[0],
        raw_description=day_text(content, d.date_key)[1],
        description=[p.strip() for p in desc.split("\n\n") if p.strip()],
        route=d.route,
        route_json=script_json(d.route),
        route_thumbs_json=script_json(route_thumbs),
        route_slide_indices_json=script_json(route_slide_indices),
        items=items_ctx,
        slides_json=script_json(slides),
        prev_href=prev_href,
        prev_href_json=script_json(prev_href),
        prev_label=prev_day.date_key if prev_day else "",
        next_href=next_href,
        next_href_json=script_json(next_href),
        next_label=next_day.date_key if next_day else "",
    )


# --------------------------------------------------------------------------
# Generated data: a rough outline of the continents, used by the maps wherever
# no tile is available (see "Working without internet" above).
# Source: Natural Earth 110m land (public domain), via the world-atlas npm
# package 2.0.2. Cut at the 180th meridian so a flat map never draws a band
# across the world, simplified to about 0.2 degrees, and stored as integer
# tenths of a degree: [lon, lat, lon, lat, ...] per ring, rings per polygon.
# --------------------------------------------------------------------------
_WORLD_LAND_JSON = "[[[-596,-800,-602,-810,-645,-809,-663,-803,-619,-804,-606,-796]],[[-1592,-795,-1611,-796,-1637,-786,-1631,-782,-1612,-784,-1595,-790]],[[-452,-780,-439,-785,-433,-800,-505,-810,-542,-806,-540,-802,-510,-796,-487,-780,-467,-778]],[[-1212,-735,-1187,-735,-1202,-741,-1226,-737,-1224,-733]],[[-1256,-735,-1240,-739,-1273,-735,-1266,-732]],[[-990,-719,-968,-720,-962,-725,-1008,-725,-1023,-719]],[[-685,-710,-683,-714,-688,-722,-711,-725,-724,-725,-719,-721,-742,-724,-750,-721,-750,-717,-732,-712,-721,-712,-717,-695,-712,-690,-703,-689]],[[-1800,-847,-1791,-841,-1773,-845,-1759,-841,-1744,-845,-1729,-841,-1700,-839,-1670,-846,-1581,-854,-1552,-851,-1485,-856,-1431,-850,-1429,-846,-1501,-843,-1509,-839,-1536,-837,-1527,-825,-1529,-820,-1568,-811,-1521,-810,-1506,-813,-1464,-803,-1468,-799,-1495,-794,-1553,-791,-1581,-780,-1584,-769,-1570,-773,-1537,-771,-1529,-775,-1513,-774,-1461,-765,-1465,-757,-1462,-754,-1449,-752,-1443,-755,-1352,-743,-1197,-745,-1175,-740,-1162,-742,-1139,-737,-1123,-747,-1113,-744,-1076,-752,-1049,-749,-1006,-753,-1001,-749,-1013,-742,-1025,-741,-1031,-737,-1037,-726,-991,-729,-977,-736,-963,-736,-924,-732,-901,-733,-892,-726,-884,-730,-860,-731,-852,-735,-815,-739,-803,-731,-762,-740,-689,-730,-680,-728,-671,-720,-685,-697,-676,-685,-674,-682,-677,-673,-673,-669,-630,-646,-578,-633,-572,-635,-590,-644,-606,-643,-620,-648,-626,-655,-621,-662,-637,-665,-655,-676,-657,-680,-648,-687,-632,-692,-618,-707,-607,-732,-608,-737,-620,-744,-633,-746,-644,-753,-698,-762,-706,-766,-772,-767,-769,-771,-737,-779,-779,-784,-780,-792,-754,-803,-597,-824,-582,-832,-498,-817,-428,-821,-408,-814,-286,-803,-297,-796,-297,-793,-356,-795,-359,-791,-358,-783,-289,-767,-225,-761,-175,-751,-157,-745,-154,-741,-165,-739,-154,-731,-123,-724,-103,-713,-74,-717,-69,-709,-43,-715,-7,-712,-2,-716,63,-705,77,-699,85,-701,95,-700,108,-708,134,-700,147,-700,151,-704,159,-700,193,-699,215,-701,226,-707,271,-705,320,-697,339,-685,362,-692,372,-692,386,-698,420,-686,465,-676,474,-677,490,-671,508,-669,509,-665,518,-662,545,-658,564,-660,587,-673,614,-680,624,-680,641,-674,689,-679,697,-692,696,-697,678,-703,679,-707,691,-707,679,-719,699,-723,710,-721,739,-699,776,-695,791,-683,828,-672,868,-671,875,-669,880,-662,888,-670,897,-671,942,-671,958,-674,997,-672,1028,-656,1062,-669,1102,-667,1136,-659,1156,-667,1198,-673,1232,-665,1288,-668,1348,-662,1351,-653,1357,-656,1366,-668,1375,-670,1455,-669,1462,-672,1460,-676,1466,-679,1488,-684,1525,-689,1536,-689,1543,-686,1568,-694,1592,-696,1616,-706,1673,-708,1712,-717,1693,-737,1661,-744,1642,-755,1636,-762,1635,-771,1647,-782,1666,-783,1670,-788,1618,-792,1598,-809,1637,-824,1689,-833,1694,-838,1723,-840,1732,-844,1760,-842,1783,-845]],[[-678,-538,-651,-547,-655,-552,-665,-552,-670,-549,-681,-556,-692,-555,-723,-545,-747,-528,-711,-541,-703,-529,-693,-525,-686,-526]],[[-585,-511,-577,-515,-580,-519,-594,-522,-598,-518,-607,-523,-612,-518,-600,-513,-592,-515]],[[703,-497,687,-498,689,-486,705,-491]],[[1454,-408,1464,-411,1483,-409,1484,-421,1479,-432,1476,-429,1469,-436,1460,-435,1447,-412,1447,-407]],[[1730,-409,1732,-413,1740,-409,1742,-413,1742,-418,1727,-434,1731,-439,1715,-442,1706,-459,1693,-466,1667,-462,1665,-459,1670,-451,1683,-441,1705,-430,1720,-415,1721,-410,1728,-405]],[[1746,-362,1753,-372,1754,-365,1758,-368,1760,-376,1768,-379,1785,-377,1780,-392,1772,-391,1770,-399,1760,-413,1752,-417,1747,-413,1752,-405,1749,-399,1738,-395,1739,-391,1746,-388,1747,-374,1726,-345,1730,-345,1743,-353]],[[1671,-222,1667,-224,1655,-217,1640,-201,1650,-205]],[[1784,-173,1787,-176,1786,-182,1774,-182,1773,-177,1777,-174]],[[1800,-166,1787,-170,1786,-166,1800,-161]],[[1671,-149,1673,-157,1668,-157,1666,-146]],[[501,-136,504,-157,502,-160,499,-154,497,-157,498,-169,471,-249,454,-256,440,-250,433,-221,444,-201,440,-174,444,-162,463,-158,477,-146,480,-141,479,-137,483,-138,492,-120]],[[1436,-138,1439,-145,1446,-142,1454,-150,1455,-163,1464,-190,1488,-204,1487,-206,1497,-223,1501,-221,1505,-226,1507,-224,1509,-235,1529,-253,1531,-261,1531,-273,1536,-281,1531,-309,1524,-326,1517,-330,1503,-357,1500,-374,1494,-378,1483,-378,1463,-390,1449,-384,1450,-379,1436,-388,1406,-380,1400,-374,1396,-361,1391,-357,1381,-356,1384,-351,1382,-344,1377,-351,1368,-353,1379,-336,1378,-329,1364,-341,1360,-349,1352,-345,1352,-339,1341,-328,1343,-326,1313,-315,1261,-322,1242,-330,1237,-339,1199,-340,1180,-351,1166,-350,1150,-342,1150,-336,1157,-333,1157,-316,1152,-306,1150,-295,1133,-261,1138,-265,1134,-256,1142,-263,1134,-244,1141,-218,1142,-225,1146,-218,1167,-207,1174,-207,1193,-200,1209,-197,1222,-182,1223,-173,1230,-164,1234,-173,1239,-171,1235,-166,1238,-161,1243,-163,1244,-156,1257,-142,1261,-143,1261,-141,1271,-138,1284,-149,1296,-150,1294,-144,1303,-134,1302,-131,1306,-125,1312,-122,1326,-121,1326,-116,1318,-113,1324,-111,1353,-122,1365,-119,1370,-124,1363,-133,1360,-133,1361,-137,1355,-150,1402,-177,1409,-174,1413,-164,1417,-150,1417,-124,1425,-107,1435,-128]],[[1207,-102,1190,-96,1199,-94,1208,-100]],[[1617,-96,1615,-98,1606,-83,1609,-83]],[[1244,-101,1235,-102,1240,-93,1251,-87,1273,-84,1251,-94]],[[1179,-81,1189,-83,1191,-87,1167,-90,1171,-85,1176,-84]],[[1229,-81,1228,-87,1199,-88,1199,-84,1207,-82,1213,-85]],[[1086,-68,1105,-69,1108,-65,1126,-69,1130,-76,1145,-78,1157,-84,1146,-88,1135,-83,1083,-78,1065,-74,1063,-69,1054,-69,1061,-59,1073,-60,1085,-64]],[[1347,-62,1342,-69,1341,-61,1345,-54]],[[1559,-68,1552,-65,1545,-51,1560,-65]],[[1520,-55,1502,-63,1483,-57,1484,-54,1498,-55,1501,-50,1502,-55,1508,-55,1516,-48,1515,-42,1523,-43]],[[1272,-35,1269,-38,1262,-36,1260,-32,1270,-31]],[[1305,-31,1308,-39,1300,-34,1279,-34,1281,-28]],[[1531,-45,1528,-48,1524,-38,1507,-27,1509,-25,1522,-32,1530,-40]],[[1341,-12,1344,-28,1355,-34,1363,-23,1374,-17,1383,-17,1446,-39,1458,-49,1460,-55,1476,-61,1479,-66,1470,-67,1472,-74,1481,-80,1487,-91,1493,-91,1493,-95,1508,-103,1507,-106,1479,-101,1466,-89,1460,-81,1447,-76,1433,-82,1434,-90,1426,-93,1410,-91,1401,-83,1391,-81,1389,-84,1376,-84,1380,-76,1387,-73,1379,-54,1352,-45,1337,-35,1330,-41,1328,-33,1320,-28,1338,-25,1337,-22,1322,-22,1318,-16,1309,-14,1305,-9,1324,-4,1340,-8]],[[1252,14,1244,4,1237,2,1202,2,1200,-5,1209,-14,1215,-10,1233,-6,1233,-11,1228,-9,1224,-15,1215,-19,1225,-32,1223,-35,1232,-47,1232,-53,1226,-56,1222,-53,1227,-45,1217,-49,1215,-46,1216,-42,1209,-36,1210,-26,1203,-29,1204,-55,1198,-57,1194,-54,1197,-45,1195,-35,1191,-35,1188,-28,1200,6,1209,13,1229,9,1241,9,1251,16]],[[1287,11,1286,3,1281,4,1280,-3,1284,-8,1281,-9,1274,10,1279,22,1280,16,1286,15]],[[1058,-59,1047,-59,1026,-42,993,2,986,18,954,50,953,55,975,52,1006,21,1017,21,1038,1,1034,-7,1044,-11,1049,-23,1056,-24,1061,-31]],[[1179,18,1190,9,1178,8,1175,1,1175,-8,1166,-15,1161,-40,1160,-37,1149,-41,1145,-35,1133,-31,1121,-35,1117,-30,1102,-29,1101,-16,1091,-5,1091,13,1097,20,1104,17,1112,19,1114,27,1130,31,1167,69,1171,69,1177,60,1192,54,1191,50,1184,50,1186,45,1179,41,1173,32,1180,23]],[[1264,84,1265,72,1262,63,1258,73,1254,68,1257,60,1254,56,1242,62,1239,69,1242,74,1236,78,1221,69,1219,72,1223,80,1235,87,1238,82,1246,85,1248,90,1255,90,1254,98,1262,93]],[[812,62,803,60,799,68,797,82,801,98,808,93,818,75,816,65]],[[-609,101,-620,101,-617,108,-611,109]],[[1240,103,1230,90,1224,97,1229,109,1235,109,1233,103,1241,112]],[[1185,93,1172,84,1195,114,1197,106]],[[1219,119,1231,116,1231,112,1220,104]],[[1255,122,1258,110,1250,113,1253,104,1248,101,1248,108,1245,109,1243,115,1249,114,1249,118,1243,126,1252,125]],[[1215,131,1213,122,1203,135,1212,134]],[[1213,185,1219,182,1222,185,1225,171,1223,163,1217,159,1217,143,1227,143,1240,138,1241,125,1229,136,1227,132,1220,138,1206,139,1210,145,1207,148,1206,144,1201,150,1199,164,1203,160,1207,185]],[[-656,182,-658,180,-672,179,-672,184,-663,185]],[[-769,179,-772,177,-783,182,-778,185,-769,184,-762,179]],[[-726,199,-700,196,-698,193,-692,193,-693,190,-683,186,-687,182,-700,184,-705,182,-707,184,-714,176,-717,180,-724,182,-739,180,-745,183,-744,187,-727,184,-723,187,-728,195,-734,196,-732,199]],[[1103,187,1095,182,1087,185,1086,194,1091,198,1108,201,1110,197]],[[-1555,191,-1559,191,-1559,203,-1548,195]],[[-797,228,-793,224,-783,225,-765,212,-756,210,-757,207,-742,203,-750,199,-778,199,-771,204,-781,207,-787,216,-822,224,-818,226,-828,227,-841,219,-850,219,-838,228,-823,232]],[[-775,238,-778,237,-784,246,-782,252,-779,252]],[[1212,228,1207,220,1201,236,1215,253,1220,250]],[[1346,341,1348,338,1342,332,1338,335,1333,333,1330,327,1324,330,1324,335,1329,341,1335,339,1339,344]],[[346,357,339,352,340,350,330,346,323,351]],[[237,357,242,354,263,353,262,350,247,349,235,353]],[[155,382,151,366,124,376,126,381]],[[92,412,98,405,97,392,88,389,84,392,82,410]],[[1410,371,1406,363,1408,358,1403,351,1390,347,1372,346,1358,335,1351,338,1351,346,1310,339,1320,331,1313,315,1307,310,1302,314,1304,323,1298,326,1294,333,1304,336,1326,354,1346,357,1357,355,1367,373,1374,368,1394,382,1401,394,1399,406,1403,412,1414,414,1419,400,1419,392,1410,382]],[[96,422,92,414,88,416,85,423,94,430]],[[1439,442,1446,440,1453,444,1455,433,1441,430,1432,420,1416,427,1411,416,1400,416,1398,426,1403,433,1414,434,1420,456]],[[-637,465,-620,464,-629,460,-644,467,-640,470]],[[-618,491,-636,494,-645,499,-629,497]],[[-1235,485,-1240,484,-1257,488,-1270,498,-1281,500,-1284,508,-1258,503]],[[-561,507,-568,498,-561,501,-555,499,-558,496,-535,492,-538,485,-531,487,-526,475,-531,467,-542,468,-540,476,-542,478,-554,469,-560,469,-553,474,-563,476,-593,476,-594,479,-588,483,-592,485,-584,491,-574,507,-567,513,-554,516]],[[-1327,540,-1317,541,-1321,530,-1312,522,-1316,522,-1331,534,-1332,542]],[[1436,507,1447,490,1432,493,1426,479,1435,468,1435,461,1427,467,1421,460,1419,489,1422,510,1416,519,1417,533,1426,538,1422,542,1427,544]],[[-68,523,-86,517,-100,518,-92,529,-97,539,-76,551,-67,552,-57,546,-62,539,-60,532]],[[127,556,121,548,110,554,109,558,124,561]],[[-1530,571,-1540,567,-1545,570,-1547,575,-1532,580,-1521,576]],[[-30,586,-41,576,-20,577,-22,569,-31,560,-21,559,-11,546,-4,545,5,529,17,527,16,521,10,518,14,513,5,508,-25,505,-30,507,-36,502,-45,503,-52,500,-58,502,-43,512,-34,514,-50,516,-53,520,-42,523,-48,528,-46,535,-31,534,-29,540,-36,546,-48,548,-51,551,-47,555,-50,558,-56,553,-56,563,-62,568,-58,578,-50,586]],[[-1656,599,-1662,598,-1675,602,-1657,603]],[[-793,622,-797,616,-804,620,-799,624]],[[-819,627,-831,622,-840,625,-832,629]],[[-1717,638,-1687,633,-1695,630,-1707,634,-1716,633]],[[-852,657,-850,652,-845,654,-816,645,-816,640,-808,641,-801,637,-810,634,-825,637,-831,641,-855,631,-859,636,-872,635,-864,640,-859,657]],[[-145,665,-147,658,-136,651,-149,644,-187,635,-228,640,-218,644,-240,649,-222,651,-222,654,-243,656,-237,663,-221,664,-206,657,-191,663,-178,660,-162,665]],[[-759,671,-770,671,-772,676,-768,681,-759,683,-751,680,-752,674]],[[1800,690,1800,650,1787,645,1774,646,1794,630,1792,623,1774,625,1737,617,1707,603,1703,599,1689,606,1663,598,1658,602,1649,597,1635,599,1632,592,1620,582,1621,578,1632,576,1631,562,1621,561,1617,553,1621,549,1604,543,1600,532,1585,530,1582,519,1568,510,1554,554,1559,568,1568,574,1568,578,1584,581,1619,603,1637,611,1645,626,1633,625,1627,616,1601,605,1593,618,1567,614,1542,598,1550,591,1513,588,1513,595,1498,597,1485,592,1455,593,1422,590,1351,547,1367,546,1372,540,1382,538,1388,543,1399,542,1413,531,1414,522,1406,512,1401,484,1382,463,1349,434,1335,428,1329,428,1323,433,1300,419,1297,416,1297,409,1275,398,1274,392,1284,386,1295,368,1295,356,1291,351,1265,344,1266,357,1261,367,1269,369,1262,377,1257,379,1253,377,1247,381,1252,387,1253,396,1243,399,1211,389,1216,394,1214,398,1222,404,1216,409,1196,399,1190,393,1180,392,1175,387,1181,381,1189,379,1189,374,1197,372,1208,379,1224,375,1225,369,1211,367,1192,349,1202,344,1219,317,1219,309,1213,307,1215,301,1221,298,1217,282,1211,281,1187,245,1159,228,1148,227,1142,222,1138,225,1132,221,1108,214,1104,203,1099,203,1096,210,1099,214,1085,217,1067,207,1059,198,1057,191,1074,167,1089,153,1093,134,1092,117,1052,86,1048,92,1051,99,1043,105,1035,106,1026,122,1017,126,1008,126,1010,134,1001,134,1000,123,992,100,992,92,999,92,1005,74,1030,55,1034,49,1035,28,1042,13,1035,12,1014,28,1002,53,1001,65,985,84,983,78,981,83,988,114,984,120,985,131,981,136,972,169,954,157,942,160,945,173,943,182,935,194,937,197,931,199,924,207,914,228,905,228,903,218,890,221,889,217,870,215,870,207,865,202,851,195,822,170,822,166,803,159,800,151,803,130,799,121,799,104,793,103,789,95,792,92,783,89,775,80,766,89,757,113,749,127,744,146,735,160,726,214,712,208,705,209,692,221,696,225,693,228,674,239,664,254,615,251,574,257,570,270,565,271,547,265,515,279,501,301,496,300,489,303,486,299,480,300,488,277,502,267,501,259,508,248,510,260,516,258,514,246,518,240,540,241,564,264,564,249,568,242,587,236,598,223,585,204,578,202,577,189,566,186,563,179,553,176,553,172,524,164,522,156,496,147,487,140,456,133,450,127,435,126,426,152,428,153,427,168,409,195,398,203,391,213,391,226,385,237,375,243,369,256,351,281,346,281,349,295,339,276,331,284,324,299,341,261,357,239,355,231,369,220,375,186,384,180,393,159,433,124,433,120,427,117,441,104,446,104,511,120,510,106,495,68,477,42,466,29,431,3,416,-17,403,-26,396,-43,392,-47,387,-59,388,-65,394,-68,392,-85,405,-108,408,-147,401,-161,395,-167,374,-176,348,-198,347,-205,356,-221,355,-241,326,-257,329,-262,325,-283,301,-311,282,-328,258,-339,226,-339,196,-348,184,-341,179,-326,182,-324,182,-317,152,-271,143,-221,118,-181,116,-167,125,-135,136,-120,137,-107,129,-92,132,-86,119,-50,88,-11,98,31,85,48,59,43,43,63,27,63,11,59,-20,47,-46,52,-80,44,-129,78,-132,89,-146,102,-148,109,-161,115,-166,122,-167,136,-176,147,-167,156,-163,172,-163,201,-171,210,-170,219,-163,227,-160,237,-151,245,-144,263,-138,266,-131,276,-126,280,-117,281,-96,299,-98,312,-93,326,-87,332,-69,341,-59,358,-52,358,-46,353,-22,352,15,366,53,367,63,371,84,369,95,374,102,372,102,367,110,371,111,369,106,364,106,359,109,357,108,348,102,343,103,338,109,338,115,331,152,323,157,314,191,303,201,310,198,318,201,322,215,328,229,326,232,322,249,319,252,316,265,316,289,309,301,315,310,316,317,314,320,309,322,313,338,310,346,315,360,346,361,358,358,363,362,367,347,368,340,362,325,361,317,366,306,367,304,363,297,361,287,367,276,367,263,382,268,390,262,395,273,404,288,405,292,412,311,411,335,420,352,420,383,409,404,410,416,415,415,426,367,452,374,454,382,462,377,466,391,470,391,473,350,463,350,457,365,455,363,451,339,444,333,446,335,450,325,453,336,459,333,461,317,463,317,467,307,466,296,450,288,449,286,437,277,426,281,416,290,413,288,411,276,410,264,402,261,408,249,409,237,407,244,401,239,400,233,400,228,405,226,403,234,392,230,390,240,382,240,377,231,379,234,374,228,373,232,364,225,364,217,368,211,383,194,403,195,417,189,423,160,435,152,442,154,443,149,451,143,452,140,448,137,451,139,456,131,457,123,454,123,446,126,441,135,436,140,428,151,420,159,420,162,417,159,415,185,402,183,398,177,403,169,404,165,398,172,394,171,389,166,388,161,380,157,379,161,390,154,400,136,412,121,417,105,429,102,439,89,444,65,431,46,434,31,431,30,419,21,412,8,410,-3,393,1,387,-5,383,-7,376,-14,374,-21,367,-44,367,-54,359,-59,360,-65,369,-89,369,-88,383,-95,387,-88,408,-90,426,-94,430,-80,437,-19,434,-14,440,-12,460,-30,476,-45,480,-46,487,-33,489,-16,486,-19,498,-10,493,13,501,16,509,38,516,47,531,71,537,81,535,88,540,81,555,81,565,85,571,106,577,103,569,109,565,96,555,99,546,109,544,109,540,125,545,141,538,176,549,197,544,199,549,213,552,211,568,216,574,225,578,233,570,241,570,244,584,241,583,234,586,233,592,259,596,280,595,291,600,281,605,229,598,213,607,215,617,211,626,215,632,254,651,253,655,239,660,222,657,212,650,214,644,178,627,171,613,188,601,179,590,168,587,164,570,159,561,147,562,141,554,129,554,110,589,104,595,84,583,71,581,57,586,50,620,59,626,86,635,105,645,148,678,192,698,214,703,230,702,245,710,282,712,313,705,300,702,311,696,321,699,338,693,365,691,411,675,411,668,384,660,339,668,332,666,348,659,349,644,370,639,371,643,365,648,372,651,396,645,404,648,398,655,421,665,440,661,445,668,437,674,442,680,435,686,463,682,468,677,456,676,456,670,463,667,479,669,481,675,537,689,545,688,535,682,547,681,554,684,573,685,588,689,599,683,611,689,600,695,606,699,635,695,685,681,692,686,681,694,669,695,673,699,667,710,685,719,692,728,699,730,726,728,728,722,718,714,725,711,728,704,726,690,737,684,732,677,713,663,724,662,739,668,742,673,751,678,745,683,749,690,738,691,736,696,744,706,731,714,749,721,747,728,752,729,757,723,753,713,764,712,759,719,776,723,797,723,815,717,806,726,805,736,868,739,860,745,872,751,929,758,932,760,967,759,989,764,1008,764,1020,773,1044,777,1061,774,1047,771,1070,770,1072,765,1111,767,1141,758,1139,753,1094,742,1121,738,1130,740,1135,733,1156,738,1188,736,1190,731,1232,730,1233,737,1270,736,1286,730,1291,724,1285,720,1297,712,1313,708,1323,718,1339,714,1356,717,1375,713,1382,716,1399,715,1391,724,1405,728,1495,722,1504,716,1530,708,1590,709,1598,705,1597,697,1609,694,1678,696,1696,687,1708,690,1700,697,1705,701,1757,699]],[[-1800,690,-1749,672,-1750,666,-1743,663,-1746,671,-1719,669,-1699,660,-1709,655,-1725,654,-1726,645,-1730,643,-1760,649,-1762,654,-1784,654,-1789,657,-1787,661,-1799,659,-1794,654,-1800,650]],[[-956,691,-963,688,-998,694,-982,701]],[[1800,715,1800,708,1789,708,1787,711]],[[-1800,715,-1776,713,-1800,708]],[[-905,695,-905,685,-892,693,-880,686,-883,679,-873,672,-856,688,-855,699,-826,697,-813,692,-812,687,-820,681,-813,676,-814,671,-833,664,-847,663,-858,666,-873,648,-885,641,-899,640,-907,636,-908,630,-919,628,-932,620,-942,609,-946,601,-947,589,-932,588,-923,571,-909,573,-850,553,-823,551,-824,543,-821,533,-814,522,-799,512,-791,515,-786,526,-791,541,-798,547,-782,551,-765,565,-766,572,-773,581,-785,588,-773,599,-781,623,-774,626,-747,622,-738,624,-714,611,-696,611,-693,590,-684,588,-677,582,-662,588,-646,603,-614,570,-618,563,-596,552,-573,546,-569,538,-558,533,-557,521,-571,514,-588,511,-600,502,-664,502,-672,495,-685,491,-711,468,-703,470,-687,483,-666,491,-651,492,-642,487,-651,481,-645,462,-632,457,-615,459,-605,470,-605,463,-598,459,-642,443,-654,435,-661,436,-662,445,-644,453,-671,451,-670,448,-701,437,-707,430,-708,423,-705,418,-701,418,-702,421,-699,419,-700,416,-737,409,-719,409,-740,408,-743,405,-740,404,-742,397,-749,389,-755,395,-751,384,-759,372,-757,379,-762,383,-764,391,-763,381,-770,382,-763,379,-757,356,-764,348,-791,335,-813,314,-813,300,-801,269,-804,252,-812,252,-817,259,-829,279,-827,286,-829,291,-841,301,-851,296,-864,304,-892,303,-896,302,-892,293,-902,291,-909,291,-916,297,-938,297,-966,283,-974,274,-971,259,-977,243,-979,224,-972,206,-963,193,-959,188,-948,186,-944,181,-920,187,-908,193,-903,210,-871,215,-868,208,-876,196,-874,195,-878,183,-883,185,-884,165,-889,159,-881,157,-850,160,-834,153,-832,143,-838,111,-822,90,-814,88,-796,96,-768,86,-757,94,-755,106,-749,111,-734,112,-718,124,-711,121,-714,115,-719,114,-716,104,-721,99,-717,91,-713,91,-710,99,-714,110,-702,114,-703,118,-699,122,-696,115,-689,114,-682,106,-662,106,-649,101,-643,106,-619,107,-627,104,-624,99,-616,99,-608,94,-607,86,-602,86,-591,80,-585,73,-585,68,-571,60,-540,58,-529,54,-517,42,-513,42,-505,19,-500,17,-499,10,-507,2,-504,-1,-486,-2,-486,-12,-478,-6,-449,-16,-444,-21,-446,-27,-434,-24,-415,-29,-400,-29,-372,-48,-356,-51,-352,-55,-347,-73,-351,-90,-387,-131,-393,-179,-398,-196,-408,-209,-409,-219,-418,-224,-420,-230,-446,-234,-476,-249,-485,-259,-489,-287,-496,-292,-507,-310,-523,-322,-538,-344,-549,-350,-562,-349,-578,-345,-584,-339,-585,-344,-572,-353,-574,-360,-567,-364,-568,-369,-577,-382,-592,-387,-623,-388,-621,-407,-627,-410,-638,-412,-647,-408,-651,-411,-650,-421,-643,-424,-638,-420,-635,-426,-652,-435,-656,-450,-665,-450,-673,-456,-676,-463,-666,-470,-656,-472,-660,-481,-672,-487,-678,-499,-691,-507,-688,-518,-682,-524,-695,-523,-708,-529,-710,-538,-726,-535,-749,-523,-756,-487,-752,-477,-741,-469,-756,-466,-747,-458,-744,-441,-732,-445,-727,-424,-734,-421,-737,-434,-743,-432,-737,-399,-732,-393,-736,-372,-732,-371,-714,-324,-717,-309,-714,-301,-715,-289,-709,-276,-701,-214,-702,-198,-704,-183,-714,-178,-715,-174,-760,-146,-764,-138,-763,-135,-798,-72,-813,-61,-809,-57,-814,-47,-798,-27,-800,-22,-804,-27,-810,-22,-809,-11,-806,-9,-801,8,-789,14,-790,17,-786,18,-784,26,-779,27,-771,38,-775,41,-775,67,-782,75,-784,81,-782,83,-791,90,-796,89,-805,81,-800,75,-809,72,-811,78,-815,77,-817,81,-828,83,-828,81,-835,84,-836,91,-850,101,-851,96,-857,99,-857,108,-859,109,-857,111,-877,129,-873,130,-875,133,-885,132,-912,139,-934,156,-947,162,-966,157,-1008,172,-1019,179,-1035,183,-1055,199,-1057,204,-1054,205,-1053,214,-1060,228,-1084,252,-1093,256,-1093,264,-1104,272,-1106,279,-1112,279,-1122,290,-1131,312,-1148,318,-1147,302,-1116,267,-1107,243,-1102,243,-1094,232,-1100,228,-1103,234,-1122,247,-1123,260,-1151,277,-1146,277,-1142,286,-1155,296,-1173,330,-1185,340,-1206,346,-1207,352,-1217,362,-1225,378,-1237,390,-1239,398,-1244,403,-1242,420,-1245,428,-1239,455,-1241,469,-1247,482,-1246,484,-1231,480,-1226,471,-1223,474,-1228,490,-1256,504,-1274,508,-1280,517,-1278,523,-1291,528,-1293,536,-1305,543,-1305,548,-1320,555,-1322,564,-1335,572,-1341,581,-1366,582,-1399,595,-1426,601,-1440,600,-1471,609,-1482,607,-1480,600,-1517,592,-1519,597,-1514,607,-1503,610,-1506,613,-1540,594,-1533,589,-1542,581,-1563,574,-1566,570,-1581,565,-1584,560,-1648,544,-1649,546,-1618,559,-1606,560,-1577,576,-1570,589,-1582,586,-1585,588,-1591,584,-1597,589,-1600,586,-1604,591,-1620,587,-1619,596,-1625,600,-1638,598,-1653,605,-1654,611,-1661,615,-1646,631,-1631,631,-1623,635,-1608,638,-1610,642,-1615,644,-1608,648,-1628,643,-1650,644,-1664,647,-1681,657,-1645,666,-1637,666,-1638,661,-1617,661,-1654,680,-1668,684,-1662,689,-1644,689,-1632,694,-1629,699,-1619,703,-1581,708,-1566,714,-1551,711,-1543,707,-1522,708,-1523,706,-1507,704,-1449,700,-1436,702,-1365,689,-1344,696,-1329,695,-1298,702,-1291,698,-1284,700,-1281,705,-1258,695,-1244,702,-1243,694,-1227,699,-1215,698,-1176,690,-1152,689,-1139,684,-1153,679,-1135,677,-1099,680,-1089,674,-1078,679,-1088,683,-1082,687,-1061,688,-1043,680,-1032,681,-1015,676,-984,678,-986,684,-977,686,-961,682,-961,673,-955,681,-947,681,-942,691,-965,701,-964,712,-952,719,-929,713,-915,702,-924,697]],[[-1142,731,-1147,727,-1124,730,-1111,725,-1099,730,-1090,726,-1082,717,-1077,721,-1084,731,-1065,731,-1054,727,-1045,710,-1010,700,-1011,696,-1027,695,-1021,691,-1024,688,-1060,692,-1133,685,-1139,690,-1161,692,-1173,700,-1124,704,-1179,705,-1184,709,-1161,713,-1194,716,-1179,727,-1152,733]],[[-1045,734,-1054,728,-1069,735,-1053,736]],[[-763,731,-763,728,-795,727,-809,733,-808,737,-804,738,-781,737]],[[-866,732,-858,725,-849,733,-823,738,-806,727,-808,721,-778,728,-742,718,-741,713,-722,716,-712,709,-688,705,-670,692,-688,687,-649,678,-634,669,-619,669,-622,662,-639,650,-667,664,-680,663,-681,657,-653,644,-647,634,-650,627,-688,637,-662,619,-710,629,-722,634,-719,637,-748,647,-748,644,-777,642,-786,646,-779,653,-740,655,-743,658,-727,673,-729,677,-748,686,-769,689,-762,691,-773,698,-790,702,-813,697,-849,700,-887,704,-895,708,-885,712,-899,712,-902,722,-894,731,-884,735,-858,738]],[[-1004,738,-974,738,-971,735,-981,730,-965,726,-967,717,-993,714,-1025,725,-1025,728,-1004,727,-1015,734]],[[1436,732,1399,734,1421,739]],[[-932,728,-943,720,-954,721,-960,729,-960,734,-945,741,-905,739,-920,730]],[[-1205,714,-1231,709,-1236,713,-1259,719,-1239,737,-1249,743,-1215,744,-1176,742,-1155,735,-1192,725,-1205,718]],[[1507,751,1496,747,1461,752,1464,755]],[[-936,750,-942,746,-968,749,-963,754,-949,756]],[[1451,756,1443,748,1390,746,1370,753,1375,759,1388,761]],[[-985,767,-977,763,-982,750,-998,749,-1009,751,-1009,756,-1025,756,-1026,763]],[[-1082,762,-1078,758,-1059,760,-1057,755,-1063,750,-1122,744,-1137,744,-1139,747,-1118,752,-1177,752,-1163,762,-1154,765,-1091,755,-1105,764,-1096,768,-1086,767]],[[575,707,537,708,534,712,516,715,515,720,525,722,524,728,544,736,535,738,559,746,556,751,612,763,662,768,682,769,689,765,616,753,585,743,554,724,556,715]],[[-947,771,-936,768,-916,768,-907,765,-910,761,-892,756,-811,757,-801,753,-798,749,-819,744,-898,745,-924,748,-929,759,-939,763,-971,768,-967,772]],[[-1162,776,-1163,769,-1171,765,-1215,759,-1229,761,-1191,775]],[[-938,775,-962,776,-964,778,-944,778]],[[-1102,777,-1121,774,-1135,777,-1127,781,-1099,780]],[[247,779,225,774,207,777,214,779,208,783,229,785,233,781]],[[-1097,786,-1125,784,-1115,789]],[[-958,781,-973,778,-981,781,-986,789,-956,784]],[[-1001,783,-997,779,-1052,784,-1042,787,-1054,789,-1055,793,-1008,788]],[[1051,783,994,779,1013,792,1021,793,1054,787]],[[183,797,215,790,190,786,185,778,176,776,171,768,159,768,138,774,147,777,112,789,104,797,132,800,137,797,151,797,155,800,170,801]],[[254,804,274,801,259,795,230,794,201,796,199,798,185,799,174,803,205,806,219,804,229,807]],[[511,805,476,800,465,802,471,806,448,806,483,808,485,805,500,809,515,807]],[[999,789,950,790,933,794,925,801,912,803,959,813,1002,798]],[[-870,797,-858,793,-890,783,-908,782,-929,783,-940,788,-939,791,-931,794,-950,794,-967,802,-953,809,-943,810,-947,812,-924,813,-911,807,-878,803]],[[-685,831,-619,826,-619,824,-677,815,-655,815,-695,806,-712,798,-769,793,-755,792,-762,790,-754,785,-798,772,-796,770,-779,770,-779,768,-806,762,-895,765,-896,770,-878,772,-883,779,-850,775,-863,782,-880,784,-854,790,-851,793,-865,797,-869,803,-834,801,-818,805,-876,805,-894,809,-914,816,-916,819,-855,827,-832,823,-824,829,-793,831]],[[-271,835,-208,827,-227,823,-319,822,-248,818,-229,821,-221,817,-232,812,-158,819,-128,817,-122,813,-168,803,-200,802,-177,801,-197,788,-197,776,-185,770,-217,766,-198,761,-196,752,-207,752,-194,743,-216,742,-204,738,-208,735,-236,733,-223,726,-223,722,-243,726,-248,723,-221,715,-218,707,-235,705,-255,714,-252,708,-264,702,-223,701,-277,685,-318,681,-342,667,-364,660,-398,655,-407,648,-407,641,-412,635,-428,627,-424,619,-434,601,-448,600,-463,609,-483,609,-516,636,-521,643,-523,652,-537,661,-533,668,-540,672,-530,684,-515,687,-509,699,-535,693,-547,696,-548,703,-544,708,-514,706,-540,715,-558,717,-547,726,-573,747,-586,751,-586,755,-613,761,-685,761,-714,770,-668,774,-733,780,-732,784,-657,794,-653,798,-680,801,-671,805,-622,813,-626,818,-572,822,-541,822,-530,819,-504,824,-445,817,-469,822,-468,826,-434,832,-399,832,-386,835]]]"
JS = JS.replace("__WORLD_LAND__", _WORLD_LAND_JSON)
