# Third-party notices

AlbumMaker's own source code is licensed under the PolyForm Noncommercial
License 1.0.0 in [`LICENSE`](./LICENSE). This does **not** relicense third-party software or
data distributed with AlbumMaker. Each component remains under its own terms.

The executable build generates `THIRD_PARTY_NOTICES.txt` with the packaged
Python component license texts, the Qt for Python notice, the Python runtime
license, the PyInstaller bootloader notice, a pointer to the release's source
for the LGPL libraries, and the LGPL/GPL texts they refer to. Each download
has it next to the app, together with this file and `LICENSE`. Those
texts are stored in the repository's `licenses/` directory so builds do not
need to fetch them from a license website. Read the generated file alongside
this inventory for the specific executable you are using. Static sites that
include downloaded offline assets carry their notices under
`assets/vendor/licenses/`.

**FFmpeg is not part of AlbumMaker.** It is not bundled in the executable or in
the source, and AlbumMaker does not download it. The app can ask the user's
own package manager (winget or Homebrew) to install it, after the user agrees.
FFmpeg is licensed by its authors (LGPL or GPL, depending on the build); see
<https://ffmpeg.org/legal.html>. FFmpeg is a trademark of Fabrice Bellard.

## Components

| Component | Purpose | License / notice |
|---|---|---|
| FFmpeg and ffprobe | Read video metadata and generate video previews | **Not bundled.** Installed separately by the user; licensed by its authors (LGPL or GPL depending on the build). See [FFmpeg legal](https://ffmpeg.org/legal.html). |
| `pi-heif` and bundled libraries | Read and convert HEIF images (decode only) | The Python package is BSD-3-Clause. Its binary wheels bundle libheif and libde265 (LGPL-3.0), and contain no GPL encoder; the wheel's `LICENSES_bundled.txt` identifies versions and source locations. The executable notice includes it. |
| Pillow | Image reading and thumbnail generation | MIT-CMU / Pillow license; see the [Pillow project](https://github.com/python-pillow/Pillow). |
| Jinja2 and MarkupSafe | HTML templates and escaping | BSD-3-Clause; see [Jinja2](https://github.com/pallets/jinja) and [MarkupSafe](https://github.com/pallets/markupsafe). |
| PySide6, shiboken6 and Qt | Desktop GUI | © The Qt Company Ltd. and other contributors. Used under the LGPL-3.0 option (Qt for Python is also available under GPL or commercial terms). Qt contains third-party code under permissive licenses (FreeType, HarfBuzz, libpng, zlib, PCRE2, md4c, double-conversion, and ICU on Linux); see [Licenses used in Qt](https://doc.qt.io/qt-6/licenses-used-in-qt.html) and the [Qt for Python licensing information](https://doc.qt.io/qtforpython-6/licenses.html). Source: <https://download.qt.io/official_releases/QtForPython/>. |
| Python runtime | Runs the desktop app (bundled in the executables only) | Python Software Foundation License 2.0, with the licenses of the libraries Python includes (OpenSSL, libffi, zlib, expat, bzip2, xz, SQLite, mpdecimal). The full text is in the executable's `THIRD_PARTY_NOTICES.txt`. |
| PyInstaller bootloader | Starts the executables | GPL-2.0-or-later with the PyInstaller bootloader exception, which allows use with programs under any license. See [PyInstaller's license](https://github.com/pyinstaller/pyinstaller/blob/develop/COPYING.txt). |
| `tzdata` | IANA time-zone database | Apache-2.0; see the [tzdata project](https://github.com/python/tzdata). |
| Leaflet | Maps in generated sites | BSD-2-Clause; the license is included in `assets/vendor/licenses/leaflet-LICENSE.txt` when offline assets are bundled. |
| Fraunces, IBM Plex Sans and IBM Plex Mono | Fonts in generated sites | SIL Open Font License 1.1; the license files are included in `assets/vendor/licenses/` when offline assets are bundled. |
| GeoNames | Offline place-name database | Creative Commons Attribution 4.0; AlbumMaker credits GeoNames in pages that use place names. See [GeoNames](https://www.geonames.org/) and [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). |
| OpenStreetMap contributors | Default map tiles | Map data © OpenStreetMap contributors under the Open Database License (ODbL); the standard tile images are licensed separately as described on [copyright and license](https://www.openstreetmap.org/copyright). Every map shows the attribution with a link to that page. With a custom tile server (`--tile-url`), follow that provider's terms. |
| Natural Earth | Land outlines the maps fall back to without internet (`albummaker/static/world_land.json`) | Public domain; simplified from Natural Earth 1:110m land. See [terms of use](https://www.naturalearthdata.com/about/terms-of-use/). |

## LGPL libraries in executable distributions

The desktop executable bundles Qt (LGPL-3.0 option) and the libheif and
libde265 libraries from `pi-heif` (LGPL-3.0). The build includes their license
texts. Nothing in the AlbumMaker license restricts your rights under the LGPL
to modify or replace these libraries, or to reverse-engineer AlbumMaker to
debug such changes. AlbumMaker's noncommercial license applies only to
AlbumMaker's own code, not to these libraries.

The complete source of each release, including the script that builds the
executables (`tools/build_executable.py`), is published at
<https://github.com/pajotsetrapper/AlbumMaker> under the release's tag (for
example `v3.0.0`), so you can rebuild the app with modified versions of these
libraries. Library sources: Qt at <https://download.qt.io/official_releases/qt/>
and Qt for Python at <https://download.qt.io/official_releases/QtForPython/>;
libheif and libde265 as listed in the `pi-heif` section of
`THIRD_PARTY_NOTICES.txt` (from the package's `LICENSES_bundled.txt`) and at
<https://github.com/bigcat88/pi_heif>. On Python 3.15, `requirements.txt` installs `pillow-heif` for source users because `pi-heif` has no wheel there yet; that is the user's own install. Do **not** use `pillow-heif` for executable builds (the build script refuses): its binary
wheels include x265, which is GPL-2.0 and does not mix with a noncommercial
license. Obtain qualified legal advice before you sell or otherwise
redistribute the application.

## Data and other files

`albummaker/static/world_land.json` (land outlines for the map): simplified
from Natural Earth 1:110m land, which is in the public domain (see the table
above).

`docs/demo/media/` (the sample pictures in the demo site): illustrations made
by the author, Pieter Remi Coppens, for the demo; see
[`docs/demo/NOTICE.txt`](./docs/demo/NOTICE.txt).
The sample trip's texts and GPS track in `docs/demo-source/` are fictional and
written by the author.

`albummaker/gui/assets/albummaker.ico` (the app icon): original artwork made
for AlbumMaker by the author, Pieter Remi Coppens, with the help of GitHub
Copilot. It is part of AlbumMaker and covered by its license
([`LICENSE`](./LICENSE)).
