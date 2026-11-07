"""Translate GUI settings to the existing TripServer CLI arguments."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sys
from typing import Optional


def _command_prefix(python: str, subcommand: str) -> list[str]:
    launcher = Path(sys.argv[0])
    if launcher.suffix.lower() in (".pyz", ".zip"):
        return [python, "-u", str(launcher), subcommand]
    return [python, "-u", "-m", "tripsite", subcommand]


def _validate_common(build: dict, large_thumb_size: Optional[int] = None) -> None:
    thumb_size = int(build.get("thumb_size", 400))
    if large_thumb_size is None:
        large_thumb_size = int(build.get("large_thumb_size", 0))
    if not 16 <= thumb_size <= 4096:
        raise ValueError("Thumbnail size must be between 16 and 4096 pixels.")
    if large_thumb_size and (not 16 <= large_thumb_size <= 4096 or large_thumb_size <= thumb_size):
        raise ValueError("High-resolution thumbnail size must be larger than the normal size, or Off.")
    tz_offset = str(build.get("tz_offset", "")).strip()
    if tz_offset:
        try:
            float(tz_offset)
        except ValueError as exc:
            raise ValueError("Video UTC offset must be a number, such as 2 or -5.5.") from exc


def _append_bool(args: list[str], enabled: bool, flag: str) -> None:
    if enabled:
        args.append(flag)


def _append_value(args: list[str], flag: str, value: str) -> None:
    value = str(value).strip()
    if value:
        args.extend((flag, value))


def _shared_options(build: dict) -> list[str]:
    args = []
    _append_value(args, "--tz-offset", build.get("tz_offset", ""))
    _append_bool(args, build.get("no_dedupe", False), "--no-dedupe")
    _append_bool(args, build.get("no_live_photos", False), "--no-live-photos")
    _append_bool(args, build.get("no_date_check", False), "--no-date-check")
    no_gpx = bool(build.get("no_gpx", False))
    _append_bool(args, no_gpx, "--no-gpx")
    _append_bool(args, build.get("no_places", False), "--no-places")
    if not no_gpx:
        for gpx in build.get("gpx_files", []):
            if str(gpx).strip():
                args.extend(("--gpx", str(Path(gpx).expanduser())))
    return args


def _validate_date_range(build: dict) -> Optional[tuple[str, str]]:
    if build.get("no_date_check", False):
        return None
    start = str(build.get("date_range_start", "")).strip()
    end = str(build.get("date_range_end", "")).strip()
    if bool(start) != bool(end):
        raise ValueError("Enter both the start and end dates, or leave both blank.")
    if not start:
        return None
    try:
        first = datetime.strptime(start, "%Y-%m-%d").date()
        last = datetime.strptime(end, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError("Dates must use YYYY-MM-DD format.") from exc
    if last < first:
        raise ValueError("The end date must be on or after the start date.")
    return start, end


def _library_with_date_range(
    library_file: Path, date_range: Optional[tuple[str, str]]
) -> Path:
    if date_range is None:
        return library_file
    start, end = date_range
    lines = []
    for line in library_file.read_text(encoding="utf-8").splitlines():
        parts = line.split("|")
        option = f"--date-range {start} {end}"
        if len(parts) == 1:
            parts.extend(("", f" {option}"))
        elif len(parts) == 3:
            parts[2] += f" {option}"
        else:
            raise ValueError("The generated trip library file has an invalid line.")
        lines.append(" | ".join(part.strip() for part in parts))
    library_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return library_file


def server_command(python: str, library_file: Path, settings: dict) -> list[str]:
    server, build, advanced = settings["server"], settings["build"], settings["advanced"]
    _validate_common(build, int(server.get("large_thumb_size", 1600)))
    if not 1 <= int(server["port"]) <= 65535:
        raise ValueError("Port must be between 1 and 65535.")
    if int(server.get("memory_cache_mb", 512)) < 16:
        raise ValueError("Image cache must be at least 16 MB.")
    tile_url = str(advanced.get("tile_url", "")).strip()
    if tile_url and not (
        tile_url.startswith(("http://", "https://"))
        and all(part in tile_url for part in ("{z}", "{x}", "{y}"))
    ):
        raise ValueError("Tile URL must start with http:// or https:// and contain {z}, {x}, and {y}.")
    date_range = _validate_date_range(build)
    library = _library_with_date_range(library_file, date_range)
    args = _command_prefix(python, "serve") + ["--library", str(library)]
    args.extend(("--port", str(server["port"])))
    args.extend(_shared_options(build))
    _append_value(args, "--thumb-size", build.get("thumb_size", "400"))
    _append_value(args, "--large-thumb-size", server.get("large_thumb_size", "1600"))
    _append_value(args, "--memory-cache-mb", server.get("memory_cache_mb", "512"))
    _append_bool(args, server.get("lan", False), "--lan")
    _append_bool(args, server.get("read_only", False), "--read-only")
    _append_bool(args, server.get("editor_link_only", False), "--editor-link-only")
    _append_bool(args, server.get("verbose", True), "--verbose")
    _append_value(args, "--tile-cache", advanced.get("tile_cache", ""))
    _append_value(args, "--tile-url", advanced.get("tile_url", ""))
    _append_value(args, "--index-cache", advanced.get("index_cache", ""))
    for hostname in advanced.get("allow_hosts", []):
        _append_value(args, "--allow-host", hostname)
    return args


def build_command(python: str, library_file: Path, output_dir: str, settings: dict) -> list[str]:
    build = settings["build"]
    _validate_common(build)
    workers = build.get("workers", "")
    if workers and int(workers) < 1:
        raise ValueError("Parallel workers must be at least 1, or Automatic.")
    date_range = _validate_date_range(build)
    library = _library_with_date_range(library_file, date_range)
    args = _command_prefix(python, "build") + [
        "--library", str(library), "--output", str(Path(output_dir).expanduser())
    ]
    args.extend(_shared_options(build))
    _append_value(args, "--thumb-size", build.get("thumb_size", "400"))
    _append_value(args, "--large-thumb-size", build.get("large_thumb_size", "1600"))
    _append_value(args, "--workers", build.get("workers", ""))
    _append_bool(args, build.get("force", False), "--force")
    return args


def offline_assets_command(python: str) -> list[str]:
    return _command_prefix(python, "serve") + ["--fetch-offline-assets"]
