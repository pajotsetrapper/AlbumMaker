#!/usr/bin/env python3
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Pieter Remi Coppens
"""Build a self-contained AlbumMaker desktop app for the current OS."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = ROOT / "dist" / "executable"
LICENSES_DIR = ROOT / "licenses"
GPL_V3_LICENSE_FILE = LICENSES_DIR / "GPL-3.0.txt"
LGPL_V3_LICENSE_FILE = LICENSES_DIR / "LGPL-3.0.txt"


def _run(
    command: list[str], *, cwd: Path = ROOT, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess:
    return subprocess.run(command, cwd=cwd, env=env, check=True)


def _notice() -> str:
    """License notices shipped inside the app.

    AlbumMaker does not bundle FFmpeg: it is installed separately by the user
    (see albummaker/ffmpeg_setup.py), so no FFmpeg or GPL codec notices are
    needed here. The bundled HEIF decoder (pi-heif) contains LGPL libraries only.
    """
    lgpl_v3_license = _read_license_text(LGPL_V3_LICENSE_FILE, "GNU LGPL version 3")
    gpl_v3_license = _read_license_text(GPL_V3_LICENSE_FILE, "GNU GPL version 3")
    return (
        "AlbumMaker is licensed under the PolyForm Noncommercial License 1.0.0 (see LICENSE).\n"
        "AlbumMaker does not include FFmpeg. If you install FFmpeg separately, it is\n"
        "licensed by its authors; see https://ffmpeg.org/legal.html.\n\n"
        f"{_python_package_notices()}\n\n"
        "GNU LGPL version 3 license text (relevant to bundled Qt and HEIF libraries):\n"
        f"{lgpl_v3_license}\n\n"
        "GNU GPL version 3 license text (the LGPL version 3 is an additional\n"
        "permission on top of it):\n"
        f"{gpl_v3_license}\n"
    )


def _read_license_text(path: Path, license_name: str) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise RuntimeError(
            f"Could not read {license_name} text from {path}: {exc}"
        ) from exc


def _python_package_notices() -> str:
    from importlib.metadata import distribution

    packages = (
        ("Pillow", ("licenses/LICENSE",)),
        ("pi-heif", ("licenses/LICENSE.txt", "licenses/LICENSES_bundled.txt")),
        ("Jinja2", ("licenses/LICENSE.txt",)),
        ("MarkupSafe", ("licenses/LICENSE.txt",)),
        ("tzdata", ("LICENSE_APACHE",)),
    )
    sections = ["Third-party Python package licenses and bundled-library notices:"]
    for package_name, required_files in packages:
        package = distribution(package_name)
        paths = {path.as_posix(): path for path in package.files or ()}
        sections.append(f"\n--- {package.metadata['Name']} {package.version} ---")
        for required_file in required_files:
            path = next(
                (file for name, file in paths.items() if name.endswith(required_file)),
                None,
            )
            if path is None:
                raise RuntimeError(
                    f"Could not find {required_file} for {package.metadata['Name']} "
                    f"{package.version}; refusing to build without its license notice."
                )
            sections.append(
                f"\n[{required_file}]\n"
                f"{Path(package.locate_file(path)).read_text(encoding='utf-8').strip()}"
            )
    return "\n".join(sections)


def _pyinstaller(
    entry: Path,
    name: str,
    dist: Path,
    work: Path,
    extra: list[str],
) -> None:
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--name",
        name,
        "--distpath",
        str(dist),
        "--workpath",
        str(work),
        "--specpath",
        str(work),
        "--paths",
        str(ROOT),
        "--collect-data",
        "albummaker",
        "--collect-all",
        "tzdata",
        "--hidden-import",
        "albummaker.build",
        "--hidden-import",
        "albummaker.server",
        *extra,
        str(entry),
    ]
    _run(command)


def _binary_argument(source: Path, destination: str) -> str:
    separator = ";" if os.name == "nt" else ":"
    return f"{source}{separator}{destination}"


def _data_argument(source: Path, destination: str) -> str:
    separator = ";" if os.name == "nt" else ":"
    return f"{source}{separator}{destination}"


def _build(output: Path) -> Path:
    output = output.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    if output == ROOT:
        raise ValueError(f"Choose a build output folder below or outside the source tree: {output}")

    notice = _notice()
    extension = ".exe" if os.name == "nt" else ""

    with tempfile.TemporaryDirectory(prefix="albummaker-build-") as temporary:
        work_root = Path(temporary)
        notice_file = work_root / "THIRD_PARTY_NOTICES.txt"
        notice_file.write_text(notice, encoding="utf-8")
        cli_dist = work_root / "cli-dist"
        gui_dist = work_root / "gui-dist"
        cli_work = work_root / "cli-work"
        gui_work = work_root / "gui-work"
        cli_entry = ROOT / "tools" / "frozen_cli.py"

        _pyinstaller(
            cli_entry,
            "albummaker-cli",
            cli_dist,
            cli_work,
            ["--onefile", "--collect-all", "pi_heif"],
        )
        cli_binary = cli_dist / f"albummaker-cli{extension}"
        if not cli_binary.is_file():
            raise RuntimeError(f"PyInstaller did not create the CLI helper: {cli_binary}")
        _run([str(cli_binary), "--packaging-smoke-test"])
        for subcommand in ("serve", "build"):
            _run([str(cli_binary), subcommand, "--help"])

        gui_extra = [
            "--windowed",
            "--onedir",
            "--add-binary",
            _binary_argument(cli_binary, "bin"),
            "--add-data",
            _data_argument(notice_file, "."),
            "--add-data",
            _data_argument(ROOT / "LICENSE", "."),
            "--add-data",
            _data_argument(ROOT / "THIRD_PARTY_NOTICES.md", "."),
        ]
        if os.name == "nt":
            gui_extra.extend(("--icon", str(ROOT / "albummaker" / "gui" / "assets" / "albummaker.ico")))
        if sys.platform == "darwin":
            gui_extra.extend(("--osx-bundle-identifier", "com.albummaker.desktop"))
        _pyinstaller(ROOT / "album_gui.py", "AlbumMaker", gui_dist, gui_work, gui_extra)

        built = gui_dist / "AlbumMaker"
        if sys.platform == "darwin":
            built = gui_dist / "AlbumMaker.app"
        if not built.exists():
            raise RuntimeError(f"PyInstaller did not create the desktop app: {built}")
        gui_executable = built / "Contents" / "MacOS" / "AlbumMaker" if sys.platform == "darwin" else (
            built / f"AlbumMaker{extension}"
        )
        smoke_env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
        _run([str(gui_executable), "--packaging-smoke-test"], env=smoke_env)

        if not list(gui_dist.rglob(f"bin/albummaker-cli{extension}")):
            raise RuntimeError("The CLI helper was not included in the desktop app.")

        target = output / built.name
        if target.is_symlink():
            raise RuntimeError(f"Refusing to replace a symlink build target: {target}")
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()
        if built.is_dir():
            shutil.copytree(built, target)
        else:
            shutil.copy2(built, target)

    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Build output folder.")
    args = parser.parse_args(argv)
    target = _build(args.output)
    print(f"Built self-contained desktop app: {target}")
    print("Project and third-party license notices are included with the app.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
