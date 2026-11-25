# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Pieter Remi Coppens
"""IANA time-zone helpers shared by the CLI and desktop app."""

from __future__ import annotations

import os
from datetime import date, datetime, timezone
from functools import lru_cache
from importlib.resources import files
from pathlib import Path
from typing import Iterator, Optional, Sequence
from zoneinfo import TZPATH, ZoneInfo, ZoneInfoNotFoundError, available_timezones

MAX_RECENT_ZONES = 5
UTC_LABEL = "UTC (Coordinated Universal Time)"

# Spellings of UTC. People pick "UTC"; the others are shown as the same thing.
_UTC_NAMES = frozenset({
    "UTC", "Etc/UTC", "Etc/UCT", "UCT", "Etc/Zulu", "Zulu", "Etc/Universal", "Universal",
})
# Only used when the database's own zone.tab cannot be read.
_HIDDEN_PREFIXES = (
    "Etc/", "SystemV/", "posix/", "right/", "US/", "Canada/", "Brazil/", "Chile/", "Mexico/",
)


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


@lru_cache(maxsize=1)
def known_timezones() -> frozenset[str]:
    """Every zone name the database has, including old aliases. Reads the whole database once."""
    return frozenset(timezone_choices())


def _read_zone_tab() -> Optional[str]:
    try:
        return (files("tzdata.zoneinfo") / "zone.tab").read_text(encoding="utf-8")
    except (ImportError, OSError):
        pass
    for folder in TZPATH:
        try:
            return (Path(folder) / "zone.tab").read_text(encoding="utf-8")
        except OSError:
            continue
    return None


@lru_cache(maxsize=1)
def _zone_tab_names() -> frozenset[str]:
    """The zones the database itself lists for countries (zone.tab): one real zone per
    place, without the old aliases, the Etc/GMT+N entries or the POSIX-style names."""
    names = set()
    for line in (_read_zone_tab() or "").splitlines():
        if line and not line.startswith("#"):
            columns = line.split("\t")
            if len(columns) >= 3:
                names.add(columns[2].strip())
    return frozenset(names)


@lru_cache(maxsize=1)
def selectable_timezones() -> tuple[str, ...]:
    """The zones offered in the full list: places people travel to, plus UTC. Old aliases
    and oddities (Etc/GMT+5, Factory, EST5EDT...) are left out, though a zone that is
    already set, or typed by its full name, is still accepted."""
    known = known_timezones()
    listed = _zone_tab_names()
    if listed:
        names = {name for name in listed if name in known}
    else:
        names = {name for name in known if "/" in name and not name.startswith(_HIDDEN_PREFIXES)}
    if "UTC" in known:
        names.add("UTC")
    return tuple(sorted(names))


def utc_offset_text(name: str, when: Optional[datetime] = None) -> str:
    """'UTC+01:00', 'UTC-05:00', 'UTC+05:30': the zone's offset at `when` (now, by default).
    Offsets change with daylight saving time. Empty if it cannot be worked out."""
    when = when or datetime.now(timezone.utc)
    try:
        offset = when.astimezone(get_timezone(name)).utcoffset()
    except (ValueError, OverflowError, OSError):
        return ""
    if offset is None:
        return ""
    minutes = round(offset.total_seconds() / 60)
    hours, rest = divmod(abs(minutes), 60)
    return f"UTC{'-' if minutes < 0 else '+'}{hours:02d}:{rest:02d}"


def timezone_label(name: str, when: Optional[datetime] = None) -> str:
    """The text people see and search for: 'Paris · UTC+02:00 · Europe'. An empty name
    is the Automatic setting. The offset is today's unless `when` says otherwise."""
    if not name:
        return "Automatic"
    if name in _UTC_NAMES:
        return UTC_LABEL
    city = name.rsplit("/", 1)[-1].replace("_", " ")
    region = name.split("/", 1)[0] if "/" in name else ""
    return " · ".join(part for part in (city, utc_offset_text(name, when), region) if part)


@lru_cache(maxsize=1)
def _search_choices_on(_day: date) -> tuple[tuple[str, str], ...]:
    return tuple(sorted(
        ((timezone_label(name), name) for name in selectable_timezones()),
        key=lambda choice: choice[0].casefold(),
    ))


def timezone_search_choices() -> tuple[tuple[str, str], ...]:
    """Human-searchable labels mapped to IANA zone names. Building the list reads the
    whole time-zone database, so it is done once a day (the day's offsets are in the
    labels) and kept."""
    return _search_choices_on(date.today())


def _local_timezone_candidates() -> Iterator[str]:
    configured = os.environ.get("TZ", "").lstrip(":")
    if configured:
        yield configured
    try:
        target = os.readlink("/etc/localtime")      # macOS and Linux; one hop, so a zone that
    except OSError:                                 # is itself a link keeps its own name
        target = ""
    if "zoneinfo/" in target:
        name = target.split("zoneinfo/", 1)[1]
        for prefix in ("posix/", "right/"):
            if name.startswith(prefix):
                name = name[len(prefix):]
        yield name
    try:
        name = Path("/etc/timezone").read_text(encoding="utf-8").strip()
    except OSError:
        name = ""
    if name:
        yield name
    try:                                            # optional; the only way on Windows
        import tzlocal
        yield tzlocal.get_localzone_name()
    except Exception:
        pass


@lru_cache(maxsize=1)
def local_timezone_name() -> str:
    """This computer's IANA zone, or '' when it cannot be told reliably. It is never guessed
    from the UTC offset, because many zones share one."""
    known = known_timezones()
    for name in _local_timezone_candidates():
        name = (name or "").strip()
        if name in _UTC_NAMES:
            return "UTC" if "UTC" in known else ""
        if name in known:
            return name
    return ""


def timezone_dropdown_groups(current: str = "",
                             recent: Sequence[str] = ()) -> list[list[tuple[str, str]]]:
    """The time-zone list as groups of (label, zone name), for a separator between groups:

    1. Automatic (and the zone already set, if the full list leaves it out);
    2. this computer's zone, the recently used zones, UTC;
    3. every other zone, A to Z.

    A zone is listed once, in the first group that has it."""
    known = known_timezones()
    selectable = set(selectable_timezones())
    used: set[str] = set()

    def entry(zone: str, label: str = "") -> Optional[tuple[str, str]]:
        if not zone or zone in used or zone not in known:
            return None
        used.add(zone)
        return (label or timezone_label(zone), zone)

    top = [("Automatic", "")]
    if current and current not in selectable:
        top.extend(filter(None, [entry(current)]))
    pinned = []
    local = local_timezone_name()
    if local:
        pinned.extend(filter(None, [entry(local, f"{timezone_label(local)} · this computer")]))
    for zone in recent:
        pinned.extend(filter(None, [entry(zone)]))
    pinned.extend(filter(None, [entry("UTC")]))
    rest = [(label, zone) for label, zone in timezone_search_choices() if zone not in used]
    return [group for group in (top, pinned, rest) if group]


def remember_recent_zone(recent: Sequence[str], zone: str,
                         limit: int = MAX_RECENT_ZONES) -> list[str]:
    """`recent` with `zone` moved to the front, at most `limit` long."""
    if not zone:
        return list(recent)[:limit]
    return ([zone] + [name for name in recent if name != zone])[:limit]


def clean_recent_zones(value: object) -> list[str]:
    """Recently used zones read from a settings file: whatever is not a usable zone name
    (an old file, a zone the database no longer has) is dropped rather than reported."""
    if not isinstance(value, list):
        return []
    cleaned: list[str] = []
    for name in value:
        if not isinstance(name, str) or not name or name in cleaned:
            continue
        try:
            get_timezone(name)
        except ValueError:
            continue
        cleaned.append(name)
    return cleaned[:MAX_RECENT_ZONES]
