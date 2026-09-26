#!/usr/bin/env python3
"""
trip_server.py — serve your trip photos/videos as a live website, generated
on the fly. No thumbnails, no converted copies, no HTML files, no output
folder of any kind — everything is computed in memory, per request, and
thrown away afterwards. Put this script in (or point it at) a folder of
photos/videos and run it; it starts a local web server with the same
day-by-day site, maps, and full-screen slideshow as generate_site.py, just
served live instead of pre-built.

    python trip_server.py -i /path/to/photos

Then open the URL it prints (defaults to http://127.0.0.1:8000/).

This is a sister script to generate_site.py, not a replacement for it — pick
whichever fits how you want to view your trip:
  - generate_site.py: builds a folder of static files you can open without
    Python running, copy anywhere, or put on a USB drive.
  - trip_server.py (this one): nothing is written to disk at all; you just
    run it whenever you want to browse, from right inside the photos folder.

See README.md for the full walkthrough.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import mimetypes
import re
import socket
import subprocess
import sys
import threading
import traceback
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse, unquote

from PIL import Image, ImageOps
from PIL.ExifTags import GPSTAGS
from jinja2 import Template

try:
    import pillow_heif
    pillow_heif.register_heif_opener()
    HEIF_SUPPORT = True
except ImportError:
    HEIF_SUPPORT = False


IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".tif", ".tiff", ".bmp", ".webp"}
VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".3gp"}
BROWSER_NATIVE_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
DAY_COLORS = ["#E3A857", "#4FA5A0", "#C97B84", "#7C9473", "#8C7BC9", "#D9B24C"]
UNKNOWN_DAY_KEY = "unknown"
UNKNOWN_DAY_COLOR = "#6B7280"

# mimetypes' built-in table is inconsistent across OS/Python installs for a
# few formats that matter a lot here (HEIC especially) — pin the ones we
# actually care about rather than trusting whatever's locally registered.
MIME_OVERRIDES = {
    ".heic": "image/heic", ".heif": "image/heif",
    ".tif": "image/tiff", ".tiff": "image/tiff",
    ".mov": "video/quicktime", ".mp4": "video/mp4", ".m4v": "video/mp4",
    ".avi": "video/x-msvideo", ".mkv": "video/x-matroska", ".3gp": "video/3gpp",
}


def guess_mime(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in MIME_OVERRIDES:
        return MIME_OVERRIDES[ext]
    guessed, _ = mimetypes.guess_type(str(path))
    return guessed or "application/octet-stream"


# --------------------------------------------------------------------------
# Data model
# --------------------------------------------------------------------------

@dataclass
class MediaItem:
    id: int                    # stable per-run identity used in URLs, e.g. /thumb/42
    path: Path                 # the actual original file — never copied, never renamed
    media_type: str            # 'image' | 'video'
    dt: datetime
    dt_is_fallback: bool
    lat: Optional[float]
    lon: Optional[float]
    width: Optional[int] = None
    height: Optional[int] = None
    detail: dict = field(default_factory=dict)
    is_heic: bool = False
    needs_jpeg_fallback: bool = False  # HEIC/HEIF, TIFF, or a multi-stream/MPO .jpg

    @property
    def has_gps(self) -> bool:
        return self.lat is not None and self.lon is not None


@dataclass
class DayGroup:
    date_key: str
    date_obj: Optional[datetime]
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
        return _route_distance_km(self.route)


# --------------------------------------------------------------------------
# EXIF / metadata extraction — same logic as generate_site.py
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


def format_long_date(dt) -> str:
    return f"{dt.strftime('%A, %B')} {dt.day}, {dt.year}"


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


def read_image_metadata(path: Path) -> dict:
    meta = {}
    try:
        with Image.open(path) as img:
            meta["width"], meta["height"] = img.size
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

                camera = format_camera(exif.get(271), exif.get(272))
                if camera:
                    meta["camera"] = camera
                lens = clean_str(exif_ifd.get(42036))
                if lens:
                    meta["lens"] = lens
                if 33437 in exif_ifd:
                    ap = format_fnumber(exif_ifd[33437])
                    if ap:
                        meta["aperture"] = ap
                if 33434 in exif_ifd:
                    sh = format_exposure(exif_ifd[33434])
                    if sh:
                        meta["shutter"] = sh
                if 34855 in exif_ifd:
                    iso = format_iso(exif_ifd[34855])
                    if iso:
                        meta["iso"] = iso
                if 37386 in exif_ifd:
                    fl = format_focal(exif_ifd[37386], exif_ifd.get(41989))
                    if fl:
                        meta["focal_length"] = fl
    except Exception as e:
        print(f"  ! could not read EXIF from {path.name}: {e}", file=sys.stderr)
    return meta


def read_video_metadata(path: Path, tz_offset_hours: Optional[float] = None) -> dict:
    meta = {}
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_format", "-show_streams", str(path)],
            capture_output=True, text=True, timeout=30,
        )
        data = json.loads(result.stdout)
    except Exception as e:
        print(f"  ! ffprobe failed on {path.name}: {e}", file=sys.stderr)
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

    return meta


def fast_fingerprint(path: Path, sample_size: int = 65536) -> Optional[tuple]:
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


def is_multi_stream_jpeg(path: Path) -> bool:
    """Detects MPO-style files: a genuine second complete JPEG image
    directly concatenated after the first, with no gap — the actual
    on-disk structure of real MPO files (dual-camera capture, portrait/
    depth mode, panorama, some HDR modes), as opposed to the ordinary
    embedded EXIF preview thumbnail that's already tucked inside almost
    every camera JPEG's own header. Looking for a JPEG end-of-image marker
    immediately followed by another start-of-image marker, with nothing in
    between, is specific enough that it doesn't false-positive on an EXIF
    thumbnail or on ordinary compressed image/metadata bytes — a looser
    "is there a second start-of-image marker anywhere later in the file"
    check turned out to trigger even on completely ordinary photos, purely
    by coincidence, once you're scanning several megabytes of binary data.
    Reads in bounded chunks rather than loading the whole file into memory,
    since photos can be 50MB+."""
    chunk_size = 2 * 1024 * 1024  # 2MB
    pattern = b"\xff\xd9\xff\xd8"
    try:
        with open(path, "rb") as f:
            overlap = b""
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    return False
                window = overlap + chunk
                if pattern in window:
                    return True
                overlap = window[-(len(pattern) - 1):]
    except Exception:
        return False


def group_by_day(items: list) -> list:
    days = {}
    for it in items:
        key = UNKNOWN_DAY_KEY if it.dt_is_fallback else it.dt.strftime("%Y-%m-%d")
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
# Index building — metadata only. No thumbnails, no derivative files, no
# disk writes of any kind; this just figures out what's in the folder and
# when/where it was taken, and keeps it in memory for the life of the server.
# --------------------------------------------------------------------------

def build_index(input_dir: Path, tz_offset: Optional[float], dedupe: bool) -> list:
    candidates = []
    for path in sorted(p for p in input_dir.rglob("*") if p.is_file()):
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
                print(f"  ~ skipping {path.name}: exact duplicate of {original.name}", file=sys.stderr)
                skipped += 1
                continue
            if fp is not None:
                seen[fp] = path
            deduped.append((path, media_type))
        if skipped:
            print(f"Skipped {skipped} duplicate file(s) (identical content to a file already found).")
        candidates = deduped

    total = len(candidates)
    items = []
    for n, (path, media_type) in enumerate(candidates, 1):
        print(f"[{n}/{total}] indexing {media_type:5s} {path.name}")
        try:
            if media_type == "image":
                meta = read_image_metadata(path)
            else:
                meta = read_video_metadata(path, tz_offset)

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

            items.append(MediaItem(
                id=len(items),
                path=path,
                media_type=media_type,
                dt=dt,
                dt_is_fallback=dt_is_fallback,
                lat=gps[0] if gps else None,
                lon=gps[1] if gps else None,
                width=w, height=h,
                detail=detail,
                is_heic=(media_type == "image" and path.suffix.lower() in (".heic", ".heif")),
                needs_jpeg_fallback=(
                    media_type == "image" and (
                        path.suffix.lower() in (".heic", ".heif", ".tif", ".tiff")
                        or (path.suffix.lower() in (".jpg", ".jpeg") and is_multi_stream_jpeg(path))
                    )
                ),
            ))
        except Exception as e:
            print(f"  ! failed to index {path.name}: {e}", file=sys.stderr)
            continue

    return items


# --------------------------------------------------------------------------
# On-the-fly rendering of images. Nothing here ever touches disk — thumbnails
# and HEIC/TIFF fallback JPEGs are built straight into an in-memory buffer
# and handed to the HTTP response, then discarded (or kept briefly in the
# small in-memory cache below so repeat requests in the same session don't
# redo the work).
# --------------------------------------------------------------------------

def render_thumbnail_bytes(item: MediaItem, thumb_size: int) -> Optional[bytes]:
    try:
        if item.media_type == "video":
            for seek in ("00:00:01", "00:00:00"):
                try:
                    result = subprocess.run(
                        ["ffmpeg", "-v", "quiet", "-ss", seek, "-i", str(item.path),
                         "-frames:v", "1", "-vf", f"scale={thumb_size}:-1",
                         "-f", "image2pipe", "-vcodec", "mjpeg", "pipe:1"],
                        capture_output=True, timeout=30,
                    )
                    if result.returncode == 0 and result.stdout:
                        return result.stdout
                except Exception:
                    continue
            return None
        with Image.open(item.path) as img:
            img = ImageOps.exif_transpose(img)
            if img.mode != "RGB":
                img = img.convert("RGB")
            img.thumbnail((thumb_size, thumb_size), Image.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, "JPEG", quality=82, optimize=True)
            return buf.getvalue()
    except Exception as e:
        print(f"  ! thumbnail failed for {item.path.name}: {e}", file=sys.stderr)
        return None


def render_fallback_jpeg_bytes(path: Path) -> Optional[bytes]:
    """HEIC/HEIF and TIFF -> full-resolution near-lossless JPEG, EXIF
    preserved (orientation corrected since exif_transpose already bakes the
    rotation into the pixels — see generate_site.py for the same logic and
    the reasoning behind it). Used only for browsers that can't decode the
    original format inline; browsers that can (Safari, for HEIC) are served
    the real original directly via /original/<id> instead."""
    try:
        with Image.open(path) as img:
            exif = img.getexif()
            img = ImageOps.exif_transpose(img)
            if img.mode != "RGB":
                img = img.convert("RGB")
            exif_bytes = None
            try:
                exif[0x0112] = 1
                exif_bytes = exif.tobytes()
            except Exception:
                pass
            buf = io.BytesIO()
            save_kwargs = {"quality": 97, "optimize": True}
            if exif_bytes:
                save_kwargs["exif"] = exif_bytes
            img.save(buf, "JPEG", **save_kwargs)
            return buf.getvalue()
    except Exception as e:
        print(f"  ! on-the-fly conversion failed for {path.name}: {e}", file=sys.stderr)
        return None


class LRUCache:
    """A small in-memory (never on disk) bounded cache so scrolling back to
    a day you already visited doesn't redo image work, without unbounded
    memory growth on a long-running server."""

    def __init__(self, maxsize: int):
        self.maxsize = maxsize
        self._data = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key):
        with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
                return self._data[key]
        return None

    def set(self, key, value):
        with self._lock:
            self._data[key] = value
            self._data.move_to_end(key)
            if len(self._data) > self.maxsize:
                self._data.popitem(last=False)


THUMB_CACHE = LRUCache(maxsize=1000)
FALLBACK_CACHE = LRUCache(maxsize=80)   # full-res, so kept smaller


# --------------------------------------------------------------------------
# trip_content.json — same additive semantics as generate_site.py (titles/
# descriptions are only ever added to, never overwritten), but hot-reloaded:
# since the server keeps running, edits made in a text editor show up on the
# next page refresh with no restart needed.
# --------------------------------------------------------------------------

class ContentStore:
    def __init__(self, path: Path, day_keys: list):
        self.path = path
        self._lock = threading.Lock()
        self._mtime = None
        self._cache = None
        self._ensure_keys(day_keys)

    def _read(self) -> dict:
        content = {"title": "My Trip", "subtitle": "", "days": {}}
        if self.path.exists():
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
                content.update({k: v for k, v in loaded.items() if k != "days"})
                content["days"] = loaded.get("days", {})
            except Exception as e:
                print(f"! could not parse {self.path}, using defaults ({e})", file=sys.stderr)
        return content

    def _write(self, content: dict):
        self.path.write_text(json.dumps(content, indent=2, ensure_ascii=False), encoding="utf-8")

    def _mtime_now(self):
        try:
            return self.path.stat().st_mtime
        except OSError:
            return None

    def _ensure_keys(self, day_keys: list):
        content = self._read()
        changed = not self.path.exists()
        for key in day_keys:
            if key not in content["days"]:
                content["days"][key] = {"title": "", "description": ""}
                changed = True
        if changed:
            self._write(content)
            print(f"-> wrote {self.path} (fill in titles/descriptions any time — "
                  f"just refresh the page after saving, no restart needed)")
        self._cache = content
        self._mtime = self._mtime_now()

    def get(self) -> dict:
        with self._lock:
            mtime = self._mtime_now()
            if mtime != self._mtime:
                self._cache = self._read()
                self._mtime = mtime
            return self._cache


# --------------------------------------------------------------------------
# Templates — CSS/JS are carried over essentially unchanged from
# generate_site.py (none of it cares whether a URL points at a static file
# or a live endpoint), plus one small addition: a "download original" link
# in the details panel, natural to add now that every file can be streamed
# directly on request without needing a pre-made copy.
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
.info-download{display:block; margin-top:1rem; font-family:var(--mono); font-size:.78rem; text-align:center;
  padding:.6rem; border:1px solid rgba(31,42,61,.25); border-radius:8px; color:var(--ink);}
.info-download:hover{background:rgba(31,42,61,.06); text-decoration:none;}
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
        marker.bindPopup(
          `<img src="${thumbs[i]}" class="map-popup-thumb">` +
          `<div class="map-popup-hint">Click photo to view full-size</div>`
        );
        marker.on('popupopen', () => {
          const img = document.querySelector('.leaflet-popup-content .map-popup-thumb');
          if(img) img.addEventListener('click', () => openSlideshow(slideIndices[i]));
        });
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
  if(item.type === 'video'){
    // muted is required for autoplay to actually work under browser
    // autoplay policies — without it, browsers silently block autoplay
    // and the video just sits paused on a blank frame. poster gives it a
    // real starting frame either way; native controls let people unmute.
    const poster = item.poster ? ` poster="${item.poster}"` : '';
    return `<video src="${item.display}" controls autoplay muted playsinline${poster}></video>`;
  }
  // always the converted JPEG here (item.display), even for HEIC — an
  // earlier version offered the real original to any browser that claimed
  // it could decode HEIC natively via <picture>, but some platforms' HEIC
  // decoders (notably some Windows OS-level codec packs) handle orientation
  // inconsistently, which could render worse than the verified-correct
  // converted copy. The true original is still available via the download
  // link, just not used for inline display anymore.
  return `<img src="${item.display}" alt="">`;
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

  img.style.transform = `scale(${ZOOM.scale})`;
  const imgRect = img.getBoundingClientRect();
  const stageRect = stage.getBoundingClientRect();
  const maxX = Math.max(0, (imgRect.width - stageRect.width) / 2);
  const maxY = Math.max(0, (imgRect.height - stageRect.height) / 2);
  ZOOM.x = Math.max(-maxX, Math.min(maxX, ZOOM.x));
  ZOOM.y = Math.max(-maxY, Math.min(maxY, ZOOM.y));
  img.style.transform = `translate(${ZOOM.x}px, ${ZOOM.y}px) scale(${ZOOM.scale})`;
  img.classList.toggle('zoomed', ZOOM.scale > 1.01);

  document.querySelectorAll('.slideshow-nav').forEach(el => {
    el.style.pointerEvents = ZOOM.scale > 1.01 ? 'none' : '';
  });
}

function zoomBy(delta){
  if(!document.querySelector('#slideshow-media img')) return;
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
  mediaEl.innerHTML = buildSlideStage(item);

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

// ---------- info panel ----------
function buildInfoRows(detail){
  return INFO_ORDER
    .filter(key => detail[key])
    .map(key => `<div class="info-row"><span class="k">${INFO_LABELS[key]}</span><span class="v">${detail[key]}</span></div>`)
    .join('');
}

function renderInfoPanel(){
  const item = SLIDES[currentSlide];
  const detail = item.detail || {};
  document.getElementById('info-title').textContent = item.time ? fmtTime(item.time) : 'Details';
  document.getElementById('info-rows').innerHTML = buildInfoRows(detail);

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
    else if(e.key === '+' || e.key === '=') zoomBy(0.4);
    else if(e.key === '-' || e.key === '_') zoomBy(-0.4);
    else if(e.key === '0') resetZoom();
  });

  const stage = document.getElementById('slideshow-stage');
  const mediaEl = document.getElementById('slideshow-media');

  stage.addEventListener('wheel', (e) => {
    if(!document.querySelector('#slideshow-media img')) return;
    e.preventDefault();
    zoomBy(e.deltaY < 0 ? 0.25 : -0.25);
  }, {passive: false});

  stage.addEventListener('dblclick', (e) => {
    if(!document.querySelector('#slideshow-media img')) return;
    e.preventDefault();
    toggleZoomAt();
  });

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
<link rel="stylesheet" href="/assets/style.css">
</head>"""

INDEX_TEMPLATE = Template("""<!doctype html>
<html lang="en">
""" + HEAD + """
<body>
<header class="site-header">
  <span class="brand"><a href="/">{{ title }}</a></span>
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

<footer class="site-footer">Served live by trip_server.py &middot; nothing written to disk</footer>

<script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.js"></script>
<script src="/assets/script.js"></script>
<script>
try { initOverviewMap('overview-map', {{ days_json }}); }
catch(e) { console.error('Map failed to load (check your internet connection):', e); }
</script>
</body>
</html>
""")

DAY_TEMPLATE = Template("""<!doctype html>
<html lang="en">
""" + HEAD + """
<body>
<header class="site-header">
  <span class="brand"><a href="/">{{ trip_title }}</a></span>
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
    <p>No description yet — add one for {{ date_key }} in trip_content.json, then refresh this page.</p>
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
    <img src="/thumb/{{ item.id }}" loading="lazy" alt="">
    {% if item.media_type == 'video' %}
    <div class="play"><svg viewBox="0 0 24 24" fill="white"><circle cx="12" cy="12" r="11" fill="rgba(14,23,41,.55)"/><path d="M10 8l6 4-6 4V8z"/></svg></div>
    {% endif %}
    {% if item.time_display %}<span class="badge">{{ item.time_display }}</span>{% endif %}
  </div>
  {% endfor %}
</div>

<footer class="site-footer">Served live by trip_server.py &middot; nothing written to disk</footer>

""" + SLIDESHOW_HTML + """
<script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.js"></script>
<script src="/assets/script.js"></script>
<script>
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
# Page rendering — same shape as generate_site.py's render_site, just
# returning a string instead of writing a file, and building URLs against
# the running server (/thumb/<id>, /media/<id>, ...) instead of relative
# paths to files that don't exist here.
# --------------------------------------------------------------------------

def build_day_summaries(days: list, content: dict):
    real_day_count = sum(1 for d in days if not d.is_unknown)
    real_rank = {}
    rank = 0
    for d in days:
        if not d.is_unknown:
            rank += 1
            real_rank[d.date_key] = rank

    summaries = []
    for d in days:
        c = content["days"].get(d.date_key, {})
        default_title = "Unsorted" if d.is_unknown else f"Day {real_rank[d.date_key]}"
        title = c.get("title") or default_title
        desc = c.get("description", "").strip()
        excerpt = (desc[:110] + "\u2026") if len(desc) > 110 else desc
        cover = f"/thumb/{d.items[0].id}" if d.items else None
        date_display = "Date unknown" if d.is_unknown else format_long_date(d.date_obj)
        color = UNKNOWN_DAY_COLOR if d.is_unknown else DAY_COLORS[(real_rank[d.date_key] - 1) % len(DAY_COLORS)]
        summaries.append({
            "href": f"/day/{d.date_key}",
            "date_display": date_display,
            "title": title,
            "excerpt": excerpt,
            "cover": cover,
            "photo_count": d.photo_count,
            "video_count": d.video_count,
            "color": color,
            "route": d.route,
            "label": ("Unsorted" if d.is_unknown else f"Day {real_rank[d.date_key]}") + f" \u2014 {d.date_key}",
            "is_unknown": d.is_unknown,
            "day_number": None if d.is_unknown else real_rank[d.date_key],
        })
    return summaries, real_rank, real_day_count


def render_homepage() -> str:
    content = CONTENT.get()
    summaries, _, real_day_count = build_day_summaries(DAYS, content)
    total_photos = sum(d.photo_count for d in DAYS)
    total_videos = sum(d.video_count for d in DAYS)
    total_km = round(sum(d.distance_km for d in DAYS), 1)
    return INDEX_TEMPLATE.render(
        page_title=content["title"],
        title=content["title"],
        subtitle=content.get("subtitle", ""),
        day_count=real_day_count,
        photo_count=total_photos,
        video_count=total_videos,
        distance_km=total_km,
        days=summaries,
        days_json=json.dumps(summaries),
    )


def render_day(date_key: str) -> Optional[str]:
    content = CONTENT.get()
    _, real_rank, real_day_count = build_day_summaries(DAYS, content)
    idx = next((i for i, d in enumerate(DAYS) if d.date_key == date_key), None)
    if idx is None:
        return None
    d = DAYS[idx]

    c = content["days"].get(d.date_key, {})
    default_title = "Unsorted" if d.is_unknown else f"Day {real_rank[d.date_key]}"
    title = c.get("title") or default_title
    desc_raw = c.get("description", "").strip()
    description = [p.strip() for p in desc_raw.split("\n\n") if p.strip()] if desc_raw else []
    date_display = "Date unknown" if d.is_unknown else format_long_date(d.date_obj)

    items_ctx = []
    slides = []
    route_thumbs = []
    route_slide_indices = []
    for slide_idx, it in enumerate(d.items):
        item_ctx = {"id": it.id, "media_type": it.media_type}
        if not d.is_unknown:
            item_ctx["time_display"] = it.dt.strftime("%H:%M")
        items_ctx.append(item_ctx)

        slide = {
            "type": it.media_type,
            "display": f"/media/{it.id}",
            "time": None if d.is_unknown else it.dt.isoformat(),
            "lat": it.lat,
            "lon": it.lon,
            "detail": it.detail,
            "downloadUrl": f"/download/{it.id}",
        }
        if it.media_type == "video":
            slide["poster"] = f"/thumb/{it.id}"
        slides.append(slide)
        if it.has_gps:
            route_thumbs.append(f"/thumb/{it.id}")
            route_slide_indices.append(slide_idx)

    prev_href = f"/day/{DAYS[idx-1].date_key}" if idx > 0 else None
    next_href = f"/day/{DAYS[idx+1].date_key}" if idx < len(DAYS) - 1 else None

    return DAY_TEMPLATE.render(
        page_title=f"{title} \u2014 {content['title']}",
        trip_title=content["title"],
        day_number=None if d.is_unknown else real_rank[d.date_key],
        day_total=real_day_count,
        is_unknown=d.is_unknown,
        day_title=title,
        date_display=date_display,
        date_key=d.date_key,
        description=description,
        route=d.route,
        route_json=json.dumps(d.route),
        route_thumbs_json=json.dumps(route_thumbs),
        route_slide_indices_json=json.dumps(route_slide_indices),
        items=items_ctx,
        slides_json=json.dumps(slides),
        prev_href=prev_href,
        prev_href_json=json.dumps(prev_href),
        prev_label=DAYS[idx-1].date_key if idx > 0 else "",
        next_href=next_href,
        next_href_json=json.dumps(next_href),
        next_label=DAYS[idx+1].date_key if idx < len(DAYS) - 1 else "",
    )


# --------------------------------------------------------------------------
# HTTP server
# --------------------------------------------------------------------------

ARGS = None
ITEMS: list = []
DAYS: list = []
CONTENT: Optional[ContentStore] = None

RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)")


class RequestHandler(BaseHTTPRequestHandler):
    server_version = "TripServer/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        sys.stderr.write(f"{self.address_string()} - {fmt % args}\n")

    def do_GET(self):
        self._handle()

    def do_HEAD(self):
        self._handle()

    # -- helpers ------------------------------------------------------

    def _send_text(self, body: str, content_type: str, status: int = 200):
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _send_bytes(self, data: bytes, content_type: str, cache_seconds: Optional[int] = None):
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        if cache_seconds:
            self.send_header("Cache-Control", f"public, max-age={cache_seconds}")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _send_file(self, path: Path, content_type: str, download_name: Optional[str] = None):
        """Stream a file straight off disk, with HTTP Range support so
        <video> seeking works properly. This is the only way media ever
        leaves this server for native formats and video — no copy, no
        conversion, the actual bytes of your actual file."""
        try:
            stat = path.stat()
        except OSError:
            self.send_error(404, "File not found")
            return
        file_size = stat.st_size

        start, end = 0, file_size - 1
        status = 200
        range_header = self.headers.get("Range")
        if range_header:
            m = RANGE_RE.match(range_header)
            if m and (m.group(1) or m.group(2)):
                if m.group(1):
                    start = int(m.group(1))
                    end = int(m.group(2)) if m.group(2) else file_size - 1
                else:
                    suffix = int(m.group(2))
                    start = max(0, file_size - suffix)
                    end = file_size - 1
                end = min(end, file_size - 1)
                if start > end or start >= file_size:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{file_size}")
                    self.end_headers()
                    return
                status = 206

        length = end - start + 1
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Last-Modified", self.date_time_string(int(stat.st_mtime)))
        self.send_header("Cache-Control", "public, max-age=3600")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
        if download_name:
            self.send_header("Content-Disposition", f'attachment; filename="{download_name}"')
        self.end_headers()

        if self.command == "HEAD":
            return
        try:
            with open(path, "rb") as f:
                f.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = f.read(min(65536, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass  # normal when a browser cancels a video/image fetch mid-stream

    def _lookup_item(self, raw_path: str, prefix: str) -> Optional[MediaItem]:
        id_str = raw_path[len(prefix):].split("/")[0]
        try:
            item_id = int(id_str)
            if not 0 <= item_id < len(ITEMS):
                raise ValueError
            return ITEMS[item_id]
        except ValueError:
            self.send_error(404, "No such item")
            return None

    # -- routing --------------------------------------------------------

    def _handle(self):
        try:
            raw_path = unquote(urlparse(self.path).path)

            if raw_path in ("/", ""):
                self._send_text(render_homepage(), "text/html")
            elif raw_path.startswith("/day/"):
                key = raw_path[len("/day/"):].strip("/")
                html = render_day(key)
                if html is None:
                    self.send_error(404, "No such day")
                else:
                    self._send_text(html, "text/html")
            elif raw_path.startswith("/thumb/"):
                item = self._lookup_item(raw_path, "/thumb/")
                if item is not None:
                    self._serve_thumb(item)
            elif raw_path.startswith("/media/"):
                item = self._lookup_item(raw_path, "/media/")
                if item is not None:
                    self._serve_media(item)
            elif raw_path.startswith("/original/"):
                item = self._lookup_item(raw_path, "/original/")
                if item is not None:
                    self._send_file(item.path, guess_mime(item.path))
            elif raw_path.startswith("/download/"):
                item = self._lookup_item(raw_path, "/download/")
                if item is not None:
                    self._send_file(item.path, guess_mime(item.path), download_name=item.path.name)
            elif raw_path == "/assets/style.css":
                self._send_text(CSS, "text/css")
            elif raw_path == "/assets/script.js":
                self._send_text(JS, "application/javascript")
            else:
                self.send_error(404, "Not found")
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            traceback.print_exc()
            try:
                self.send_error(500, "Internal server error")
            except Exception:
                pass

    def _serve_thumb(self, item: MediaItem):
        try:
            mtime = item.path.stat().st_mtime
        except OSError:
            self.send_error(404, "File not found")
            return
        cache_key = ("thumb", item.id, mtime, ARGS.thumb_size)
        data = THUMB_CACHE.get(cache_key)
        if data is None:
            data = render_thumbnail_bytes(item, ARGS.thumb_size)
            if data is None:
                self.send_error(404, "Could not generate thumbnail")
                return
            THUMB_CACHE.set(cache_key, data)
        self._send_bytes(data, "image/jpeg", cache_seconds=86400)

    def _serve_media(self, item: MediaItem):
        if item.media_type == "video":
            self._send_file(item.path, guess_mime(item.path))
            return
        if not item.needs_jpeg_fallback:
            self._send_file(item.path, guess_mime(item.path))
            return
        # HEIC/HEIF, TIFF, or a multi-stream/MPO .jpg: on-the-fly JPEG
        # fallback, cached in memory only
        try:
            mtime = item.path.stat().st_mtime
        except OSError:
            self.send_error(404, "File not found")
            return
        cache_key = ("fallback", item.id, mtime)
        data = FALLBACK_CACHE.get(cache_key)
        if data is None:
            data = render_fallback_jpeg_bytes(item.path)
            if data is None:
                self.send_error(500, "Could not convert image")
                return
            FALLBACK_CACHE.set(cache_key, data)
        self._send_bytes(data, "image/jpeg", cache_seconds=86400)


def _lan_ip() -> Optional[str]:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return None


def main():
    global ARGS, ITEMS, DAYS, CONTENT

    parser = argparse.ArgumentParser(
        description="Serve your trip photos/videos as a live website — nothing is written to disk."
    )
    parser.add_argument("-i", "--input", type=Path, default=Path("."),
                         help="Folder with your photos/videos (default: current folder)")
    parser.add_argument("-c", "--content", type=Path, default=Path("trip_content.json"),
                         help="Where titles/descriptions are stored (default: ./trip_content.json). "
                              "This is the one file the script does write — see README.md.")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--lan", action="store_true",
                         help="Allow other devices on your network to connect (binds 0.0.0.0 instead "
                              "of localhost-only). Anyone on your network can then open the site — "
                              "there's no login.")
    parser.add_argument("--thumb-size", type=int, default=400, help="Max thumbnail dimension in px")
    parser.add_argument("--tz-offset", type=float, default=None,
                         help="Fixed UTC offset in hours (e.g. 2 or -5.5) for converting VIDEO "
                              "timestamps to your trip's local time. Photos are unaffected.")
    parser.add_argument("--no-dedupe", action="store_true",
                         help="Don't skip exact-duplicate files. Duplicate detection is on by default.")
    args = parser.parse_args()
    ARGS = args

    if not args.input.is_dir():
        print(f"Input folder not found: {args.input}", file=sys.stderr)
        sys.exit(1)
    if not HEIF_SUPPORT:
        print("Note: pillow-heif not installed — .heic/.heif files will be skipped.\n"
              "      Install with: pip install pillow-heif", file=sys.stderr)

    print(f"Indexing {args.input} ...")
    items = build_index(args.input, args.tz_offset, dedupe=not args.no_dedupe)
    if not items:
        print("No supported photos or videos found.", file=sys.stderr)
        sys.exit(1)

    fallback_count = sum(1 for it in items if it.dt_is_fallback)
    gps_count = sum(1 for it in items if it.has_gps)
    print(f"\nFound {len(items)} media files "
          f"({fallback_count} without EXIF date, using file date instead; "
          f"{gps_count} with GPS location).")

    days = group_by_day(items)
    print(f"Grouped into {len(days)} day(s).")

    ITEMS = items
    DAYS = days
    CONTENT = ContentStore(args.content, [d.date_key for d in days])

    host = "0.0.0.0" if args.lan else "127.0.0.1"
    server = ThreadingHTTPServer((host, args.port), RequestHandler)

    print(f"\nNothing has been written to disk except {args.content} — this server generates "
          f"every page and image on the fly, in memory, per request.")
    print(f"\nServing at http://127.0.0.1:{args.port}/")
    if args.lan:
        lan_ip = _lan_ip()
        if lan_ip:
            print(f"Also reachable on your network at: http://{lan_ip}:{args.port}/")
        print("(anyone on your network can open this while the server is running — there's no login)")
    print("Press Ctrl+C to stop.\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping.")
        server.shutdown()


if __name__ == "__main__":
    main()
