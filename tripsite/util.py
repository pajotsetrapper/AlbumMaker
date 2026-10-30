"""Small helpers with no knowledge of photos: atomic file writes, JSON that is
safe inside <script>, and reading files that ship inside the package (which
also works when the package runs from a zipapp)."""

from __future__ import annotations

import json
import os
import tempfile
from importlib import resources
from pathlib import Path

from markupsafe import Markup


def _write_atomically(path: Path, data: bytes, fsync: bool):
    """Write via a uniquely named temp file in the same folder + rename, so a
    crash or full disk never leaves a half-written file, and two threads or
    processes writing the same file can't trip over each other's temp file.

    A symlink is followed, so the file it points to is replaced and the link
    itself is kept (for example a trip_content.json linked into Dropbox)."""
    path = Path(os.path.realpath(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            if fsync:
                f.flush()
                os.fsync(f.fileno())
        try:
            # mkstemp creates the file 0600; keep the usual permissions instead
            mode = path.stat().st_mode & 0o777 if path.exists() else 0o666 & ~_UMASK
            os.chmod(tmp, mode)
        except OSError:
            pass
        os.replace(tmp, path)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _read_umask() -> int:
    # os.umask can only be read by setting it, which isn't thread-safe, so
    # it's done once, at import time, before any server threads exist
    mask = os.umask(0)
    os.umask(mask)
    return mask


_UMASK = _read_umask()


def atomic_write_text(path: Path, text: str):
    """Text file, fsynced: used for trip_content.json and the build record."""
    _write_atomically(Path(path), text.encode("utf-8"), fsync=True)


def atomic_write_bytes(path: Path, data: bytes):
    """Binary file (thumbnails, map tiles, vendor files). Not fsynced: these
    can always be rebuilt, and fsync per thumbnail is slow."""
    _write_atomically(Path(path), data, fsync=False)


def script_json(obj) -> Markup:
    """JSON that's safe to drop inside a <script> block: escapes the
    characters that could otherwise close the script tag or start an HTML
    comment/entity. json.dumps alone is NOT safe there."""
    s = (json.dumps(obj)
         .replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026"))
    return Markup(s)


def read_resource(*parts: str) -> str:
    """A text file shipped inside the tripsite package (static/, templates/)."""
    return resources.files("tripsite").joinpath(*parts).read_text(encoding="utf-8")


def project_dir() -> Path:
    """The folder holding the scripts (and the optional `vendor` folder). When
    running from a zipapp, that is the folder the .pyz file is in."""
    here = Path(__file__).resolve().parent.parent
    while not here.is_dir():          # .../tripsite.pyz/tripsite -> .../tripsite.pyz
        here = here.parent
    return here
