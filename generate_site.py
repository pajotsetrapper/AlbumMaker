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

Needs trip_common.py (code shared with trip_server.py) in the same folder.
See README.md for the full walkthrough.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from PIL import Image, ImageOps

try:
    from trip_common import (
        BROWSER_NATIVE_IMAGE_EXTS, CSS, DAY_TEMPLATE, INDEX_TEMPLATE, JS, SITE_MARKER, VIDEO_EXTS,
        HEIF_SUPPORT, ContentFileError, MediaItem, SiteUrls, atomic_write_text, build_day_context,
        build_index_context, build_media_item, check_ffmpeg, collect_candidates, dedupe_candidates,
        default_content, group_by_day, pair_live_photos, parse_content_file, placeholder_thumbnail,
        positive_thumb_size, read_image_metadata, read_video_metadata,
    )
except ModuleNotFoundError as e:
    if e.name != "trip_common":
        raise
    sys.exit("trip_common.py is missing: keep it in the same folder as this script.")


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
        version for display, plus an untouched copy of the original, which
        the slideshow offers through its "Download original file" link.

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
                            thumb_size: int, regen_thumb: bool, force_copy: bool) -> bool:
    """Copy the video and extract a preview frame. Returns True if a
    placeholder had to be used for the preview."""
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
                     "-frames:v", "1", "-vf", f"scale={thumb_size}:{thumb_size}:force_original_aspect_ratio=decrease",
                     str(thumb_path)],
                    capture_output=True, timeout=30,
                )
                if thumb_path.exists() and thumb_path.stat().st_size > 0:
                    return False
            except Exception:
                continue
        placeholder_thumbnail(thumb_size).save(thumb_path, "JPEG", quality=82)
        return True
    return False


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
    thumb_is_placeholder = False
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
        thumb_is_placeholder = make_video_derivatives(
            path, thumbs_dir / thumb_file, media_dir / display_file,
            thumb_size, regen_thumb, force_copy,
        )

    return build_media_item(
        path, media_type, meta,
        thumb_file=thumb_file,
        display_file=display_file,
        original_file=original_file,
        thumb_is_placeholder=thumb_is_placeholder,
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

    # the output folder is excluded explicitly too: on the very first run
    # it doesn't hold a build record yet
    candidates = collect_candidates(input_dir, exclude_dirs=(output_dir,))
    if dedupe:
        candidates = dedupe_candidates(candidates)

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
                     and prev.get("size") == st.st_size and prev.get("mtime_ns") == st.st_mtime_ns
                     and not prev.get("retry"))  # e.g. a video preview that failed last time
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
            if item.thumb_is_placeholder:
                records[rel_paths[path]]["retry"] = True

    return items, records


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
# Rendering
# --------------------------------------------------------------------------

class StaticUrls(SiteUrls):
    """Links between the files of the static site, all relative, so the
    folder works from anywhere (a USB drive, file://, any web server).
    Filenames are URL-encoded, since output names keep the original's
    characters ('#', '%', spaces, ...)."""
    footer = "Generated with generate_site.py"
    edit_hint = " and re-run the generator."

    def home(self, from_index):
        return "index.html" if from_index else "../index.html"

    def assets_prefix(self, from_index):
        return "" if from_index else "../"

    def day(self, date_key, from_index):
        return f"days/{date_key}.html" if from_index else f"{date_key}.html"

    def thumb(self, item, from_index):
        return ("" if from_index else "../") + f"thumbs/{quote(item.thumb_file)}"

    def display(self, item):
        return f"../media/{quote(item.display_file)}"

    def download(self, item):
        # the untouched original: its own copy for converted formats,
        # otherwise the display copy, which is byte-for-byte the original
        return f"../media/{quote(item.original_file or item.display_file)}"

    def live(self, item):
        return f"../media/{quote(item.live_clip.display_file)}"


def render_site(days: list, content: dict, output_dir: Path) -> list:
    """Write all pages. Returns the day pages written (relative paths)."""
    urls = StaticUrls()
    (output_dir / "days").mkdir(parents=True, exist_ok=True)
    (output_dir / "index.html").write_text(
        INDEX_TEMPLATE.render(**build_index_context(days, content, urls)), encoding="utf-8")

    pages = []
    for i, d in enumerate(days):
        html = DAY_TEMPLATE.render(**build_day_context(days, i, content, urls))
        (output_dir / "days" / f"{d.date_key}.html").write_text(html, encoding="utf-8")
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
    parser.add_argument("--thumb-size", type=positive_thumb_size, default=400,
                         help="Max thumbnail dimension in px (16-4096)")
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

    check_ffmpeg()
    print(f"Scanning {args.input} ...")
    manifest = load_manifest(args.output)
    items, records = scan_media(args.input, args.output, args.thumb_size, args.force,
                                tz_offset=args.tz_offset, workers=args.workers,
                                dedupe=not args.no_dedupe, manifest=manifest)
    if not items:
        print("No supported photos or videos found.", file=sys.stderr)
        sys.exit(1)
    # workers finish in any order: sort by path so the result is the same
    # on every run (pairing returns its items sorted too)
    items = sorted(items, key=lambda it: it.path) if args.no_live_photos else pair_live_photos(items)

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
