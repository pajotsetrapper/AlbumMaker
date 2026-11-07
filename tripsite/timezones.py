"""IANA time-zone helpers shared by the CLI and desktop app."""

from __future__ import annotations

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
