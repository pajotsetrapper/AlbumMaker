# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Pieter Remi Coppens
"""Per-user application paths shared by the GUI and command-line tools."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def config_dir() -> Path:
    override = os.environ.get("ALBUMMAKER_CONFIG_DIR")
    if override:
        return Path(override).expanduser()
    if sys.platform == "win32":
        base = os.environ.get("APPDATA")
        return (Path(base) if base else Path.home() / "AppData" / "Roaming") / "AlbumMaker"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "AlbumMaker"
    base = os.environ.get("XDG_CONFIG_HOME")
    return (Path(base) if base else Path.home() / ".config") / "albummaker"


def settings_path() -> Path:
    return config_dir() / "settings.json"


def offline_assets_dir() -> Path:
    return config_dir() / "offline-assets"
