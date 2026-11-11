# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Pieter Remi Coppens
"""trip_content.json: titles, descriptions, captions, favorites, hidden
photos and day covers. The user's text is never lost: a file that can't be
parsed is never written, and only the field being changed is touched."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Optional

from .util import atomic_write_text

CONTENT_FILENAME = "trip_content.json"


class ContentFileError(Exception):
    """trip_content.json exists but can't be used as-is. Never write to the
    file when this is raised; the user's text must stay untouched."""


def default_content() -> dict:
    return {"title": "My Trip", "subtitle": "", "days": {}, "photos": {}}


def parse_content_file(path: Path) -> dict:
    """Read and validate trip_content.json. Raises ContentFileError with a
    human-readable message (including line/column for JSON syntax errors)
    instead of silently falling back to defaults."""
    try:
        # utf-8-sig: tolerate the BOM that Windows Notepad adds on save
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as e:
        raise ContentFileError(f"could not read {path}: {e}") from e
    try:
        loaded = json.loads(text)
    except json.JSONDecodeError as e:
        line = e.lineno
        if "trailing comma" in e.msg.lower():
            closing = text.find("}" if "object" in e.msg.lower() else "]", e.pos + 1)
            if closing >= 0:
                line = text.count("\n", 0, closing) + 1
        raise ContentFileError(
            f"{path} is not valid JSON: {e.msg} at line {line}, column {e.colno}. "
            f"(A trailing comma after the last entry, or a missing comma between "
            f"entries, are the usual suspects.)"
        ) from e
    if not isinstance(loaded, dict):
        raise ContentFileError(f"{path}: the top level must be an object, i.e. {{ ... }}")
    days = loaded.get("days")
    if days is None:
        days = {}
    if not isinstance(days, dict):
        raise ContentFileError(f'{path}: "days" must be an object mapping dates to entries')
    photos = loaded.get("photos")
    if photos is not None and not isinstance(photos, dict):
        raise ContentFileError(f'{path}: "photos" must be an object mapping file paths to entries')
    content = default_content()
    content.update({k: v for k, v in loaded.items() if k != "days"})
    content["days"] = days
    return content


def _text(v) -> str:
    """Coerce a value from the hand-edited JSON into display text, so a
    null or a number in the file can't crash page rendering."""
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    if isinstance(v, list):  # tolerate a list of paragraphs
        return "\n\n".join(_text(x) for x in v)
    return str(v)


def trip_title(content: dict) -> str:
    return _text(content.get("title")).strip() or "My Trip"


def day_text(content: dict, date_key: str) -> tuple:
    """(title, description) for a day, both plain strings."""
    entry = content.get("days", {}).get(date_key)
    if not isinstance(entry, dict):
        entry = {}
    return _text(entry.get("title")).strip(), _text(entry.get("description")).strip()


# --------------------------------------------------------------------------
# Per-photo and per-day edits, stored in trip_content.json:
#
#   "photos": { "<path relative to the photos folder>":
#                 {"caption": "...", "favorite": true, "hidden": true} }
#   "days":   { "<date>": {"title": "...", "description": "...",
#                          "cover": "<same kind of relative path>"} }
#
# Only values that differ from the defaults are stored. Anything else in the
# file (including keys this code doesn't know) is left exactly as it was.
# --------------------------------------------------------------------------

CAPTION_MAX = 2000



def photo_entry(content: dict, rel, aliases=()) -> dict:
    """Caption / favorite / hidden for one photo, as clean plain values.
    `rel` may also be a MediaItem; then its byte-identical duplicates count
    too (the first entry with a caption supplies it, and a favorite or hidden
    flag on any of them applies)."""
    if not isinstance(rel, str):
        rel, aliases = rel.rel, rel.aliases
    photos = content.get("photos")
    photos = photos if isinstance(photos, dict) else {}
    entries = [e for e in (photos.get(k) for k in [rel, *aliases]) if isinstance(e, dict)]
    caption = next((_text(e.get("caption")).strip() for e in entries if _text(e.get("caption")).strip()), "")
    return {"caption": caption,
            "favorite": any(e.get("favorite") is True for e in entries),
            "hidden": any(e.get("hidden") is True for e in entries)}


def hidden_rels(content: dict) -> set:
    photos = content.get("photos")
    if not isinstance(photos, dict):
        return set()
    return {rel for rel, e in photos.items() if isinstance(e, dict) and e.get("hidden") is True}


def is_hidden(item, hidden: set) -> bool:
    """True if the file, or any byte-identical copy of it, is marked hidden.
    Hiding one copy must never let another copy of the same photo through."""
    return any(k in hidden for k in item.content_keys)


def _day_entry(content: dict, date_key: str) -> dict:
    days = content.get("days")
    if not isinstance(days, dict):
        days = content["days"] = {}
    entry = days.get(date_key)
    if not isinstance(entry, dict):
        entry = days[date_key] = {}
    return entry


def set_photo_fields(content: dict, rel: str, caption=None, favorite=None, hidden=None,
                     aliases=()) -> dict:
    """Change a photo's fields in `content` (in place). Returns its new entry.
    Values are stored on `rel`. Clearing a field also clears it on `aliases`
    (the photo's byte-identical duplicates), since photo_entry merges them:
    otherwise "unhide" could leave the photo hidden through a copy."""
    photos = content.get("photos")
    if not isinstance(photos, dict):
        photos = content["photos"] = {}
    changes = {}
    if caption is not None:
        changes["caption"] = caption.strip()[:CAPTION_MAX]
    if favorite is not None:
        changes["favorite"] = bool(favorite)
    if hidden is not None:
        changes["hidden"] = bool(hidden)
    for key in [rel, *[a for a in aliases if a != rel]]:
        entry = photos.get(key)
        if key != rel and not isinstance(entry, dict):
            continue
        entry = dict(entry) if isinstance(entry, dict) else {}
        for field, value in changes.items():
            if key == rel or not value:
                entry[field] = value
        for field in ("caption", "favorite", "hidden"):   # store only non-default values
            if not entry.get(field):
                entry.pop(field, None)
        if entry:
            photos[key] = entry
        else:
            photos.pop(key, None)
    return photo_entry(content, rel, aliases)


def set_cover(content: dict, date_key: str, rel: Optional[str]):
    entry = _day_entry(content, date_key)
    if rel:
        entry["cover"] = rel
    else:
        entry.pop("cover", None)


def set_trip_cover(content: dict, rel: Optional[str]):
    """Set or clear the trip-level cover photo stored at the top level of
    trip_content.json."""
    if rel:
        content["cover"] = rel
    else:
        content.pop("cover", None)


def set_day_text(content: dict, date_key: str, title=None, description=None):
    entry = _day_entry(content, date_key)
    if title is not None:
        entry["title"] = title.strip()
    if description is not None:
        entry["description"] = description.replace("\r\n", "\n").strip()


def report_unknown_photo_keys(content: dict, known_rels):
    """Captions/flags for files that weren't found (renamed or moved?) stay in
    the file untouched; this just says so."""
    photos = content.get("photos")
    if not isinstance(photos, dict):
        return
    missing = sorted(set(photos) - set(known_rels))
    if missing:
        shown = ", ".join(missing[:3]) + (", ..." if len(missing) > 3 else "")
        print(f"Note: trip_content.json has captions/flags for {len(missing)} file(s) that "
              f"weren't found ({shown}). They're kept, but only apply to that exact path.")


def write_content(path: Path, content: dict):
    atomic_write_text(path, json.dumps(content, indent=2, ensure_ascii=False) + "\n")


def load_or_init_content(content_path: Path, day_keys, write: bool = True,
                         title: Optional[str] = None) -> dict:
    """Load trip_content.json and add blank entries for any new days. Only
    ever adds; never overwrites. If the existing file can't be parsed this
    raises ContentFileError WITHOUT writing anything, since falling back to
    defaults and saving would wipe every title and description. With
    write=False (--read-only) nothing is written at all. A new file starts
    with `title` as the trip's title, if given."""
    exists = content_path.exists()
    content = parse_content_file(content_path) if exists else default_content()
    if not exists and title:
        content["title"] = title
    changed = not exists
    for key in day_keys:
        if key not in content["days"]:
            content["days"][key] = {"title": "", "description": ""}
            changed = True
    if changed and write:
        write_content(content_path, content)
        print(f"-> wrote {content_path} (fill in titles and descriptions any time)")
    return content


def resolve_content_path(given: Optional[Path], input_dir: Path, fallback: Optional[Path] = None) -> Path:
    """Where trip_content.json lives. Given with -c: that file. Otherwise the
    photos folder, so the notes travel with the photos. Earlier versions
    used the folder the script was run from; such a file is still found and
    used (with a note) when the photos folder has none yet, and the current
    folder is the fallback when the photos folder can't be written to.

    A trip in a library passes its own `fallback` file instead: the current
    folder is shared by every trip, so it must not be looked at at all."""
    if given is not None:
        return Path(given)
    preferred = Path(input_dir) / CONTENT_FILENAME
    if fallback is not None:
        fallback = Path(fallback)
        if preferred.exists():
            return preferred
        if fallback.exists():
            # used before, while the photos folder was read-only: keep using it
            # rather than starting an empty file now that the folder is writable
            print(f"Note: using {fallback} for {input_dir}. Move it to {preferred} to keep the notes "
                  f"with the photos.", file=sys.stderr)
            return fallback
        if os.access(input_dir, os.W_OK):
            return preferred
        print(f"Note: the photos folder {input_dir} can't be written to, so its titles and notes "
              f"go in {fallback}.", file=sys.stderr)
        return fallback
    legacy = Path.cwd() / CONTENT_FILENAME
    try:
        same = preferred.exists() and legacy.exists() and os.path.samefile(preferred, legacy)
    except OSError:
        same = False
    if preferred.exists():
        if legacy.exists() and not same:
            print(f"Note: using {preferred}. There is also a {legacy}, which is ignored "
                  f"(pass -c {legacy} to use that one instead).", file=sys.stderr)
        return preferred
    if legacy.exists():
        print(f"Note: using {legacy} from the current folder (where earlier versions kept it). "
              f"Move it into your photos folder ({preferred}) to keep it with the photos.",
              file=sys.stderr)
        return legacy
    if not os.access(input_dir, os.W_OK):
        print(f"Note: the photos folder can't be written to, so titles and notes go in {legacy}.",
              file=sys.stderr)
        return legacy
    return preferred
