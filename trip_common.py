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
import filecmp
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
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
    if(isTyping(e.target)) return;                // typing in the title/caption boxes
    if(inMap(document.activeElement)) return;     // let Leaflet's own keyboard map-panning work
    if(e.key === 'ArrowLeft' && prevHref) window.location.href = prevHref;
    else if(e.key === 'ArrowRight' && nextHref) window.location.href = nextHref;
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
let EDITABLE = false;     // true only when trip_server.py says this viewer may edit
let DAY_KEY = '';
let FAV_ONLY = false;
let lastFocus = null;

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
  lastFocus = document.activeElement;
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
  if(lastFocus && lastFocus.focus) lastFocus.focus();
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

  renderCaption();
  if(infoPanelOpen) renderInfoPanel();
  syncEditBar();

  const liveBtn = document.getElementById('slideshow-live');
  if(liveBtn) liveBtn.hidden = !item.live;

  preloadSlide((currentSlide + 1) % SLIDES.length);
  preloadSlide((currentSlide - 1 + SLIDES.length) % SLIDES.length);
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
        openSlideshow(parseInt(el.dataset.slideIndex, 10));
      }
    });
  });
  initFavFilter();
  if(EDITABLE) initEditBar();
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
    if(isTyping(e.target)){ if(e.key === 'Escape') e.target.blur(); return; }
    if(e.key === 'ArrowRight') nextSlide();
    else if(e.key === 'ArrowLeft') prevSlide();
    else if(e.key === 'Escape'){ infoPanelOpen ? closeInfoPanel() : closeSlideshow(); }
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

<footer class="site-footer">{{ footer }}</footer>

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
  {% if prev_href or next_href %}<div class="day-nav-hint">&larr; &rarr; keys or swipe also work</div>{% endif %}
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
  <div class="size-toggle" role="group" aria-label="Thumbnail size">
    <button type="button" class="size-btn" data-size="small">S</button>
    <button type="button" class="size-btn" data-size="medium">M</button>
    <button type="button" class="size-btn" data-size="large">L</button>
  </div>
</div>
{% endif %}

<div class="grid" id="thumb-grid">
  {% for item in items %}
  <div class="thumb{{ ' is-fav' if item.favorite else '' }}{{ ' is-hidden' if item.hidden else '' }}" data-slide-index="{{ loop.index0 }}" role="button" tabindex="0" aria-label="{{ item.caption or ('Open video' if item.media_type == 'video' else 'Open photo') }}">
    <img src="{{ item.thumb_url }}" loading="lazy" alt="{{ item.caption }}">
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
<script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.js"></script>
<script src="{{ assets_prefix }}assets/script.js"></script>
<script>
// slideshow first: it must keep working even if the map/tiles can't load (no internet, blocked CDN, etc.)
initSlideshow({{ slides_json }}, {editable: {{ 'true' if editable else 'false' }}, dayKey: {{ date_key_json }}});
initThumbSizeControl();
initDayNav({{ prev_href_json }}, {{ next_href_json }});
{% if editable %}initDayEditor({{ date_key_json }});{% endif %}
{% if route %}
try { initDayMap('day-map', {{ route_json }}, {{ route_thumbs_json }}, {{ route_slide_indices_json }}); }
catch(e) { console.error('Map failed to load (check your internet connection):', e); }
{% endif %}
</script>
</body>
</html>
""")


class SiteUrls:
    """Where the pages link to. Each script subclasses this: generate_site
    links to files by relative path, trip_server to its own routes.
    `from_index` is True for links on the homepage rather than a day page,
    which matters for relative paths."""
    footer = ""     # the line at the bottom of every page
    edit_hint = ""  # completes "add one for <date> in trip_content.json..."

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
                    "favorite": pe["favorite"], "hidden": pe["hidden"]}
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
        assets_prefix=urls.assets_prefix(from_index=False),
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
