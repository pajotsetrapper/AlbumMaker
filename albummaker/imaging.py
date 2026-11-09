# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Pieter Remi Coppens
"""Thumbnails, browser-safe display JPEGs and video preview frames. Both
scripts use these: generate_site writes the bytes to files, album_server
sends (and briefly caches) them. Everything returns encoded bytes."""

from __future__ import annotations

import io
import subprocess
from pathlib import Path
from typing import Iterable, Optional

from PIL import Image, ImageOps

from .metadata import ffmpeg_input, multimedia_tool

THUMB_QUALITY = 82
DISPLAY_QUALITY = 97
LANCZOS = Image.Resampling.LANCZOS


def placeholder_thumbnail(thumb_size: int) -> Image.Image:
    """Plain dark tile used when a video's preview frame can't be extracted
    (no ffmpeg, or an undecodable stream), so the grid never shows a broken
    image. The grid draws its play icon on top, as for any video."""
    return Image.new("RGB", (thumb_size, thumb_size * 3 // 4), (34, 50, 82))


def encode_jpeg(img: Image.Image, quality: int = THUMB_QUALITY, optimize: bool = True,
                exif: Optional[bytes] = None) -> bytes:
    buf = io.BytesIO()
    kwargs = {"quality": quality, "optimize": optimize}
    if exif:
        kwargs["exif"] = exif
    img.save(buf, "JPEG", **kwargs)
    return buf.getvalue()


def placeholder_bytes(thumb_size: int) -> bytes:
    return encode_jpeg(placeholder_thumbnail(thumb_size), optimize=False)


def open_upright(path: Path, draft_to: int = 0) -> tuple:
    """(image, exif): the photo decoded, rotated upright and in RGB, and its
    original EXIF. With draft_to, a JPEG is decoded at the smallest 1/2, 1/4
    or 1/8 scale that still covers draft_to pixels, instead of decoding every
    pixel just to throw most away (a no-op for other formats). Rotation and
    conversion happen in place where possible, so only one full-size copy of
    the pixels exists at a time. The caller closes the image."""
    img = Image.open(path)
    try:
        exif = img.getexif()
        if draft_to:
            img.draft("RGB", (draft_to, draft_to))
        img.load()
        ImageOps.exif_transpose(img, in_place=True)   # a no-op when already upright
        if img.mode != "RGB":
            rgb = img.convert("RGB")
            img.close()
            img = rgb
        return img, exif
    except Exception:
        img.close()
        raise


def thumbnails_from(img: Image.Image, sizes: Iterable[int]) -> dict:
    """{size: JPEG bytes} for each requested maximum dimension. Works largest
    first and shrinks `img` IN PLACE each time, so making a 1600px and a 400px
    thumbnail costs no extra full-size copy (the image is consumed)."""
    out = {}
    for size in sorted(set(s for s in sizes if s), reverse=True):
        img.thumbnail((size, size), LANCZOS)
        out[size] = encode_jpeg(img)
    return out


def display_jpeg(img: Image.Image, exif) -> bytes:
    """Full-resolution, near-lossless JPEG of an upright image, carrying the
    original EXIF (camera, GPS, timestamp, ...). The pixels are already
    rotated, so Orientation is reset to "normal" to stop EXIF-aware viewers
    rotating the image a second time."""
    exif_bytes = None
    try:
        exif[0x0112] = 1
        exif_bytes = exif.tobytes()
    except Exception:
        pass
    return encode_jpeg(img, quality=DISPLAY_QUALITY, exif=exif_bytes)


def display_jpeg_bytes(path: Path) -> bytes:
    """HEIC/HEIF, TIFF or multi-image JPEG -> a JPEG every browser shows."""
    img, exif = open_upright(path)
    try:
        return display_jpeg(img, exif)
    finally:
        img.close()


def image_thumbnails(path: Path, sizes: Iterable[int]) -> dict:
    """{size: JPEG bytes} for a photo."""
    sizes = [s for s in sizes if s]
    img, _ = open_upright(path, draft_to=max(sizes) * 2)
    try:
        return thumbnails_from(img, sizes)
    finally:
        img.close()


def video_frame(path: Path, size: int, timeout: int = 30) -> Optional[Image.Image]:
    """One frame of the video (1 s in, or the very start for shorter clips),
    at most size x size pixels, or None if ffmpeg can't produce one."""
    for seek in ("00:00:01", "00:00:00"):
        try:
            result = subprocess.run(
                [multimedia_tool("ffmpeg"), "-v", "quiet", "-nostdin", "-ss", seek,
                 "-i", ffmpeg_input(path),
                 "-frames:v", "1",
                 "-vf", f"scale={size}:{size}:force_original_aspect_ratio=decrease",
                 "-f", "image2pipe", "-vcodec", "mjpeg", "pipe:1"],
                capture_output=True, timeout=timeout,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if result.returncode == 0 and result.stdout:
            try:
                frame = Image.open(io.BytesIO(result.stdout))
                frame.load()
                return frame.convert("RGB") if frame.mode != "RGB" else frame
            except Exception:
                continue
    return None


def video_thumbnails(path: Path, sizes: Iterable[int]) -> tuple:
    """({size: JPEG bytes}, used_placeholder) for a video: ONE ffmpeg run at
    the largest size, smaller sizes scaled down from that frame."""
    sizes = [s for s in sizes if s]
    frame = video_frame(path, max(sizes))
    if frame is None:
        return {s: placeholder_bytes(s) for s in sizes}, True
    return thumbnails_from(frame, sizes), False
