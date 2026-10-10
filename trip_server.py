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

Needs trip_common.py (code shared with generate_site.py) in the same folder.
See README.md for the full walkthrough.
"""

from __future__ import annotations

import argparse
import errno
import hashlib
import io
import ipaddress
import json
import mimetypes
import os
import re
import socket
import subprocess
import sys
import threading
import traceback
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, quote, unquote, urlparse, urlsplit

from PIL import Image, ImageOps
from markupsafe import Markup

try:
    from trip_common import (
        CSS, DAY_TEMPLATE, HEIF_SUPPORT, INDEX_TEMPLATE, JS, ContentFileError, MediaItem, SiteUrls,
        atomic_write_text, build_day_context, build_index_context, build_media_item, check_ffmpeg,
        collect_candidates, dedupe_candidates, default_content, group_by_day, pair_live_photos,
        parse_content_file, placeholder_thumbnail, positive_thumb_size, read_image_metadata,
        read_video_metadata,
    )
except ModuleNotFoundError as e:
    if e.name != "trip_common":
        raise
    sys.exit("trip_common.py is missing: keep it in the same folder as this script.")


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
# Index building — metadata only. No thumbnails, no derivative files, no
# disk writes of any kind; this just figures out what's in the folder and
# when/where it was taken, and keeps it in memory for the life of the server.
# --------------------------------------------------------------------------

def build_index(input_dir: Path, tz_offset: Optional[float], dedupe: bool,
                live_photos: bool = True) -> list:
    candidates = collect_candidates(input_dir)
    if dedupe:
        candidates = dedupe_candidates(candidates)

    total = len(candidates)
    items = []
    for n, (path, media_type) in enumerate(candidates, 1):
        print(f"[{n}/{total}] indexing {media_type:5s} {path.name}")
        try:
            if media_type == "image":
                meta = read_image_metadata(path)
            else:
                meta = read_video_metadata(path, tz_offset)
            items.append(build_media_item(path, media_type, meta))
        except Exception as e:
            print(f"  ! failed to index {path.name}: {e}", file=sys.stderr)
            continue

    if live_photos:
        items = pair_live_photos(items)
    for n, it in enumerate(items):
        it.id = n  # URLs use the position in this list, so keep it gapless
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
                         "-frames:v", "1", "-vf", f"scale={thumb_size}:{thumb_size}:force_original_aspect_ratio=decrease",
                         "-f", "image2pipe", "-vcodec", "mjpeg", "pipe:1"],
                        capture_output=True, timeout=30,
                    )
                    if result.returncode == 0 and result.stdout:
                        return result.stdout
                except Exception:
                    continue
            buf = io.BytesIO()
            placeholder_thumbnail(thumb_size).save(buf, "JPEG", quality=82)
            return buf.getvalue()
        with Image.open(item.path) as img:
            # let libjpeg decode at 1/2, 1/4 or 1/8 scale instead of decoding
            # every pixel just to throw most away (a no-op for non-JPEG
            # formats). Must happen before exif_transpose, which loads pixels.
            img.draft("RGB", (thumb_size * 2, thumb_size * 2))
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
    """HEIC/HEIF, TIFF or multi-image JPEG -> full-resolution, near-lossless
    JPEG with the original EXIF (Orientation reset, since exif_transpose
    bakes the rotation into the pixels). Used for display in every browser;
    the untouched original is available through /download/<id>."""
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


def file_version(path: Path) -> str:
    """Short token that changes whenever the file behind an ID changes,
    whether it was edited or, after a restart with new photos, the ID now
    points at a different file. It goes into every media URL (?v=...), so
    browser-cached responses can never be shown for the wrong photo."""
    try:
        st = path.stat()
    except OSError:
        return "missing"
    h = hashlib.sha1(os.fsencode(path))
    h.update(f"\0{st.st_mtime_ns}\0{st.st_size}".encode())
    return h.hexdigest()[:12]


def item_version(item: MediaItem) -> str:
    return file_version(item.path)


def item_url(kind: str, item: MediaItem, version: Optional[str] = None) -> str:
    return f"/{kind}/{item.id}?v={version or item_version(item)}"


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
    """trip_content.json, hot-reloaded on change. It never overwrites the
    user's text: at startup a file that fails to parse aborts with a clear
    message (nothing written), and at runtime a broken save is reported once
    while the last version that loaded fine keeps being served."""

    def __init__(self, path: Path, day_keys: list):
        self.path = path
        self._lock = threading.Lock()
        self._cache = None
        self._sig = None
        self._warned_sig = object()
        self._ensure_keys(day_keys)

    def _sig_now(self):
        # mtime_ns + size: catches edits even on filesystems with coarse
        # (1-2 s) mtime resolution, as long as the length changed
        try:
            st = self.path.stat()
            return (st.st_mtime_ns, st.st_size)
        except OSError:
            return None

    def _ensure_keys(self, day_keys: list):
        """Add blank entries for days not yet in the file. Raises
        ContentFileError, without writing anything, if the existing file
        can't be parsed."""
        exists = self.path.exists()
        content = parse_content_file(self.path) if exists else default_content()
        changed = not exists
        for key in day_keys:
            if key not in content["days"]:
                content["days"][key] = {"title": "", "description": ""}
                changed = True
        if changed:
            atomic_write_text(self.path, json.dumps(content, indent=2, ensure_ascii=False) + "\n")
            print(f"-> wrote {self.path} (fill in titles/descriptions any time; "
                  f"just refresh the page after saving, no restart needed)")
        self._cache = content
        self._sig = self._sig_now()

    def get(self) -> dict:
        with self._lock:
            sig = self._sig_now()
            if sig == self._sig:
                return self._cache
            if sig is None:
                problem = f"{self.path} has disappeared"
            else:
                try:
                    self._cache = parse_content_file(self.path)
                    self._sig = sig
                    self._warned_sig = object()
                    return self._cache
                except ContentFileError as e:
                    problem = str(e)
            # don't advance self._sig: the next request retries, so the fix
            # shows up as soon as the file is saved correctly
            if sig != self._warned_sig:
                print(f"! {problem}\n  Still showing the last version that loaded fine. "
                      f"Fix the file and refresh. (Nothing was written to it.)", file=sys.stderr)
                self._warned_sig = sig
            return self._cache


# --------------------------------------------------------------------------
# Page rendering: the shared builders in trip_common.py produce the same
# pages as generate_site.py; LiveUrls only decides where links point (this
# server's routes, /thumb/<id> etc., instead of relative file paths).
# --------------------------------------------------------------------------


class LiveUrls(SiteUrls):
    """The server's own routes. Media URLs carry a ?v= version token (see
    file_version). One instance per page render, so each file is only
    stat()ed once per page."""
    footer = Markup("Served live by trip_server.py &middot; nothing written to disk")
    edit_hint = ", then refresh this page."

    def __init__(self):
        self._versions = {}

    def _v(self, item: MediaItem) -> str:
        if item.id not in self._versions:
            self._versions[item.id] = item_version(item)
        return self._versions[item.id]

    def home(self, from_index):
        return "/"

    def assets_prefix(self, from_index):
        return "/"

    def day(self, date_key, from_index):
        return f"/day/{date_key}"

    def thumb(self, item, from_index):
        return item_url("thumb", item, self._v(item))

    def display(self, item):
        return item_url("media", item, self._v(item))

    def download(self, item):
        return item_url("download", item, self._v(item))

    def live(self, item):
        return f"/live/{item.id}?v={file_version(item.live_clip.path)}"


def render_homepage() -> str:
    return INDEX_TEMPLATE.render(**build_index_context(DAYS, CONTENT.get(), LiveUrls()))


def render_day(date_key: str) -> Optional[str]:
    idx = next((i for i, d in enumerate(DAYS) if d.date_key == date_key), None)
    if idx is None:
        return None
    return DAY_TEMPLATE.render(**build_day_context(DAYS, idx, CONTENT.get(), LiveUrls()))


# --------------------------------------------------------------------------
# HTTP server
# --------------------------------------------------------------------------

ARGS = None
ITEMS: list = []
DAYS: list = []
CONTENT: Optional[ContentStore] = None
# Host names (besides IP literals) the server answers to. Checking the Host
# header blocks DNS-rebinding attacks, where a web page you visit points its
# own domain name at 127.0.0.1 to read this server from your browser.
ALLOWED_HOSTNAMES: set = {"localhost"}

RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)")
CACHE_IMMUTABLE = "public, max-age=31536000, immutable"
CACHE_REVALIDATE = "no-cache"


def content_disposition(filename: str) -> str:
    """RFC 6266 attachment header that works for any filename: an ASCII-only
    fallback plus the exact UTF-8 name. Raw non-Latin-1 names would crash
    http.server's header encoding, and quotes or newlines would break the
    header."""
    fallback = "".join(
        c if 0x20 <= ord(c) < 0x7F and c not in '"\\' else "_" for c in filename
    ) or "download"
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(filename, safe='')}"


class RequestHandler(BaseHTTPRequestHandler):
    server_version = "TripServer/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        sys.stderr.write(f"{self.address_string()} - {fmt % args}\n")

    def send_response(self, code, message=None):
        self._response_started = True
        super().send_response(code, message)

    def do_GET(self):
        self._handle()

    def do_HEAD(self):
        self._handle()

    # -- helpers ------------------------------------------------------

    def _host_allowed(self) -> bool:
        host = self.headers.get("Host")
        if not host:
            return True  # very old clients; browsers always send Host
        try:
            name = urlsplit("//" + host).hostname
        except ValueError:
            return False
        if not name:
            return False
        name = name.rstrip(".").lower()
        try:
            ipaddress.ip_address(name)
            return True  # DNS rebinding needs a domain name; IP literals are safe
        except ValueError:
            pass
        return name in ALLOWED_HOSTNAMES

    def _cache_control(self, current_version: str) -> str:
        # URL carries the file's current version -> its bytes can never
        # change, so let the browser keep it. Anything else (an old or
        # missing ?v=) must be revalidated rather than trusted.
        return CACHE_IMMUTABLE if self._req_version == current_version else CACHE_REVALIDATE

    def _send_text(self, body: str, content_type: str, status: int = 200):
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", CACHE_REVALIDATE)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _send_bytes(self, data: bytes, content_type: str, cache_control: Optional[str] = None):
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        if cache_control:
            self.send_header("Cache-Control", cache_control)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _send_file(self, path: Path, content_type: str, download_name: Optional[str] = None,
                   cache_control: str = CACHE_REVALIDATE):
        """Stream a file straight off disk, with HTTP Range support so
        <video> seeking works properly. This is the only way media ever
        leaves this server for native formats and video: no copy, no
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
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                status = 206

        length = end - start + 1
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Last-Modified", self.date_time_string(int(stat.st_mtime)))
        self.send_header("Cache-Control", cache_control)
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
        if download_name:
            self.send_header("Content-Disposition", content_disposition(download_name))
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
            if remaining > 0:
                # file shrank mid-send: the promised Content-Length can't be
                # met, so the connection can't be reused
                self.close_connection = True
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
        # one handler instance serves every request on a keep-alive
        # connection, so per-request state is reset here
        self._response_started = False
        self._req_version = None
        try:
            if not self._host_allowed():
                self.send_error(403, "Unrecognized Host header (see --allow-host)")
                return
            parsed = urlparse(self.path)
            raw_path = unquote(parsed.path)
            self._req_version = (parse_qs(parsed.query).get("v") or [None])[0]

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
            elif raw_path.startswith("/download/"):
                item = self._lookup_item(raw_path, "/download/")
                if item is not None:
                    self._send_file(item.path, guess_mime(item.path), download_name=item.path.name,
                                    cache_control=self._cache_control(item_version(item)))
            elif raw_path.startswith("/live/"):
                item = self._lookup_item(raw_path, "/live/")
                if item is not None:
                    clip = item.live_clip
                    if clip is None:
                        self.send_error(404, "Not a Live Photo")
                    else:
                        self._send_file(clip.path, guess_mime(clip.path),
                                        cache_control=self._cache_control(file_version(clip.path)))
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
            if not self._response_started:
                try:
                    self.send_error(500, "Internal server error")
                except Exception:
                    pass
            else:
                # headers (maybe part of a body) already went out: writing an
                # error response now would corrupt the stream, so just drop
                # the connection
                self.close_connection = True

    def _serve_thumb(self, item: MediaItem):
        if not item.path.exists():
            self.send_error(404, "File not found")
            return
        version = item_version(item)
        cache_key = ("thumb", item.id, version, ARGS.thumb_size)
        data = THUMB_CACHE.get(cache_key)
        if data is None:
            data = render_thumbnail_bytes(item, ARGS.thumb_size)
            if data is None:
                self.send_error(404, "Could not generate thumbnail")
                return
            THUMB_CACHE.set(cache_key, data)
        self._send_bytes(data, "image/jpeg", cache_control=self._cache_control(version))

    def _serve_media(self, item: MediaItem):
        version = item_version(item)
        cache_control = self._cache_control(version)
        if item.media_type == "video" or not item.needs_jpeg_fallback:
            self._send_file(item.path, guess_mime(item.path), cache_control=cache_control)
            return
        # HEIC/HEIF, TIFF, or a multi-stream/MPO .jpg: on-the-fly JPEG
        # fallback, cached in memory only
        if not item.path.exists():
            self.send_error(404, "File not found")
            return
        cache_key = ("fallback", item.id, version)
        data = FALLBACK_CACHE.get(cache_key)
        if data is None:
            data = render_fallback_jpeg_bytes(item.path)
            if data is None:
                self.send_error(500, "Could not convert image")
                return
            FALLBACK_CACHE.set(cache_key, data)
        self._send_bytes(data, "image/jpeg", cache_control=cache_control)


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
    global ARGS, ITEMS, DAYS, CONTENT, ALLOWED_HOSTNAMES

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
    parser.add_argument("--thumb-size", type=positive_thumb_size, default=400,
                         help="Max thumbnail dimension in px (16-4096)")
    parser.add_argument("--tz-offset", type=float, default=None,
                         help="Fixed UTC offset in hours (e.g. 2 or -5.5) for converting VIDEO "
                              "timestamps to your trip's local time. Without it, iPhone videos use "
                              "their recorded local time. Photos are unaffected.")
    parser.add_argument("--no-dedupe", action="store_true",
                         help="Don't skip exact-duplicate files. Duplicate detection is on by default.")
    parser.add_argument("--no-live-photos", action="store_true",
                         help="Show iPhone Live Photo clips as separate videos instead of "
                              "attaching them to their photos.")
    parser.add_argument("--allow-host", action="append", default=[], metavar="NAME",
                         help="Extra host name the server should answer to, e.g. a custom local DNS "
                              "name for this computer. IP addresses, 'localhost' and this computer's "
                              "own name (also with .local) always work. Repeatable.")
    args = parser.parse_args()
    ARGS = args

    if not args.input.is_dir():
        print(f"Input folder not found: {args.input}", file=sys.stderr)
        sys.exit(1)
    if not HEIF_SUPPORT:
        print("Note: pillow-heif not installed — .heic/.heif files will be skipped.\n"
              "      Install with: pip install pillow-heif", file=sys.stderr)

    # check the content file *before* the (possibly long) indexing step, so a
    # typo is reported immediately and never leads to the file being rewritten
    if args.content.exists():
        try:
            parse_content_file(args.content)
        except ContentFileError as e:
            print(f"! {e}\n  Nothing was changed. Fix the file and start the server again.",
                  file=sys.stderr)
            sys.exit(1)

    hostname = socket.gethostname().lower().rstrip(".")
    ALLOWED_HOSTNAMES = {"localhost", hostname, hostname.split(".")[0] + ".local"}
    ALLOWED_HOSTNAMES |= {h.lower().rstrip(".") for h in args.allow_host}

    check_ffmpeg()
    print(f"Indexing {args.input} ...")
    items = build_index(args.input, args.tz_offset, dedupe=not args.no_dedupe,
                        live_photos=not args.no_live_photos)
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
    try:
        CONTENT = ContentStore(args.content, [d.date_key for d in days])
    except ContentFileError as e:
        print(f"! {e}\n  Nothing was changed. Fix the file and start the server again.",
              file=sys.stderr)
        sys.exit(1)

    host = "0.0.0.0" if args.lan else "127.0.0.1"
    try:
        server = ThreadingHTTPServer((host, args.port), RequestHandler)
    except OSError as e:
        if e.errno in (errno.EADDRINUSE, 10048):  # 10048: the Windows code
            print(f"Port {args.port} is already in use. Is the server already running? "
                  f"Otherwise pick another port, e.g. --port {args.port + 1}", file=sys.stderr)
        else:
            print(f"Could not start the server on {host}:{args.port}: {e}", file=sys.stderr)
        sys.exit(1)

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
