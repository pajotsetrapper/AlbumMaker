# AlbumMaker

> **Your trips, *told by the photos.***

[![Latest release](https://img.shields.io/github/v/release/pajotsetrapper/AlbumMaker?label=download&color=2f6fdb)](https://github.com/pajotsetrapper/AlbumMaker/releases/latest)
[![License: PolyForm Noncommercial](https://img.shields.io/badge/license-PolyForm%20Noncommercial-6e7b91)](./LICENSE)
[![Support on Ko-fi](https://img.shields.io/badge/Ko--fi-buy%20me%20a%20coffee-e0565b?logo=kofi&logoColor=white)](https://ko-fi.com/pietercoppens)
[![GitHub Sponsors](https://img.shields.io/badge/GitHub%20Sponsors-%E2%99%A5-9d4ba6?logo=githubsponsors&logoColor=white)](https://github.com/sponsors/pajotsetrapper)

Turn a folder of trip photos, videos, and GPS tracks into a website. It has one
chapter per day, a route map per day, an overview map and title on the
homepage, and a full-screen slideshow you can drive with the keyboard.

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

---

## 💡 The Core Workflow: Live Server vs. Static Export

AlbumMaker offers two ways to enjoy your trips, designed to fit different needs:

1. **The Live Server (Your Primary, Everyday Experience):** For most users, running the live server is all you need. It turns your photo folder into a responsive, editable website right on your computer (or shared locally over your home Wi-Fi). You can instantly curate titles, captions, cover photos, and hide private items. Everything saves automatically into a clean `trip_content.json` file. Hidden photos remain dimmed and visible only to you.
2. **Static Site Export (For Public Hosting & Stripping Privacy):** Exporting is entirely optional—use it only when you want to publish a public-facing website or share a permanent standalone copy. When you export a static site, hidden photos are left out entirely, and you can publish clean versions without exposing sensitive underlying GPS tracks or data if you choose.

---

## Quick start

```bash
pip install -r requirements.txt

# Live and editable (open http://127.0.0.1:8000/):
python album_server.py -i /path/to/your/photos

# Or build a standalone folder you can copy anywhere:
python generate_site.py -i /path/to/your/photos -o site

# Optional, once, while online: so the pages work without internet
# (see "Working without internet"):
python album_server.py --fetch-offline-assets

# Several trips at once, one page linking them (see "Several trips"):
python generate_site.py --library trips.txt -o ~/AlbumMakerSites

# Optional desktop app (Mac, Windows, Linux):
python -m pip install -r requirements-gui.txt
python album_gui.py

# Which version is this? (works with every script; see "Version number"):
python album_server.py --version
```

A `.gpx` track in the photos folder is picked up by itself (see "GPS
tracks and place names").

For videos you also need FFmpeg, which is installed separately (see
[Installing FFmpeg](#installing-ffmpeg)); photos work without it. On
Python 3.15, see [HEIC photos and your Python version](#heic-photos-and-your-python-version).

There are two scripts. Both produce the same site with the same look and
features; they differ in how it's delivered, and in whether you can edit
in the page:

| | `generate_site.py` | `album_server.py` |
|---|---|---|
| What it does | Builds a folder of static HTML, thumbnails and media copies | Runs a small local web server that builds every page and image on request |
| Python needed while viewing? | No. Open `index.html` (maps need a simple web server; see below), copy the folder to a USB drive, etc. | Yes, the whole time |
| Disk space | A site folder with thumbnails, copies of visible media and browser-ready conversions where needed | No generated site; optionally `trip_content.json`, metadata and map-tile caches |
| Without internet | Everything works except the map tiles; the map library and fonts can be bundled into the site. Maps need the site to be served over http(s), not opened from disk (see [Viewing a static site](#viewing-a-static-site-maps-need-a-web-server)) | The same, and map tiles you have already viewed can be kept in a local cache |
| After adding or changing photos | Re-run the script (only new or changed files are processed) | Restart the server |
| Editing titles, descriptions, captions, favorites, hidden photos, covers | Edit `trip_content.json`, then re-run the script | In the page itself (from the computer running the server, or any browser with the editor link), or edit the file and refresh |
| Cost | A build step | A little CPU on first view of each day |

Both use the same `requirements.txt` and the same `trip_content.json`
format, so you can switch between them, or use both, at any time. A
typical workflow is to curate in the live server, then run
`generate_site.py` to produce the version you share.

<details>
<summary><b>Desktop App Instructions & Download Guide</b></summary>

The optional desktop app provides a resizable interface for managing a
trip library, starting the live server, generating static sites, and
watching progress and HTTP requests.

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

1. **Get the programs ready (once):** Install Python 3.9+ and run `python -m pip install -r requirements.txt`. Install FFmpeg via `python -m albummaker install-ffmpeg`.
2. **Put a trip in one folder:** Copy photos/videos into one folder. Optionally drop a `.gpx` track file there.
3. **Look at it:** Run `python album_server.py -i "/path/to/your/photos"` and open <http://127.0.0.1:8000/>.
4. **Tell the story:** Use the in-browser live editor to write titles, descriptions, captions, pick covers, mark favorites, or hide photos.
5. **Make a copy to share:** Stop the server and build a standalone public site with `generate_site.py`.

---

<details>
<summary><b>Click to expand Full Technical Reference (CLI flags, formats, caching, tests, and customization)</b></summary>

### 1. Install & FFmpeg
* **HEIC Support:** Installed automatically via `pi-heif` (Python 3.9–3.14). Python 3.15 falls back to `pillow-heif` for running from source.
* **FFmpeg:** Install via `python -m albummaker install-ffmpeg` or package managers (`brew install ffmpeg`, `sudo apt install ffmpeg`, `winget install Gyan.FFmpeg`).

### 2a. Static Site: `generate_site.py`
```bash
python generate_site.py -i /path/to/photos -o /path/to/output
```
* Key flags: `-i`, `-o`, `-c` (content json), `--thumb-size`, `--large-thumb-size`, `--force`, `--tz-offset`, `--timezone`, `--date-range`, `--no-date-check`, `--workers`, `--no-dedupe`, `--no-live-photos`, `--gpx`, `--no-gpx`, `--no-places`, `--fetch-offline-assets`, `--version`.

### 2b. Live Server: `album_server.py`
```bash
python album_server.py -i /path/to/photos
```
* Key flags: `--port`, `--lan`, `--read-only`, `--editor-link-only`, `--allow-host`, `--tile-cache`, `--tile-url`, `--tile-attribution`, `--memory-cache-mb`, `--index-cache`, `--verbose`.

### 2c. Library (`--library LIST`)
List photos folders one per line in a text file to build or serve multiple trips simultaneously.

### 3. `trip_content.json` & Editing
Saves titles, day descriptions, captions (`photos`), favorites (`favorite: true`), and hidden states (`hidden: true`) safely in plain JSON.

### 4. Offline Assets & Caching
* Run `python album_server.py --fetch-offline-assets` once to download Leaflet maps and fonts locally.
* Use `--tile-cache DIR` to cache map tiles for offline viewing.

### 5. GPS Tracks & Place Names
Drop `.gpx` files into the folder to map geotagless camera photos and draw routes. GeoNames town lists provide offline place names.

### Running Tests
```bash
python -m unittest discover -s tests -t .
```

### Customization & License
* Templates and styles reside in the `albummaker` folder (`style.css`, `script.js`, Jinja2 templates in `templates/`).
* Licensed under the **PolyForm Noncommercial License 1.0.0**.
</details>

---

## License & Support
Licensed under the **PolyForm Noncommercial License 1.0.0** (Free for personal, educational, and noncommercial use). See [LICENSE](./LICENSE) and [THIRD_PARTY_NOTICES.md](./THIRD_PARTY_NOTICES.md).

If AlbumMaker helps preserve your memories, consider supporting development via [Ko-fi](https://ko-fi.com/pietercoppens) or [GitHub Sponsors](https://github.com/sponsors/pajotsetrapper), or leave a ⭐ star on GitHub!
