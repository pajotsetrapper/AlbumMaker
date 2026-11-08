# Third-party notices

AlbumMaker's own source code is licensed under the MIT License in
[`LICENSE`](./LICENSE). This does **not** relicense third-party software or
data distributed with AlbumMaker. Each component remains under its own terms.

The component versions and even the licenses of some binary dependencies can
change as dependencies are updated. The executable build generates
`THIRD_PARTY_NOTICES.txt` with the exact FFmpeg version, its reported license,
the FFmpeg source tag and build configuration, and the full GPL/LGPL and
packaged Python component license texts. The GPL/LGPL texts are stored in the
repository's `licenses/` directory so executable builds do not need to fetch
them from a license website. Read the generated file alongside this inventory
for the specific executable you are using. Static sites that include
downloaded offline assets carry their notices under `assets/vendor/licenses/`.

## Components

| Component | Purpose | License / notice |
|---|---|---|
| FFmpeg and ffprobe | Read video metadata and generate video previews | GPL-2.0-or-later for the current executable build; exact version and reported license are recorded in its `THIRD_PARTY_NOTICES.txt`. See [FFmpeg source](https://github.com/FFmpeg/FFmpeg) and the [binary provider](https://github.com/zackees/ffmpeg_bins). |
| `pillow-heif` and bundled codec libraries | Read and convert HEIF images | The Python package is BSD-3-Clause. Its binary wheels also bundle libheif and libde265 (LGPL-3.0) and x265 (GPL-2.0); the wheel's `LICENSES_bundled.txt` identifies versions and source locations. The executable notice includes this wheel notice. |
| Pillow | Image reading and thumbnail generation | MIT-CMU / Pillow license; see the [Pillow project](https://github.com/python-pillow/Pillow). |
| Jinja2 and MarkupSafe | HTML templates and escaping | BSD-3-Clause; see [Jinja2](https://github.com/pallets/jinja) and [MarkupSafe](https://github.com/pallets/markupsafe). |
| PySide6 and Qt | Desktop GUI | Qt for Python is available under LGPLv3, GPLv2/GPLv3, or commercial terms. See the [Qt for Python licensing information](https://doc.qt.io/qtforpython-6/licenses.html) and the per-build notices for included license texts. |
| `static-ffmpeg` | Locates and downloads the FFmpeg binaries used by executable builds | MIT; the license text is included in the executable's `THIRD_PARTY_NOTICES.txt`. |
| `tzdata` | IANA time-zone database | Apache-2.0; see the [tzdata project](https://github.com/python/tzdata). |
| Leaflet | Maps in generated sites | BSD-2-Clause; the license is included in `assets/vendor/licenses/leaflet-LICENSE.txt` when offline assets are bundled. |
| Fraunces, IBM Plex Sans and IBM Plex Mono | Fonts in generated sites | SIL Open Font License 1.1; the license files are included in `assets/vendor/licenses/` when offline assets are bundled. |
| GeoNames | Offline place-name database | Creative Commons Attribution 4.0; AlbumMaker credits GeoNames in pages that use place names. See [GeoNames](https://www.geonames.org/) and [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). |
| OpenStreetMap contributors | Default map tiles | Open Database License (ODbL); map pages display attribution. See [copyright and license](https://www.openstreetmap.org/copyright). |

## GPL source availability for executable distributions

The desktop executable bundles GPL-enabled FFmpeg/ffprobe. The current
`pillow-heif` binary wheels also contain GPL-licensed x265, in addition to
LGPL-licensed libraries. These obligations apply whether a binary is sold or
given away. Including license text and a source-code link is important, but
does not by itself establish that a distribution meets every source-code
requirement.

The build records the exact FFmpeg version and configuration and points to its
upstream source tag and binary provider. The provider repository is a binary
repository; AlbumMaker has **not independently verified** that it publishes
the complete corresponding source and build scripts for every binary. Before
publicly distributing an executable, provide durable access to the complete
corresponding source and applicable build/configuration materials for the
exact FFmpeg and x265 binaries, and verify the Qt, LGPL and other third-party
terms for each platform. Keep those materials available for every released
binary. Obtain qualified legal advice if you plan to sell or otherwise
redistribute the application.
