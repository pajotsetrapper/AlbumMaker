"""Persistent desktop-app settings."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional

SETTINGS_VERSION = 1


class SettingsError(Exception):
    """Settings could not be loaded or saved."""


def config_dir() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("APPDATA")
        return (Path(base) if base else Path.home() / "AppData" / "Roaming") / "TripServer"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "TripServer"
    base = os.environ.get("XDG_CONFIG_HOME")
    return (Path(base) if base else Path.home() / ".config") / "tripserver"


def settings_path() -> Path:
    return config_dir() / "settings.json"


def default_settings() -> dict:
    return {
        "version": SETTINGS_VERSION,
        "trips": [],
        "output_dir": str(Path.home() / "TripSites"),
        "server": {
            "port": 8000,
            "large_thumb_size": 1600,
            "lan": False,
            "read_only": False,
            "editor_link_only": False,
            "memory_cache_mb": 512,
            "verbose": True,
        },
        "build": {
            "thumb_size": 400,
            "large_thumb_size": 0,
            "tz_offset": "",
            "workers": "",
            "no_dedupe": False,
            "no_live_photos": False,
            "no_date_check": False,
            "date_range_start": "",
            "date_range_end": "",
            "no_gpx": False,
            "no_places": False,
            "force": False,
            "gpx_files": [],
        },
        "advanced": {
            "tile_cache": "",
            "tile_url": "",
            "index_cache": "",
            "allow_hosts": [],
        },
    }


def _merge_defaults(saved: object) -> dict:
    if not isinstance(saved, dict):
        raise SettingsError("Settings file must contain a JSON object.")
    if saved.get("version") != SETTINGS_VERSION:
        raise SettingsError(
            f"Unsupported settings version: {saved.get('version')!r}. "
            f"Expected version {SETTINGS_VERSION}."
        )
    merged = default_settings()
    for key in ("trips", "output_dir"):
        if key in saved:
            merged[key] = saved[key]
    for section in ("server", "build", "advanced"):
        if section in saved:
            value = saved[section]
            if not isinstance(value, dict):
                raise SettingsError(f"Settings section {section!r} must be an object.")
            merged[section].update(value)
    if not isinstance(merged["trips"], list) or not all(isinstance(p, str) for p in merged["trips"]):
        raise SettingsError("'trips' must be a list of folder paths.")
    if not isinstance(merged["output_dir"], str):
        raise SettingsError("'output_dir' must be a folder path.")
    for key in ("port", "large_thumb_size", "memory_cache_mb"):
        value = merged["server"][key]
        if not isinstance(value, int) or isinstance(value, bool):
            raise SettingsError(f"'server.{key}' must be an integer.")
    if not 1 <= merged["server"]["port"] <= 65535:
        raise SettingsError("'server.port' must be between 1 and 65535.")
    if not 16 <= merged["server"]["memory_cache_mb"] <= 65536:
        raise SettingsError("'server.memory_cache_mb' must be between 16 and 65536.")
    if not 0 <= merged["server"]["large_thumb_size"] <= 4096:
        raise SettingsError("'server.large_thumb_size' must be between 0 and 4096.")
    for key in ("lan", "read_only", "editor_link_only", "verbose"):
        if not isinstance(merged["server"][key], bool):
            raise SettingsError(f"'server.{key}' must be true or false.")
    for key in ("thumb_size", "large_thumb_size"):
        value = merged["build"][key]
        if not isinstance(value, int) or isinstance(value, bool):
            raise SettingsError(f"'build.{key}' must be an integer.")
    if not 16 <= merged["build"]["thumb_size"] <= 4096:
        raise SettingsError("'build.thumb_size' must be between 16 and 4096.")
    if not 0 <= merged["build"]["large_thumb_size"] <= 4096:
        raise SettingsError("'build.large_thumb_size' must be between 0 and 4096.")
    workers = merged["build"]["workers"]
    if workers != "" and (not isinstance(workers, int) or isinstance(workers, bool)):
        raise SettingsError("'build.workers' must be an integer or blank.")
    if workers != "" and not 1 <= workers <= 256:
        raise SettingsError("'build.workers' must be between 1 and 256.")
    for key in ("tz_offset", "date_range_start", "date_range_end"):
        if not isinstance(merged["build"][key], str):
            raise SettingsError(f"'build.{key}' must be text.")
    for key in ("no_dedupe", "no_live_photos", "no_date_check", "no_gpx", "no_places", "force"):
        if not isinstance(merged["build"][key], bool):
            raise SettingsError(f"'build.{key}' must be true or false.")
    for key in ("tile_cache", "tile_url", "index_cache"):
        if not isinstance(merged["advanced"][key], str):
            raise SettingsError(f"'advanced.{key}' must be text.")
    if not isinstance(merged["build"]["gpx_files"], list) or not all(
        isinstance(path, str) for path in merged["build"]["gpx_files"]
    ):
        raise SettingsError("'build.gpx_files' must be a list of paths.")
    if not isinstance(merged["advanced"]["allow_hosts"], list) or not all(
        isinstance(host, str) for host in merged["advanced"]["allow_hosts"]
    ):
        raise SettingsError("'advanced.allow_hosts' must be a list of host names.")
    return merged


def load_settings(path: Optional[Path] = None) -> dict:
    path = Path(path or settings_path())
    if not path.exists():
        return default_settings()
    try:
        return _merge_defaults(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SettingsError(f"Could not read settings from {path}: {exc}") from exc


def save_settings(settings: dict, path: Optional[Path] = None) -> None:
    path = Path(path or settings_path())
    payload = _merge_defaults(settings)
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.",
            suffix=".tmp", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            json.dump(payload, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
        os.replace(temporary, path)
    except OSError as exc:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
        raise SettingsError(f"Could not save settings to {path}: {exc}") from exc


def write_library_file(trips: list[str], path: Path) -> Path:
    """Write a library list understood by tripsite.library.read_trip_list."""
    if not trips:
        raise SettingsError("Add at least one trip folder before starting.")
    lines = []
    for raw_path in trips:
        trip = Path(raw_path).expanduser()
        if not trip.is_dir():
            raise SettingsError(f"Trip folder does not exist: {trip}")
        if "|" in str(trip) or "\n" in str(trip) or "\r" in str(trip):
            raise SettingsError(f"Trip folder path cannot contain '|', or a newline: {trip}")
        lines.append(str(trip.resolve()))
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError as exc:
        raise SettingsError(f"Could not write the trip library file {path}: {exc}") from exc
    return path
