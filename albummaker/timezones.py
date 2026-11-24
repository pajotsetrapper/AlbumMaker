# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Pieter Remi Coppens
"""IANA time-zone helpers shared by the CLI and desktop app."""

from __future__ import annotations

from functools import lru_cache
from importlib.resources import files
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones


def get_timezone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"Unknown IANA time zone: {name}") from exc


def timezone_choices() -> list[str]:
    zones = set(available_timezones())
    try:
        bundled = files("tzdata.zoneinfo")
    except ModuleNotFoundError:
        return sorted(zones)

    def collect_tzif_files(directory, prefix: str = "") -> None:
        for entry in directory.iterdir():
            key = f"{prefix}/{entry.name}" if prefix else entry.name
            if entry.is_dir():
                collect_tzif_files(entry, key)
            elif entry.is_file():
                with entry.open("rb") as stream:
                    if stream.read(4) == b"TZif":
                        zones.add(key)

    collect_tzif_files(bundled)
    return sorted(zones)


def timezone_label(name: str) -> str:
    """The text people see and search for: 'Paris (Europe/Paris)'. An empty
    name is the Automatic setting."""
    if not name:
        return "Automatic"
    return f"{name.rsplit('/', 1)[-1].replace('_', ' ')} ({name})"


@lru_cache(maxsize=1)
def timezone_search_choices() -> tuple[tuple[str, str], ...]:
    """Human-searchable labels mapped to IANA zone names. Building the list
    reads the whole time-zone database, so it is done once and kept."""
    return tuple(sorted(
        ((timezone_label(name), name) for name in timezone_choices()),
        key=lambda choice: choice[0].casefold(),
    ))
