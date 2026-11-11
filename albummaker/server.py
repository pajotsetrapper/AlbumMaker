# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Pieter Remi Coppens
"""
album_server: serve your trip photos/videos as a live website, generated on
the fly. No thumbnails, no converted copies and no HTML files on disk:
everything is computed in memory, per request, and kept only in a small,
size-limited in-memory cache.

    python album_server.py -i /path/to/photos

All of the server's state lives in one App object (server.app), not in
module globals, so several servers can run side by side (tests do).
"""

from __future__ import annotations

import argparse
import errno
import hashlib
import hmac
import http.client
import ipaddress
import json
import mimetypes
import os
import re
import secrets
import socket
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import parse_qs, quote, unquote, urlencode, urlparse, urlsplit

from markupsafe import Markup

from . import __version__

from .cli import (add_content_arg, add_date_check_args, add_location_args, add_offline_args, add_timezone_arg,
                  large_thumb_size_arg, positive_thumb_size, resolve_date_range)
from .content import (CAPTION_MAX, ContentFileError, day_text, default_content, hidden_rels, is_hidden,
                      load_or_init_content, photo_entry, parse_content_file, report_unknown_photo_keys,
                      resolve_content_path, set_cover, set_day_text, set_photo_fields, set_trip_cover, write_content)
from .imaging import display_jpeg_bytes, image_thumbnails, video_thumbnails
from .locations import describe_days, locate_items
from .metadata import HEIF_SUPPORT, MetadataCache, check_ffmpeg, read_metadata
from .model import MediaItem
from .offline import DEFAULT_TILE_URL, VENDOR_DIR, fetch_offline_assets, vendor_ready
from .library import (TripListError, build_library_context, check_library_args, library_title_arg, missing_folder,
                      read_trip_list, trip_args, trip_summary)
from .places import ATTRIBUTION as PLACES_ATTRIBUTION
from .render import (ASSET_VERSION, CSS, DAY_TEMPLATE, INDEX_TEMPLATE, JS, LIBRARY_TEMPLATE, SiteUrls,
                     build_day_context, build_index_context)
from .scan import (apply_date_check, assign_rel_paths, build_media_item, collect_candidates,
                   dedupe_groups, group_by_day, no_dedupe_groups, pair_live_photos, visible_days)
from .util import atomic_write_bytes, script_json

# mimetypes' built-in table is inconsistent across OS/Python installs for a
# few formats that matter a lot here (HEIC especially) — pin the ones we
# actually care about rather than trusting whatever's locally registered.
MIME_OVERRIDES = {
    ".heic": "image/heic", ".heif": "image/heif",
    ".tif": "image/tiff", ".tiff": "image/tiff",
    ".mov": "video/quicktime", ".mp4": "video/mp4", ".m4v": "video/mp4",
    ".avi": "video/x-msvideo", ".mkv": "video/x-matroska", ".3gp": "video/3gpp",
}
VENDOR_MIME = {".js": "application/javascript", ".css": "text/css", ".woff2": "font/woff2",
               ".png": "image/png", ".txt": "text/plain; charset=utf-8"}

RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)")   # one range only; others get the whole file
KEY_RE = re.compile(r"^[0-9a-f]{10,40}$")
# private: browsers keep it, but a shared cache (a proxy, or a tunnel such as
# Cloudflare's in front of the server) must not. Hiding a photo doesn't change
# its URL, so a shared copy would go on being served to anyone with the link,
# and only an editor's browser may hold a hidden photo at all.
CACHE_IMMUTABLE = "private, max-age=31536000, immutable"
CACHE_REVALIDATE = "no-cache"
PROXY_HEADERS = ("Forwarded", "X-Forwarded-For", "X-Forwarded-Host", "X-Forwarded-Proto",
                 "X-Real-IP", "Via")
SLOW_REQUEST_SECONDS = 2.0


def guess_mime(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in MIME_OVERRIDES:
        return MIME_OVERRIDES[ext]
    guessed, _ = mimetypes.guess_type(str(path))
    return guessed or "application/octet-stream"


def file_version(path: Path) -> str:
    """Short token that changes whenever the file behind a URL changes. It
    goes into every media URL (?v=...), so browser-cached responses can
    never be shown for the wrong photo."""
    try:
        st = path.stat()
    except OSError:
        return "missing"
    h = hashlib.sha1(os.fsencode(path))
    h.update(f"\0{st.st_mtime_ns}\0{st.st_size}".encode())
    return h.hexdigest()[:12]


def assign_keys(items: list):
    """A stable URL key per item: a hash of its path relative to the photos
    folder. Unlike a position in a list, it doesn't change when files are
    added, so /thumb/<key> and #p=<key> links keep working across restarts.
    Lengthened on the (astronomically unlikely) chance of a collision."""
    for length in (10, 16, 40):
        keys = [hashlib.sha1(it.rel.encode("utf-8")).hexdigest()[:length] for it in items]
        if len(set(keys)) == len(keys):
            break
    for it, key in zip(items, keys):
        it.key = key


def content_disposition(filename: str) -> str:
    """RFC 6266 attachment header that works for any filename: an ASCII-only
    fallback plus the exact UTF-8 name. Raw non-Latin-1 names would crash
    http.server's header encoding, and quotes or newlines would break the
    header."""
    fallback = "".join(
        c if 0x20 <= ord(c) < 0x7F and c not in '"\\' else "_" for c in filename
    ) or "download"
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(filename, safe='')}"


# --------------------------------------------------------------------------
# Index building — metadata only, kept in memory for the life of the server
# --------------------------------------------------------------------------

def build_index(input_dir: Path, tz_offset: Optional[float], dedupe: bool,
                live_photos: bool = True, cache: Optional[MetadataCache] = None,
                workers: Optional[int] = None, timezone_name: Optional[str] = None) -> list:
    """Every photo/video's metadata, read in parallel (EXIF reading and
    ffprobe both spend most of their time waiting on the disk or the ffprobe
    process, so threads help), and taken from the --index-cache where a file
    hasn't changed since it was last read."""
    candidates = collect_candidates(input_dir)
    groups = dedupe_groups(candidates) if dedupe else no_dedupe_groups(candidates)
    cache = cache or MetadataCache(None, tz_offset, timezone_name)

    def rel(p: Path) -> str:
        return p.relative_to(input_dir).as_posix()

    todo, metas = [], {}
    for path, media_type, _ in groups:
        meta = cache.get(rel(path), path, media_type)
        if meta is not None:
            metas[path] = meta
        else:
            todo.append((path, media_type))
    if metas:
        print(f"{len(metas)} file(s) taken from the index cache.")

    total = len(todo)
    done = 0

    def read(job):
        path, media_type = job
        return read_metadata(path, media_type, tz_offset, timezone_name)

    if todo:
        workers = workers or min(8, (os.cpu_count() or 2) * 2)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for (path, media_type), fut in zip(todo, [pool.submit(read, j) for j in todo]):
                done += 1
                try:
                    metas[path] = fut.result()
                    cache.put(rel(path), path, metas[path])
                    print(f"[{done}/{total}] indexed {media_type:5s} {path.name}")
                except Exception as e:
                    print(f"[{done}/{total}]  ! failed to index {path.name}: {e}", file=sys.stderr)

    items = []
    for path, media_type, dups in groups:
        if path not in metas:
            continue
        try:
            items.append(build_media_item(path, media_type, metas[path], duplicates=list(dups)))
        except OSError as e:
            print(f"  ! failed to index {path.name}: {e}", file=sys.stderr)
    cache.save({rel(p) for p, _, _ in groups})

    items = pair_live_photos(items) if live_photos else sorted(items, key=lambda it: it.path)
    assign_rel_paths(items, input_dir)   # the keys trip_content.json uses for captions etc.
    assign_keys(items)
    return items


# --------------------------------------------------------------------------
# In-memory caches and the render limiter
# --------------------------------------------------------------------------

class ByteLRU:
    """Least-recently-used cache limited by the total size of its values in
    bytes, not by their number: a full-resolution JPEG of a 48 MP photo is
    hundreds of times the size of a thumbnail. A single value larger than a
    quarter of the budget isn't kept at all."""

    def __init__(self, max_bytes: int):
        self.max_bytes = max(0, int(max_bytes))
        self.size = 0
        self._data = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key):
        with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
                return self._data[key]
        return None

    def set(self, key, value: bytes):
        n = len(value)
        if n > self.max_bytes // 4:
            return
        with self._lock:
            old = self._data.pop(key, None)
            if old is not None:
                self.size -= len(old)
            self._data[key] = value
            self.size += n
            while self.size > self.max_bytes and self._data:
                _, evicted = self._data.popitem(last=False)
                self.size -= len(evicted)

    def __len__(self):
        return len(self._data)


class _Flight:
    __slots__ = ("done", "value")

    def __init__(self):
        self.done = threading.Event()
        self.value = None


class Renderer:
    """Runs the CPU-heavy work (decoding photos, ffmpeg) with two guarantees:

    - at most `limit` renders at once, however many requests arrive (one
      thread per connection would otherwise decode dozens of HEICs at once);
    - single flight: concurrent requests for the same thing (the grid and
      the slideshow's preload asking for one photo) wait for one render
      instead of each doing it."""

    def __init__(self, limit: int):
        self._slots = threading.BoundedSemaphore(max(1, limit))
        self._lock = threading.Lock()
        self._flights = {}

    def get(self, cache: ByteLRU, key, render: Callable[[], Optional[bytes]]) -> Optional[bytes]:
        value = cache.get(key)
        if value is not None:
            return value
        with self._lock:
            value = cache.get(key)
            if value is not None:
                return value
            flight = self._flights.get(key)
            leader = flight is None
            if leader:
                flight = self._flights[key] = _Flight()
        if not leader:
            flight.done.wait()
            return flight.value
        try:
            with self._slots:
                flight.value = render()
            if flight.value is not None:
                cache.set(key, flight.value)
        finally:
            with self._lock:
                self._flights.pop(key, None)
            flight.done.set()
        return flight.value


# --------------------------------------------------------------------------
# Map tiles (--tile-cache)
# --------------------------------------------------------------------------

TILE_MAX_AGE = 7 * 24 * 3600            # older cached tiles are refreshed when possible
TILE_UA = "AlbumMaker/1.0 (personal photo trip viewer with a local tile cache)"
TILE_ROUTE = re.compile(r"^/tiles/(\d{1,2})/(\d{1,8})/(\d{1,8})\.png$")


def sniff_image(data: bytes) -> Optional[str]:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


class TileCache:
    """Map tiles kept on disk, downloaded on first use. At most two downloads
    at once (as OSM asks), one download per tile however many requests
    arrive, and a back-off when the network is down or the provider refuses."""

    def __init__(self, folder: Path, upstream: str):
        self.folder = Path(folder)
        self.upstream = upstream
        self._gate = threading.Semaphore(2)
        self._guard = threading.Lock()
        self._locks = {}            # tile -> [lock, number of threads using it]
        self._blocked_until = 0.0   # after a failure, don't make every tile wait for a timeout

    def path(self, z: int, x: int, y: int) -> Path:
        return self.folder / str(z) / str(x) / f"{y}.png"

    def _blocked(self) -> bool:
        with self._guard:
            return time.monotonic() < self._blocked_until

    def _block(self, seconds: float):
        with self._guard:
            self._blocked_until = max(self._blocked_until, time.monotonic() + seconds)

    def download(self, z: int, x: int, y: int) -> Optional[bytes]:
        if self._blocked():
            return None
        url = self.upstream.replace("{z}", str(z)).replace("{x}", str(x)).replace("{y}", str(y))
        request = urllib.request.Request(url, headers={"User-Agent": TILE_UA})
        with self._gate:
            if self._blocked():   # someone else just found out
                return None
            try:
                with urllib.request.urlopen(request, timeout=6) as resp:
                    data = resp.read(1_000_001)
            except urllib.error.HTTPError as e:
                if e.code in (403, 429, 503):   # being refused: back off, don't hammer
                    self._block(300)
                return None
            except (urllib.error.URLError, OSError, TimeoutError, http.client.HTTPException):
                self._block(30)              # (HTTPException: a cut-off or garbled answer)
                return None
        if len(data) > 1_000_000 or sniff_image(data) is None:
            return None
        return data

    def _acquire(self, key):
        with self._guard:
            entry = self._locks.setdefault(key, [threading.Lock(), 0])
            entry[1] += 1
        entry[0].acquire()
        return entry

    def _release(self, key, entry):
        entry[0].release()
        with self._guard:
            entry[1] -= 1
            if entry[1] == 0 and self._locks.get(key) is entry:
                del self._locks[key]

    def get(self, z: int, x: int, y: int) -> Optional[Path]:
        """The tile's file in the cache. A missing or old tile is downloaded
        first; if that fails, an old cached copy is still used."""
        path = self.path(z, x, y)
        key = (z, x, y)
        entry = self._acquire(key)
        try:
            try:
                age = time.time() - path.stat().st_mtime
            except OSError:
                age = None
            if age is not None and age < TILE_MAX_AGE:
                return path
            data = self.download(z, x, y)
            if data is not None:
                try:
                    atomic_write_bytes(path, data)
                    return path
                except OSError as e:
                    print(f"Could not save a map tile in {self.folder}: {e}", file=sys.stderr)
            return path if age is not None else None
        finally:
            self._release(key, entry)


# --------------------------------------------------------------------------
# trip_content.json, hot-reloaded
# --------------------------------------------------------------------------

class ContentStore:
    """trip_content.json, hot-reloaded on change. It never overwrites the
    user's text: at startup a file that fails to parse aborts with a clear
    message (nothing written), and at runtime a broken save is reported once
    while the last version that loaded fine keeps being served. With
    write=False (--read-only) the file is never written at all."""

    def __init__(self, path: Path, day_keys: list, write: bool = True, title: Optional[str] = None):
        self.path = path
        self.write = write
        self._lock = threading.Lock()
        self._warned_sig = object()
        self._cache = load_or_init_content(path, day_keys, write=write, title=title)
        self._sig = self._sig_now()
        self._hidden = hidden_rels(self._cache)

    def _sig_now(self):
        # mtime_ns + size: catches edits even on filesystems with coarse
        # (1-2 s) mtime resolution, as long as the length changed
        try:
            st = self.path.stat()
            return (st.st_mtime_ns, st.st_size)
        except OSError:
            return None

    def _set(self, content: dict):
        self._cache = content
        self._hidden = hidden_rels(content)

    def update(self, mutate) -> dict:
        """Change the file safely: re-read it as it is on disk right now, let
        `mutate(content)` edit that in place, write it back atomically. If the
        file currently can't be parsed this raises ContentFileError and writes
        nothing, so a hand-edit in progress is never overwritten."""
        if not self.write:
            raise ContentFileError("editing is switched off (--read-only)")
        with self._lock:
            content = parse_content_file(self.path) if self.path.exists() else default_content()
            mutate(content)
            write_content(self.path, content)
            self._set(content)
            self._sig = self._sig_now()
            self._warned_sig = object()
            return content

    def get(self) -> dict:
        with self._lock:
            self._refresh()
            return self._cache

    def hidden(self) -> set:
        """hidden_rels() of the current content, computed once per change
        rather than on every thumbnail request."""
        with self._lock:
            self._refresh()
            return self._hidden

    def _refresh(self):
        sig = self._sig_now()
        if sig == self._sig:
            return
        if sig is None:
            problem = f"{self.path} has disappeared"
        else:
            try:
                self._set(parse_content_file(self.path))
                self._sig = sig
                self._warned_sig = object()
                return
            except ContentFileError as e:
                problem = str(e)
        # don't advance self._sig: the next request retries, so the fix
        # shows up as soon as the file is saved correctly
        if sig != self._warned_sig:
            print(f"! {problem}\n  Still showing the last version that loaded fine. "
                  f"Fix the file and refresh. (Nothing was written to it.)", file=sys.stderr)
            self._warned_sig = sig


# --------------------------------------------------------------------------
# The application
# --------------------------------------------------------------------------

class Shared:
    """What every trip on one server shares: the settings that aren't per trip,
    the editor link, the map tiles, and one memory budget and render limiter
    (a library of ten trips must not use ten times --memory-cache-mb)."""

    def __init__(self, args, tiles: Optional[TileCache] = None, tile_client_url: str = DEFAULT_TILE_URL,
                 vendor: bool = False, allowed_hostnames: Optional[set] = None,
                 editor_token: Optional[str] = None, tile_attribution: Optional[str] = None):
        self.args = args
        self.tiles = tiles
        self.tile_client_url = tile_client_url
        # plain text for the maps' corner credit; None: the pages' own OpenStreetMap credit
        self.tile_attribution = tile_attribution
        self.vendor = vendor
        self.allowed_hostnames = allowed_hostnames or {"localhost"}
        self.editor_token = editor_token or secrets.token_urlsafe(32)
        total_mb = max(16, getattr(args, "memory_cache_mb", 512) or 512)
        self.thumb_cache = ByteLRU(total_mb * 1024 * 1024 * 15 // 100)
        self.hi_thumb_cache = ByteLRU(total_mb * 1024 * 1024 * 25 // 100)
        self.fallback_cache = ByteLRU(total_mb * 1024 * 1024 * 60 // 100)
        self.renderer = Renderer(getattr(args, "render_threads", None) or max(2, (os.cpu_count() or 2)))


class App:
    """Everything a request handler needs for one trip: its index, its content
    file, and (through `shared`) the caches and settings. One per trip: a
    library serves each under its own `prefix` ("/italy")."""

    def __init__(self, args, items: list, days: list, content: ContentStore,
                 tiles: Optional[TileCache] = None, tile_client_url: str = DEFAULT_TILE_URL,
                 vendor: bool = False, allowed_hostnames: Optional[set] = None,
                 editor_token: Optional[str] = None, shared: Optional[Shared] = None, prefix: str = ""):
        self.args = args
        self.items = items
        self.days = days
        self.content = content
        self.prefix = prefix
        self.shared = shared or Shared(args, tiles, tile_client_url, vendor, allowed_hostnames, editor_token)
        self.by_key = {it.key: it for it in items}
        self.by_rel = {it.rel: it for it in items}
        self.known_rels = set(self.by_rel)
        self.day_keys = {d.date_key for d in days}
        self.day_rels = {d.date_key: {it.rel for it in d.items} for d in days}

    def __getattr__(self, name):
        # tiles, vendor, editor_token, the caches, ...: kept once, in Shared
        if name == "shared":
            raise AttributeError(name)
        return getattr(self.shared, name)

    @property
    def editor_cookie(self) -> str:
        # Per port, so two servers on one host don't overwrite each other's
        # cookie. It is NOT a privacy boundary: browsers send a host's cookies
        # to every port of it, so any other server on localhost (or this host
        # name) receives this cookie too, under its own name.
        return f"trip_editor_{self.args.port}"

    def page_extras(self) -> dict:
        """What the live pages have on top of the shared page context: the
        map credit set with --tile-attribution (or made for --tile-url)."""
        return {"tile_attribution_json": script_json(self.tile_attribution)}

    def urls(self) -> "LiveUrls":
        return LiveUrls(self)

    def render_homepage(self, editable: bool = False) -> str:
        content = self.content.get()
        days = visible_days(self.days, content, editable)
        template = INDEX_TEMPLATE
        return template.render(**build_index_context(days, content, self.urls()), **self.page_extras())

    def render_day(self, date_key: str, editable: bool = False) -> Optional[str]:
        content = self.content.get()
        days = visible_days(self.days, content, editable)   # hidden photos (and emptied days) drop out for visitors
        idx = next((i for i, d in enumerate(days) if d.date_key == date_key), None)
        if idx is None:
            return None
        template = DAY_TEMPLATE
        return template.render(**build_day_context(days, idx, content, self.urls(), editable=editable),
                               **self.page_extras())

    # -- image work, cached and limited -------------------------------------

    def thumbnail(self, item: MediaItem, size: int, large: bool, version: str) -> Optional[bytes]:
        cache = self.hi_thumb_cache if large else self.thumb_cache

        def render():
            try:
                if item.media_type == "video":
                    return video_thumbnails(item.path, [size])[0][size]
                return image_thumbnails(item.path, [size])[size]
            except Exception as e:
                print(f"  ! thumbnail failed for {item.path.name}: {e}", file=sys.stderr)
                return None
        return self.renderer.get(cache, ("thumb", self.prefix, item.key, version, size), render)

    def display_jpeg(self, item: MediaItem, version: str) -> Optional[bytes]:
        def render():
            try:
                return display_jpeg_bytes(item.path)
            except Exception as e:
                print(f"  ! on-the-fly conversion failed for {item.path.name}: {e}", file=sys.stderr)
                return None
        return self.renderer.get(self.fallback_cache, ("fallback", self.prefix, item.key, version), render)


class Library:
    """Several trips on one server (--library): the page at / lists them,
    each trip lives under /<name>/."""

    def __init__(self, trips: dict, titles: dict, library_title: str = "Trips"):
        self.trips = trips          # name -> App, in list-file order
        self.titles = titles        # name -> its folder's name (for untitled trips)
        self.library_title = library_title
        self._summaries = {}        # name -> (the content it was made from, summary)

    def summary(self, name: str) -> Optional[dict]:
        """A trip's card, made again only when its trip_content.json changed
        (ContentStore hands out a new dict for every change)."""
        app = self.trips[name]
        content = app.content.get()
        cached = self._summaries.get(name)
        if cached is not None and cached[0] is content:
            return cached[1]
        days = visible_days(app.days, content, editable=False)   # the library shows what visitors see
        summary = (trip_summary(name, days, content, app.urls(), href=f"{app.prefix}/",
                                folder_title=self.titles.get(name, "")) if days else None)
        self._summaries[name] = (content, summary)
        return summary

    def render(self) -> str:
        summaries = [s for s in (self.summary(name) for name in self.trips) if s is not None]
        first = next(iter(self.trips.values()))
        return LIBRARY_TEMPLATE.render(**build_library_context(
            summaries, first.urls(), footer=LiveUrls.footer, places_credit=PLACES_ATTRIBUTION,
            title=self.library_title), **first.page_extras())


class LiveUrls(SiteUrls):
    """The server's own routes. Media URLs carry a ?v= version token (see
    file_version). One instance per page render, so each file is only
    stat()ed once per page."""
    footer = Markup(f"Served live by album_server.py, version {__version__}")
    edit_hint = ", then refresh this page."

    def __init__(self, app: App):
        self.app = app
        self._versions = {}
        self.tile_url = app.tile_client_url
        self.prefix = app.prefix           # "/italy" for a trip in a library
        self.api_base = app.prefix

    def _v(self, item: MediaItem) -> str:
        if item.key not in self._versions:
            self._versions[item.key] = file_version(item.path)
        return self._versions[item.key]

    def _url(self, kind: str, item: MediaItem) -> str:
        return f"{self.prefix}/{kind}/{item.key}?v={self._v(item)}"

    def library_href(self, from_index):
        return "/" if self.prefix else None

    def vendor_url(self, from_index):
        return "/vendor/" if self.app.vendor else None

    def home(self, from_index):
        return f"{self.prefix}/"

    def assets_prefix(self, from_index):
        return "/"

    def asset(self, name, from_index):
        return f"/assets/{name}?v={ASSET_VERSION}"

    def day(self, date_key, from_index):
        return f"{self.prefix}/day/{date_key}"

    def thumb(self, item, from_index):
        return self._url("thumb", item)

    def hi_thumb(self, item, from_index):
        size = self.app.args.large_thumb_size
        return f"{self._url('thumb', item)}&w={size}" if size else None

    def display(self, item):
        return self._url("media", item)

    def download(self, item):
        return self._url("download", item)

    def live(self, item):
        return f"{self.prefix}/live/{item.key}?v={file_version(item.live_clip.path)}"


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

class TripHTTPServer(ThreadingHTTPServer):
    # Python's default accept queue holds only 5 waiting connections. A burst of
    # simultaneous requests (several devices opening a day at once, a page full of
    # thumbnails) can overflow it, and the overflow is dropped as "connection reset".
    request_queue_size = 128
    daemon_threads = True
    # On Windows, SO_REUSEADDR (which http.server turns on) lets a second server
    # bind a port that another one is already listening on, so a new server could
    # start "successfully" while the browser keeps talking to the old one. Without
    # it, Windows refuses the second bind, and the "port already in use" message
    # below is shown instead.
    allow_reuse_address = sys.platform != "win32"

    def __init__(self, address, handler, app: App, library: Optional[Library] = None):
        self.app = app              # the trip (in a library: any trip, for the shared settings)
        self.library = library
        super().__init__(address, handler)


class EditConflict(Exception):
    """An edit made over text that has changed since the page was loaded
    (trip_content.json edited by hand, another browser): answered with 409
    and nothing saved, so neither version is silently lost."""


def _original_text(body: dict, field: str) -> Optional[str]:
    """The `original_<field>` an edit was made from: the text the page showed.
    None when it isn't sent (older pages, scripts), which skips the check."""
    value = body.get("original_" + field)
    if value is not None and not isinstance(value, str):
        raise ValueError(f"original_{field} must be text")
    return value


def _comparable(text: str, single_line: bool = False) -> str:
    # as a browser hands the text back: CRLF as LF; a one-line <input> drops line breaks
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if single_line:
        text = text.replace("\n", "")
    return text.strip()


def _check_unchanged(current: str, original: str, what: str, single_line: bool = False):
    if _comparable(current, single_line) != _comparable(original, single_line):
        raise EditConflict(f"Nothing was saved: {what} was changed elsewhere (in trip_content.json or "
                           f"another browser) since this page was loaded. Copy your text, reload the "
                           f"page to see the current version, then edit that.")


class RequestHandler(BaseHTTPRequestHandler):
    server_version = "AlbumMaker/2.0"
    protocol_version = "HTTP/1.1"
    # Without a timeout, an idle keep-alive connection (or a client that
    # never finishes its request) holds a thread forever. This applies to
    # each read/write on the socket, not to a whole download.
    timeout = 30

    @property
    def app(self) -> App:
        """The trip this request is for (see _select_trip); before that's known,
        or for what all trips share, the server's."""
        return getattr(self, "_app", None) or self.server.app

    def _select_trip(self, raw_path: str) -> tuple:
        """(App, path within that trip) for a request path. In a library a
        trip's pages are under /<name>/; the library page itself, and unknown
        names, give (None, "/") and (None, None)."""
        library = self.server.library
        if library is None:
            return self.server.app, raw_path
        if raw_path in ("", "/"):
            return None, "/"
        first, _, rest = raw_path[1:].partition("/")
        if first in ("assets", "vendor", "tiles"):          # shared by every trip
            return self.server.app, raw_path
        app = library.trips.get(first)
        return (app, "/" + rest) if app is not None else (None, None)

    # -- logging: errors and slow requests only (--verbose for everything) --

    def log_message(self, fmt, *args):
        if self.app.args.verbose:
            sys.stderr.write(f"{self.address_string()} - {fmt % args}\n")

    def log_request(self, code="-", size="-"):
        self._status = code
        if self.app.args.verbose:
            super().log_request(code, size)

    def _log_outcome(self):
        if self.app.args.verbose:
            return
        status = getattr(self, "_status", None)
        elapsed = time.monotonic() - self._started
        try:
            status_n = int(status)
        except (TypeError, ValueError):
            status_n = 0
        if status_n >= 500 or (status_n >= 400 and status_n != 404) or elapsed > SLOW_REQUEST_SECONDS:
            sys.stderr.write(f"{self.address_string()} - {self.command} {self.path} -> {status} "
                             f"({elapsed:.1f}s)\n")

    def send_response(self, code, message=None):
        self._response_started = True
        super().send_response(code, message)

    def _begin(self):
        # one handler instance serves every request on a keep-alive
        # connection, so per-request state is reset here
        self._started = time.monotonic()
        self._status = None
        self._response_started = False
        self._req_version = None
        self._req_width = None
        self._preview = False
        self._set_cookie = None
        self._app = None

    def do_GET(self):
        self._begin()
        try:
            self._handle()
        finally:
            self._log_outcome()

    def do_HEAD(self):
        self.do_GET()

    def do_POST(self):
        self._begin()
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
        finally:
            self._log_outcome()

    # -- who is asking --------------------------------------------------

    def _host_allowed(self) -> bool:
        """Blocks DNS rebinding, where a web page you visit points its own
        domain name at this machine to read the server from your browser."""
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
        return name in self.app.allowed_hostnames

    def _direct_local(self) -> bool:
        """A request straight from the computer running the server: a
        loopback connection, or this machine reaching itself through its own
        network address, with no sign of a proxy in between. A reverse proxy
        or tunnel (nginx, Tailscale Serve, cloudflared, ngrok, ...) also
        connects from this machine, so its requests must not count; most add
        a Forwarded/X-Forwarded-For/Via header, and --editor-link-only covers
        the ones that don't."""
        if self.app.args.editor_link_only:
            return False
        if any(self.headers.get(h) for h in PROXY_HEADERS):
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

    def _token_matches(self, value: str) -> bool:
        # bytes: compare_digest raises TypeError for a str with non-ASCII characters
        return hmac.compare_digest(value.encode("utf-8", "surrogateescape"),
                                   self.app.editor_token.encode("ascii"))

    def _has_editor_cookie(self) -> bool:
        # Parsed by hand: SimpleCookie silently drops *every* cookie when one of
        # them is outside its grammar (e.g. a JSON value set by another app on
        # localhost: browsers share cookies across ports), which would quietly
        # take away edit rights.
        for raw in self.headers.get_all("Cookie") or ():
            for part in raw.split(";"):
                name, sep, value = part.partition("=")
                if sep and name.strip() == self.app.editor_cookie:
                    if self._token_matches(value.strip().strip('"')):
                        return True
        return False

    def _can_edit(self) -> bool:
        """Editing (and seeing hidden photos) is for the owner: whoever sits
        at the computer running the server, or a browser that has opened the
        secret editor link printed at startup. --read-only turns it off."""
        if self.app.args.read_only:
            return False
        return self._direct_local() or self._has_editor_cookie()

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

    def _cookie_header(self) -> str:
        return (f"{self.app.editor_cookie}={self.app.editor_token}; Path=/; HttpOnly; "
                f"SameSite=Lax")

    def _redeem_editor_link(self, parsed, query) -> bool:
        """/?key=<token> (the editor link printed at startup): remember this
        browser as an editor, then redirect to the same page without the key,
        so it doesn't linger in the address bar or the history."""
        key = (query.get("key") or [None])[0]
        if key is None:
            return False
        if self.app.args.read_only or not self._token_matches(key):
            self.send_error(403, "This editor link isn't valid (it changes every time the server starts)")
            return True
        rest = {k: v for k, v in query.items() if k != "key"}
        target = (parsed.path or "/") + ("?" + urlencode(rest, doseq=True) if rest else "")
        self.send_response(303)
        self.send_header("Location", target)
        self.send_header("Set-Cookie", self._cookie_header())
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Length", "0")
        self.end_headers()
        return True

    # -- responses ------------------------------------------------------

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

    def _send_text(self, body: str, content_type: str, status: int = 200,
                   cache_control: str = CACHE_REVALIDATE):
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", cache_control)
        if self._set_cookie:
            self.send_header("Set-Cookie", self._set_cookie)
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

    def _cache_control(self, current_version: str) -> str:
        # URL carries the file's current version -> its bytes can never
        # change, so let the browser keep it. Anything else (an old or
        # missing ?v=) must be revalidated rather than trusted.
        return CACHE_IMMUTABLE if self._req_version == current_version else CACHE_REVALIDATE

    def _send_file(self, path: Path, content_type: str, download_name: Optional[str] = None,
                   cache_control: str = CACHE_REVALIDATE):
        """Stream a file straight off disk, with HTTP Range support so
        <video> seeking works properly. No copy, no conversion: the actual
        bytes of your actual file."""
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
            m = RANGE_RE.fullmatch(range_header.strip())
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

    # -- editing API ----------------------------------------------------

    def _handle_post(self):
        if not self._host_allowed():
            return self._fail(403, "Unrecognized Host header")
        if not self._can_edit():
            return self._fail(403, "Editing is only available on the computer running the server, or "
                                   "through the editor link it prints (and is switched off by --read-only).")
        if not self._origin_ok():
            return self._fail(403, "Cross-origin request refused")
        self._app, path = self._select_trip(unquote(urlparse(self.path).path))
        if self._app is None or path not in ("/api/photo", "/api/cover", "/api/day", "/api/trip-cover"):
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
        except (ValueError, UnicodeDecodeError, RecursionError):   # RecursionError: [[[[...]]]] nested too deep
            return self._send_json({"ok": False, "error": "Invalid JSON"}, 400)
        if not isinstance(body, dict):
            return self._send_json({"ok": False, "error": "Expected a JSON object"}, 400)
        try:
            if path == "/api/photo":
                result = self._api_photo(body)
            elif path == "/api/cover":
                result = self._api_cover(body)
            elif path == "/api/trip-cover":
                result = self._api_trip_cover(body)
            else:
                result = self._api_day(body)
        except ContentFileError as e:
            return self._send_json({"ok": False, "error": f"Nothing was saved: {e}"}, 409)
        except EditConflict as e:
            return self._send_json({"ok": False, "error": str(e)}, 409)
        except ValueError as e:
            return self._send_json({"ok": False, "error": str(e)}, 400)
        except OSError as e:      # disk full, no permission, ...: the file itself is untouched
            print(f"! Could not save {self.app.content.path}: {e}", file=sys.stderr)
            return self._send_json({"ok": False, "error": f"Nothing was saved: could not write "
                                                         f"{self.app.content.path.name} ({e.strerror or e})"}, 500)
        self._send_json({"ok": True, **result})

    def _item_by_rel(self, rel) -> MediaItem:
        if not isinstance(rel, str) or rel not in self.app.by_rel:
            raise ValueError("Unknown photo")
        return self.app.by_rel[rel]

    def _api_photo(self, body: dict) -> dict:
        item = self._item_by_rel(body.get("rel"))
        fields = {}
        original = _original_text(body, "caption")
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

        def mutate(c):
            if original is not None and "caption" in fields:
                _check_unchanged(photo_entry(c, item)["caption"], original, "This photo's caption")
            out.update(photo=set_photo_fields(c, item.rel, aliases=item.aliases, **fields))
        self.app.content.update(mutate)
        return out

    def _api_cover(self, body: dict) -> dict:
        date, rel = body.get("date"), body.get("rel")
        if not isinstance(date, str) or date not in self.app.day_keys:
            raise ValueError("Unknown day")
        if rel is not None:
            if not isinstance(rel, str) or rel not in self.app.known_rels:
                raise ValueError("Unknown photo")
            if rel not in self.app.day_rels.get(date, ()):
                raise ValueError("That photo isn't from this day")
        self.app.content.update(lambda c: set_cover(c, date, rel))
        return {}

    def _api_trip_cover(self, body: dict) -> dict:
        rel = body.get("rel")
        if rel is not None:
            if not isinstance(rel, str) or rel not in self.app.known_rels:
                raise ValueError("Unknown photo")

        def mutate(c):
            # checked against the file as it is now: visitors never see a hidden
            # photo, so it can't be what the trip shows them on its card
            if rel is not None and is_hidden(self.app.by_rel[rel], hidden_rels(c)):
                raise ValueError("A hidden photo can't be the trip's cover: visitors don't see it. "
                                 "Unhide it first, or pick another photo.")
            set_trip_cover(c, rel)
        self.app.content.update(mutate)
        return {}

    def _api_day(self, body: dict) -> dict:
        date = body.get("date")
        if not isinstance(date, str) or date not in self.app.day_keys:
            raise ValueError("Unknown day")
        fields, originals = {}, {}
        for key, limit in (("title", 200), ("description", 20000)):
            original = _original_text(body, key)
            if key in body:
                if not isinstance(body[key], str):
                    raise ValueError(f"{key} must be text")
                if len(body[key]) > limit:
                    raise ValueError(f"{key} is too long (max {limit} characters)")
                fields[key] = body[key]
                if original is not None:
                    originals[key] = original
        if not fields:
            raise ValueError("Nothing to change")

        def mutate(c):
            # only the fields being changed are checked: an edit of the title
            # must not fail because the description was changed elsewhere
            title, description = day_text(c, date)
            if "title" in originals:
                _check_unchanged(title, originals["title"], "This day's title", single_line=True)
            if "description" in originals:
                _check_unchanged(description, originals["description"], "This day's description")
            set_day_text(c, date, **fields)
        self.app.content.update(mutate)
        return {}

    # -- routing --------------------------------------------------------

    def _lookup_item(self, raw_path: str, prefix: str) -> Optional[MediaItem]:
        key = raw_path[len(prefix):].split("/")[0]
        item = self.app.by_key.get(key) if KEY_RE.match(key) else None
        # a hidden photo must not be reachable by guessing its key either
        if item is None or (not self._can_edit() and is_hidden(item, self.app.content.hidden())):
            self.send_error(404, "No such item")
            return None
        return item

    def _handle(self):
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

            self._app, raw_path = self._select_trip(raw_path)
            if self._app is None:
                if raw_path == "/":                              # the library's own page
                    if self._redeem_editor_link(parsed, query):
                        return
                    if (not self.app.args.read_only and self._direct_local()
                            and not self._has_editor_cookie()):
                        self._set_cookie = self._cookie_header()
                    self._send_text(self.server.library.render(), "text/html")
                else:
                    self.send_error(404, "No such trip")
                return

            if raw_path in ("/", "") or raw_path.startswith("/day/"):
                if self._redeem_editor_link(parsed, query):
                    return
                if (not self.app.args.read_only and self._direct_local()
                        and not self._has_editor_cookie()):
                    # the owner at this computer: also remember the browser, so
                    # its requests keep working if they later arrive another way
                    self._set_cookie = self._cookie_header()
                if raw_path in ("/", ""):
                    self._send_text(self.app.render_homepage(self._editor_view()), "text/html")
                else:
                    html = self.app.render_day(raw_path[len("/day/"):].strip("/"), self._editor_view())
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
                                    cache_control=self._cache_control(file_version(item.path)))
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
                self._send_text(CSS, "text/css", cache_control=self._cache_control(ASSET_VERSION))
            elif raw_path == "/assets/script.js":
                self._send_text(JS, "application/javascript", cache_control=self._cache_control(ASSET_VERSION))
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
        version = file_version(item.path)
        size, large = self.app.args.thumb_size, False
        if self._req_width is not None:
            # the larger thumbnail: only the one configured size is offered, so a client
            # can't make the server build (and cache) thumbnails of arbitrary sizes
            wanted = self.app.args.large_thumb_size
            if not wanted or self._req_width != str(wanted):
                self.send_error(404, "No such thumbnail size")
                return
            size, large = wanted, True
        data = self.app.thumbnail(item, size, large, version)
        if data is None:
            self.send_error(404, "Could not generate thumbnail")
            return
        self._send_bytes(data, "image/jpeg", cache_control=self._cache_control(version))

    def _serve_media(self, item: MediaItem):
        version = file_version(item.path)
        cache_control = self._cache_control(version)
        if item.media_type == "video" or not item.needs_jpeg_fallback:
            self._send_file(item.path, guess_mime(item.path), cache_control=cache_control)
            return
        # HEIC/HEIF, TIFF, or a multi-image JPEG: converted on the fly,
        # cached in memory only
        if not item.path.exists():
            self.send_error(404, "File not found")
            return
        data = self.app.display_jpeg(item, version)
        if data is None:
            self.send_error(500, "Could not convert image")
            return
        self._send_bytes(data, "image/jpeg", cache_control=cache_control)

    def _serve_tile(self, raw_path: str):
        m = TILE_ROUTE.match(raw_path)
        tiles = self.app.tiles
        if tiles is None or not m:
            self.send_error(404, "No such tile")
            return
        z, x, y = (int(g) for g in m.groups())
        if z > 19 or x >= 2 ** z or y >= 2 ** z:
            self.send_error(404, "No such tile")
            return
        path = tiles.get(z, x, y)
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
        if not self.app.vendor or not rel or any(p in ("", ".", "..") or "\\" in p for p in parts):
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


def _lan_ip() -> Optional[str]:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))   # no packet is sent; this just picks the outgoing interface
            return s.getsockname()[0]
    except Exception:
        return None


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Serve your trip photos/videos as a live website, generated on the fly."
    )
    parser.add_argument("--version", action="version", version=f"AlbumMaker {__version__}")
    parser.add_argument("-i", "--input", type=Path, default=None,
                        help="Folder with your photos/videos (default: current folder)")
    parser.add_argument("--library", type=Path, metavar="LIST",
                        help="Serve several trips: a page at / linking them all, each trip under its own "
                             "/name/. LIST is a text file with one photos folder per line. See README.md.")
    parser.add_argument("--library-title", type=library_title_arg, default="Trips", metavar="TITLE",
                        help="Title shown on the overview page created with --library (default: Trips).")
    add_content_arg(parser)
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
    add_timezone_arg(parser)
    parser.add_argument("--no-dedupe", action="store_true",
                        help="Don't skip exact-duplicate files. Duplicate detection is on by default.")
    parser.add_argument("--no-live-photos", action="store_true",
                        help="Show iPhone Live Photo clips as separate videos instead of "
                             "attaching them to their photos.")
    add_date_check_args(parser)
    add_location_args(parser)
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
    parser.add_argument("--tile-attribution", metavar="TEXT",
                        help="The credit shown in the corner of the maps, as plain text, e.g. "
                             "\"Map tiles: Example Maps, data: OpenStreetMap contributors\". Default: the "
                             "OpenStreetMap credit, or \"Map tiles: <host>\" with --tile-url. Use the "
                             "wording your tile provider asks for.")
    parser.add_argument("--read-only", action="store_true",
                        help="Turn off in-page editing (captions, favorites, hiding, covers, day text). "
                             "Nothing is written to trip_content.json at all, not even at startup.")
    parser.add_argument("--editor-link-only", action="store_true",
                        help="Don't make the computer running the server an editor automatically: "
                             "editing only works in a browser that opened the editor link printed at "
                             "startup. Use this behind a reverse proxy or tunnel.")
    parser.add_argument("--allow-host", action="append", default=[], metavar="NAME",
                        help="Extra host name the server should answer to, e.g. a custom local DNS "
                             "name for this computer. IP addresses, 'localhost' and this computer's "
                             "own name (also with .local) always work. Repeatable.")
    parser.add_argument("--memory-cache-mb", type=int, default=512, metavar="MB",
                        help="Memory for thumbnails and converted photos kept between requests "
                             "(default 512).")
    parser.add_argument("--index-cache", type=Path, metavar="PATH",
                        help="Keep the photos' metadata for reuse on the next start. For one trip, "
                             "PATH is a cache file; with --library, it is a folder holding one "
                             "cache file per trip.")
    parser.add_argument("--verbose", action="store_true",
                        help="Log every request (normally only errors and slow requests are shown).")
    return parser


def tile_host_credit(tile_url: str) -> str:
    """The neutral credit for a --tile-url without --tile-attribution: the
    tiles' host name ("{s}." subdomain placeholders left out). Not the
    OpenStreetMap credit, which would be wrong for most other tile sources."""
    try:
        host = urlsplit(tile_url).netloc.rsplit("@", 1)[-1]
    except ValueError:
        host = ""
    host = re.sub(r"^(\{[a-z]\}\.)+", "", host)
    return f"Map tiles: {host}" if host else "Map tiles: custom source"


def main(argv=None):
    # the server runs until stopped, so when its output goes to a file or a
    # pipe (a log, a service manager), each message must appear as it is printed,
    # not when a block buffer happens to fill (the editor link would never show)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)
    parser = build_parser()
    args = parser.parse_args(argv)
    print(f"AlbumMaker {__version__} (live server)")
    if args.tz_offset is not None and args.timezone:
        parser.error("--tz-offset and --timezone cannot be used together")
    date_range = resolve_date_range(args, parser)
    if args.large_thumb_size is None:             # not given: on, unless the normal thumbnails are already that big
        args.large_thumb_size = 1600 if args.thumb_size < 1600 else 0
    elif args.large_thumb_size and args.large_thumb_size <= args.thumb_size:
        parser.error("--large-thumb-size must be larger than --thumb-size (or 0 to turn it off)")
    if args.memory_cache_mb < 16:
        parser.error("--memory-cache-mb must be at least 16")
    if args.fetch_offline_assets:
        sys.exit(fetch_offline_assets())

    tile_upstream = tile_client_url = DEFAULT_TILE_URL
    if args.tile_url:
        if not (args.tile_url.startswith(("http://", "https://")) and all(
                part in args.tile_url for part in ("{z}", "{x}", "{y}"))):
            parser.error("--tile-url must start with http:// or https:// and contain {z}, {x} and {y}")
        tile_upstream = tile_client_url = args.tile_url
    tile_attribution = None              # None: the pages show the OpenStreetMap credit
    if args.tile_attribution is not None:
        tile_attribution = " ".join(args.tile_attribution.split())
        if not tile_attribution or len(tile_attribution) > 300:
            parser.error("--tile-attribution must be some text, at most 300 characters")
    elif args.tile_url:
        tile_attribution = tile_host_credit(args.tile_url)
    tiles = None
    if args.tile_cache:
        try:
            args.tile_cache.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            parser.error(f"can't use --tile-cache {args.tile_cache}: {e}")
        tiles = TileCache(args.tile_cache, tile_upstream)
        tile_client_url = "/tiles/{z}/{x}/{y}.png"   # the pages ask this server, which keeps the tiles

    if args.library and args.input is not None:
        parser.error("give either -i/--input (one trip) or --library (several), not both")
    if args.input is None and not args.library:
        args.input = Path(".")
    if args.library:
        problem = check_library_args(args)
        if args.index_cache and args.index_cache.exists() and not args.index_cache.is_dir():
            problem = (f"with --library, --index-cache is a folder (one file per trip in it), "
                       f"but {args.index_cache} is a file")
        if problem:
            parser.error(problem)
    if not HEIF_SUPPORT:
        print("Note: pi-heif not installed — .heic/.heif files will be skipped.\n"
              "      Install with: pip install pi-heif", file=sys.stderr)

    hostname = socket.gethostname().lower().rstrip(".")
    allowed = {"localhost", hostname, hostname.split(".")[0] + ".local"}
    allowed |= {h.lower().rstrip(".") for h in args.allow_host}
    shared = Shared(args, tiles=tiles, tile_client_url=tile_client_url, vendor=vendor_ready(),
                    allowed_hostnames=allowed, tile_attribution=tile_attribution)

    library = None
    if args.library:
        library = load_library(args, shared)
        app = next(iter(library.trips.values()))
        content_files = [str(a.content.path) for a in library.trips.values()]
    else:
        try:
            check_ffmpeg()
            app = load_trip(args, date_range, shared, index_cache=args.index_cache)
        except TripLoadError as e:
            print(e, file=sys.stderr)
            sys.exit(1)
        content_files = [str(app.content.path)]

    host = "0.0.0.0" if args.lan else "127.0.0.1"
    try:
        server = TripHTTPServer((host, args.port), RequestHandler, app, library=library)
    except OSError as e:
        if e.errno in (errno.EADDRINUSE, 10048):  # 10048: the Windows code
            print(f"Port {args.port} is already in use. Is the server already running? "
                  f"Otherwise pick another port, e.g. --port {args.port + 1}", file=sys.stderr)
        else:
            print(f"Could not start the server on {host}:{args.port}: {e}", file=sys.stderr)
        sys.exit(1)

    writes = [] if args.read_only else (content_files if not library else
                                        [f"each trip's trip_content.json ({', '.join(content_files)})"])
    writes += [f"the map tile cache in {args.tile_cache}"] if tiles else []
    writes += [f"the index cache {args.index_cache}"] if args.index_cache else []
    print("\nThis server generates every page and image on the fly, in memory. It writes to disk "
          + (("only " + " and ".join(writes)) if writes else "nothing") + ".")
    if args.large_thumb_size:
        print(f"Larger thumbnails ({args.large_thumb_size}px) are loaded only where the screen can use "
              f"them (L/XL sizes, high-resolution screens); --large-thumb-size 0 turns that off.")
    if shared.vendor:
        print(f"Maps and fonts: using the local copies in {VENDOR_DIR} (no internet needed for them).")
    else:
        print("Maps and fonts load from the internet. To make them work offline, run once while "
              "online:  python album_server.py --fetch-offline-assets")
    if args.tile_url and args.tile_attribution is None:
        print(f"The maps credit their tiles as \"{tile_attribution}\". Your tile provider likely asks for "
              f"its own wording (and the map data's, e.g. OpenStreetMap): set it with --tile-attribution.")
    if tiles is not None:
        cached = sum(1 for _ in args.tile_cache.rglob("*.png"))
        print(f"Map tiles are cached in {args.tile_cache} ({cached} so far); browse the maps while online "
              f"and those areas keep working offline.")
    lan_ip = _lan_ip() if args.lan else None
    if not args.read_only:
        where = ("in a browser that has opened the editor link below" if args.editor_link_only
                 else "on this computer, and in a browser that has opened the editor link below")
        saved = "each trip's own trip_content.json" if library else content_files[0]
        print(f"Editing works {where}: captions, favorites, hiding, cover photos and day text are "
              f"saved into {saved}. Use --read-only to turn it off.")
        link_host = lan_ip if lan_ip else "127.0.0.1"
        print(f"Editor link (keep it private; it changes every start): "
              f"http://{link_host}:{args.port}/?key={shared.editor_token}")
    print(f"\nServing at http://127.0.0.1:{args.port}/")
    if library:
        for name, trip in library.trips.items():
            print(f"  {library.titles[name]}:  http://127.0.0.1:{args.port}/{name}/")
    if args.lan:
        if lan_ip:
            print(f"Also reachable on your network at: http://{lan_ip}:{args.port}/")
        print("(anyone on your network can open this while the server is running — there's no login)")
    if not args.read_only and not args.editor_link_only:
        print("Note: a tunnel or port forward (ssh -R, VS Code port forwarding, ...) reaches this server from "
              "this computer, so its visitors count as editors unless it adds proxy headers; use "
              "--editor-link-only with one.")
    print("Press Ctrl+C to stop.\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        server.shutdown()
        server.server_close()


class TripLoadError(Exception):
    """A trip that can't be served; the message says why (and was printed as is before)."""


def load_trip(args, date_range, shared: "Shared", prefix: str = "", index_cache: Optional[Path] = None,
              content_fallback: Optional[Path] = None, title: Optional[str] = None) -> App:
    """Index one trip and set it up for serving under `prefix` ("" for a
    single trip, "/italy" in a library)."""
    if not args.input.is_dir():
        raise TripLoadError(f"Input folder not found: {args.input}")
    content_path = resolve_content_path(args.content, args.input, fallback=content_fallback)
    # check the content file *before* the (possibly long) indexing step, so
    # a typo is reported immediately and never leads to the file being rewritten
    if content_path.exists():
        try:
            parse_content_file(content_path)
        except ContentFileError as e:
            raise TripLoadError(f"! {e}\n  Nothing was changed. Fix the file and start the server again.")

    print(f"Indexing {args.input} ...")
    cache = MetadataCache(index_cache, args.tz_offset, args.timezone)
    items = build_index(args.input, args.tz_offset, dedupe=not args.no_dedupe,
                        live_photos=not args.no_live_photos, cache=cache, timezone_name=args.timezone)
    if not items:
        raise TripLoadError("No supported photos or videos found.")

    if not args.no_date_check:
        apply_date_check(items, date_range)
    located = locate_items(items, args)
    fallback_count = sum(1 for it in items if it.dt_is_fallback)
    gps_count = sum(1 for it in items if it.has_gps)
    print(f"\nFound {len(items)} media files "
          f"({fallback_count} without a usable capture date, filed under Unsorted; "
          f"{gps_count} with GPS location).")

    days = group_by_day(items)
    describe_days(days, items, located, args)
    print(f"Grouped into {len(days)} day(s).")

    try:
        content = ContentStore(content_path, [d.date_key for d in days], write=not args.read_only, title=title)
    except ContentFileError as e:
        raise TripLoadError(f"! {e}\n  Nothing was changed. Fix the file and start the server again.")
    report_unknown_photo_keys(content.get(), {k for it in items for k in it.content_keys})
    return App(args, items, days, content, shared=shared, prefix=prefix)


def load_library(args, shared: "Shared") -> "Library":
    """Every trip in the --library list that can be loaded; exits if none can."""
    try:
        entries = read_trip_list(args.library)
    except TripListError as e:
        print(f"! {e}", file=sys.stderr)
        sys.exit(1)
    if args.index_cache:
        try:
            args.index_cache.mkdir(parents=True, exist_ok=True)   # a folder: one cache file per trip
        except OSError as e:
            print(f"! can't use --index-cache {args.index_cache}: {e}", file=sys.stderr)
            sys.exit(1)
    list_dir = Path(args.library).expanduser().resolve().parent
    check_ffmpeg()
    trips, titles = {}, {}
    for n, entry in enumerate(entries, 1):
        print(f"\n=== Trip {n} of {len(entries)}: {entry.path}  ->  /{entry.name}/ ===")
        try:
            problem = missing_folder(entry, args.library)
            if problem:
                raise TripLoadError(problem)
            trip, date_range = trip_args(args, entry, args.library)
            trips[entry.name] = load_trip(
                trip, date_range, shared, prefix=f"/{entry.name}",
                index_cache=args.index_cache / f"{entry.name}.json" if args.index_cache else None,
                content_fallback=list_dir / f"trip_content-{entry.name}.json", title=entry.path.name)
            titles[entry.name] = entry.path.name
        except TripListError as e:
            print(f"! {args.library}, line {entry.line}: {e}\n  This trip is left out.", file=sys.stderr)
        except TripLoadError as e:
            print(f"{e}\n  This trip is left out.", file=sys.stderr)
        except Exception:      # anything else: the other trips are still served
            traceback.print_exc()
            print(f"! {entry.path} could not be loaded (see above). This trip is left out.", file=sys.stderr)
    if not trips:
        print("! None of the trips could be loaded.", file=sys.stderr)
        sys.exit(1)
    return Library(trips, titles, args.library_title)
