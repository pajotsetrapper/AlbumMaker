"""IANA time-zone helpers shared by the CLI and desktop app."""

from __future__ import annotations

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones


def get_timezone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"Unknown IANA time zone: {name}") from exc


def timezone_choices() -> list[str]:
    return sorted(available_timezones())
