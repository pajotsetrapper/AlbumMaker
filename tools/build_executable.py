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
# Run as a script (python tools/build_executable.py), Python only looks for
# imports next to this file; the notices need albummaker.__version__.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DEFAULT_OUTPUT = ROOT / "dist" / "executable"
LICENSES_DIR = ROOT / "licenses"
GPL_V3_LICENSE_FILE = LICENSES_DIR / "GPL-3.0.txt"
LGPL_V3_LICENSE_FILE = LICENSES_DIR / "LGPL-3.0.txt"


def _run(
    command: list[str], *, cwd: Path = ROOT, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess:
    return subprocess.run(command, cwd=cwd, env=env, check=True)


REPOSITORY_URL = "https://github.com/pajotsetrapper/AlbumMaker"
NOTICE_FILES = ("LICENSE", "THIRD_PARTY_NOTICES.md")


def _notice() -> str:
    """License notices shipped inside the app.

    AlbumMaker does not bundle FFmpeg: it is installed separately by the user
    (see albummaker/ffmpeg_setup.py), so no FFmpeg or GPL codec notices are
    needed here. The bundled HEIF decoder (pi-heif) contains LGPL libraries only.
    """
    from albummaker import __version__

    lgpl_v3_license = _read_license_text(LGPL_V3_LICENSE_FILE, "GNU LGPL version 3")
    gpl_v3_license = _read_license_text(GPL_V3_LICENSE_FILE, "GNU GPL version 3")
    return (
        f"AlbumMaker {__version__} is licensed under the PolyForm Noncommercial\n"
        "License 1.0.0 (see LICENSE). That license covers AlbumMaker's own code only;\n"
        "the components below keep their own licenses.\n\n"
        "AlbumMaker does not include FFmpeg. If you install FFmpeg separately, it is\n"
        "licensed by its authors; see https://ffmpeg.org/legal.html.\n\n"
        f"{_lgpl_source_notice(__version__)}\n\n"
        f"{_qt_notice()}\n\n"
        f"{_python_runtime_notice()}\n\n"
        "--- PyInstaller bootloader ---\n"
        "The program that starts this app is the PyInstaller bootloader, licensed\n"
        "under the GNU GPL version 2 or later with the PyInstaller bootloader\n"
        "exception, which allows it to be used with programs under any license.\n"
        "See https://github.com/pyinstaller/pyinstaller/blob/develop/COPYING.txt.\n\n"
        f"{_python_package_notices()}\n\n"
        "GNU LGPL version 3 license text (relevant to bundled Qt and HEIF libraries):\n"
        f"{lgpl_v3_license}\n\n"
        "GNU GPL version 3 license text (the LGPL version 3 is an additional\n"
        "permission on top of it):\n"
        f"{gpl_v3_license}\n"
    )


def _lgpl_source_notice(version: str) -> str:
    return (
        "--- LGPL libraries: your rights ---\n"
        "This app contains Qt (PySide6, shiboken6) and libheif and libde265 (inside\n"
        "pi-heif), used under the GNU LGPL version 3. Nothing in AlbumMaker's license\n"
        "restricts your rights under the LGPL to modify or replace these libraries,\n"
        "or to reverse-engineer AlbumMaker to debug such changes.\n"
        "The complete source of this AlbumMaker release and the script that built\n"
        f"this app are at {REPOSITORY_URL}/tree/v{version}\n"
        "(tools/build_executable.py), so you can rebuild it with modified versions\n"
        "of these libraries. Library sources:\n"
        "  Qt and Qt for Python: https://download.qt.io/official_releases/qt/ and\n"
        "    https://download.qt.io/official_releases/QtForPython/\n"
        "  libheif and libde265: see the pi-heif LICENSES_bundled.txt section below\n"
        "    and https://github.com/bigcat88/pi_heif"
    )


def _qt_notice() -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        qt_version = version("PySide6")
    except PackageNotFoundError:
        qt_version = "(version unknown)"
    return (
        f"--- PySide6, shiboken6 and Qt {qt_version} ---\n"
        "Copyright (C) The Qt Company Ltd. and other contributors.\n"
        "Used under the GNU LGPL version 3 (text below). See\n"
        "https://doc.qt.io/qtforpython-6/licenses.html.\n"
        "Qt itself includes third-party code under permissive licenses, such as\n"
        "FreeType (FreeType License), HarfBuzz (MIT), libpng (libpng License),\n"
        "zlib (zlib License), PCRE2 (BSD-3-Clause), md4c (MIT), double-conversion\n"
        "(BSD-3-Clause) and, on Linux, ICU (Unicode License). Their copyright\n"
        f"notices are listed at https://doc.qt.io/qt-6/licenses-used-in-qt.html\n"
        "for this Qt version."
    )


def _python_license_file() -> Path:
    import sysconfig

    candidates = [
        Path(sysconfig.get_paths()["stdlib"]) / "LICENSE.txt",
        Path(sys.base_prefix) / "LICENSE.txt",
        Path(sys.base_prefix) / "LICENSE",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise RuntimeError(
        "Could not find the Python license file (looked in: "
        + ", ".join(str(c) for c in candidates)
        + "); refusing to build without the Python runtime's license notice."
    )


def _python_runtime_notice() -> str:
    path = _python_license_file()
    return (
        f"--- Python {sys.version.split()[0]} runtime ---\n"
        "The app includes the Python interpreter and standard library, with the\n"
        "libraries it uses (such as OpenSSL, libffi, zlib, expat, bzip2, xz,\n"
        "SQLite and mpdecimal). Their licenses follow.\n\n"
        f"{path.read_text(encoding='utf-8', errors='replace').strip()}"
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
        # Put the license files next to the app as well, where people see them
        # when they unpack the download (inside the app they are hard to find).
        shutil.copy2(notice_file, output / notice_file.name)
        for name in NOTICE_FILES:
            shutil.copy2(ROOT / name, output / name)

    return target


def _require_decode_only_heif() -> None:
    """Refuse to build with pillow-heif: its wheels contain the GPL x265 encoder."""
    from importlib.util import find_spec
    if find_spec("pi_heif") is None or find_spec("pillow_heif") is not None:
        raise SystemExit(
            "Executables must be built with pi-heif and without pillow-heif, "
            "whose wheels bundle the GPL x265 encoder. Use Python 3.9 to 3.14 "
            "(pi-heif has no Python 3.15 wheel yet) in a clean environment, then "
            "run: python -m pip uninstall pillow-heif && python -m pip install pi-heif"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Build output folder.")
    args = parser.parse_args(argv)
    _require_decode_only_heif()
    target = _build(args.output)
    print(f"Built self-contained desktop app: {target}")
    print("Project and third-party license notices are included with the app.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
