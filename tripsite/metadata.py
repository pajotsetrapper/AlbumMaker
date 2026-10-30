"""Reading capture time, location and camera details out of photos (EXIF)
and videos (ffprobe), and the format used to cache that metadata between
runs."""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from PIL import Image
from PIL.ExifTags import GPSTAGS

try:
    import pillow_heif
    pillow_heif.register_heif_opener()
    HEIF_SUPPORT = True
except ImportError:
    HEIF_SUPPORT = False


def ffmpeg_input(path: Path) -> str:
    """The way to name a file on an ffmpeg/ffprobe command line. An absolute
    path behind the file: protocol, so a name that starts with '-' isn't read
    as an option and one that looks like 'name:...' isn't read as a protocol."""
    return "file:" + os.path.abspath(path)

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
             "-show_format", "-show_streams", ffmpeg_input(path)],
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
# Metadata cache format. Reading every file's EXIF and running ffprobe on
# every video is the slow part of a scan; both scripts can keep the result,
# keyed on the file's size and modification time.
# --------------------------------------------------------------------------

META_FORMAT = 1   # bump when read_*_metadata start returning something new


def meta_to_json(meta: dict, tz_offset: Optional[float] = None) -> dict:
    out = {}
    for k, v in meta.items():
        if isinstance(v, datetime):
            out[k] = {"__dt__": v.isoformat()}
        elif isinstance(v, tuple):
            out[k] = list(v)
        else:
            out[k] = v
    out["_format"] = META_FORMAT
    out["_tz"] = tz_offset
    return out


def meta_from_json(data, tz_offset: Optional[float] = None, media_type: str = "image") -> Optional[dict]:
    """The cached metadata, or None if it can't be used (other format, or a
    video read with a different --tz-offset)."""
    if not isinstance(data, dict) or data.get("_format") != META_FORMAT:
        return None
    if media_type == "video" and data.get("_tz") != tz_offset:
        return None
    meta = {}
    try:
        for k, v in data.items():
            if k.startswith("_"):
                continue
            if isinstance(v, dict) and "__dt__" in v:
                meta[k] = datetime.fromisoformat(v["__dt__"])
            elif k == "gps" and isinstance(v, list):
                meta[k] = tuple(v)
            else:
                meta[k] = v
    except (TypeError, ValueError):
        return None
    return meta


def read_metadata(path: Path, media_type: str, tz_offset: Optional[float] = None) -> dict:
    if media_type == "image":
        return read_image_metadata(path)
    return read_video_metadata(path, tz_offset)


class MetadataCache:
    """A JSON file of {relative path: {size, mtime_ns, meta}} (trip_server
    --index-cache). Only touched from the main thread."""

    def __init__(self, path: Optional[Path], tz_offset: Optional[float]):
        self.path = Path(path) if path else None
        self.tz = tz_offset
        self.entries = {}
        self.dirty = False
        if self.path and self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(data, dict) and isinstance(data.get("files"), dict):
                    self.entries = data["files"]
            except (OSError, ValueError) as e:
                print(f"! ignoring unreadable index cache {self.path} ({e})", file=sys.stderr)

    def get(self, rel: str, path: Path, media_type: str) -> Optional[dict]:
        rec = self.entries.get(rel)
        if not isinstance(rec, dict):
            return None
        try:
            st = path.stat()
        except OSError:
            return None
        if rec.get("size") != st.st_size or rec.get("mtime_ns") != st.st_mtime_ns:
            return None
        return meta_from_json(rec.get("meta"), self.tz, media_type)

    def put(self, rel: str, path: Path, meta: dict):
        try:
            st = path.stat()
        except OSError:
            return
        self.entries[rel] = {"size": st.st_size, "mtime_ns": st.st_mtime_ns,
                             "meta": meta_to_json(meta, self.tz)}
        self.dirty = True

    def save(self, keep: set):
        if not self.path:
            return
        stale = set(self.entries) - keep
        for rel in stale:
            del self.entries[rel]
        if not (self.dirty or stale):
            return
        from .util import atomic_write_text
        try:
            atomic_write_text(self.path, json.dumps({"version": 1, "files": self.entries}) + "\n")
        except OSError as e:
            print(f"! could not save the index cache {self.path}: {e}", file=sys.stderr)
