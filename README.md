# AlbumMaker

> **Your trips, *told by the photos.***

[![Latest release](https://img.shields.io/github/v/release/pajotsetrapper/AlbumMaker?label=download&color=2f6fdb)](https://github.com/pajotsetrapper/AlbumMaker/releases/latest)
[![License: PolyForm Noncommercial](https://img.shields.io/badge/license-PolyForm%20Noncommercial-6e7b91)](./LICENSE)
[![Support on Ko-fi](https://img.shields.io/badge/Ko--fi-buy%20me%20a%20coffee-e0565b?logo=kofi&logoColor=white)](https://ko-fi.com/pietercoppens)
[![GitHub Sponsors](https://img.shields.io/badge/GitHub%20Sponsors-%E2%99%A5-9d4ba6?logo=githubsponsors&logoColor=white)](https://github.com/sponsors/pajotsetrapper)

Turn a folder of trip photos, videos, and GPS tracks into a website. It has one
chapter per day, a route map per day, an overview map and title on the
homepage, and a full-screen slideshow you can drive with the keyboard. Pick
one of four looks (classic, journal, scrapbook or cinema) without changing a
word of your content.

It reads EXIF (for photos) and container metadata (for videos) to work
out *when* and *where* each file was captured. You don't type anything
in by hand except the titles and descriptions, if you want them. With the
live server you can also add captions, mark favorites, hide photos and
pick each day's cover photo right in the page.

It also names the places you visited (offline, no account needed), can
use a GPS track from a watch or phone app to put camera photos on the map
and draw the route you travelled, and can gather all your trips on one
page, wherever their folders are.

🌐 **[Explore the Live Sample Trip](https://pajotsetrapper.github.io/AlbumMaker/demo/index.html)** · 📖 **[Official Product Page](https://pajotsetrapper.github.io/AlbumMaker/)**

The same sample trip in every template: [Classic](https://pajotsetrapper.github.io/AlbumMaker/demo/index.html) · [Journal](https://pajotsetrapper.github.io/AlbumMaker/demo-journal/index.html) · [Scrapbook](https://pajotsetrapper.github.io/AlbumMaker/demo-scrapbook/index.html) · [Cinema](https://pajotsetrapper.github.io/AlbumMaker/demo-cinema/index.html)

---

## What you get

- **A chapter per day.** A trip homepage with an overview map and a card per
  day, and a page per day with its story, route map and photos. Files without
  a usable date go to an *Unsorted* page.
- **Four templates.** `classic`, `journal`, `scrapbook` and `cinema`: same
  content, same viewer and editing, a completely different look (see
  [Templates](#3b-templates---template-name)).
- **A full-screen slideshow.** Keyboard control, autoplay with adjustable speed,
  zoom, Live Photo playback, a details panel (camera settings, place, a small
  map, a link to the original file), a favorites-only mode, and a link to any
  single photo.
- **Maps and GPS.** Every geotagged photo is a point on the maps. A `.gpx` track
  puts camera photos without GPS on the map and draws your route.
- **Place names, offline.** The nearest town to each photo, from a built-in
  list of about 160,000 towns. No account, no internet.
- **Live editing.** Titles, day descriptions, captions, favorites, hidden photos
  and covers, saved straight into `trip_content.json`. Your photo files are
  never changed.
- **Many trips on one page.** A trip library page with a card per trip and a map
  of all trips; with the live server the owner can switch its template from a
  menu.
- **Photos, HEIC and video.** JPEG, PNG, WebP, BMP, TIFF and HEIC/HEIF photos
  (iPhone), and MP4, MOV, M4V, AVI, MKV and 3GP videos (with FFmpeg). Exact
  duplicates are skipped and Live Photo clips are attached to their photos.
  Dates that look wrong are checked.
- **Works offline.** Once the map library and fonts are downloaded, pages work
  without internet (map tiles can be cached too).
- **Desktop app** for Windows, macOS and Linux, with in-app help for every
  control, and **command-line scripts** for everything else.

---

## 💡 The Core Workflow: Live Server vs. Static Export

AlbumMaker offers two ways to enjoy your trips, designed to fit different needs:

1. **The Live Server (Your Primary, Everyday Experience):** For most users, running the live server is all you need. It turns your photo folder into a responsive, editable website right on your computer (or shared locally over your home Wi-Fi). You can instantly curate titles, captions, cover photos, and hide private items. Everything saves automatically into a clean `trip_content.json` file. Hidden photos remain dimmed and visible only to you.
2. **Static Site Export (For Public Hosting & Sharing a Copy):** Exporting is entirely optional. Use it when you want to publish a public-facing website or share a permanent standalone copy. When you export a static site, hidden photos are left out entirely. Keep in mind that the copies of your photos are the untouched originals, so metadata embedded in them (including GPS positions) is part of the published files, and photos with a location appear on the maps. Use `--no-gpx` to leave GPS tracks (routes) out and `--no-places` to leave out place names.

---

## Quick start

```bash
pip install -r requirements.txt

# Live and editable (open http://127.0.0.1:8000/):
python album_server.py -i /path/to/your/photos

# Or build a standalone folder you can copy anywhere:
python generate_site.py -i /path/to/your/photos -o site

# Optional, once, while online: so the pages work without internet
# (see "Offline assets & caching" in the technical reference below):
python album_server.py --fetch-offline-assets

# Several trips at once, one page linking them (see "Library" below):
python generate_site.py --library trips.txt -o ~/AlbumMakerSites

# Optional desktop app (Mac, Windows, Linux):
python -m pip install -r requirements-gui.txt
python album_gui.py

# Give the site another look (see "Templates" below):
python generate_site.py -i /path/to/your/photos -o site --template journal

# Which version is this? (works with every script):
python album_server.py --version
```

A `.gpx` track in the photos folder is picked up by itself (see "GPS tracks
and place names" in the technical reference below).

For videos you also need FFmpeg, which is installed separately (see
[Installing FFmpeg](#installing-ffmpeg)); photos work without it. On
Python 3.15, see [HEIC photos and your Python version](#heic-photos-and-your-python-version).

There are two scripts. Both produce the same site with the same look and
features; they differ in how it's delivered, and in whether you can edit
in the page:

| | `generate_site.py` | `album_server.py` |
|---|---|---|
| What it does | Builds a folder of static HTML, thumbnails and media copies | Runs a small local web server that builds every page and image on request |
| Python needed while viewing? | No. Copy the folder to a web host, a USB drive, etc. (maps need the site to be served over http(s); see [Viewing a static site](#viewing-a-static-site-maps-need-a-web-server)) | Yes, the whole time |
| Disk space | A site folder with thumbnails, copies of visible media and browser-ready conversions where needed | No generated site; optionally `trip_content.json`, metadata and map-tile caches |
| Without internet | Everything works except the map tiles; the map library and fonts can be bundled into the site | The same, and map tiles you have already viewed can be kept in a local cache |
| After adding or changing photos | Re-run the script (only new or changed files are processed) | Restart the server |
| Editing titles, descriptions, captions, favorites, hidden photos, covers | Edit `trip_content.json`, then re-run the script | In the page itself (from the computer running the server, or any browser with the editor link), or edit the file and refresh |
| Cost | A build step | A little CPU on first view of each day |

Both use the same `requirements.txt` and the same `trip_content.json`
format, so you can switch between them, or use both, at any time. A
typical workflow is to curate in the live server, then run
`generate_site.py` to produce the version you share.

The same programs are also available as one command, `python -m albummaker`
(`serve`, `build`, `gui`, `install-ffmpeg`); see [Other ways to run it](#2d-other-ways-to-run-it).

<details>
<summary><b>Desktop App Instructions & Download Guide</b></summary>

The optional desktop app provides a resizable interface for managing a
trip library, choosing the site template, starting the live server,
generating static sites, and watching progress and HTTP requests. Every
control is explained in its **Help** window.

### Download a standalone app

Download the file for your computer from the
[latest release](https://github.com/pajotsetrapper/AlbumMaker/releases/latest):

| Your computer | Download | Then |
|---|---|---|
| Windows 10 or 11 (64-bit) | `AlbumMaker-<version>-windows-x64.zip` | Unzip it, open the `AlbumMaker` folder and run `AlbumMaker.exe`. Keep the folder together. |
| Mac with Apple silicon (M1 or later) | `AlbumMaker-<version>-macos-arm64.zip` | Unzip it and drag `AlbumMaker.app` to your Applications folder. |
| Mac with an Intel processor | `AlbumMaker-<version>-macos-intel.zip` | As above. |
| Linux x64 (Ubuntu 22.04 or newer) | `AlbumMaker-<version>-linux-x64.tar.gz` | Unzip and run `AlbumMaker/AlbumMaker` inside. |

Each download contains the app, its background command runner and
everything it needs, including the map library and fonts; installing Python is not required.
FFmpeg is not included (see [Installing FFmpeg](#installing-ffmpeg)); the app offers to install it.

**The first time you open it:**
- **macOS:** Close warning, open **System Settings → Privacy & Security**, and click **Open Anyway**. Or run: `xattr -dr com.apple.quarantine /Applications/AlbumMaker.app`
- **Windows:** Choose **More info**, then **Run anyway**.
- **Linux:** If it doesn't start, ensure desktop libraries are installed (e.g., `sudo apt install libxcb-cursor0 libxkbcommon-x11-0 libegl1`).

### Run from source (GUI)
```bash
python -m pip install -r requirements.txt
python -m pip install -r requirements-gui.txt
python album_gui.py
```
</details>

---

## Step by step

1. **Get the programs ready (once):** Install Python 3.9+ and run `python -m pip install -r requirements.txt`. Install FFmpeg via `python -m albummaker install-ffmpeg` (only needed for videos).
2. **Put a trip in one folder:** Copy photos/videos into one folder. Optionally drop a `.gpx` track file there.
3. **Look at it:** Run `python album_server.py -i "/path/to/your/photos"` and open <http://127.0.0.1:8000/>. Add `--template journal` (or `scrapbook`, `cinema`) for another look.
4. **Tell the story:** Use the in-browser live editor to write titles, descriptions, captions, pick covers, mark favorites, or hide photos.
5. **Make a copy to share:** Stop the server and build a standalone public site with `generate_site.py`.

---

## Good to know

### Installing FFmpeg
AlbumMaker uses FFmpeg, a separate program, to read video dates and locations and to make video previews. It is not included. Without it, photos work normally, but videos have no date (so they end up under *Unsorted*), no location and a plain placeholder instead of a preview. Once FFmpeg is installed, the missing previews are made the next time you start the server or build the site.

* `python -m albummaker install-ffmpeg` installs it with `winget` (Windows) or Homebrew (macOS and Linux). The desktop app offers the same when it starts and cannot find FFmpeg.
* Or install it yourself: `winget install Gyan.FFmpeg` (Windows), `brew install ffmpeg` (macOS), `sudo apt install ffmpeg` (Debian/Ubuntu), `sudo dnf install ffmpeg-free` (Fedora), `sudo pacman -S ffmpeg` (Arch), or download a build from [ffmpeg.org](https://ffmpeg.org/download.html) and put both `ffmpeg` and `ffprobe` in the `ffmpeg` folder inside AlbumMaker's settings folder (see [Where AlbumMaker keeps its files](#where-albummaker-keeps-its-files)).

### HEIC photos and your Python version
HEIC/HEIF photos (iPhone) are read with `pi-heif` on Python 3.9 to 3.14, which `requirements.txt` installs for you. `pi-heif` has no Python 3.15 wheel yet, so on 3.15 `requirements.txt` installs `pillow-heif` instead. Use that only for running from source: its wheels contain the GPL x265 encoder, so never build executables with it.

### Viewing a static site: maps need a web server
Static sites load map tiles straight from OpenStreetMap, which refuses tile requests that carry no `Referer` header. A browser sends none for a page opened directly from disk (`file://`, for example by double-clicking `index.html`, or from a USB drive); the maps then show "Access blocked" tiles or only a rough outline, while photos and everything else still work. To get working maps:

* **Publish the folder** on any web host (GitHub Pages, Netlify, your own server), so it is opened through an `https://` address. Do not make the host send a `Referrer-Policy: no-referrer` header.
* **Or test locally** by running `python -m http.server` inside the site folder and opening `http://localhost:8000/`. OpenStreetMap may still reject `localhost`, so this is only for testing.
* **Or use the live server**, which fetches and caches tiles itself.

OpenStreetMap's policy expects light traffic; a heavily visited site should use a commercial tile provider instead (live server: `--tile-url`).

### Where AlbumMaker keeps its files
The desktop app's settings, the downloaded offline assets (`offline-assets`) and your own FFmpeg (`ffmpeg`) live in one per-user folder: `%APPDATA%\AlbumMaker` on Windows, `~/Library/Application Support/AlbumMaker` on macOS, `~/.config/albummaker` on Linux. Set `ALBUMMAKER_CONFIG_DIR` to use another folder. Your photo folders are only read; the one thing written there is `trip_content.json`.

### Version number
`--version` works with every script and with `python -m albummaker`. The version is also shown at the bottom of the desktop app's Help window; please include it when reporting a problem.

---

<details>
<summary><b>Click to expand Full Technical Reference (CLI flags, formats, caching, tests, and customization)</b></summary>

### 1. Install & FFmpeg
* **Python:** 3.9 or newer, then `pip install -r requirements.txt` (Pillow, Jinja2, MarkupSafe, tzdata and `pi-heif`).
* **HEIC support:** see [HEIC photos and your Python version](#heic-photos-and-your-python-version).
* **FFmpeg** (videos only): see [Installing FFmpeg](#installing-ffmpeg).

### 2a. Static Site: `generate_site.py`
```bash
python generate_site.py -i /path/to/photos -o /path/to/output
```
* Key flags: `-i`, `-o`, `-c` (content json), `--library`, `--library-title`, `--template`, `--thumb-size`, `--large-thumb-size`, `--force`, `--tz-offset`, `--timezone`, `--date-range`, `--no-date-check`, `--workers`, `--no-dedupe`, `--no-live-photos`, `--gpx`, `--no-gpx`, `--no-places`, `--fetch-offline-assets`, `--version`. `python generate_site.py --help` explains each one.
* Only new or changed files are processed on a re-run; `--force` redoes everything.
* `--large-thumb-size` (off by default for static sites) adds a larger thumbnail that the pages use only where the screen can show more pixels (the L and XL sizes, high-resolution screens).

### 2b. Live Server: `album_server.py`
```bash
python album_server.py -i /path/to/photos
```
* Server-only flags: `--port`, `--lan`, `--read-only`, `--editor-link-only`, `--allow-host`, `--tile-cache`, `--tile-url`, `--tile-attribution`, `--memory-cache-mb`, `--index-cache`, `--verbose`.
* Flags shared with `generate_site.py`: `-i`, `-c`, `--library`, `--library-title`, `--template`, `--thumb-size`, `--tz-offset`, `--timezone`, `--date-range`, `--no-date-check`, `--no-dedupe`, `--no-live-photos`, `--gpx`, `--no-gpx`, `--no-places`, `--fetch-offline-assets`, `--large-thumb-size` (on by default here, 1600; thumbnails are made on demand and kept in memory).
* `--template` on the server defaults to the template last picked on the library page, else `classic` (see [Templates](#3b-templates---template-name)).
* The server only listens on this computer unless you add `--lan`; there is no password, so use `--lan` on a trusted network only. Other devices can look but not edit, unless they open the editor link printed at startup.

### 2c. Library (`--library LIST`)
Gather several trips on one page; both scripts take it. `generate_site.py` builds every trip into its own folder under `-o` plus a library page; `album_server.py` serves them all, each trip under its own `/name/`. `LIST` is a text file with one photos folder per line:

```text
# trips.txt: '#' starts a comment
/Volumes/Photos/2024 Japan
~/Pictures/Italy 2025 | italy
/Volumes/NAS/Iceland | iceland | --timezone Atlantic/Reykjavik --no-dedupe
```

* After the folder, `| name` sets the name the trip gets in web addresses and in the output folder (lower case letters, digits, `-` and `_`, up to 64 characters; if you leave it out, it is made from the folder's name).
* After the name, a second `|` can carry options for that trip only (for example `--timezone`, `--date-range`, `--gpx`, `--tz-offset`, `--no-dedupe`, `--no-gpx`).
* Relative paths are relative to the list file. Each trip keeps its own `trip_content.json` in its photos folder.
* `--library-title TITLE` sets the heading of the overview page (default *Trips*).
* The library page shows a card per trip and a map of all trips. On the live server, `--index-cache` takes a folder (one cache file per trip).
* The template choice is remembered in `library_settings.json`, next to the list file (see [Templates](#3b-templates---template-name)).

### 2d. Other ways to run it
```bash
python -m albummaker serve -i /path/to/photos      # = album_server.py
python -m albummaker build -i /path/to/photos -o site   # = generate_site.py
python -m albummaker gui                            # the desktop app
python -m albummaker install-ffmpeg                 # winget / Homebrew
python -m albummaker --version
```
`python tools/build_zipapp.py` packs the package into one file, `dist/albummaker.pyz`, that runs the same way (`python dist/albummaker.pyz serve -i ...`). Python and the requirements are still needed.

### 3. `trip_content.json` & Editing
Saved in plain JSON next to your photos (or wherever `-c` points), and never lost: a file that cannot be parsed is reported and never overwritten, and only the field being changed is touched.

```json
{
  "title": "My Trip",
  "subtitle": "Spring in the Alps",
  "cover": "day1/IMG_0001.jpg",
  "days": {
    "2025-05-14": { "title": "Lake Lucerne", "description": "…", "cover": "day1/IMG_0002.jpg" }
  },
  "photos": {
    "day1/IMG_0002.jpg": { "caption": "Sunrise over the lake", "favorite": true, "hidden": false }
  }
}
```
Photos are identified by their path relative to the photos folder. The trip `title` and `subtitle` are edited in this file (not in the browser); everything else can be edited in the page on the live server. A `"template"` key, if present, is ignored: the template is chosen when you run the program (see below).

### 3b. Templates (`--template NAME`)
Both scripts, the library builds and the desktop app (*Site template*) take a template. All templates show the same content and keep the viewer, maps and in-page editing; only the look changes. The whole site (every trip of a library) uses one template.

| Template | Look |
|---|---|
| `classic` (default) | Dark navy pages, parchment text panels, a card per day. |
| `journal` | A light travel-journal look: full-width cover photo, a floating statistics card, a timeline of numbered steps (one per day, with a photo collage) beside a sticky map that follows your scrolling, and a story page per day with a photo grid whose thumbnail size (S/M/L/XL) visitors can change, as in the classic view. Use `--large-thumb-size 1600` for a crisp cover. |
| `scrapbook` | A warm paper scrapbook: taped polaroid photos scattered on each day's page, rubber-stamp statistics, a push-pin map and lined notebook paper for the day story. Visitors can change the polaroid size (S/M/L/XL) on a day page. |
| `cinema` | Dark and cinematic: a full-screen scene for each day (big photo, outlined day number, film-strip of more photos) that fades in as you scroll, with a mini-map that flies to the day you are reading and opens up to show the whole route. Day pages have a drop-cap story, a glowing route map and a photo gallery in rows that follow each photo's shape. Use `--large-thumb-size 1600` for sharp scenes. |

`journal`, `scrapbook` and `cinema` use only system fonts, so they need nothing to be downloaded for typography; `classic` uses the bundled Fraunces and IBM Plex fonts.

```bash
python generate_site.py -i photos -o site --template journal
python album_server.py -i photos --template journal
```
With `--library`, one template applies to the whole library.

**Picking it on the library page.** On the live server with `--library`, the owner (the computer running the server, or a browser that opened the editor link) sees a small *Template* menu in the corner of the library page. Choosing a template switches every page at once and is remembered in `library_settings.json`, stored next to the `--library` list file, so it is still there after a restart. An explicit `--template` on the command line overrides the remembered choice. Visitors and `--read-only` servers do not see the menu. The desktop app's *Site template* setting is used for the static sites it builds; the live server it starts remembers the page's choice instead.

**Adding your own:** put `index.html`, `day.html` and `library.html` in `albummaker/templates/<name>/` (reuse `slideshow.html` with `{% include "slideshow.html" %}`; the existing templates show which element ids the viewer and editor need), optional `<name>.css` / `<name>.js` in `albummaker/static/` (added after the shared `style.css` / `script.js`), and register it in `albummaker/site_templates.py`. The colours of `journal`, `scrapbook` and `cinema` are variables at the top of `journal.css`, `scrapbook.css` and `cinema.css`.

### 4. Offline Assets & Caching
* Run `python album_server.py --fetch-offline-assets` once (needs internet) to download the Leaflet map library and the fonts into AlbumMaker's settings folder. Static sites bundle them when they are present. The desktop app already includes them. The live server downloads them by itself on start if they are missing (from source).
* Map tiles are separate and come from the map provider. With the live server, `--tile-cache DIR` keeps the tiles you view, so those map areas work offline later; `--tile-url` and `--tile-attribution` use another provider.
* `--index-cache PATH` keeps the metadata read from your files (dates, GPS, dimensions, camera, video details) so an unchanged file is not read again on the next start.

### 5. GPS Tracks & Place Names
* Drop `.gpx` files into the photos folder (or give `--gpx FILE-OR-FOLDER`, repeatable) to place photos that have no location and to draw your route on the maps. `--no-gpx` ignores tracks. `--timezone` and `--tz-offset` matter here and for video times.
* Place names come from a built-in list of about 160,000 towns (GeoNames, CC BY 4.0), looked up offline. `--no-places` leaves them out.

### 6. Dates, duplicates and Live Photos
* A date counts as wrong if it is before 1995, in the future, or more than a year from the rest of your files; such files go to *Unsorted*. `--date-range START END` sets the range yourself and `--no-date-check` trusts every date.
* Exact duplicate files are skipped (`--no-dedupe` keeps them). iPhone Live Photo clips are attached to their photos (`--no-live-photos` lists them as videos).

### Running Tests
```bash
python -m unittest discover -s tests -t .
```
The browser-behaviour tests (viewer, editing, maps) are skipped unless Node.js 18+ is installed and `npm install` has been run once in `tests/js`. The desktop-app tests need `PySide6` (`requirements-gui.txt`).

### Tools for maintainers
* `python tools/build_executable.py` builds the desktop app for the current OS (`requirements-packaging.txt`; use Python 3.9 to 3.14 and never `pillow-heif`).
* `python tools/build_zipapp.py` builds `dist/albummaker.pyz`.
* `python tools/build_demo.py [TEMPLATE ...]` rebuilds the sample trip from `docs/demo-source` in every template (`docs/demo`, `docs/demo-journal`, `docs/demo-scrapbook`, `docs/demo-cinema`) or only the ones you name.
* `python tools/build_screenshots.py [--offline]` takes the template screenshots on the product page from those sample sites (needs `pip install playwright` and `python -m playwright install chromium`; needs internet for map tiles unless `--offline`).
* `python tools/build_places.py` rebuilds the offline town list from GeoNames.
* `python tools/check_version.py vX.Y.Z` checks a release tag against `albummaker.__version__` (used by CI).

### Customization & License
* Templates and styles reside in the `albummaker` folder (shared `static/style.css` and `static/script.js`, per-template CSS and JS in `static/`, Jinja2 pages in `templates/`). See [Templates](#3b-templates---template-name).
* Licensed under the **PolyForm Noncommercial License 1.0.0**.
</details>

---

## License & Support
Licensed under the **PolyForm Noncommercial License 1.0.0** (Free for personal, educational, and noncommercial use). See [LICENSE](./LICENSE) and [THIRD_PARTY_NOTICES.md](./THIRD_PARTY_NOTICES.md).

If AlbumMaker helps preserve your memories, consider supporting development via [Ko-fi](https://ko-fi.com/pietercoppens) or [GitHub Sponsors](https://github.com/sponsors/pajotsetrapper), or leave a ⭐ star on GitHub!
