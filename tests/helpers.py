"""Shared building blocks for the test suite.

Everything the tests need is created on the fly in temporary folders: small
JPEGs with chosen EXIF dates and GPS, short videos made with ffmpeg, a copy of
the three scripts to run from, and a live server on a free port. Nothing
touches your real photos, trip_content.json or the project's vendor folder.
"""
from __future__ import annotations

import http.client
import http.server
import json
import os
import shutil
import socket
import socketserver
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from albummaker import cli, content, metadata, model, offline, render, scan, util  # noqa: E402

tc = SimpleNamespace(
    CAPTION_MAX=content.CAPTION_MAX,
    ContentFileError=content.ContentFileError,
    DayGroup=model.DayGroup,
    LIVE_CLIP_MAX_SECONDS=metadata.LIVE_CLIP_MAX_SECONDS,
    MediaItem=model.MediaItem,
    SITE_MARKER=scan.SITE_MARKER,
    UNKNOWN_DAY_KEY=model.UNKNOWN_DAY_KEY,
    _VENDOR_FONT_WEIGHTS=offline._VENDOR_FONT_WEIGHTS,
    _font_faces=offline._font_faces,
    _parse_iso_datetime=metadata._parse_iso_datetime,
    _route_distance_km=model.route_distance_km,
    add_date_check_args=cli.add_date_check_args,
    apply_date_check=scan.apply_date_check,
    assign_rel_paths=scan.assign_rel_paths,
    atomic_write_bytes=util.atomic_write_bytes,
    atomic_write_text=util.atomic_write_text,
    collect_candidates=scan.collect_candidates,
    day_text=content.day_text,
    dedupe_candidates=scan.dedupe_candidates,
    fast_fingerprint=scan.fast_fingerprint,
    fetch_offline_assets=offline.fetch_offline_assets,
    find_suspicious_dates=scan.find_suspicious_dates,
    format_altitude=metadata.format_altitude,
    format_camera=metadata.format_camera,
    format_duration=metadata.format_duration,
    format_exposure=metadata.format_exposure,
    format_fnumber=metadata.format_fnumber,
    format_focal=metadata.format_focal,
    format_gps_display=metadata.format_gps_display,
    format_iso=metadata.format_iso,
    format_long_date=scan.format_long_date,
    group_by_day=scan.group_by_day,
    hidden_rels=content.hidden_rels,
    human_filesize=metadata.human_filesize,
    is_ignored_path=scan.is_ignored_path,
    is_live_photo_clip=metadata.is_live_photo_clip,
    list_media_files=scan.list_media_files,
    pair_live_photos=scan.pair_live_photos,
    parse_content_file=content.parse_content_file,
    photo_entry=content.photo_entry,
    pick_cover=render.pick_cover,
    positive_thumb_size=cli.positive_thumb_size,
    report_unknown_photo_keys=content.report_unknown_photo_keys,
    resolve_date_range=cli.resolve_date_range,
    set_cover=content.set_cover,
    set_day_text=content.set_day_text,
    set_photo_fields=content.set_photo_fields,
    trip_title=content.trip_title,
    valid_gps=metadata.valid_gps,
    vendor_ready=offline.vendor_ready,
    visible_days=scan.visible_days,
)

SCRIPTS = ("album_server.py", "generate_site.py")
HAVE_FFMPEG = shutil.which("ffmpeg") is not None
needs_ffmpeg = unittest.skipUnless(HAVE_FFMPEG, "ffmpeg is not installed")


# ---------------------------------------------------------------- fixtures

def _dms(value: float):
    value = abs(value)
    d = int(value)
    m = int((value - d) * 60)
    s = round(((value - d) * 60 - m) * 60)
    return (d, m, s)


def make_jpeg(path: Path, when: str | None = "2026:05:01 09:00:00", gps=None,
              size=(320, 240), color=(90, 120, 200), offset: str | None = None) -> Path:
    """A small JPEG. `when` is the EXIF capture time ('YYYY:MM:DD HH:MM:SS',
    or None for no date at all); `gps` is (lat, lon) in decimal degrees;
    `offset` is the EXIF OffsetTimeOriginal of `when`, e.g. '+02:00'."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    exif = Image.Exif()
    if when:
        exif[306] = when
        exif.get_ifd(0x8769)[36867] = when
        if offset:
            exif.get_ifd(0x8769)[36881] = offset
    if gps:
        g = exif.get_ifd(0x8825)
        g[1], g[2] = ("N" if gps[0] >= 0 else "S"), _dms(gps[0])
        g[3], g[4] = ("E" if gps[1] >= 0 else "W"), _dms(gps[1])
    Image.new("RGB", size, color).save(path, "JPEG", exif=exif)
    return path


def make_video(path: Path, creation_time: str | None = None, seconds: float = 2,
               size: str = "160x120") -> Path:
    """A tiny test-pattern video (needs ffmpeg)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-v", "quiet", "-y", "-f", "lavfi",
           "-i", f"testsrc=duration={seconds}:size={size}:rate=15", "-pix_fmt", "yuv420p"]
    if creation_time:
        cmd += ["-metadata", f"creation_time={creation_time}"]
    subprocess.run(cmd + [str(path)], check=True)
    return path


def make_gpx(path: Path, segments, version: str = "1.1") -> Path:
    """A GPX file: `segments` is a list of lists of (UTC 'YYYY-MM-DDTHH:MM:SSZ', lat, lon)."""
    ns = {"1.1": "http://www.topografix.com/GPX/1/1", "1.0": "http://www.topografix.com/GPX/1/0"}[version]
    segs = "".join(
        "<trkseg>" + "".join(f'<trkpt lat="{lat}" lon="{lon}"><ele>10</ele><time>{t}</time></trkpt>'
                             for t, lat, lon in seg) + "</trkseg>"
        for seg in segments)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'<?xml version="1.0" encoding="UTF-8"?>\n<gpx version="{version}" creator="test" '
                    f'xmlns="{ns}"><trk><name>t</name>{segs}</trk></gpx>', encoding="utf-8")
    return path


def standard_trip(photos: Path) -> dict:
    """Two days, five photos. Day 1 (2026-05-01): a1, a2 (both with GPS) and
    sub/a3 (no GPS). Day 2 (2026-05-02): b1 (GPS) and b2."""
    photos = Path(photos)
    return {
        "a1": make_jpeg(photos / "a1.jpg", "2026:05:01 09:00:00", (50, 4), color=(200, 60, 60)),
        "a2": make_jpeg(photos / "a2.jpg", "2026:05:01 10:00:00", (51, 4), color=(60, 200, 60)),
        "a3": make_jpeg(photos / "sub" / "a3.jpg", "2026:05:01 11:00:00", None, color=(60, 60, 200)),
        "b1": make_jpeg(photos / "b1.jpg", "2026:05:02 09:00:00", (50, 5), color=(200, 200, 60)),
        "b2": make_jpeg(photos / "b2.jpg", "2026:05:02 10:00:00", None, color=(60, 200, 200)),
    }


def install_app(dest: Path, with_vendor: Path | None = None) -> Path:
    """Copy the launchers and package into an isolated test installation."""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    for name in SCRIPTS:
        shutil.copy2(ROOT / name, dest / name)
    shutil.copytree(ROOT / "albummaker", dest / "albummaker",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    if with_vendor is not None:
        shutil.copytree(
            with_vendor,
            dest / ".albummaker-data" / "offline-assets",
        )
    return dest


def write_json(path: Path, data) -> Path:
    Path(path).write_text(json.dumps(data, indent=2), encoding="utf-8")
    return Path(path)


def read_json(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


class TempDirMixin:
    """A fresh temporary folder per test class, removed afterwards."""
    @classmethod
    def make_tmp(cls) -> Path:
        d = Path(tempfile.mkdtemp(prefix="albummaker-test-"))
        cls.addClassCleanup(shutil.rmtree, d, True)
        return d


# ---------------------------------------------------------------- running things

class QuietHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    """A small web server for tests (fake registry, fake tile provider, static site).
    It stays silent when the other side hangs up mid-answer, which the tests do on purpose."""
    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request, client_address):
        pass


def local_env(config_dir: Path | None = None) -> dict:
    """The environment for a child process, with local addresses exempt from any proxy."""
    env = dict(os.environ)
    env["no_proxy"] = env["NO_PROXY"] = "127.0.0.1,localhost"
    if config_dir is not None:
        env["ALBUMMAKER_CONFIG_DIR"] = str(config_dir)
    return env


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def run_script(app: Path, name: str, *args, cwd: Path | None = None,
               timeout: int = 180) -> subprocess.CompletedProcess:
    """Run one of the scripts as a user would, from a copy in `app`."""
    return subprocess.run([sys.executable, str(Path(app) / name), *map(str, args)],
                          cwd=str(cwd or app), capture_output=True, text=True, timeout=timeout,
                          env=local_env(Path(app) / ".albummaker-data"))


class AlbumMaker:
    """album_server.py running for real on a free port.

        server = AlbumMaker(app, photos, work, "--read-only")
        server.start()          # waits until it answers
        server.get("/")         # -> (status, headers, body)
        server.stop()
    """

    def __init__(self, app: Path, photos: Path, work: Path, *extra, content="content.json"):
        self.app, self.work = Path(app), Path(work)
        self.photos = None if photos is None else Path(photos)
        self.port = free_port()
        self.content = self.work / content
        self.extra = [str(e) for e in extra]
        self.log_path = self.work / f"server-{self.port}.log"
        self.proc = None

    def start(self, timeout: float = 30):
        self.work.mkdir(parents=True, exist_ok=True)
        with open(self.log_path, "w", encoding="utf-8") as log:      # the child keeps its own copy open
            # photos=None: no -i/-c (e.g. --library in `extra`)
            trip = [] if self.photos is None else ["-i", str(self.photos), "-c", str(self.content)]
            self.proc = subprocess.Popen(
                [sys.executable, str(self.app / "album_server.py"), *trip, "--port", str(self.port), *self.extra],
                cwd=str(self.work), stdout=log, stderr=subprocess.STDOUT, text=True,
                env=local_env(self.app / ".albummaker-data"))
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f"the server exited early:\n{self.log()}")
            try:
                self.request("GET", "/", timeout=2)
                return self
            except OSError:
                time.sleep(0.2)
        self.stop()
        raise RuntimeError(f"the server did not start in {timeout}s:\n{self.log()}")

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    def log(self) -> str:
        try:
            return self.log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def request(self, method: str, path: str, headers: dict | None = None,
                body: bytes | None = None, timeout: float = 30):
        """Sent exactly as written (no client-side tidying of the path)."""
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=timeout)
        try:
            conn.request(method, path, body=body, headers=headers or {})
            resp = conn.getresponse()
            return resp.status, {k.lower(): v for k, v in resp.getheaders()}, resp.read()
        finally:
            conn.close()

    def get(self, path, headers=None):
        return self.request("GET", path, headers)

    def text(self, path, headers=None) -> str:
        return self.get(path, headers)[2].decode("utf-8", "replace")

    def post_json(self, path: str, obj, headers: dict | None = None):
        h = {"Content-Type": "application/json"}
        h.update(headers or {})
        status, hdrs, body = self.request("POST", path, h, json.dumps(obj).encode())
        try:
            return status, json.loads(body)
        except ValueError:
            return status, None


def item_ids(server: AlbumMaker, day: str) -> list:
    """The URL keys a day page uses for its photos, in page order."""
    import re
    html = server.text(f"/day/{day}")
    return re.findall(r'class="thumb[^"]*"[^>]*>\s*<img src="/thumb/([0-9a-f]+)\?v=', html)


# ---------------------------------------------------------------- shared page helpers

import re as _re


def slides_of(html: str) -> list:
    """The slideshow data embedded in a day page (what the viewer's JavaScript gets)."""
    m = _re.search(r"initSlideshow\((\[.*?\]), \{editable", html, _re.S)
    if not m:
        raise AssertionError("no slideshow data found in the page")
    return json.loads(m.group(1))


def slide_for(html: str, filename: str) -> dict:
    for s in slides_of(html):
        if s.get("detail", {}).get("filename") == filename:
            return s
    raise AssertionError(f"{filename} is not in the page's slideshow")


def card_covers(index_html: str) -> dict:
    """{'2026-05-01': 'thumbs/a1.jpg', ...}: which thumbnail each day card uses."""
    return {m.group(1): m.group(2) for m in _re.finditer(
        r'<a class="day-card" href="days/([^"]+)\.html">\s*<div class="cover" '
        r"style=\"background-image:url\('([^']+)'\)\"", index_html)}


def make_fake_vendor(path: Path) -> Path:
    """A stand-in `vendor` folder (the real one comes from --fetch-offline-assets)."""
    path = Path(path)
    for rel, text in {
        "leaflet/leaflet.js": "/* fake leaflet */ window.L = window.L || {};",
        "leaflet/leaflet.css": ".leaflet-control-layers-toggle{background-image:url(images/layers.png)}",
        "leaflet/images/layers.png": "png",
        "fonts/fonts.css": "@font-face { font-family: 'Fraunces'; src: url(f.woff2) format('woff2'); }",
        "fonts/f.woff2": "font",
        "licenses/leaflet-LICENSE.txt": "BSD-2-Clause",
        "VERSIONS.txt": "fake",
    }.items():
        p = path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return path


def size_toggle(html: str, target: str):
    """(storage key, [(size, label), ...]) of the S/M/L/XL control that resizes `target`, or None."""
    m = _re.search(r'<div class="size-toggle"[^>]*data-target="%s" data-store="(\w+)">(.*?)</div>' % _re.escape(target),
                   html, _re.S)
    if not m:
        return None
    return m.group(1), _re.findall(r'data-size="(\w+)">(\w+)</button>', m.group(2))


def day_nav_args(html: str):
    """The three arguments the day page gives initDayNav: (previous, next, where Esc leads)."""
    m = _re.search(r"initDayNav\((.*?), (.*?), (\"[^\"]*\")\);", html)
    if not m:
        raise AssertionError("initDayNav is not called in the page")
    return tuple(json.loads(g) for g in m.groups())


def css_rule(css: str, selector: str) -> str:
    m = _re.search(_re.escape(selector) + r"\{([^}]*)\}", css)
    if not m:
        raise AssertionError(f"no CSS rule for {selector}")
    return m.group(1)
