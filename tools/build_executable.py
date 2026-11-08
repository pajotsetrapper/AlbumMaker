#!/usr/bin/env python3
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
SOURCE_URL = "https://github.com/zackees/ffmpeg_bins"
LICENSES_DIR = ROOT / "licenses"
GPL_LICENSE_FILES = {
    "2": LICENSES_DIR / "GPL-2.0.txt",
    "3": LICENSES_DIR / "GPL-3.0.txt",
}
LGPL_V3_LICENSE_FILE = LICENSES_DIR / "LGPL-3.0.txt"


def _run(
    command: list[str], *, cwd: Path = ROOT, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess:
    return subprocess.run(command, cwd=cwd, env=env, check=True)


def _ffmpeg_tools() -> tuple[Path, Path]:
    try:
        from static_ffmpeg import run as static_ffmpeg_run
    except ImportError as exc:
        raise SystemExit(
            "Packaging dependencies are missing. Install them with "
            "'python -m pip install -r requirements-packaging.txt'."
        ) from exc
    platform_key = static_ffmpeg_run.get_platform_key()
    download_url = static_ffmpeg_run.PLATFORM_ZIP_FILES[platform_key]
    static_ffmpeg_run.PLATFORM_ZIP_FILES[platform_key] = download_url.replace(
        "https://github.com/zackees/ffmpeg_bins/raw/",
        "https://media.githubusercontent.com/media/zackees/ffmpeg_bins/",
    )
    ffmpeg, ffprobe = static_ffmpeg_run.get_or_fetch_platform_executables_else_raise()
    paths = (Path(ffmpeg), Path(ffprobe))
    for path in paths:
        if not path.is_file():
            raise RuntimeError(f"static-ffmpeg did not provide an executable: {path}")
    return paths


def _ffmpeg_notice(ffmpeg: Path, ffprobe: Path) -> str:
    details = []
    for tool in (ffmpeg, ffprobe):
        license_result = subprocess.run([str(tool), "-L"], check=True, capture_output=True, text=True)
        details.append(license_result.stdout.strip() or license_result.stderr.strip())
    ffmpeg_license = "\n\n".join(details)
    version_result = subprocess.run(
        [str(ffmpeg), "-version"], check=True, capture_output=True, text=True
    )
    version_info = version_result.stdout.strip() or version_result.stderr.strip()
    if "--enable-gpl" not in version_info or "--enable-nonfree" in version_info:
        raise RuntimeError("The bundled FFmpeg must enable GPL codecs and must not enable non-free codecs.")
    license_match = re.search(
        r"GNU General Public License.*?either version\s+([23]).*?any later version",
        ffmpeg_license,
        re.IGNORECASE | re.DOTALL,
    )
    if not license_match:
        raise RuntimeError("The bundled FFmpeg's GPL version could not be determined.")
    gpl_version = license_match.group(1)
    version_match = re.search(r"ffmpeg version\s+(\S+)", version_info, re.IGNORECASE)
    if not version_match:
        raise RuntimeError("The bundled FFmpeg version could not be determined.")
    ffmpeg_version = version_match.group(1)
    source_tag = f"n{ffmpeg_version}"
    gpl_license = _read_license_text(
        GPL_LICENSE_FILES[gpl_version], f"GNU GPL version {gpl_version}"
    )
    if gpl_version == "2":
        gpl_v2_license = gpl_license
    else:
        gpl_v2_license = _read_license_text(
            GPL_LICENSE_FILES["2"], "GNU GPL version 2"
        )
    lgpl_v3_license = _read_license_text(
        LGPL_V3_LICENSE_FILE, "GNU LGPL version 3"
    )
    python_notices = _python_package_notices()
    return (
        "AlbumMaker includes FFmpeg and ffprobe binaries provided by static-ffmpeg 3.0.\n"
        f"FFmpeg binary version: {ffmpeg_version}\n"
        f"FFmpeg source code: https://github.com/FFmpeg/FFmpeg/tree/{source_tag}\n"
        f"Binary provider repository (not verified as a corresponding-source archive): {SOURCE_URL}\n"
        f"FFmpeg build configuration:\n{version_info}\n\n"
        f"{python_notices}\n\n"
        "FFmpeg / ffprobe license information:\n"
        f"{ffmpeg_license}\n\n"
        "GNU GPL version 2 license text (also relevant to bundled x265):\n"
        f"{gpl_v2_license}\n\n"
        f"GNU GPL version {gpl_version} license text:\n"
        f"{gpl_license}\n\n"
        "GNU LGPL version 3 license text (relevant to bundled Qt and HEIF libraries):\n"
        f"{lgpl_v3_license}\n"
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
        ("pillow-heif", ("licenses/LICENSE.txt", "licenses/LICENSES_bundled.txt")),
        ("Jinja2", ("licenses/LICENSE.txt",)),
        ("MarkupSafe", ("licenses/LICENSE.txt",)),
        ("tzdata", ("LICENSE_APACHE",)),
        ("static-ffmpeg", ("licenses/LICENSE",)),
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

    ffmpeg, ffprobe = _ffmpeg_tools()
    notice = _ffmpeg_notice(ffmpeg, ffprobe)
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
            [
                "--onefile",
                "--add-binary",
                _binary_argument(ffmpeg, "ffmpeg"),
                "--add-binary",
                _binary_argument(ffprobe, "ffmpeg"),
            ],
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
