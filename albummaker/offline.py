# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Pieter Remi Coppens
"""Working without internet: checksum-verified local Leaflet and font assets."""

from __future__ import annotations

import base64
import hashlib
import io
import os
import re
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Optional

from .app_paths import offline_assets_dir
from .util import atomic_write_bytes

# --------------------------------------------------------------------------
# Working without internet
#
# The pages use Leaflet (the map library), three web fonts and map tiles. By
# default all of that comes from the internet. To make the pages independent
# of it, run this once while you're online:
#
#     python album_server.py --fetch-offline-assets
#
# That saves Leaflet and the fonts in AlbumMaker's per-user data directory,
# and from then on both scripts use those local copies. Map tiles can be
# cached too (album_server.py --tile-cache DIR), and wherever no tile is
# available the maps show a rough outline of the continents instead of
# nothing, so the points on them still make sense.
# --------------------------------------------------------------------------

VENDOR_DIR = offline_assets_dir()
DEFAULT_TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"

_NPM_REGISTRY = "https://registry.npmjs.org"

# Exact versions, each with the SHA-512 that npm publishes for it, so what
# ends up in the offline-assets folder is exactly what was reviewed: a download that doesn't
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


# The desktop apps carry a copy of these files (tools/build_executable.py
# downloads and checks them at build time), so the live server needs no
# download on its first start. The copy lives in this folder inside the app.
BUNDLED_ASSETS_FOLDER = "offline-assets"


def bundled_assets_dir() -> Optional[Path]:
    """The copy of the offline assets inside a packaged app, or None (running
    from source, or a build without them)."""
    base = getattr(sys, "_MEIPASS", None)
    if not getattr(sys, "frozen", False) or not base:
        return None
    folder = Path(base) / BUNDLED_ASSETS_FOLDER
    return folder if vendor_ready(folder) else None


def install_bundled_assets(vendor_dir: Optional[Path] = None) -> bool:
    """Copy the app's own offline assets into the per-user folder when that
    folder is not complete yet. True when it copied them. Never raises: if it
    can't, the assets are simply downloaded as before."""
    vendor_dir = Path(vendor_dir or VENDOR_DIR)
    source = bundled_assets_dir()
    if source is None or vendor_ready(vendor_dir):
        return False
    try:
        files = [f for f in sorted(source.rglob("*")) if f.is_file()]
        # VERSIONS.txt last: until it is there, the folder never counts as ready
        files.sort(key=lambda f: f.name == "VERSIONS.txt")
        for f in files:
            atomic_write_bytes(vendor_dir / f.relative_to(source), f.read_bytes())
    except OSError as e:
        print(f"Could not copy the included map library and fonts ({e}); "
              "they will be downloaded instead.", file=sys.stderr)
        return False
    return vendor_ready(vendor_dir)


# A slow or stalling network must never leave the download hanging without a
# word: each network step may take at most _DOWNLOAD_TIMEOUT seconds, a whole
# package at most _DOWNLOAD_DEADLINE, and a failed or stalled try is repeated.
_DOWNLOAD_TIMEOUT = 20
_DOWNLOAD_DEADLINE = 120
_DOWNLOAD_ATTEMPTS = 3
_RETRY_PAUSE = 3
_PROGRESS_EVERY = 5
_MAX_PACKAGE_BYTES = 60_000_000


def _read_url(url: str) -> bytes:
    """One try at downloading `url`, in chunks, with a progress line every few
    seconds; raises TimeoutError when it stalls or takes too long."""
    req = urllib.request.Request(url, headers={"User-Agent": "AlbumMaker-offline-assets/1.0"})
    start = last_report = time.monotonic()
    with urllib.request.urlopen(req, timeout=_DOWNLOAD_TIMEOUT) as resp:
        expected = int(resp.headers.get("Content-Length") or 0)
        chunks, received = [], 0
        while True:
            chunk = resp.read(64 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
            received += len(chunk)
            if received > _MAX_PACKAGE_BYTES:
                raise RuntimeError("the download is far larger than expected")
            now = time.monotonic()
            if now - start > _DOWNLOAD_DEADLINE:
                raise TimeoutError(f"it took longer than {_DOWNLOAD_DEADLINE} seconds")
            if now - last_report >= _PROGRESS_EVERY:
                size = f" of {expected / 1024:.0f}" if expected else ""
                print(f"  ... {received / 1024:.0f}{size} KB", flush=True)
                last_report = now
    if expected and received < expected:
        raise OSError(f"the connection closed after {received} of {expected} bytes")
    return b"".join(chunks)


def _download(url: str) -> bytes:
    """_read_url, tried up to _DOWNLOAD_ATTEMPTS times on network trouble."""
    for attempt in range(1, _DOWNLOAD_ATTEMPTS + 1):
        try:
            return _read_url(url)
        except (urllib.error.URLError, OSError, TimeoutError) as e:   # incl. socket timeouts
            if attempt == _DOWNLOAD_ATTEMPTS:
                raise
            reason = getattr(e, "reason", None) or e
            print(f"  The download did not work ({reason}); trying again "
                  f"({attempt + 1} of {_DOWNLOAD_ATTEMPTS}) ...", flush=True)
            time.sleep(_RETRY_PAUSE * attempt)
    raise AssertionError("unreachable")


def _download_package(key: str, wanted: set) -> dict:
    """Download one pinned npm package, check it against its published
    checksum, and return {path inside the package: bytes} for the wanted files."""
    name, version, integrity = _VENDOR_PACKAGES[key]
    url = f"{_NPM_REGISTRY}/{name}/-/{name.split('/')[-1]}-{version}.tgz"
    data = _download(url)
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


def fetch_offline_assets(vendor_dir: Optional[Path] = None) -> int:
    """Download Leaflet and the fonts into the user data directory by default."""
    vendor_dir = Path(vendor_dir or VENDOR_DIR)
    files = {}   # path inside vendor -> bytes
    try:
        print("Downloading Leaflet ...", flush=True)
        package = _download_package("leaflet", set(_LEAFLET_FILES))
        for source, target in _LEAFLET_FILES.items():
            files[target] = package[source]

        css = ["/* Local copies of the fonts, saved by --fetch-offline-assets. */"]
        for slug, weights in _VENDOR_FONT_WEIGHTS.items():
            print(f"Downloading the {slug} font ...", flush=True)
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
                        help="Download Leaflet and the fonts into AlbumMaker's user data folder "
                             "(needs internet, once), then exit. Afterwards the pages load them "
                             "from there instead of from the internet.")
