# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Pieter Remi Coppens
"""Help people install FFmpeg, which AlbumMaker uses but does not ship.

AlbumMaker never contains or downloads FFmpeg itself. FFmpeg is separate
software with its own license (LGPL or GPL, depending on how a given build was
made). This module only runs the user's own package manager (winget on
Windows, Homebrew on macOS and Linux) after the user agrees, or explains how
to install it by hand. The package manager downloads FFmpeg from its own
sources, so AlbumMaker does not redistribute it.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

from .app_paths import config_dir
from .util import external_tool_env

FFMPEG_LEGAL_URL = "https://ffmpeg.org/legal.html"
FFMPEG_DOWNLOAD_URL = "https://ffmpeg.org/download.html"
WINGET_PACKAGE_ID = "Gyan.FFmpeg"
TOOLS = ("ffmpeg", "ffprobe")

LICENSE_NOTE = (
    "FFmpeg is separate software that is not part of AlbumMaker. It is made by "
    "the FFmpeg project and other authors and is licensed by them (LGPL or GPL, "
    f"depending on the build). See {FFMPEG_LEGAL_URL}. FFmpeg is a trademark "
    "of Fabrice Bellard."
)


def user_tool_dir() -> Path:
    """Folder where a person can place their own ffmpeg and ffprobe programs."""
    return config_dir() / "ffmpeg"


def user_search_dirs() -> list[str]:
    folder = user_tool_dir()
    return [str(folder), str(folder / "bin")]


def system_search_dirs() -> list[str]:
    """Usual install folders that a desktop app's PATH often misses.

    Apps started from Finder or the Start menu do not see Homebrew, and a
    program installed by winget is not on the PATH of programs that were
    already running.
    """
    dirs: list[str] = []
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA")
        if local:
            dirs.append(str(Path(local) / "Microsoft" / "WinGet" / "Links"))
    elif sys.platform == "darwin":
        dirs += ["/opt/homebrew/bin", "/usr/local/bin"]
    else:
        dirs += ["/home/linuxbrew/.linuxbrew/bin", str(Path.home() / ".linuxbrew" / "bin")]
    return dirs


def _declined_marker() -> Path:
    return config_dir() / "ffmpeg-prompt-declined"


def was_declined() -> bool:
    return _declined_marker().exists()


def mark_declined() -> None:
    try:
        _declined_marker().parent.mkdir(parents=True, exist_ok=True)
        _declined_marker().write_text("The user chose not to be asked about FFmpeg again.\n",
                                      encoding="utf-8")
    except OSError:
        pass


def ffmpeg_missing() -> bool:
    from .metadata import find_multimedia_tool
    return any(find_multimedia_tool(tool) is None for tool in TOOLS)


def _brew() -> Optional[str]:
    found = shutil.which("brew")
    if found:
        return found
    for candidate in ("/opt/homebrew/bin/brew", "/usr/local/bin/brew",
                      "/home/linuxbrew/.linuxbrew/bin/brew"):
        if Path(candidate).is_file():
            return candidate
    return None


def install_plan() -> Optional[list[str]]:
    """The package-manager command that installs FFmpeg, or None if there is none."""
    if sys.platform == "win32":
        winget = shutil.which("winget")
        if winget:
            return [winget, "install", "--id", WINGET_PACKAGE_ID, "-e",
                    "--accept-package-agreements", "--accept-source-agreements"]
        return None
    brew = _brew()
    if brew:
        return [brew, "install", "ffmpeg"]
    return None


def suggested_command() -> str:
    """One command the user can paste into a terminal to install FFmpeg."""
    if sys.platform == "win32":
        return f"winget install {WINGET_PACKAGE_ID}"
    if sys.platform == "darwin":
        return "brew install ffmpeg"
    if shutil.which("dnf"):
        return "sudo dnf install ffmpeg-free"
    if shutil.which("pacman"):
        return "sudo pacman -S ffmpeg"
    return "sudo apt install ffmpeg"


def manual_instructions() -> str:
    folder = user_tool_dir()
    lines = ["FFmpeg could not be installed automatically. Install it yourself:"]
    if sys.platform == "win32":
        lines.append("  - Windows: winget install Gyan.FFmpeg   (or download from "
                     f"{FFMPEG_DOWNLOAD_URL})")
    elif sys.platform == "darwin":
        lines.append("  - macOS: install Homebrew (https://brew.sh), then run: brew install ffmpeg")
    else:
        lines += ["  - Debian/Ubuntu: sudo apt install ffmpeg",
                  "  - Fedora: sudo dnf install ffmpeg-free",
                  "  - Arch: sudo pacman -S ffmpeg"]
    lines += [f"  - Or pick a build from {FFMPEG_DOWNLOAD_URL} and copy ffmpeg and ffprobe "
              f"into: {folder}",
              "Then restart AlbumMaker."]
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(
        prog="albummaker install-ffmpeg",
        description="Install FFmpeg with your system's package manager. "
                    "AlbumMaker does not include FFmpeg.")
    parser.add_argument("--yes", action="store_true",
                        help="do not ask for confirmation")
    parser.add_argument("--print-only", action="store_true",
                        help="only show the command that would be run")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    from .metadata import find_multimedia_tool
    if not ffmpeg_missing():
        print(f"FFmpeg is already installed: {find_multimedia_tool('ffmpeg')}")
        return 0

    print(LICENSE_NOTE)
    plan = install_plan()
    if plan is None:
        print(manual_instructions())
        return 1
    command = " ".join(f'"{part}"' if " " in part else part for part in plan)
    print(f"Command: {command}")
    if args.print_only:
        return 0
    if not args.yes:
        if not sys.stdin or not sys.stdin.isatty():
            print("Not running without confirmation. Re-run with --yes to agree.")
            return 2
        if input("Install FFmpeg now? [y/N] ").strip().lower() not in ("y", "yes"):
            print("Cancelled.")
            return 2
    try:
        code = subprocess.run(plan, env=external_tool_env()).returncode
    except OSError as exc:
        print(f"Could not start the installer: {exc}")
        print(manual_instructions())
        return 1
    if code != 0:
        print(f"The installer exited with code {code}.")
        print(manual_instructions())
        return code
    if ffmpeg_missing():
        print("FFmpeg was installed, but AlbumMaker cannot see it yet. "
              "Restart AlbumMaker (or sign out and in again).")
        return 0
    print("FFmpeg is ready.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
