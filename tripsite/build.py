"""
generate_site: build a static, browsable website from a folder of trip
photos and videos.

Reads EXIF (and, for video, container metadata) to find when and where each
file was captured, groups everything into day "chapters", and renders:

  - index.html        overview map of the whole route + trip title + day list
  - days/<date>.html   one page per day: description, map of that day's route,
                       thumbnail grid, and a full-screen keyboard slideshow

Titles, descriptions and photo notes live in trip_content.json (in the
photos folder by default), which this script only ever *adds to*.
"""

from __future__ import annotations

import argparse
import json
import traceback
import os
import re
import shutil
import sys
import time
import unicodedata
from concurrent.futures import ProcessPoolExecutor, as_completed
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from .cli import (add_content_arg, add_date_check_args, add_location_args, add_offline_args, large_thumb_size_arg,
                  positive_thumb_size, resolve_date_range)
from .content import (ContentFileError, hidden_rels, load_or_init_content, parse_content_file,
                      report_unknown_photo_keys, resolve_content_path)
from .imaging import display_jpeg, image_thumbnails, open_upright, thumbnails_from, video_thumbnails
from .locations import describe_days, locate_items
from .metadata import HEIF_SUPPORT, check_ffmpeg, meta_from_json, meta_to_json, read_metadata
from .model import BROWSER_NATIVE_IMAGE_EXTS, VIDEO_EXTS, StaticOutputs
from .offline import VENDOR_DIR, fetch_offline_assets, vendor_ready
from .library import (TripListError, build_library_context, check_library_args, missing_folder, read_trip_list,
                      trip_args, trip_summary)
from .places import ATTRIBUTION as PLACES_ATTRIBUTION
from .render import (CSS, DAY_TEMPLATE, INDEX_TEMPLATE, JS, LIBRARY_TEMPLATE, SiteUrls, build_day_context,
                     build_index_context)
from .scan import (LIBRARY_MARKER, SITE_MARKER, apply_date_check, assign_rel_paths, build_media_item, collect_candidates,
                   dedupe_groups, drop_hidden_groups, group_by_day, no_dedupe_groups, pair_live_photos)
from .util import atomic_write_bytes, atomic_write_text

# --------------------------------------------------------------------------
# Output names
# --------------------------------------------------------------------------

ILLEGAL_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
JPEG_EXTS = (".jpg", ".jpeg")

# Build record (SITE_MARKER) kept in the output folder. Per source file it
# remembers the output name it was given, the outputs made for it, the
# source's size and modification time when they were made, and its
# metadata. That lets a re-run keep every file's output name stable,
# regenerate exactly the files that changed, skip re-reading unchanged
# files, and delete the outputs of files that were removed.
MANIFEST_VERSION = 1


def _natural_stem(path: Path) -> str:
    stem = ILLEGAL_FILENAME_CHARS.sub("_", path.stem).rstrip(" .") or "file"
    return stem[:180]  # defensive cap well under filesystem limits


def _claims(path: Path, base: str) -> set:
    """The (lower-case) names a file with this base may occupy in media/:
    its own, and for a .jpg also '<base>_display', the name its display copy
    gets if it turns out to be a multi-image JPEG. Claiming both up front
    means no other file can ever be given a clashing name."""
    names = {base.lower()}
    if path.suffix.lower() in JPEG_EXTS:
        names.add(f"{base}_display".lower())
    return names


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
       get '_2', '_3', ... appended, skipping names already taken."""
    taken = set()
    bases = {}
    for path, rel in rel_paths.items():
        prev = previous.get(rel)
        base = prev.get("base") if isinstance(prev, dict) else None
        if (isinstance(base, str) and base and not ILLEGAL_FILENAME_CHARS.search(base)
                and not (_claims(path, base) & taken)):
            bases[path] = base
            taken |= _claims(path, base)

    pending = []
    photos_first = sorted(rel_paths, key=lambda p: (p.suffix.lower() in VIDEO_EXTS, rel_paths[p]))
    for path in photos_first:
        if path in bases:
            continue
        stem = _natural_stem(path)
        if _claims(path, stem) & taken:
            pending.append(path)
        else:
            bases[path] = stem
            taken |= _claims(path, stem)

    for path in pending:
        stem = _natural_stem(path)
        n = 2
        while _claims(path, f"{stem}_{n}") & taken:
            n += 1
        bases[path] = f"{stem}_{n}"
        taken |= _claims(path, bases[path])
    return bases


def planned_outputs(path: Path, media_type: str, base: str, is_mpo: bool) -> StaticOutputs:
    """The output file names for one source file. Formats a browser shows
    directly keep the original's name and extension; HEIC/HEIF, TIFF and
    multi-image JPEGs get a converted .jpg for display plus an untouched copy
    of the original."""
    ext = path.suffix   # exact original case for output names
    if media_type == "video":
        return StaticOutputs(thumb_file=f"{base}.jpg", display_file=f"{base}{ext}")
    if ext.lower() in BROWSER_NATIVE_IMAGE_EXTS and not is_mpo:
        return StaticOutputs(thumb_file=f"{base}.jpg", display_file=f"{base}{ext}")
    display = f"{base}_display.jpg" if ext.lower() in JPEG_EXTS else f"{base}.jpg"
    return StaticOutputs(thumb_file=f"{base}.jpg", display_file=display, original_file=f"{base}{ext}")


def output_list(outputs: StaticOutputs, large: bool) -> list:
    """Paths (relative to the output folder) of everything made for a file."""
    out = [f"thumbs/{outputs.thumb_file}", f"media/{outputs.display_file}"]
    if large:
        out.append(f"thumbs_large/{outputs.thumb_file}")
    if outputs.original_file:
        out.append(f"media/{outputs.original_file}")
    return out


# --------------------------------------------------------------------------
# Build record
# --------------------------------------------------------------------------

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


def save_manifest(output_dir: Path, thumb_size: int, records: dict, pages: list, large_thumb_size: int = 0):
    data = {
        "version": MANIFEST_VERSION,
        "note": "Build record for generate_site.py. If it is deleted, the next run "
                "regenerates everything, but can no longer remove files from earlier runs.",
        "thumb_size": thumb_size,
        "large_thumb_size": large_thumb_size,
        "pages": sorted(pages),
        "files": records,
    }
    atomic_write_text(output_dir / SITE_MARKER, json.dumps(data, indent=1, ensure_ascii=False) + "\n")


def remove_stale_outputs(output_dir: Path, previous: dict, keep: set):
    """Delete outputs listed in the previous build record that the current
    site no longer uses (files removed from the input folder, duplicates,
    hidden photos, days that no longer exist). Only files the record itself
    lists are ever touched, and only inside the output folder."""
    old = set()
    for rec in (previous.get("files") or {}).values():
        if isinstance(rec, dict):
            old.update(o for o in rec.get("outputs", []) if isinstance(o, str))
    old.update(p for p in previous.get("pages", []) if isinstance(p, str))

    root = output_dir.resolve()
    twins = _kept_spellings(keep)
    removed = 0
    for rel in sorted(old - keep):
        try:
            target = (output_dir / rel).resolve()
        except OSError:
            continue
        if root not in target.parents or not target.is_file():
            continue
        # On a case-insensitive disk (macOS, Windows) an old "IMG_1.JPG" and a
        # new "IMG_1.jpg" are one and the same file: deleting the old name would
        # delete the new output. Give it the new name's spelling instead.
        twin = _kept_twin(output_dir, rel, twins)
        if twin is not None:
            try:
                os.replace(target, output_dir / twin)
            except OSError:
                pass
            continue
        try:
            target.unlink()
            removed += 1
        except OSError as e:
            print(f"  ! could not remove {target}: {e}", file=sys.stderr)
    if removed:
        print(f"Removed {removed} output file(s) the site no longer uses.")


def _same_file(a: Path, b: Path) -> bool:
    """The same file or folder on disk, however the two paths are spelled."""
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def _fold(rel: str) -> str:
    # how a case-insensitive, normalization-insensitive disk (macOS, Windows) compares names
    return unicodedata.normalize("NFC", rel).casefold()


def _kept_spellings(keep: set) -> dict:
    out = {}
    for k in keep:
        out.setdefault(_fold(k), []).append(k)
    return out


def _kept_twin(output_dir: Path, rel: str, spellings: dict) -> Optional[str]:
    """The kept output that `rel` is the very same file as, under another
    spelling (only possible on a disk that ignores case or normalization)."""
    for k in spellings.get(_fold(rel), ()):
        try:
            if os.path.samefile(output_dir / rel, output_dir / k):
                return k
        except OSError:
            pass
    return None


def report_unrecorded_outputs(output_dir: Path, keep: set, since: float):
    """Without a build record (deleted, unreadable, or from an older version)
    nothing can be cleaned up, since only recorded files are ever deleted. Say
    so when the site's folders hold files this build didn't make: they may be
    left over from earlier builds, including photos that are now hidden. Files
    written since `since` (part of a file that failed this time) don't count."""
    spellings = _kept_spellings(keep)
    extra = []
    for sub in ("days", "media", "thumbs", "thumbs_large"):
        folder = output_dir / sub
        if folder.is_dir():
            for p in folder.iterdir():
                rel = f"{sub}/{p.name}"
                if (p.is_file() and not p.name.startswith(".") and rel not in keep
                        and _kept_twin(output_dir, rel, spellings) is None
                        and not _written_since(p, since)):
                    extra.append(p)
    if extra:
        print(f"! {len(extra)} file(s) in {output_dir} are not part of this build, for example "
              f"{extra[0].relative_to(output_dir).as_posix()}. There was no build record to tell "
              f"whether an earlier run made them, so they were left alone. They may include photos "
              f"you have since hidden: if the folder holds only this site, delete it and build again.",
              file=sys.stderr)


def _written_since(path: Path, since: float) -> bool:
    # st_ctime, not st_mtime: copies of originals keep the source's mtime, but
    # writing or renaming a file always updates its ctime (its creation time on Windows)
    try:
        return path.stat().st_ctime >= since
    except OSError:
        return False


def _copy_is_current(src: Path, dst: Path) -> bool:
    """shutil.copy2 preserves size and modification time, so a copy whose
    size and mtime still match the source is up to date. (2 s tolerance for
    FAT/exFAT drives, which store mtimes at 2-second resolution.)"""
    try:
        s, d = src.stat(), dst.stat()
    except OSError:
        return False
    return s.st_size == d.st_size and abs(s.st_mtime - d.st_mtime) <= 2


def _copy_original(src: Path, dst: Path):
    """shutil.copy2 into a temp name, then rename: an interrupted copy never
    leaves a truncated file under the real name."""
    tmp = dst.with_name(f".{dst.name}.partial")
    try:
        shutil.copy2(src, tmp)
        os.replace(tmp, dst)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


# --------------------------------------------------------------------------
# Derivatives (run in worker processes)
# --------------------------------------------------------------------------

def make_image_derivatives(src: Path, out: StaticOutputs, output_dir: Path, thumb_size: int,
                           large_thumb_size: int, regen_thumb: bool, regen_converted: bool,
                           force_copy: bool):
    """Thumbnail(s) and the full-screen display version of one photo.

    The photo is decoded once. When a converted display JPEG is needed, it
    is encoded from the full image first; the thumbnails are then made by
    shrinking that same image in place (largest first), so at most one
    full-size copy of the pixels is ever held. When only thumbnails are
    needed, a JPEG is decoded at a reduced scale to begin with."""
    media_dir, thumbs_dir = output_dir / "media", output_dir / "thumbs"
    large_path = (output_dir / "thumbs_large" / out.thumb_file) if large_thumb_size else None
    thumb_path = thumbs_dir / out.thumb_file
    display_path = media_dir / out.display_file
    native = out.original_file is None

    thumb_needed = regen_thumb or not thumb_path.exists() or (large_path is not None and not large_path.exists())
    if native:
        display_needed = force_copy or not _copy_is_current(src, display_path)
    else:
        display_needed = regen_converted or not display_path.exists()
    convert = display_needed and not native

    if convert:
        img, exif = open_upright(src)
        try:
            atomic_write_bytes(display_path, display_jpeg(img, exif))
            if thumb_needed:
                thumbs = thumbnails_from(img, [large_thumb_size, thumb_size])
        finally:
            img.close()
    elif thumb_needed:
        thumbs = image_thumbnails(src, [large_thumb_size, thumb_size])
    if thumb_needed:
        atomic_write_bytes(thumb_path, thumbs[thumb_size])
        if large_path is not None:
            atomic_write_bytes(large_path, thumbs[large_thumb_size])

    if native and display_needed:
        _copy_original(src, display_path)
    if not native:
        original_path = media_dir / out.original_file
        if force_copy or not _copy_is_current(src, original_path):
            _copy_original(src, original_path)


def make_video_derivatives(src: Path, out: StaticOutputs, output_dir: Path, thumb_size: int,
                           large_thumb_size: int, regen_thumb: bool, force_copy: bool) -> bool:
    """Copy the video and save its preview frame(s). Returns True if a
    placeholder had to be used (no ffmpeg, or an undecodable stream)."""
    display_path = output_dir / "media" / out.display_file
    if force_copy or not _copy_is_current(src, display_path):
        _copy_original(src, display_path)
    thumb_path = output_dir / "thumbs" / out.thumb_file
    large_path = (output_dir / "thumbs_large" / out.thumb_file) if large_thumb_size else None
    if not (regen_thumb or not thumb_path.exists() or (large_path is not None and not large_path.exists())):
        return False
    thumbs, placeholder = video_thumbnails(src, [large_thumb_size, thumb_size])
    atomic_write_bytes(thumb_path, thumbs[thumb_size])
    if large_path is not None:
        atomic_write_bytes(large_path, thumbs[large_thumb_size])
    return placeholder


def _process_one_file(path: Path, media_type: str, output_dir: Path, thumb_size: int,
                      tz_offset: Optional[float], base: str, regen_thumb: bool, regen_converted: bool,
                      force_copy: bool, large_thumb_size: int = 0,
                      meta: Optional[dict] = None) -> tuple:
    """Metadata (unless already known) and the derivatives for one file.
    Returns (meta, outputs). This is the unit of work handed to each worker
    process, so it must stay a plain top-level function: that's what lets it
    be pickled, and it's required under Windows' and macOS' 'spawn' model."""
    if meta is None:
        meta = read_metadata(path, media_type, tz_offset)
    out = planned_outputs(path, media_type, base, bool(meta.get("is_mpo")))
    if media_type == "image":
        make_image_derivatives(path, out, output_dir, thumb_size, large_thumb_size,
                               regen_thumb, regen_converted, force_copy)
    else:
        out.thumb_is_placeholder = make_video_derivatives(
            path, out, output_dir, thumb_size, large_thumb_size, regen_thumb, force_copy)
    return meta, out


def _outputs_present(src: Path, out: StaticOutputs, output_dir: Path, large: bool) -> bool:
    """Every output of an unchanged file is still there, and the byte-for-byte
    copy of the original still matches it, so the file needs no work at all."""
    if not all((output_dir / rel).is_file() for rel in output_list(out, large)):
        return False
    copy = out.original_file or out.display_file
    return _copy_is_current(src, output_dir / "media" / copy)


# --------------------------------------------------------------------------
# Scanning
# --------------------------------------------------------------------------

def scan_media(input_dir: Path, output_dir: Path, thumb_size: int,
               force: bool, tz_offset: Optional[float] = None,
               workers: Optional[int] = None, dedupe: bool = True,
               manifest: Optional[dict] = None, skip_rels: Optional[set] = None,
               large_thumb_size: int = 0, live_photos: bool = True, exclude_dirs=()) -> tuple:
    """Returns (items, records): the processed media, and the build-record
    entry for every processed file (see save_manifest). `exclude_dirs`: more
    folders to leave out besides output_dir (a library's own folder)."""
    manifest = manifest or {}
    previous = manifest.get("files", {})
    for sub in ("thumbs", "media") + (("thumbs_large",) if large_thumb_size else ()):
        (output_dir / sub).mkdir(parents=True, exist_ok=True)

    def rel_of(p: Path) -> str:
        return p.relative_to(input_dir).as_posix()

    def cached_meta(path: Path, media_type: str, st=None) -> Optional[dict]:
        prev = previous.get(rel_of(path))
        if not isinstance(prev, dict):
            return None
        try:
            st = st or path.stat()
        except OSError:
            return None
        if prev.get("size") != st.st_size or prev.get("mtime_ns") != st.st_mtime_ns:
            return None
        return meta_from_json(prev.get("meta"), tz_offset, media_type)

    # the output folder is excluded explicitly too: on the very first run
    # it doesn't hold a build record yet
    candidates = collect_candidates(input_dir, exclude_dirs=(output_dir, *exclude_dirs))
    # Duplicates first, THEN hidden photos: a set of identical files is left
    # out as a whole if any of them is hidden (see drop_hidden_groups).
    groups = dedupe_groups(candidates) if dedupe else no_dedupe_groups(candidates)
    video_meta_seen = {}

    def video_meta(path: Path) -> dict:
        if path not in video_meta_seen:
            video_meta_seen[path] = cached_meta(path, "video") or read_metadata(path, "video", tz_offset)
        return video_meta_seen[path]

    groups = drop_hidden_groups(groups, input_dir, skip_rels or set(), live_photos, video_meta)

    total = len(groups)
    if total == 0:
        return [], {}

    rel_paths = {path: rel_of(path) for path, _, _ in groups}
    bases = compute_output_bases(rel_paths, previous)
    thumb_size_changed = (manifest.get("thumb_size") != thumb_size
                          or manifest.get("large_thumb_size", 0) != large_thumb_size)

    # decide per file what needs doing. "Unchanged" means the build record
    # has the file with the same output name, size and mtime.
    jobs, ready = [], []
    stats = {}
    changed = 0
    # Files that couldn't be read or processed this time keep their previous
    # record, so the cleanup at the end doesn't delete their existing outputs
    # (a drive that drops out for a moment must not empty the site).
    carried = {}

    def carry(path):
        prev = previous.get(rel_paths[path])
        outputs = prev.get("outputs") if isinstance(prev, dict) else None
        if isinstance(outputs, list) and all(isinstance(o, str) for o in outputs):   # (a hand-edited record)
            carried[rel_paths[path]] = prev

    for path, media_type, dups in groups:
        try:
            st = path.stat()
        except OSError as e:          # removed or unplugged since the scan
            print(f"  ! skipping {path.name}: {e}", file=sys.stderr)
            total -= 1
            carry(path)
            continue
        stats[path] = (st.st_size, st.st_mtime_ns)
        prev = previous.get(rel_paths[path])
        unchanged = (isinstance(prev, dict) and prev.get("base") == bases[path]
                     and prev.get("size") == st.st_size and prev.get("mtime_ns") == st.st_mtime_ns
                     and not prev.get("retry"))  # e.g. a video preview that failed last time
        regen_converted = force or not unchanged
        regen_thumb = regen_converted or thumb_size_changed
        changed += not unchanged
        meta = video_meta_seen.get(path) or (cached_meta(path, media_type, st) if unchanged else None)
        if meta is not None and not (regen_thumb or force):
            out = planned_outputs(path, media_type, bases[path], bool(meta.get("is_mpo")))
            if _outputs_present(path, out, output_dir, bool(large_thumb_size)):
                ready.append((path, media_type, dups, meta, out))   # nothing to do at all
                continue
        jobs.append((path, media_type, dups, (regen_thumb, regen_converted, force), meta))
    if manifest and not force:
        print(f"{total - changed} file(s) unchanged since the last run, "
              f"{changed} new or changed."
              + (" Thumbnail size changed, so all thumbnails will be rebuilt." if thumb_size_changed else ""))

    items, records = [], carried

    def add(path, media_type, dups, meta, out):
        item = build_media_item(path, media_type, meta, outputs=out, key=out.display_file,
                                duplicates=list(dups))
        items.append(item)
        size, mtime_ns = stats[path]
        records[rel_paths[path]] = {
            "base": bases[path], "size": size, "mtime_ns": mtime_ns,
            "outputs": output_list(out, bool(large_thumb_size)),
            "meta": meta_to_json(meta, tz_offset if media_type == "video" else None),
        }
        if out.thumb_is_placeholder:
            records[rel_paths[path]]["retry"] = True

    for path, media_type, dups, meta, out in ready:
        try:
            add(path, media_type, dups, meta, out)
        except OSError as e:
            print(f"  ! {path.name}: {e}", file=sys.stderr)
            carry(path)

    if jobs:
        workers = max(1, workers or min(os.cpu_count() or 1, len(jobs)))
        print(f"Processing {len(jobs)} file(s) with {workers} worker process(es)...")
        done = 0
        try:
            with ProcessPoolExecutor(max_workers=workers) as executor:
                futures = {
                    executor.submit(_process_one_file, path, media_type, output_dir, thumb_size, tz_offset,
                                    bases[path], *flags, large_thumb_size, meta): (path, media_type, dups)
                    for path, media_type, dups, flags, meta in jobs
                }
                for future in as_completed(futures):
                    path, media_type, dups = futures[future]
                    done += 1
                    try:
                        meta, out = future.result()
                        add(path, media_type, dups, meta, out)
                    except BrokenProcessPool:
                        raise
                    except Exception as e:
                        print(f"[{done}/{len(jobs)}] {media_type:5s} {path.name}  ! failed: {e}", file=sys.stderr)
                        carry(path)
                        continue
                    print(f"[{done}/{len(jobs)}] {media_type:5s} {path.name}")
        except BrokenProcessPool:
            # a worker died (out of memory on a huge photo, killed, ...) and took every
            # unfinished file with it (it can surface from submit() as well as from a
            # result). Carrying on would build a site without them AND delete their
            # existing thumbnails and copies.
            raise SystemExit("! A worker process stopped unexpectedly (out of memory?), so the "
                             "build was stopped before changing the site's pages. Run it again; "
                             "if it keeps happening, try --workers 1.")
    else:
        print("Nothing to (re)generate.")

    return items, records


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------

class StaticUrls(SiteUrls):
    """Links between the files of the static site, all relative, so the
    folder works from anywhere (a USB drive, file://, any web server).
    Filenames are URL-encoded, since output names keep the original's
    characters ('#', '%', spaces, ...)."""
    footer = "Generated with generate_site.py"
    edit_hint = " and re-run the generator."
    has_vendor = False    # local copies of Leaflet and the fonts are bundled in assets/vendor
    large_thumbs = False  # --large-thumb-size was used, so thumbs_large/ exists
    in_library = False    # built by --library: the library page is one folder up

    def library_href(self, from_index):
        if not self.in_library:
            return None
        return "../index.html" if from_index else "../../index.html"

    def vendor_url(self, from_index):
        if not self.has_vendor:
            return None
        return ("" if from_index else "../") + "assets/vendor/"

    def home(self, from_index):
        return "index.html" if from_index else "../index.html"

    def assets_prefix(self, from_index):
        return "" if from_index else "../"

    def day(self, date_key, from_index):
        return f"days/{date_key}.html" if from_index else f"{date_key}.html"

    def thumb(self, item, from_index):
        return ("" if from_index else "../") + f"thumbs/{quote(item.outputs.thumb_file)}"

    def hi_thumb(self, item, from_index):
        if not self.large_thumbs:
            return None
        return ("" if from_index else "../") + f"thumbs_large/{quote(item.outputs.thumb_file)}"

    def display(self, item):
        return f"../media/{quote(item.outputs.display_file)}"

    def download(self, item):
        # the untouched original: its own copy for converted formats,
        # otherwise the display copy, which is byte-for-byte the original
        return f"../media/{quote(item.outputs.original_file or item.outputs.display_file)}"

    def live(self, item):
        return f"../media/{quote(item.live_clip.outputs.display_file)}"


def site_urls(large_thumbs: bool = False, in_library: bool = False) -> StaticUrls:
    urls = StaticUrls()
    urls.has_vendor = vendor_ready()
    urls.large_thumbs = large_thumbs
    urls.in_library = in_library
    return urls


def write_assets(folder: Path, has_vendor: bool):
    """The stylesheet and script, and the local map library and fonts if any."""
    assets_dir = folder / "assets"
    atomic_write_text(assets_dir / "style.css", CSS)
    atomic_write_text(assets_dir / "script.js", JS)
    if has_vendor:
        # the map library and fonts travel with the site, so it needs no internet for them
        shutil.copytree(VENDOR_DIR, assets_dir / "vendor", dirs_exist_ok=True)


def render_site(days: list, content: dict, output_dir: Path, urls: StaticUrls) -> list:
    """Write all pages. Returns the day pages written (relative paths)."""
    atomic_write_text(output_dir / "index.html",
                      INDEX_TEMPLATE.render(**build_index_context(days, content, urls)))
    pages = []
    for i, d in enumerate(days):
        html = DAY_TEMPLATE.render(**build_day_context(days, i, content, urls))
        atomic_write_text(output_dir / "days" / f"{d.date_key}.html", html)
        pages.append(f"days/{d.date_key}.html")
    write_assets(output_dir, urls.has_vendor)
    return pages


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a static trip website from a photo/video folder.")
    parser.add_argument("-i", "--input", type=Path, help="Folder containing your photos/videos (required, "
                                                          "unless --library is given)")
    parser.add_argument("--library", type=Path, metavar="LIST",
                        help="Build several trips at once, each into its own folder under -o, plus a page "
                             "linking them all. LIST is a text file with one photos folder per line. "
                             "See README.md.")
    parser.add_argument("-o", "--output", default=Path("site"), type=Path, help="Output folder for the generated site")
    add_content_arg(parser)
    parser.add_argument("--thumb-size", type=positive_thumb_size, default=400,
                        help="Max thumbnail dimension in px (16-4096)")
    parser.add_argument("--large-thumb-size", type=large_thumb_size_arg, default=0, metavar="PIXELS",
                        help="Also make a larger thumbnail of this size (e.g. 1600) for each photo and video, "
                             "which the pages load only where the screen can use more pixels (the L and XL "
                             "sizes, high-resolution screens). Off by default (0), as it adds files to the site.")
    parser.add_argument("--force", action="store_true",
                        help="Regenerate all thumbnails and media, even for files that haven't changed")
    parser.add_argument("--tz-offset", type=float, default=None,
                        help="Fixed UTC offset in hours (e.g. 2 or -5.5) used to convert VIDEO "
                             "timestamps to your trip's local time. Without it, iPhone videos use "
                             "the local time they recorded, and other videos (which only store "
                             "UTC) are converted using this computer's timezone. With it, every "
                             "video uses this offset. Photos are unaffected: camera EXIF "
                             "timestamps are already local.")
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
    add_date_check_args(parser)
    add_location_args(parser)
    add_offline_args(parser)
    return parser


def main(argv=None):
    # progress appears as it happens, also when the output goes to a log file
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.fetch_offline_assets:
        sys.exit(fetch_offline_assets())
    if args.library and args.input is not None:
        parser.error("give either -i/--input (one trip) or --library (several), not both")
    if args.input is None and not args.library:
        parser.error("-i/--input is required")
    date_range = resolve_date_range(args, parser)
    if args.large_thumb_size and args.large_thumb_size <= args.thumb_size:
        parser.error("--large-thumb-size must be larger than --thumb-size (or 0 to turn it off)")
    if args.workers is not None and args.workers < 1:
        parser.error("--workers must be at least 1")
    if args.library:
        problem = check_library_args(args)
        if problem:
            parser.error(problem)
        sys.exit(build_library(args))
    build_trip(args, date_range)
    print_finish(args.output)


def build_trip(args, date_range, library_root: Optional[Path] = None,
               content_fallback: Optional[Path] = None) -> tuple:
    """Build one trip's site into args.output. Returns (days, content, urls).
    On a problem it says what's wrong and exits (SystemExit), which a library
    build catches to carry on with the next trip."""
    if not args.input.is_dir():
        print(f"Input folder not found: {args.input}", file=sys.stderr)
        sys.exit(1)
    in_resolved, out_resolved = args.input.resolve(), args.output.resolve()
    if (out_resolved == in_resolved or out_resolved in in_resolved.parents
            or _same_file(args.output, args.input)):         # (also when only the letter case differs)
        print("The output folder can't be the photos folder itself or a folder that "
              "contains it. Pick a separate folder with -o.", file=sys.stderr)
        sys.exit(1)

    if not HEIF_SUPPORT:
        print("Note: pillow-heif not installed — .heic/.heif files will be skipped.\n"
              "      Install with: pip install pillow-heif", file=sys.stderr)
    if args.tz_offset is not None:
        print(f"Using a fixed UTC{args.tz_offset:+g}h offset for video timestamps.")

    content_path = resolve_content_path(args.content, args.input, fallback=content_fallback)
    # check the content file *before* the (possibly long) processing step, so
    # a typo is reported immediately and never leads to the file being rewritten
    skip_rels = set()
    if content_path.exists():
        try:
            skip_rels = hidden_rels(parse_content_file(content_path))
        except ContentFileError as e:
            print(f"! {e}\n  Nothing was changed. Fix the file and run again.", file=sys.stderr)
            sys.exit(1)

    check_ffmpeg()
    print(f"Scanning {args.input} ...")
    manifest = load_manifest(args.output)
    started = time.time()
    items, records = scan_media(args.input, args.output, args.thumb_size, args.force,
                                tz_offset=args.tz_offset, workers=args.workers,
                                dedupe=not args.no_dedupe, manifest=manifest, skip_rels=skip_rels,
                                large_thumb_size=args.large_thumb_size,
                                live_photos=not args.no_live_photos,
                                exclude_dirs=(library_root,) if library_root else ())
    if not items:
        print("No supported photos or videos found.", file=sys.stderr)
        sys.exit(1)
    # workers finish in any order: sort by path so the result is the same
    # on every run (pairing returns its items sorted too)
    items = sorted(items, key=lambda it: it.path) if args.no_live_photos else pair_live_photos(items)
    assign_rel_paths(items, args.input)

    if not args.no_date_check:
        apply_date_check(items, date_range)
    located = locate_items(items, args, exclude_dirs=(args.output,) + ((library_root,) if library_root else ()))
    fallback_count = sum(1 for it in items if it.dt_is_fallback)
    gps_count = sum(1 for it in items if it.has_gps)
    print(f"\nFound {len(items)} media files "
          f"({fallback_count} without a usable capture date, filed under Unsorted; "
          f"{gps_count} with GPS location).")

    days = group_by_day(items)
    describe_days(days, items, located, args)
    print(f"Grouped into {len(days)} day(s).")

    try:
        content = load_or_init_content(content_path, [d.date_key for d in days],
                                       title=args.input.name if library_root else None)
    except ContentFileError as e:  # e.g. edited while the scan was running
        print(f"! {e}\n  Nothing was changed. Fix the file and run again.", file=sys.stderr)
        sys.exit(1)

    known = {k for it in items for k in it.content_keys}
    report_unknown_photo_keys(content, known | skip_rels)
    urls = site_urls(large_thumbs=bool(args.large_thumb_size), in_library=library_root is not None)
    pages = render_site(days, content, args.output, urls)

    # only after the site was written successfully: drop outputs nothing
    # uses any more, then record this build for the next run
    keep = {o for rec in records.values() for o in rec["outputs"]} | set(pages)
    remove_stale_outputs(args.output, manifest, keep)
    if not manifest:
        report_unrecorded_outputs(args.output, keep, started)
    save_manifest(args.output, args.thumb_size, records, pages, args.large_thumb_size)
    return days, content, urls


def print_finish(output: Path):
    print(f"\nSite generated at: {output.resolve()}")
    print(f"Open {output.resolve() / 'index.html'} in a browser to view it.")
    if vendor_ready():
        print("The map library and fonts are bundled in the site (assets/vendor), so it works without internet; "
              "only the map tiles need a connection.")
    else:
        print("Tip: the site loads its map library and fonts from the internet. To bundle them so it works "
              "offline, run once:  python generate_site.py --fetch-offline-assets  and build again.")


# --------------------------------------------------------------------------
# A library of trips (--library)
# --------------------------------------------------------------------------

def _load_library_record(root: Path) -> dict:
    try:
        data = json.loads((root / LIBRARY_MARKER).read_text(encoding="utf-8"))
        trips = data.get("trips") if isinstance(data, dict) else None
        return trips if isinstance(trips, dict) else {}
    except (OSError, ValueError):
        return {}


def build_library(args) -> int:
    """Build every trip in the list file into its own folder under -o, then
    the page linking them. Returns the exit code: 1 if any trip failed."""
    try:
        entries = read_trip_list(args.library)
    except TripListError as e:
        print(f"! {e}", file=sys.stderr)
        return 1
    root = args.output
    root_resolved = root.resolve()
    for e in entries:
        if e.path.exists() and (e.path.resolve() == root_resolved or _same_file(e.path, root)):
            print(f"! The output folder can't be a trip's photos folder ({e.path}). Pick another -o.",
                  file=sys.stderr)
            return 1
    list_dir = Path(args.library).expanduser().resolve().parent
    previous = _load_library_record(root)
    summaries, failed = {}, []
    for n, entry in enumerate(entries, 1):
        print(f"\n=== Trip {n} of {len(entries)}: {entry.path}  ->  {root / entry.name} ===")
        try:
            problem = missing_folder(entry, args.library)
            if problem:
                print(problem, file=sys.stderr)
                raise SystemExit(1)
            trip, date_range = trip_args(args, entry, args.library)
            trip.output = root / entry.name
            days, content, urls = build_trip(trip, date_range, library_root=root,
                                             content_fallback=list_dir / f"trip_content-{entry.name}.json")
        except TripListError as err:
            print(f"! {args.library}, line {entry.line}: {err}", file=sys.stderr)
            failed.append(entry)
        except SystemExit:        # build_trip already said why
            failed.append(entry)
        except Exception:         # anything else (a drive that drops out mid-build): the others still get built
            traceback.print_exc()
            print(f"! {entry.path} could not be built (see above).", file=sys.stderr)
            failed.append(entry)
        else:
            summaries[entry.name] = trip_summary(entry.name, days, content, urls,
                                                 href=f"{entry.name}/index.html", base=f"{entry.name}/",
                                                 folder_title=entry.path.name)
            continue
        # not built this time (a drive that isn't plugged in, say): keep listing
        # what was built before, if it's still there
        old = previous.get(entry.name)
        if isinstance(old, dict) and (root / entry.name / "index.html").is_file():
            summaries[entry.name] = old
            print(f"  The library keeps showing the version of this trip built earlier.")

    gone = sorted(set(previous) - {e.name for e in entries})
    urls = site_urls()
    root.mkdir(parents=True, exist_ok=True)
    write_assets(root, urls.has_vendor)
    context = build_library_context(list(summaries.values()), urls, footer=StaticUrls.footer,
                                    places_credit=PLACES_ATTRIBUTION)
    atomic_write_text(root / "index.html", LIBRARY_TEMPLATE.render(**context))
    atomic_write_text(root / LIBRARY_MARKER, json.dumps(
        {"version": 1, "note": "Record of a generate_site.py --library build.", "trips": summaries},
        indent=1, ensure_ascii=False) + "\n")

    print(f"\nLibrary of {len(summaries)} trip(s) generated at: {root_resolved}")
    print(f"Open {root_resolved / 'index.html'} in a browser to view it.")
    for name in gone:
        if (root / name).is_dir():
            print(f"Note: {root / name} is no longer in {args.library}, so it isn't listed any more. "
                  f"Delete that folder if you don't need it.")
    if failed:
        print(f"! {len(failed)} trip(s) could not be built: "
              f"{', '.join(str(e.path) for e in failed)}. See the messages above.", file=sys.stderr)
        return 1
    return 0
