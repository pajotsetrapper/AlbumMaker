# Third-party notices

AlbumMaker's own source code is licensed under the PolyForm Noncommercial
License 1.0.0 in [`LICENSE`](./LICENSE). This does **not** relicense third-party software or
data distributed with AlbumMaker. Each component remains under its own terms.

The executable build generates `THIRD_PARTY_NOTICES.txt` with the packaged
Python component license texts and the LGPL/GPL texts they refer to. Those
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
| PySide6 and Qt | Desktop GUI | Qt for Python is available under LGPLv3, GPLv2/GPLv3, or commercial terms. See the [Qt for Python licensing information](https://doc.qt.io/qtforpython-6/licenses.html) and the per-build notices for included license texts. |
| `tzdata` | IANA time-zone database | Apache-2.0; see the [tzdata project](https://github.com/python/tzdata). |
| Leaflet | Maps in generated sites | BSD-2-Clause; the license is included in `assets/vendor/licenses/leaflet-LICENSE.txt` when offline assets are bundled. |
| Fraunces, IBM Plex Sans and IBM Plex Mono | Fonts in generated sites | SIL Open Font License 1.1; the license files are included in `assets/vendor/licenses/` when offline assets are bundled. |
| GeoNames | Offline place-name database | Creative Commons Attribution 4.0; AlbumMaker credits GeoNames in pages that use place names. See [GeoNames](https://www.geonames.org/) and [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). |
| OpenStreetMap contributors | Default map tiles | Open Database License (ODbL); map pages display attribution. See [copyright and license](https://www.openstreetmap.org/copyright). |

## LGPL libraries in executable distributions

The desktop executable bundles Qt (LGPL-3.0 option) and the libheif and
libde265 libraries from `pi-heif` (LGPL-3.0). Keep these as separate,
replaceable libraries, include their license texts (the build does this), and
do not forbid users from modifying them or debugging such changes. AlbumMaker's
noncommercial license applies only to AlbumMaker's own code, not to these
libraries. On Python 3.15, `requirements.txt` installs `pillow-heif` for source users because `pi-heif` has no wheel there yet; that is the user's own install. Do **not** use `pillow-heif` for executable builds (the build script refuses): its binary
wheels include x265, which is GPL-2.0 and does not mix with a noncommercial
license. Obtain qualified legal advice before you sell or otherwise
redistribute the application.

## Data

`albummaker/static/world_land.json` (land outlines for the map): the source and
license are not yet recorded. Add them here before publishing a release.

`docs/demo/media/` (sample photos for the demo site): not covered by the
AlbumMaker license. Confirm that you own them or may publish them, and that
they show no people or places that need permission.
