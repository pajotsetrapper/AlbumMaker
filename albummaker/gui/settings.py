# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Pieter Remi Coppens
"""Persistent desktop-app settings."""

from __future__ import annotations

import json
import os
import shlex
import tempfile
from datetime import datetime
from pathlib import Path
from typing import NamedTuple, Optional
from ..app_paths import config_dir, settings_path
from ..timezones import get_timezone

SETTINGS_VERSION = 1


class SettingsError(Exception):
    """Settings could not be loaded or saved."""


def default_settings() -> dict:
    return {
        "version": SETTINGS_VERSION,
        "trips": [],
        "output_dir": str(Path.home() / "AlbumMakerSites"),
        "appearance": {"theme": "light"},
        "library": {"title": "Trips"},
        "server": {
            "port": 8000,
            "large_thumb_size": 2400,
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
            "tile_cache": str(config_dir() / "tile-cache"),
            "tile_url": "",
            "index_cache": str(config_dir() / "metadata-cache"),
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
    if "appearance" in saved:
        value = saved["appearance"]
        if not isinstance(value, dict):
            raise SettingsError("Settings section 'appearance' must be an object.")
        merged["appearance"].update(value)
    if "library" in saved:
        value = saved["library"]
        if not isinstance(value, dict):
            raise SettingsError("Settings section 'library' must be an object.")
        merged["library"].update(value)
    for section in ("server", "build", "advanced"):
        if section in saved:
            value = saved[section]
            if not isinstance(value, dict):
                raise SettingsError(f"Settings section {section!r} must be an object.")
            merged[section].update(value)
    if not isinstance(merged["trips"], list):
        raise SettingsError("'trips' must be a list of trip folders.")
    trips = []
    for trip in merged["trips"]:
        if not isinstance(trip, dict) or not isinstance(trip.get("path"), str):
            raise SettingsError("Each trip must have a folder path.")
        timezone_name = trip.get("timezone", "")
        if not isinstance(timezone_name, str):
            raise SettingsError("Each trip time zone must be text.")
        if timezone_name:
            try:
                get_timezone(timezone_name)
            except ValueError as exc:
                raise SettingsError(str(exc)) from exc
        trips.append({"path": trip["path"], "timezone": timezone_name})
    merged["trips"] = trips
    if not isinstance(merged["output_dir"], str):
        raise SettingsError("'output_dir' must be a folder path.")
    if merged["appearance"]["theme"] not in ("light", "dark"):
        raise SettingsError("'appearance.theme' must be 'light' or 'dark'.")
    title = merged["library"]["title"]
    if not isinstance(title, str) or not title.strip() or len(title) > 200:
        raise SettingsError("'library.title' must be non-empty text of at most 200 characters.")
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


class LoadedSettings(NamedTuple):
    """Result of load_settings_or_recover()."""

    settings: dict
    path: Path
    #: What was wrong with the settings file, or "" when it loaded fine.
    problem: str = ""
    #: Where the damaged file was moved, or None.
    moved_to: Optional[Path] = None
    #: Why the damaged file could not be moved aside, or "" when it was moved.
    move_error: str = ""

    @property
    def recovered(self) -> bool:
        return bool(self.problem)

    @property
    def can_save(self) -> bool:
        """False when saving would overwrite a damaged file that is still in place."""
        return not self.problem or self.moved_to is not None


def move_aside(path: Path, now: Optional[datetime] = None) -> Path:
    """Rename a damaged settings file to `<name>.bad-YYYYmmdd-HHMMSS` in the
    same folder, without overwriting an earlier backup. Raises OSError."""
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    base = path.with_name(f"{path.name}.bad-{stamp}")
    target, number = base, 1
    while os.path.lexists(target):
        target = base.with_name(f"{base.name}-{number}")
        number += 1
    os.rename(path, target)
    return target


def load_settings_or_recover(path: Optional[Path] = None,
                             now: Optional[datetime] = None) -> LoadedSettings:
    """Like load_settings(), but a damaged file does not stop the app: it is
    moved aside and the defaults are used. If it cannot be moved, the defaults
    are still used and `can_save` is False, so the caller does not overwrite it."""
    path = Path(path or settings_path())
    try:
        return LoadedSettings(load_settings(path), path)
    except SettingsError as exc:
        problem = str(exc)
    try:
        moved_to = move_aside(path, now)
    except OSError as exc:
        return LoadedSettings(default_settings(), path, problem, None, str(exc))
    return LoadedSettings(default_settings(), path, problem, moved_to)


def validate_settings(settings: dict) -> dict:
    """Check settings and fill in defaults, as save_settings() does, without writing."""
    return _merge_defaults(settings)


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


def write_library_file(trips: list, path: Path) -> Path:
    """Write a library list understood by albummaker.library.read_trip_list."""
    if not trips:
        raise SettingsError("Add at least one trip folder before starting.")
    lines = []
    for entry in trips:
        if isinstance(entry, str):
            raw_path, timezone_name = entry, ""
        else:
            raw_path, timezone_name = entry["path"], entry.get("timezone", "")
        trip = Path(raw_path).expanduser()
        if not trip.is_dir():
            raise SettingsError(f"Trip folder does not exist: {trip}")
        if "|" in str(trip) or "\n" in str(trip) or "\r" in str(trip):
            raise SettingsError(f"Trip folder path cannot contain '|', or a newline: {trip}")
        line = str(trip.resolve())
        if timezone_name:
            try:
                get_timezone(timezone_name)
            except ValueError as exc:
                raise SettingsError(str(exc)) from exc
            line += f" | | --timezone {shlex.quote(timezone_name)}"
        lines.append(line)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError as exc:
        raise SettingsError(f"Could not write the trip library file {path}: {exc}") from exc
    return path


def _prepare_cache_folder(path: str, label: str) -> Path:
    value = str(path).strip()
    if not value:
        raise SettingsError(f"Choose a {label} folder before starting the live server.")
    folder = Path(value).expanduser()
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise SettingsError(f"Could not create the {label} folder {folder}: {exc}") from exc
    if not folder.is_dir():
        raise SettingsError(f"The {label} path is not a folder: {folder}")
    try:
        with tempfile.TemporaryFile(dir=folder):
            pass
    except OSError as exc:
        raise SettingsError(f"Could not write to the {label} folder {folder}: {exc}") from exc
    return folder.resolve()


def prepare_metadata_cache(path: str) -> Path:
    """Create and validate the required metadata cache folder for GUI server mode."""
    return _prepare_cache_folder(path, "metadata cache")


def prepare_tile_cache(path: str) -> Path:
    """Create and validate the required map tile cache folder for GUI server mode."""
    return _prepare_cache_folder(path, "map tile cache")
