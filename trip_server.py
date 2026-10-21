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
import time
import traceback
import urllib.error
import urllib.request
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, quote, unquote, urlparse, urlsplit

from PIL import Image, ImageOps
from markupsafe import Markup

try:
    from trip_common import (
        CAPTION_MAX, CSS, DAY_TEMPLATE, HEIF_SUPPORT, INDEX_TEMPLATE, JS, ContentFileError, MediaItem,
        SiteUrls, assign_rel_paths, hidden_rels, report_unknown_photo_keys, set_cover, set_day_text,
        set_photo_fields, visible_days,
        atomic_write_text, build_day_context, build_index_context, build_media_item, check_ffmpeg,
        collect_candidates, dedupe_candidates, default_content, group_by_day, pair_live_photos,
        parse_content_file, placeholder_thumbnail, positive_thumb_size, read_image_metadata,
        read_video_metadata,
        add_date_check_args, apply_date_check, resolve_date_range, large_thumb_size_arg,
        DEFAULT_TILE_URL, VENDOR_DIR, add_offline_args, atomic_write_bytes, fetch_offline_assets,
        vendor_ready,
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
    assign_rel_paths(items, input_dir)   # the keys trip_content.json uses for captions etc.
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
HI_THUMB_CACHE = LRUCache(maxsize=150)  # larger thumbnails (--large-thumb-size): fewer, they are several times the size
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

    def update(self, mutate) -> dict:
        """Change the file safely: re-read it as it is on disk right now, let
        `mutate(content)` edit that in place, write it back atomically. If the
        file currently can't be parsed this raises ContentFileError and writes
        nothing, so a hand-edit in progress is never overwritten."""
        with self._lock:
            content = parse_content_file(self.path) if self.path.exists() else default_content()
            mutate(content)
            atomic_write_text(self.path, json.dumps(content, indent=2, ensure_ascii=False) + "\n")
            self._cache = content
            self._sig = self._sig_now()
            self._warned_sig = object()
            return content

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


# --------------------------------------------------------------------------
# Map tiles and local Leaflet/fonts (see "Working without internet" in
# trip_common.py)
# --------------------------------------------------------------------------

VENDOR_READY = False                    # local Leaflet + fonts present in ./vendor?
TILE_CACHE: Optional[Path] = None       # --tile-cache: where tiles are kept
TILE_UPSTREAM = DEFAULT_TILE_URL        # where a missing tile is downloaded from
TILE_CLIENT_URL = DEFAULT_TILE_URL      # what the pages' maps ask for
TILE_MAX_AGE = 7 * 24 * 3600            # older cached tiles are refreshed when possible
TILE_UA = "TripSite/1.0 (personal photo trip viewer with a local tile cache)"
TILE_ROUTE = re.compile(r"^/tiles/(\d{1,2})/(\d{1,8})/(\d{1,8})\.png$")

_tile_gate = threading.Semaphore(2)     # at most 2 downloads at once, as OSM asks
_tile_locks: dict = {}                  # one lock per tile, so it is only downloaded once
_tile_locks_guard = threading.Lock()
_tiles_blocked_until = 0.0              # after a failure, don't make every tile wait for a timeout


def sniff_image(data: bytes) -> Optional[str]:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def tile_cache_path(z: int, x: int, y: int) -> Path:
    return TILE_CACHE / str(z) / str(x) / f"{y}.png"


def download_tile(z: int, x: int, y: int) -> Optional[bytes]:
    """One tile from the upstream provider, or None. When the network is
    unreachable (or the provider turns us away) it stops trying for a while,
    so a page full of tiles doesn't wait for a timeout per tile."""
    global _tiles_blocked_until
    if time.monotonic() < _tiles_blocked_until:
        return None
    url = (TILE_UPSTREAM.replace("{z}", str(z)).replace("{x}", str(x)).replace("{y}", str(y)))
    request = urllib.request.Request(url, headers={"User-Agent": TILE_UA})
    with _tile_gate:
        if time.monotonic() < _tiles_blocked_until:   # someone else just found out
            return None
        try:
            with urllib.request.urlopen(request, timeout=6) as resp:
                data = resp.read(1_000_001)
        except urllib.error.HTTPError as e:
            if e.code in (403, 429, 503):             # being refused: back off, don't hammer
                _tiles_blocked_until = time.monotonic() + 300
            return None
        except (urllib.error.URLError, OSError, TimeoutError):
            _tiles_blocked_until = time.monotonic() + 30
            return None
    if len(data) > 1_000_000 or sniff_image(data) is None:
        return None
    return data


def get_tile(z: int, x: int, y: int) -> Optional[Path]:
    """The tile's file in the cache. A missing or old tile is downloaded
    first; if that fails, an old cached copy is still used."""
    path = tile_cache_path(z, x, y)
    key = (z, x, y)
    with _tile_locks_guard:
        lock = _tile_locks.setdefault(key, threading.Lock())
    try:
        with lock:
            try:
                age = time.time() - path.stat().st_mtime
            except OSError:
                age = None
            if age is not None and age < TILE_MAX_AGE:
                return path
            data = download_tile(z, x, y)
            if data is not None:
                try:
                    atomic_write_bytes(path, data)
                    return path
                except OSError as e:
                    print(f"Could not save a map tile in {TILE_CACHE}: {e}", file=sys.stderr)
            return path if age is not None else None
    finally:
        with _tile_locks_guard:
            _tile_locks.pop(key, None)


VENDOR_MIME = {".js": "application/javascript", ".css": "text/css", ".woff2": "font/woff2",
               ".png": "image/png", ".txt": "text/plain; charset=utf-8"}


class LiveUrls(SiteUrls):
    """The server's own routes. Media URLs carry a ?v= version token (see
    file_version). One instance per page render, so each file is only
    stat()ed once per page."""
    footer = Markup("Served live by trip_server.py")
    edit_hint = ", then refresh this page."

    def __init__(self):
        self._versions = {}
        self.tile_url = TILE_CLIENT_URL

    def _v(self, item: MediaItem) -> str:
        if item.id not in self._versions:
            self._versions[item.id] = item_version(item)
        return self._versions[item.id]

    def vendor_url(self, from_index):
        return "/vendor/" if VENDOR_READY else None

    def home(self, from_index):
        return "/"

    def assets_prefix(self, from_index):
        return "/"

    def day(self, date_key, from_index):
        return f"/day/{date_key}"

    def thumb(self, item, from_index):
        return item_url("thumb", item, self._v(item))

    def hi_thumb(self, item, from_index):
        if not ARGS.large_thumb_size:
            return None
        return f"{item_url('thumb', item, self._v(item))}&w={ARGS.large_thumb_size}"

    def display(self, item):
        return item_url("media", item, self._v(item))

    def download(self, item):
        return item_url("download", item, self._v(item))

    def live(self, item):
        return f"/live/{item.id}?v={file_version(item.live_clip.path)}"


def render_homepage(editable: bool = False) -> str:
    content = CONTENT.get()
    days = visible_days(DAYS, content, editable)
    return INDEX_TEMPLATE.render(**build_index_context(days, content, LiveUrls()))


def render_day(date_key: str, editable: bool = False) -> Optional[str]:
    content = CONTENT.get()
    days = visible_days(DAYS, content, editable)   # hidden photos (and emptied days) drop out for visitors
    idx = next((i for i, d in enumerate(days) if d.date_key == date_key), None)
    if idx is None:
        return None
    return DAY_TEMPLATE.render(**build_day_context(days, idx, content, LiveUrls(), editable=editable))


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
KNOWN_RELS: set = set()   # every photo path trip_content.json may refer to
DAY_KEYS: set = set()

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


class TripHTTPServer(ThreadingHTTPServer):
    # Python's default accept queue holds only 5 waiting connections. A burst of
    # simultaneous requests (several devices opening a day at once, a page full of
    # thumbnails) can overflow it, and the overflow is dropped as "connection reset".
    request_queue_size = 128


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

    def do_POST(self):
        self._response_started = False
        self._req_version = None
        self._preview = False
        try:
            self._handle_post()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            traceback.print_exc()
            self.close_connection = True
            if not self._response_started:
                try:
                    self._send_json({"ok": False, "error": "Internal server error"}, 500)
                except Exception:
                    pass

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

    def _serve_tile(self, raw_path: str):
        m = TILE_ROUTE.match(raw_path)
        if TILE_CACHE is None or not m:
            self.send_error(404, "No such tile")
            return
        z, x, y = (int(g) for g in m.groups())
        if z > 19 or x >= 2 ** z or y >= 2 ** z:
            self.send_error(404, "No such tile")
            return
        path = get_tile(z, x, y)
        if path is None:
            self.send_error(404, "Tile not available")
            return
        try:
            with open(path, "rb") as f:
                mime = sniff_image(f.read(16)) or "image/png"
        except OSError:
            self.send_error(404, "Tile not available")
            return
        self._send_file(path, mime, cache_control="public, max-age=86400")

    def _serve_vendor(self, raw_path: str):
        rel = raw_path[len("/vendor/"):]
        parts = rel.split("/")
        if not VENDOR_READY or not rel or any(p in ("", ".", "..") or "\\" in p for p in parts):
            self.send_error(404, "Not found")
            return
        try:
            root = VENDOR_DIR.resolve()
            target = (VENDOR_DIR / rel).resolve()
            is_file = target.is_file()
        except (OSError, ValueError):          # e.g. a NUL byte in the path
            self.send_error(404, "Not found")
            return
        mime = VENDOR_MIME.get(target.suffix.lower())
        if root not in target.parents or not is_file or mime is None:
            self.send_error(404, "Not found")
            return
        self._send_file(target, mime, cache_control="public, max-age=86400")

    def _can_edit(self) -> bool:
        """Editing is for whoever is sitting at the computer running the
        server: a loopback connection, or this machine reaching itself through
        its own network address. Other devices on the network (--lan) can
        look but never change anything."""
        if ARGS.read_only:
            return False
        client = self.client_address[0]
        try:
            if ipaddress.ip_address(client).is_loopback:
                return True
        except ValueError:
            return False
        try:
            return client == self.connection.getsockname()[0]
        except OSError:
            return False

    def _editor_view(self) -> bool:
        return self._can_edit() and not self._preview

    def _origin_ok(self) -> bool:
        """A browser tab on some other site must not be able to make edits:
        if an Origin header is sent, it has to be this very server."""
        origin = self.headers.get("Origin")
        if not origin:
            return True
        try:
            return urlsplit(origin).netloc.lower() == (self.headers.get("Host") or "").lower()
        except ValueError:
            return False

    def _send_json(self, obj, status: int = 200):
        data = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _fail(self, status: int, message: str):
        # the request body was not read, so the connection can't be reused
        self.close_connection = True
        self._send_json({"ok": False, "error": message}, status)

    def _handle_post(self):
        if not self._host_allowed():
            return self._fail(403, "Unrecognized Host header")
        if not self._can_edit():
            return self._fail(403, "Editing is only available on the computer running the server "
                                   "(and is switched off by --read-only).")
        if not self._origin_ok():
            return self._fail(403, "Cross-origin request refused")
        path = urlparse(self.path).path
        if path not in ("/api/photo", "/api/cover", "/api/day"):
            return self._fail(404, "Not found")
        if self.headers.get("Content-Type", "").split(";")[0].strip().lower() != "application/json":
            return self._fail(415, "Expected application/json")
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            return self._fail(411, "Content-Length required")
        if not 0 <= length <= 65536:
            return self._fail(413, "Request too large")
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return self._send_json({"ok": False, "error": "Invalid JSON"}, 400)
        if not isinstance(body, dict):
            return self._send_json({"ok": False, "error": "Expected a JSON object"}, 400)
        try:
            if path == "/api/photo":
                result = self._api_photo(body)
            elif path == "/api/cover":
                result = self._api_cover(body)
            else:
                result = self._api_day(body)
        except ContentFileError as e:
            return self._send_json({"ok": False, "error": f"Nothing was saved: {e}"}, 409)
        except ValueError as e:
            return self._send_json({"ok": False, "error": str(e)}, 400)
        self._send_json({"ok": True, **result})

    def _api_photo(self, body: dict) -> dict:
        rel = body.get("rel")
        if not isinstance(rel, str) or rel not in KNOWN_RELS:
            raise ValueError("Unknown photo")
        fields = {}
        if "caption" in body:
            if not isinstance(body["caption"], str):
                raise ValueError("caption must be text")
            if len(body["caption"]) > CAPTION_MAX:
                raise ValueError(f"Caption is too long (max {CAPTION_MAX} characters)")
            fields["caption"] = body["caption"]
        for key in ("favorite", "hidden"):
            if key in body:
                if not isinstance(body[key], bool):
                    raise ValueError(f"{key} must be true or false")
                fields[key] = body[key]
        if not fields:
            raise ValueError("Nothing to change")
        out = {}
        CONTENT.update(lambda c: out.update(photo=set_photo_fields(c, rel, **fields)))
        return out

    def _api_cover(self, body: dict) -> dict:
        date, rel = body.get("date"), body.get("rel")
        if date not in DAY_KEYS:
            raise ValueError("Unknown day")
        if rel is not None and (not isinstance(rel, str) or rel not in KNOWN_RELS):
            raise ValueError("Unknown photo")
        CONTENT.update(lambda c: set_cover(c, date, rel))
        return {}

    def _api_day(self, body: dict) -> dict:
        date = body.get("date")
        if date not in DAY_KEYS:
            raise ValueError("Unknown day")
        fields = {}
        for key, limit in (("title", 200), ("description", 20000)):
            if key in body:
                if not isinstance(body[key], str):
                    raise ValueError(f"{key} must be text")
                if len(body[key]) > limit:
                    raise ValueError(f"{key} is too long (max {limit} characters)")
                fields[key] = body[key]
        if not fields:
            raise ValueError("Nothing to change")
        CONTENT.update(lambda c: set_day_text(c, date, **fields))
        return {}

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
            item = ITEMS[item_id]
        except ValueError:
            self.send_error(404, "No such item")
            return None
        # a hidden photo must not be reachable by guessing its number either
        if not self._can_edit() and item.rel in hidden_rels(CONTENT.get()):
            self.send_error(404, "No such item")
            return None
        return item

    # -- routing --------------------------------------------------------

    def _handle(self):
        # one handler instance serves every request on a keep-alive
        # connection, so per-request state is reset here
        self._response_started = False
        self._req_version = None
        self._req_width = None
        self._preview = False
        try:
            if not self._host_allowed():
                self.send_error(403, "Unrecognized Host header (see --allow-host)")
                return
            parsed = urlparse(self.path)
            raw_path = unquote(parsed.path)
            query = parse_qs(parsed.query)
            self._req_version = (query.get("v") or [None])[0]
            self._req_width = (query.get("w") or [None])[0]       # ?w=<large thumbnail size>
            self._preview = "1" in query.get("preview", [])   # editor looking at the visitor view

            if raw_path in ("/", ""):
                self._send_text(render_homepage(self._editor_view()), "text/html")
            elif raw_path.startswith("/day/"):
                key = raw_path[len("/day/"):].strip("/")
                html = render_day(key, self._editor_view())
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
            elif raw_path.startswith("/tiles/"):
                self._serve_tile(raw_path)
            elif raw_path.startswith("/vendor/"):
                self._serve_vendor(raw_path)
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
        size, cache = ARGS.thumb_size, THUMB_CACHE
        if self._req_width is not None:
            # the larger thumbnail: only the one configured size is offered, so a client
            # can't make the server build (and cache) thumbnails of arbitrary sizes
            if not ARGS.large_thumb_size or self._req_width != str(ARGS.large_thumb_size):
                self.send_error(404, "No such thumbnail size")
                return
            size, cache = ARGS.large_thumb_size, HI_THUMB_CACHE
        cache_key = ("thumb", item.id, version, size)
        data = cache.get(cache_key)
        if data is None:
            data = render_thumbnail_bytes(item, size)
            if data is None:
                self.send_error(404, "Could not generate thumbnail")
                return
            cache.set(cache_key, data)
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
    global ARGS, ITEMS, DAYS, CONTENT, ALLOWED_HOSTNAMES, KNOWN_RELS, DAY_KEYS
    global VENDOR_READY, TILE_CACHE, TILE_UPSTREAM, TILE_CLIENT_URL

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
    add_date_check_args(parser)
    add_offline_args(parser)
    parser.add_argument("--large-thumb-size", type=large_thumb_size_arg, default=None, metavar="PIXELS",
                        help="Size of the larger thumbnails that are loaded only where the screen can use "
                             "more pixels (the L and XL sizes, high-resolution screens). Default 1600; 0 "
                             "turns them off. They are made on demand and kept in memory.")
    parser.add_argument("--tile-cache", type=Path, metavar="DIR",
                        help="Keep the map tiles you look at in this folder and serve them from there, "
                             "so maps you have already viewed still work without internet. A tile is "
                             "downloaded the first time it is needed (never in bulk) and refreshed "
                             "after a week when possible.")
    parser.add_argument("--tile-url", metavar="TEMPLATE",
                        help="Where map tiles come from, with {z}, {x} and {y} in it (default: "
                             "OpenStreetMap). Use this for a tile provider you have an account with.")
    parser.add_argument("--read-only", action="store_true",
                         help="Turn off in-page editing (captions, favorites, hiding, covers, day text). "
                              "Without this flag, editing works from the computer running the server only.")
    parser.add_argument("--allow-host", action="append", default=[], metavar="NAME",
                         help="Extra host name the server should answer to, e.g. a custom local DNS "
                              "name for this computer. IP addresses, 'localhost' and this computer's "
                              "own name (also with .local) always work. Repeatable.")
    args = parser.parse_args()
    ARGS = args
    date_range = resolve_date_range(args, parser)
    if args.large_thumb_size is None:             # not given: on, unless the normal thumbnails are already that big
        args.large_thumb_size = 1600 if args.thumb_size < 1600 else 0
    elif args.large_thumb_size and args.large_thumb_size <= args.thumb_size:
        parser.error("--large-thumb-size must be larger than --thumb-size (or 0 to turn it off)")
    if args.fetch_offline_assets:
        sys.exit(fetch_offline_assets())
    if args.tile_url:
        if not (args.tile_url.startswith(("http://", "https://")) and all(
                part in args.tile_url for part in ("{z}", "{x}", "{y}"))):
            parser.error("--tile-url must start with http:// or https:// and contain {z}, {x} and {y}")
        TILE_UPSTREAM = TILE_CLIENT_URL = args.tile_url
    if args.tile_cache:
        try:
            args.tile_cache.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            parser.error(f"can't use --tile-cache {args.tile_cache}: {e}")
        TILE_CACHE = args.tile_cache
        TILE_CLIENT_URL = "/tiles/{z}/{x}/{y}.png"   # the pages ask this server, which keeps the tiles
    VENDOR_READY = vendor_ready()

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

    if not args.no_date_check:
        apply_date_check(items, date_range)
    fallback_count = sum(1 for it in items if it.dt_is_fallback)
    gps_count = sum(1 for it in items if it.has_gps)
    print(f"\nFound {len(items)} media files "
          f"({fallback_count} without a usable capture date, filed under Unsorted; "
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

    KNOWN_RELS = {it.rel for it in ITEMS}
    DAY_KEYS = {d.date_key for d in DAYS}
    report_unknown_photo_keys(CONTENT.get(), KNOWN_RELS)

    host = "0.0.0.0" if args.lan else "127.0.0.1"
    try:
        server = TripHTTPServer((host, args.port), RequestHandler)
    except OSError as e:
        if e.errno in (errno.EADDRINUSE, 10048):  # 10048: the Windows code
            print(f"Port {args.port} is already in use. Is the server already running? "
                  f"Otherwise pick another port, e.g. --port {args.port + 1}", file=sys.stderr)
        else:
            print(f"Could not start the server on {host}:{args.port}: {e}", file=sys.stderr)
        sys.exit(1)

    writes = [str(args.content)] + ([f"the map tile cache in {TILE_CACHE}"] if TILE_CACHE is not None else [])
    print("\nThis server generates every page and image on the fly, in memory. It writes to disk only "
          + " and ".join(writes) + ".")
    if args.large_thumb_size:
        print(f"Larger thumbnails ({args.large_thumb_size}px) are loaded only where the screen can use "
              f"them (L/XL sizes, high-resolution screens); --large-thumb-size 0 turns that off.")
    if VENDOR_READY:
        print(f"Maps and fonts: using the local copies in {VENDOR_DIR} (no internet needed for them).")
    else:
        print("Maps and fonts load from the internet. To make them work offline, run once while "
              "online:  python trip_server.py --fetch-offline-assets")
    if TILE_CACHE is not None:
        cached = sum(1 for _ in TILE_CACHE.rglob("*.png"))
        print(f"Map tiles are cached in {TILE_CACHE} ({cached} so far); browse the maps while online "
              f"and those areas keep working offline.")
    if not args.read_only:
        print(f"Editing is on for this computer: captions, favorites, hiding, cover photos and day "
              f"text are saved into {args.content}. Use --read-only to turn it off.")
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
