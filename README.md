# AlbumMaker

Turns a folder of trip photos and videos into a website. It has one
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

**New here?** Start with [Step by step](#step-by-step). The numbered
sections after it are the full reference.

**Contents:**
[Quick start](#quick-start) ·
[Product page](./docs/index.html) ·
[Desktop app](#desktop-app) ·
[Step by step](#step-by-step) ·
[1. Install](#1-install) ·
[2a. Static site](#2a-build-a-static-site-generate_sitepy) ·
[2b. Live server](#2b-serve-it-live-album_serverpy) ·
[2c. Several trips](#2c-several-trips-a-library) ·
[3. Titles and notes](#3-titles-descriptions-and-photo-notes-trip_contentjson) ·
[4. Editing in the page](#4-editing-in-the-page-live-server) ·
[5. Without internet](#5-working-without-internet) ·
[6. GPS tracks and place names](#6-gps-tracks-and-place-names) ·
[What you get](#what-you-get) ·
[How your photos are handled](#how-your-photos-are-handled) ·
[Notes & limitations](#notes--limitations) ·
[Troubleshooting](#troubleshooting) ·
[Running the tests](#running-the-tests) ·
[Customizing the look](#customizing-the-look) ·
[License and third-party notices](#license-and-third-party-notices)

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

You also need ffmpeg (see "Install"). Everything below explains the
details.

There are two scripts. Both produce the same site with the same look and
features; they differ in how it's delivered, and in whether you can edit
in the page:

| | `generate_site.py` | `album_server.py` |
|---|---|---|
| What it does | Builds a folder of static HTML, thumbnails and media copies | Runs a small local web server that builds every page and image on request |
| Python needed while viewing? | No. Open `index.html`, copy the folder to a USB drive, etc. | Yes, the whole time |
| Disk space | A site folder with thumbnails, copies of visible media and browser-ready conversions where needed | No generated site; optionally `trip_content.json`, metadata and map-tile caches |
| Without internet | Everything works except the map tiles; the map library and fonts can be bundled into the site. Maps need the site to be served over http(s), not opened from disk (see [Viewing a static site](#viewing-a-static-site-maps-need-a-web-server)) | The same, and map tiles you have already viewed can be kept in a local cache |
| After adding or changing photos | Re-run the script (only new or changed files are processed) | Restart the server |
| Editing titles, descriptions, captions, favorites, hidden photos, covers | Edit `trip_content.json`, then re-run the script | In the page itself (from the computer running the server, or any browser with the editor link), or edit the file and refresh |
| Cost | A build step | A little CPU on first view of each day |

Both use the same `requirements.txt` and the same `trip_content.json`
format, so you can switch between them, or use both, at any time. A
typical workflow is to curate in the live server, then run
`generate_site.py` to produce the version you share.

## Desktop app

The optional desktop app provides a resizable interface for managing a
trip library, starting the live server, generating static sites, and
watching progress and HTTP requests. It uses the same server and build
commands as the terminal tools.

See the [AlbumMaker product page](./docs/index.html) for a visual introduction
and an interactive sample trip. To publish it with GitHub Pages, configure the
repository to deploy from the `main` branch's `/docs` folder.

### Download a standalone app

Tagged builds and manually started **Build desktop executables** runs in
GitHub Actions produce downloadable bundles for Windows x64, macOS Intel,
macOS Apple Silicon, and Linux x64. Each bundle contains the GUI, its
background command runner, and the Python and GUI dependencies; installing
Python is not required. **FFmpeg is not included**: it is separate software
used for video dates and previews, and the app offers to install it for you on
first start (see [Installing FFmpeg](#installing-ffmpeg)). Photos work without
it. Download the platform artifact from the corresponding run in the repository's
**Actions** tab; artifacts are retained for 30 days.

The app source is licensed under the PolyForm Noncommercial License 1.0.0,
and bundled components keep their own terms.
The executable carries the project `LICENSE`, a third-party inventory, and
build-generated notices with exact component versions and license text.
The bundled HEIC reader (`pi-heif`) is decode-only and contains LGPL
libraries, not GPL codecs. See [Third-party notices](./THIRD_PARTY_NOTICES.md). These builds are not
code-signed or notarized, so Windows or macOS may display a security warning
when you first open one. Linux builds are made on Ubuntu 22.04 and require a
compatible Linux system with glibc.

### Run from source

To run the GUI from the project source, install Python and the optional GUI
dependency, then launch it from the project folder:

```bash
python -m pip install -r requirements.txt
python -m pip install -r requirements-gui.txt
python album_gui.py
# Or: python -m albummaker gui
```

To build a standalone app locally for the current operating system, install
the packaging dependencies and run the build script. Native platform builds
are required; a macOS build cannot create the Windows or Linux app.

```bash
python -m pip install -r requirements-packaging.txt
python tools/build_executable.py
```

The app is written to `dist/executable/`. The builder smoke-tests the GUI,
background command runner, and HEIC support. To build for another
operating system or architecture, run the same command on a native machine or
use the GitHub Actions workflow described below.

### Version number

The version is defined once, as `__version__` in `albummaker/__init__.py`.
It is shown in the desktop app (window title, header, and the Help window,
which also lists Python, PySide6 and the platform for bug reports), printed
at the start of the server and the site builder, and available as
`--version` on every entry point:

```bash
python album_server.py --version
python generate_site.py --version
python album_gui.py --version
python -m albummaker --version
```

Generated pages say which version made them (in the page footer), and a
static site's build record (`.trip_site_manifest.json`) stores it as
`app_version`; the record's own `version` field is its file format, not the
program's. The `--version` flag prints `AlbumMaker 2.0.0` and exits; when it
starts, the server and the site builder print the same version as their first
line. To release: change `__version__`, commit,
then tag that commit with the same number (`v2.1.0` for `2.1.0`). In CI,
`python tools/check_version.py "$GITHUB_REF_NAME"` fails the build when the
tag and `__version__` differ.

### Build or publish all platform versions

Push the source changes to GitHub, then either:

1. Open **Actions → Build desktop executables → Run workflow** to build the
   current branch, or
2. Push a version tag (for example, `v1.2.0`) to start the same builds for
   that revision:

   ```bash
   git tag v1.2.0
   git push origin v1.2.0
   ```

The workflow builds each platform on its own native runner and uploads one
artifact per platform. Download the artifacts from the completed workflow run;
they are kept for 30 days. A tag starts builds but does not create a GitHub
Release automatically.

### Update dependencies for a new build

The executable bundles the dependencies from all three requirements files:

- `requirements.txt`: shared application libraries.
- `requirements-gui.txt`: PySide6 and time-zone data for the desktop GUI.
- `requirements-packaging.txt`: includes the two files above, plus PyInstaller
  (FFmpeg is not bundled).

When changing an application dependency, update its existing requirements
file. To refresh the packaging tools or FFmpeg binaries, edit the PyInstaller
range in `requirements-packaging.txt` first. Keep using `pi-heif`, not
`pillow-heif`, whose binary wheels include a GPL encoder. Then test the updated
dependencies and produce a native build:

```bash
python -m pip install --upgrade -r requirements-packaging.txt
python -m unittest
python tools/build_executable.py
```

For repeatable CI builds, use the same Python version as the workflow (3.11)
and keep the dependency versions or ranges in the requirements files under
review. Re-run the workflow after pushing the dependency changes.

The app guides you through three steps: add trip folders, choose a workflow,
and run it. Use **Add folders** to select several photo folders at once.
Choose each trip's IANA time zone directly in the **Time zone** column (for
example, search for `Brussels` to find `Europe/Brussels`); the setting follows
that folder and accounts for daylight-saving changes. **Automatic** does not
infer the trip's location.
The folder list and GUI settings are saved automatically and restored on the
next launch:

- macOS: `~/Library/Application Support/AlbumMaker/settings.json`
- Windows: `%APPDATA%\AlbumMaker\settings.json`
- Linux: `$XDG_CONFIG_HOME/albummaker/settings.json`, or `~/.config/albummaker/settings.json`

Choose **Run a live server** to browse the trips in a browser while the app
is running, or **Create a static site** to write a standalone website to the
output folder you choose. Server setup is separate from site generation.
**Options** contains less frequently used settings. **General** holds appearance
and shared trip-processing options, including the **Trip library page** title
(default **Trips**) shown on the all-trips overview page in both workflows;
**Live server** and **Static site** contain
options that affect only that workflow. Choose **Help** for plain-language
descriptions of the controls. The trip list follows the order in which folders
were added; use **Remove** and add folders again if you need to change it.
Choose the light or dark appearance in **Options → General**; it also
applies to the **Add folders** picker. The app's version number is shown in the
window title and under the app name, and the **Help** window ends with a
line giving the version, Python, PySide6 and platform (useful in a bug
report).

Before starting the live server in the app, set up the **Metadata cache
folder** and the **Map tile cache folder** in **Options → Live server**. Both
cache folders are created if needed and must be writable. Missing offline
assets are downloaded automatically the first time you start the server, so
an internet connection is needed for that first start. Their defaults are
`metadata-cache` and `tile-cache` folders
inside the same application configuration directory as `settings.json`:

- macOS: `~/Library/Application Support/AlbumMaker/`
- Windows: `%APPDATA%\AlbumMaker\`
- Linux: `$XDG_CONFIG_HOME/albummaker/`, or `~/.config/albummaker/`

The metadata cache keeps a JSON index per trip with media-file sizes,
modification times, and extracted metadata (such as capture time, GPS,
dimensions, and camera/video details). On later server starts, unchanged
files can reuse these results instead of being scanned again. It does not
store photo or video copies. The tile cache keeps map tiles after they are
viewed, so they do not need to be downloaded again and remain available
offline. The offline map library and fonts are stored in the `offline-assets`
subfolder beside `settings.json`; use **Download offline assets** in the
server options to refresh them manually. Static-site generation bundles these
assets when available, but does not require the live-server setup. Once the
live server is ready, the main window shows prominent buttons to open the trip
site and, when editing is enabled, the private editing link. Do not share that
link: it grants editing access. These buttons remain visible when the Activity
panel is closed. The main **Start live server** button becomes **Stop server**
while the server is running; stopping it shows a clear stopped status instead
of a platform-specific exit code.

The GUI's live-server high-resolution thumbnail defaults to **2400 pixels**
(longest side), a sensible balance for Full HD and 4K displays. Only larger
gallery tiles and trip cards on high-pixel-density displays use it, avoiding
soft-looking images when a small thumbnail is enlarged. The server creates
these previews on demand; they cost more processing and memory than normal
thumbnails. For generated sites the larger thumbnails are off by default;
2400 pixels is also a good setting when you want them.
**Automatic** in a trip's time-zone dropdown is not a location guess. Photo
capture dates remain as recorded in EXIF; a selected time zone can still
affect matching photos to a GPX track. For videos, the shared default UTC
offset is used if set; otherwise iPhone local times are kept as recorded and
other UTC times are converted to the computer's local time zone. The shared
**Default video UTC offset** is in **Options → General** and applies to both
workflows.

When running from source, the GUI needs Python 3.9 or newer and the same
`ffmpeg` setup as the command-line tools. PySide6 is optional and is not
installed for CLI-only users. Download offline assets while connected to the
internet.

## Step by step

This walks you through it from the start, assuming you don't use a
terminal much. Each step links to the detailed section if you want to
know more.

**Typing the commands.** The commands below go in a terminal: on a Mac
the *Terminal* app (Applications → Utilities), on Windows *Terminal* or
*Command Prompt* from the Start menu. Two tips:

- Instead of typing a folder's path, type the start of the command, then
  drag the folder from Finder or Explorer into the terminal window: its
  path appears. Put quotes around paths that contain spaces, as in the
  examples.
- The examples say `python`. On a Mac that is often `python3`, and on
  Windows `py`. Use whichever works on your computer.

### 1. Get the programs ready (once)

1. Check that Python 3.9 or newer is installed: `python --version`. If
   it isn't, get it from [python.org](https://www.python.org/downloads/).
2. Install **ffmpeg**, which handles the videos: run
   `python -m albummaker install-ffmpeg` (see
   [Installing FFmpeg](#installing-ffmpeg)). Without it photos work fine, but
   videos have no date or preview.
3. Go to the folder holding these scripts (`cd`, then drag the folder in)
   and install what they need:

   ```bash
   cd "/path/to/AlbumMaker"
   python -m pip install -r requirements.txt
   ```

Details: [1. Install](#1-install).

### 2. Put a trip in one folder

Copy the trip's photos and videos into one folder, from as many phones
and cameras as you like. Subfolders are fine, and so are duplicates (the
same photo imported twice is shown once). Nothing in this folder is ever
changed, moved or renamed; the scripts only add one small file,
`trip_content.json`, for your titles and notes.

Did you record your route with a GPS watch or an app like Strava? Put
its `.gpx` file in the folder too: photos from a camera without GPS then
appear on the map, and the maps show the way you went. See [6. GPS tracks
and place names](#6-gps-tracks-and-place-names).

### 3. Look at it

```bash
python album_server.py -i "/path/to/Japan 2024"
```

Open <http://127.0.0.1:8000/> in your browser. You see the trip's
homepage with a map and a card per day; click a day, then a photo for the
full-screen viewer. Press **Ctrl+C** in the terminal to stop the server
when you're done. The first view of each day takes a moment while
thumbnails are made.

Days are worked out from when each photo was taken. Photos without a
usable date go on a separate "Unsorted" page; [Troubleshooting](#troubleshooting)
says what to do about that.

### 4. Tell the story

While the server runs, you can edit right in the page:

- On a day page, **Edit title & description**. A day without a title
  offers one made of the places you visited that day.
- In the full-screen viewer: type a **caption**, mark a **★ Favorite**
  (or press `S`), **Hide** a photo from visitors, or **Set as cover** for
  the day's card.
- **Preview as visitor** shows the page the way others will see it.

Everything is saved straight away into `trip_content.json` in the photos
folder. The trip's own title and subtitle are set in that file: open it
in any text editor and change `"title"`. See [3. Titles and
notes](#3-titles-descriptions-and-photo-notes-trip_contentjson) and
[4. Editing in the page](#4-editing-in-the-page-live-server).

### 5. Make a copy to share

Stop the server (Ctrl+C), then build a standalone version:

```bash
python generate_site.py -i "/path/to/Japan 2024" -o "/path/to/Japan 2024 site"
```

That folder is the whole site: copy it to a USB stick, put it in a shared
cloud folder, or upload it to any web host. No Python is needed to view it,
and photos you hid are not in it at all. One catch: the maps only load when
the site is opened through a web server (an `http://` or `https://` address),
not by double-clicking `index.html`. See
[Viewing a static site: maps need a web server](#viewing-a-static-site-maps-need-a-web-server). Run the same command again after adding photos or
changing notes; only what changed is redone.

To make it work without internet (apart from the map tiles), run
`python generate_site.py --fetch-offline-assets` once while online, then
build again. See [5. Working without internet](#5-working-without-internet).

### 6. All your trips on one page

Make a text file listing your trips' folders, one per line:

```
/path/to/Japan 2024
/path/to/Italy 2025
/Volumes/External drive/Iceland
```

Then build them all, with a page linking them:

```bash
python generate_site.py --library trips.txt -o "/path/to/All trips"
```

or look at them all live with `python album_server.py --library trips.txt`.
See [2c. Several trips](#2c-several-trips-a-library).

### Common tasks

| I want to… | Do this |
|---|---|
| Add photos I copied later | Put them in the folder. Restart the server, or run `generate_site.py` again (only the new ones are processed). |
| See my camera's photos on the map | Add a `.gpx` track of the trip to the folder ([6](#6-gps-tracks-and-place-names)). |
| Keep a photo out of what others see | Open it and click **Hide**. It stays visible (dimmed) to you only, and isn't copied into a static site. |
| Let family on my home Wi-Fi look | Start the server with `--lan`; it prints the address to give them ([2b](#2b-serve-it-live-album_serverpy)). |
| Edit from my phone or another computer | Open the editor link the server prints at startup on that device ([4](#4-editing-in-the-page-live-server)). |
| Fix photos that landed on the wrong day or in "Unsorted" | See [Troubleshooting](#troubleshooting) (`--date-range`, `--timezone`, `--tz-offset`). |
| Use it without internet | Run `--fetch-offline-assets` once while online ([5](#5-working-without-internet)). |
| Keep my route private | Leave the `.gpx` file out of the folder, or add `--no-gpx`. |

## 1. Install

You need Python 3.9+, plus:

```bash
pip install -r requirements.txt
```

Keep these together in one folder: `generate_site.py`, `album_server.py`
and the `albummaker` folder, which holds the code both scripts share (and
the page templates, CSS and JavaScript as ordinary files). Neither script
runs without it. Offline map assets are saved in the platform-specific
AlbumMaker configuration directory beside `settings.json`; see "Working
without internet".

**Prefer a single file?** `python tools/build_zipapp.py` packs everything
into `dist/albummaker.pyz`, which you can copy anywhere and run with
`python albummaker.pyz serve -i photos` or `python albummaker.pyz build -i
photos -o site`. The requirements still need to be installed; offline assets
are read from the platform-specific AlbumMaker configuration directory.

You also need **ffmpeg**. It's used to read video metadata and grab a
thumbnail frame from each video.

### Installing FFmpeg

AlbumMaker does not include or download FFmpeg itself. FFmpeg is separate
software with its own license (LGPL or GPL, depending on the build; see
[ffmpeg.org/legal](https://ffmpeg.org/legal.html)). The easiest way is
`python -m albummaker install-ffmpeg`, which asks first and then runs your
system's package manager (`winget` on Windows, Homebrew on macOS and Linux).
The desktop app offers the same on first start, and has an **Install FFmpeg**
button. Or install it yourself:

- macOS: `brew install ffmpeg`
- Ubuntu/Debian: `sudo apt install ffmpeg`
- Windows: `winget install Gyan.FFmpeg`, or a build from
  [ffmpeg.org/download](https://ffmpeg.org/download.html)

If you downloaded builds by hand, copy `ffmpeg` and `ffprobe` into the
`ffmpeg` folder inside AlbumMaker's configuration folder, and AlbumMaker will
find them.

Without ffmpeg, videos still appear, but with no date (so they land
on the "Unsorted" page), no location, and a plain placeholder tile
instead of a preview frame. Once ffmpeg is installed, the static site
fills in the missing previews on its next run, and the live server on
its next start.

## 2a. Build a static site: `generate_site.py`

```bash
python generate_site.py -i /path/to/your/photos -o /path/to/site_output
```

Then open `site_output/index.html` in a browser. No server is needed.

Options:

- `-i / --input`: folder with your photos and videos. Subfolders are
  scanned too. Required, unless you use `--library`.
- `--library LIST`: build several trips at once, each into its own
  folder under `-o`, plus a page linking them. See "Several trips: a
  library" below.
- `-o / --output`: where the website is written (default: `./site`).
  It can be inside your photos folder; it's excluded from the scan. It
  can't be the photos folder itself, or a folder that contains it.
- `-c / --content`: where titles, descriptions and photo notes are
  stored (default: `trip_content.json` in your photos folder). See
  section 3.
- `--thumb-size`: maximum thumbnail dimension in px (default 400,
  allowed 16–4096).
- `--large-thumb-size`: also make a larger thumbnail of this size (for
  example 1600) of every photo and video. The pages load it only where the
  screen can use the extra pixels: the L and XL sizes, and high-resolution
  screens. Off by default (0), because it adds a `thumbs_large` folder to
  the site. See "Thumbnail and card size" below.
- `--force`: regenerate all thumbnails and media copies, even for files
  that haven't changed. Normally not needed; see "Re-running the static
  build" below.
- `--tz-offset`: fixed UTC offset in hours (e.g. `2` or `-5.5`) for video
  timestamps and GPS-track matching. See "Video timestamps" and "GPS
  tracks" below.
- `--timezone IANA_NAME`: named time zone, such as `Europe/Paris`, for
  video timestamps and GPS-track matching. Unlike a fixed offset, it
  follows daylight-saving changes. Use instead of (not together with)
  `--tz-offset`. It does not change photo capture dates read from EXIF.
- `--date-range START END`: the dates your files should fall between,
  e.g. `--date-range 2026-04-28 2026-05-10` (both days included). Files
  dated outside it go to "Unsorted". See "Dates that look wrong" below.
- `--no-date-check`: trust every embedded date, even ones that look
  wrong.
- `--workers`: how many files to process in parallel (default: one per
  CPU core). Lower it if you want to keep using the computer while it
  runs.
- `--no-dedupe`: include exact-duplicate files instead of skipping them.
  See "Duplicates" below.
- `--no-live-photos`: show iPhone Live Photo clips as separate videos
  instead of attaching them to their photos. See "Live Photos" below.
- `--gpx FILE`: a GPS track (`.gpx`, or a folder of them) recorded
  during the trip, for files that are somewhere else. Repeatable.
  `.gpx` files inside the photos folder are used without asking. See
  "GPS tracks and place names" below.
- `--no-gpx`: ignore GPS tracks, including those in the photos folder.
- `--no-places`: don't show place names.
- `--fetch-offline-assets`: download the map library and fonts into a
  per-user `offline-assets` folder (needs internet, once), then exit. See
  "Working without internet".

Photos you've marked **hidden** in `trip_content.json` are left out of
the build entirely, including their files. See "Hidden photos" below.

If offline assets are present in AlbumMaker's user data folder, they are
copied into the site (`assets/vendor`), so it doesn't need internet for the
map library and fonts.

### Viewing a static site: maps need a web server

Static sites load their map tiles directly from OpenStreetMap
(`https://tile.openstreetmap.org/`). OpenStreetMap's
[tile usage policy](https://operations.osmfoundation.org/policies/tiles/)
requires a `Referer` header on tile requests and blocks requests without one
(the response carries `X-Blocked: No referer sent. Access denied.`).

Browsers send **no** `Referer` for a page opened straight from disk
(`file://`), for example by double-clicking `index.html` or opening the site
from a USB stick. In that case the map tiles show up as "Access blocked"
images (or you only see the rough land outline). Photos, videos and the rest
of the site still work.

To get working maps:

- **Publish the folder** on any web host (GitHub Pages, Netlify, your own
  server) so it is opened through an `https://` address. Make sure the host
  does not send `Referrer-Policy: no-referrer`.
- **Or test locally** by running `python -m http.server` inside the site
  folder and opening `http://localhost:8000/`. OpenStreetMap may still reject
  `localhost` referrers, so treat this as a test only.
- **Or use the live server** (next section), which fetches and caches tiles
  itself and does not have this limitation. Its `--tile-url` option can point
  at another tile provider.

Static sites currently always use OpenStreetMap tiles. OpenStreetMap's policy
also expects light traffic: a heavily visited site should use a commercial
tile provider instead.

## 2b. Serve it live: `album_server.py`

```bash
python album_server.py -i /path/to/your/photos
```

Then open the URL it prints, by default **http://127.0.0.1:8000/**. Stop
the server with Ctrl+C.

Options:

- `-i / --input`: folder with your photos and videos (default: the
  current folder, so run from inside your photos folder you can leave
  it out).
- `--library LIST`: serve several trips: a page at `/` linking them all,
  each trip under its own `/<name>/`. See "Several trips: a library"
  below.
- `-c / --content`: where titles, descriptions and photo notes are
  stored (default: `trip_content.json` in your photos folder). See
  section 3.
- `--port`: default `8000`.
- `--lan`: let other devices on your network open the site too. **There
  is no login of any kind**: anyone on your Wi-Fi or network can browse
  your photos while the server runs with this on. Leave it off unless you
  want that. Other devices can look but not edit (unless you open the
  editor link on them); see section 4.
- `--read-only`: turn off in-page editing completely, even on the
  computer running the server. Nothing is written to `trip_content.json`
  at all, not even blank entries for new days. See section 4.
- `--editor-link-only`: don't treat the computer running the server as
  an editor automatically; only a browser that has opened the editor
  link can edit. Use this when you put the server behind a reverse proxy
  or a tunnel (nginx, Tailscale Serve, cloudflared, ngrok, an SSH
  tunnel...), since those connect from the same computer. See section 4.
- `--allow-host NAME`: an extra host name the server should answer to
  (repeatable). The server only responds to requests addressed to an IP
  address, `localhost`, or this computer's own name (also with
  `.local`); anything else gets a 403. That blocks "DNS rebinding", a
  trick where a web page you visit tries to read your local server
  through its own domain name. You only need this if you reach the
  computer through a custom local DNS name.
- `--thumb-size`, `--tz-offset`, `--timezone`, `--date-range`, `--no-date-check`,
  `--no-dedupe`, `--no-live-photos`, `--gpx`, `--no-gpx`, `--no-places`,
  `--fetch-offline-assets`: same as for `generate_site.py`.
- `--large-thumb-size PIXELS`: the size of the larger thumbnails that are
  loaded only where the screen can use more pixels (see "Thumbnail and
  card size" below). Unlike the static build, the server has it **on by
  default, at 1600**: the larger thumbnails are made on demand, kept in
  memory, and cost nothing until a screen asks for them. `0` turns them
  off. It must be larger than `--thumb-size`; if `--thumb-size` is already
  1600 or more, nothing larger is offered.
- `--tile-cache DIR`: keep the map tiles you look at in this folder and
  serve them from there, so maps you've already viewed still work
  without internet. See "Working without internet".
- `--tile-url TEMPLATE`: where map tiles come from, with `{z}`, `{x}` and
  `{y}` in it (default: OpenStreetMap). For a tile provider you have an
  account with.
- `--memory-cache-mb MB`: how much memory to use for thumbnails and
  converted photos kept between requests (default 512). The limit is on
  their total size, so a few huge converted HEICs can't push the server
  into gigabytes.
- `--index-cache PATH`: keep each file's metadata (dates, GPS, camera
  details) for reuse at the next start. The cache stores metadata, not
  photo or video files; unchanged files are identified by their size and
  modification time. For one trip, PATH is the JSON cache file; with
  `--library`, PATH is a folder containing a separate cache file for each
  trip. Off by default. Without it, metadata is still read in parallel.
- `--verbose`: log every request. Normally only errors (other than 404)
  and requests slower than two seconds are printed.

The GUI's **Help** window describes each field, its units, defaults and
effect. Map-tile, metadata-cache and allowed-host settings apply to the
live server; the output folder and force-regeneration setting apply to
static-site generation.

Apart from `trip_content.json` (and the tile folder or index cache, if you
use them), the server writes nothing to disk, and it writes to the JSON
file only when you save an edit in the page (plus, at startup, blank
entries for new days, unless `--read-only`).
Thumbnails and converted images are built in memory and kept in a
size-limited in-memory cache, so revisiting a day is fast. The cache is
gone when the server stops. At most one image per CPU core is built at a
time, however many requests arrive, and when several requests ask for the
same image at once it's only built once.

## 2c. Several trips: a library

If your trips are in folders all over the place, list them in a text
file, one photos folder per line:

```
# trips.txt
/Volumes/Photos/2024 Japan
~/Pictures/Italy 2025          | italy
/Volumes/NAS/Family/Iceland     | iceland | --timezone Asia/Reykjavik --no-dedupe
```

Each line is a photos folder, then optionally, after `|`, the name the
trip gets in web addresses and as its folder (otherwise it's made from
the folder's name: `2024 Japan` becomes `2024-japan`), then optionally,
after another `|`, options for that trip only. Those can be `-c`,
`--tz-offset`, `--timezone`, `--date-range`, `--no-date-check`, `--no-dedupe`,
`--no-live-photos`, `--gpx`, `--no-gpx` and `--no-places`; everything
else comes from the command line. `-c` and `--date-range` only make sense
for one trip, so with `--library` they're refused on the command line
and belong on a trip's line. Relative paths are relative to the list
file: write the full path (starting with `/`, or a drive letter such as
`C:\` on Windows) to point anywhere else. Quotes around a path are fine
but not needed. Lines starting with `#` are comments.

Use either `--timezone IANA_NAME` or `--tz-offset HOURS` on a trip line,
not both. A trip's setting overrides the command-line default; the named
time zone also accounts for daylight-saving changes.

The overview page heading and browser-tab title default to **Trips**. Set
`--library-title` to use a different title; it applies to both live and static
libraries:

```bash
# every trip into its own folder under ~/AlbumMakerSites, plus a page linking them
python generate_site.py --library trips.txt --library-title "Family Holidays" -o ~/AlbumMakerSites

# or serve them all: the list at http://127.0.0.1:8000/, each trip under /<name>/
python album_server.py --library trips.txt --library-title "Family Holidays"
```

The library page shows a card per trip (cover, dates, main towns, days,
photos and kilometres), newest first, and a map with every trip on it.
Each trip's pages get an "All trips" link, and `Esc` on a trip's homepage
goes back to the list.

- **Each trip keeps its own `trip_content.json`** in its photos folder,
  exactly as when it's built or served on its own. A new one starts with
  the folder's name as the trip's title. If the photos folder can't be
  written to, the file goes next to the list file instead
  (`trip_content-<name>.json`), never into the current folder, which every
  trip shares.
- **A trip that can't be built or loaded** (a drive that isn't plugged
  in, a mistake in its `trip_content.json`) doesn't stop the others; the
  messages say what went wrong. The static library keeps listing the
  version built earlier, if there is one. The command exits with an error
  so scripts notice.
- **Rebuilding is per trip and incremental**, as for a single trip.
  Taking a trip off the list removes it from the page; its folder is left
  alone (the build says so), so delete it yourself if you don't need it.
- **The live server** shares one memory budget (`--memory-cache-mb`), one
  editor link and one tile cache between all trips. With
  `--index-cache`, give a folder: each trip gets its own file in it.
- The library page shows what visitors may see: hidden photos don't count
  and are never a trip's cover.

## 3. Titles, descriptions and photo notes: `trip_content.json`

The first run of either script creates `trip_content.json` in your
photos folder, with an entry for every day it found:

Where the file is looked for, unless you pass `-c`:

- `trip_content.json` in your photos folder.
- If there's none there yet, but there is one in the folder you run the
  script from (where versions before 2.0 kept it), that one is used, with
  a note suggesting you move it next to your photos.
- If the photos folder can't be written to (a read-only network share,
  say), the folder you run the script from.

```json
{
  "title": "My Trip",
  "subtitle": "",
  "days": {
    "2026-05-01": { "title": "", "description": "" },
    "2026-05-02": { "title": "", "description": "" },
    "unknown": { "title": "", "description": "" }
  },
  "photos": {}
}
```

Edit `title` and `subtitle` for the whole trip, and `title` and
`description` for each day. A blank line in a description starts a new
paragraph. The `"unknown"` entry only appears if some files had no usable
capture date; it's the "Unsorted" page (see "Day grouping" below).

Per-photo notes and each day's cover live in the same file. Here's one
that's been filled in:

```json
{
  "title": "Belgium 2026",
  "subtitle": "May, with the whole family",
  "days": {
    "2026-05-01": {
      "title": "Canals & coffee",
      "description": "Arrived early.\n\nThe light was perfect.",
      "cover": "IMG_0412.jpg"
    }
  },
  "photos": {
    "IMG_0412.jpg":     { "caption": "Golden hour on the canal", "favorite": true },
    "sub/IMG_0499.jpg": { "hidden": true }
  }
}
```

- `days.<date>.cover`: the photo to use as that day's card on the
  homepage. Without one, the day uses its first photo (a photo rather
  than a video frame, when there is one).
- `photos`: keyed by the file's path **relative to your photos folder**,
  with forward slashes, even on Windows (`sub/IMG_0499.jpg`). Each entry
  can have:
  - `caption`: shown under the photo in the slideshow and used as the
    image's alt text.
  - `favorite`: `true` marks it with a ★ and includes it in the
    Favorites filter.
  - `hidden`: `true` keeps it away from visitors. See "Hidden photos".
- Only values that differ from the defaults are stored, so an entry
  disappears again when you clear its caption and un-favorite it.
- Moving or renaming a file detaches its caption and flags, because the
  path is the key. The entry is kept in the file, and the script prints
  a note at startup saying how many entries match no file.

You can edit all of this by hand, or (live server only) in the page; see
section 4.

To see your changes:

- **Static site:** re-run the same `generate_site.py` command.
- **Live server:** just save the file and refresh the page.

Your text is safe:

- **Nothing is deleted behind your back.** Later runs only add blank
  entries for new days and never change text you've written. When
  entries are added, a missing `title` or `subtitle` is filled in with
  the default.
- **Edits made in the page are careful.** The server re-reads the file
  from disk right before saving, changes only the one thing you edited,
  and replaces the file in one step, so a crash can't leave half a file.
  Keys it doesn't recognise are kept. The file is saved with standard
  2-space indentation, so hand-made formatting is tidied up.
- **A broken file is never rewritten.** If the file has a mistake (a
  trailing comma is the classic), the script stops and tells you what's
  wrong, including the line and column for syntax errors. A running
  server prints a warning and keeps showing the last version that loaded
  fine until you save a fixed one. While the file is broken, in-page
  edits are refused with a message ("Nothing was saved"), instead of
  overwriting your half-finished hand edit.
- **Titles, descriptions and captions are plain text.** Anything that
  looks like HTML is shown as-is rather than interpreted.

## 4. Editing in the page (live server)

When you open the live server on the computer that's running it, the day
pages get editing controls. Changes are saved to `trip_content.json` the
moment you make them, and the page updates without reloading.

**In the slideshow** (open any photo), a bar under the picture has:

- **Favorite** (or press `S`): marks the photo with a ★.
- A **caption** box: type, then press Enter or click away to save.
- **Set as cover**: makes this photo the day's cover on the homepage.
- **Set trip cover**: sets this photo as the cover for the entire trip (distinct from day covers).
- **Hide** / **Unhide**: see below.

**On the day page**, **Edit title & description** opens a small form.
Saving reloads the page with your text in place. **Preview as visitor**
reloads the page exactly as someone else would see it, with no editing
controls and hidden photos gone. (It applies to that one page; navigating
elsewhere puts you back in the editor.)

Who can edit:

- The computer running the server, whether or not `--lan` is on. Using
  the machine's own network address from the machine itself still counts.
  Requests that arrive through a proxy (they carry a `Forwarded`,
  `X-Forwarded-For`, `X-Real-IP` or `Via` header) don't count, even
  though they come from the same computer.
- Any browser that has opened the **editor link** the server prints at
  startup (`http://.../?key=...`). Opening it once remembers that browser
  (a cookie) until the server restarts; the key is removed from the
  address bar straight away. That's how you edit from your phone or
  another computer. The key is new every time the server starts, so an
  old link stops working. Treat the link like a password.
- Other devices on your network (with `--lan`) can look but not change
  anything, and don't see hidden photos.
- `--editor-link-only` stops the computer running the server from being
  an editor automatically, so only the editor link works. Use it behind a
  reverse proxy or tunnel, which connects from the same computer and
  might not say so.
- `--read-only` turns editing off for everyone, including you.
- Edit requests are only accepted from pages the server itself serves, so
  a web page you visit can't send edits to it.

### Favorites

Once any photo is a favorite, a **★ Favorites (n)** button appears above
the thumbnail grid. It shows only the favorites, and while it's on the
slideshow's arrows and swipes step through favorites only. Favorites
work on the static site too, since they're read from the file.

### Hidden photos

Hiding is for the receipts, screenshots and blurry shots you don't want
anyone else to see. It's not the same as deleting: your files are never
touched.

- **Live server:** visitors don't see a hidden photo anywhere, and its
  address returns 404 even if they guess it. You still see it, dimmed
  and labelled HIDDEN, so you can unhide it. A day whose photos are all
  hidden disappears for visitors, and the "Day 1, Day 2..." numbering
  skips it.
- **Static site:** hidden photos are **left out of the build entirely**,
  not just not shown, so their files never end up in a folder you might
  publish. A Live Photo's motion clip goes with its photo (the same rule
  as the live server: a clip with the same name that looks like a Live
  Photo clip; with `--no-live-photos`, clips are separate videos you hide
  separately).
- **Identical copies are hidden too.** If the same photo is in two
  folders, only one copy is shown (see "Duplicates"), and hiding either
  copy hides the photo. A copy elsewhere never slips into the site in
  its place. Captions and favorites on any copy apply as well.

## 5. Working without internet

Out of the box, three things come from the internet: the map library
(Leaflet), the fonts, and the map tiles (the background pictures). Your
photos, thumbnails, videos, slideshow and editing never need it. Without
a connection the pages still work, but the maps are blank and the fonts
fall back to your system's. Three optional steps fix that, and each one
helps on its own.

### Step 1: keep the map library and fonts locally

Run this once, while you're online:

```bash
python album_server.py --fetch-offline-assets
```

(`generate_site.py --fetch-offline-assets` does the same thing.) It saves
Leaflet and the three fonts, about 0.5 MB in 28 files, into the
`offline-assets` folder beside `settings.json`, and from then on both scripts
use those local copies automatically. The configuration directory is:

- macOS: `~/Library/Application Support/AlbumMaker/`
- Windows: `%APPDATA%\AlbumMaker\`
- Linux: `$XDG_CONFIG_HOME/albummaker/`, or `~/.config/albummaker/`

- The files come from the npm registry, in exact pinned versions, and
  each download is checked against its published SHA-512 checksum. One
  that doesn't match is refused.
- If anything fails (no connection, a bad checksum, a full disk), it says
  so and your existing `offline-assets` folder is left as it was. A half-finished
  download is never used.
- The live server picks the folder up when it starts; it prints whether
  it found it. The static build copies it into the site
  (`assets/vendor`), so the site keeps working when moved to another
  computer or a USB drive.
- Licenses: Leaflet is BSD-2-Clause and the fonts (Fraunces, IBM Plex
  Sans, IBM Plex Mono) are under the SIL Open Font License. The licenses
  are saved in `offline-assets/licenses`, and the static build copies them along.
- The place names (`albummaker/data/places.tsv.gz`) are from
  [GeoNames](https://www.geonames.org/), under the Creative Commons
  Attribution 4.0 licence. Pages that show place names say so in their
  footer, as that licence asks. `python tools/build_places.py` rebuilds
  the file from GeoNames' current data.
- To go back to loading everything from the internet, remove the
  `offline-assets` folder. To refresh the files, run the command again.

### Step 2: keep the map tiles you've looked at (live server)

```bash
python album_server.py -i /path/to/your/photos --tile-cache tile_cache
```

With this, the pages ask the server for their tiles. The server downloads
a tile the first time it's needed, saves it in the folder, and serves it
from there from then on. Browse your trip's maps once while online, and
those areas keep working with no connection. How it behaves:

- Tiles are only fetched as you look at the map, never in bulk, and at
  most two at a time. Someone who browses fast gets each tile downloaded
  once, however many requests arrive.
- A cached tile older than a week is refreshed when the provider is
  reachable. When it isn't, the old copy is used rather than nothing.
- When there's no connection, a tile that was never cached answers
  immediately instead of making every tile wait for a timeout. If the
  provider refuses the server (HTTP 403, 429 or 503), it stops asking for
  five minutes.
- Only real images are saved; a hotel Wi-Fi login page that answers in
  place of a tile is not.
- The folder grows only with what you look at (tiles are typically a few
  to a few tens of KB each) and is safe to delete at any time.
- `--tile-url` points it at another provider, for example
  `--tile-url "https://tiles.example.com/{z}/{x}/{y}.png"`. With
  `--tile-url` but no `--tile-cache`, the pages use that provider
  directly.
- The server introduces itself to the provider as "AlbumMaker". OpenStreetMap's
  free tile server is meant for light, personal use like this; for
  anything heavier, use a provider you have an account with. The credit
  shown on the maps says "OpenStreetMap contributors"; if you switch
  provider, check what credit it asks for.
- A static site can't cache tiles, since there's no server. It shows
  tiles while online and the outline below while offline.

### Step 3: a fallback outline (automatic)

Wherever no tile can be loaded (no internet, or an area you never
cached), the maps now show a rough outline of the continents instead of
a blank box, with your photo points on top, and a small notice: "Map
tiles unavailable (offline?)". It's country-level only, with no roads or
place names, but it keeps the points in context. It needs no setup apart
from step 1, because it's drawn by the map library. A few missing tiles
among loaded ones don't trigger the notice.

### What each step gets you

| Setup | Offline result |
|---|---|
| Nothing | Photos, slideshow and editing work. The maps are blank and the fonts are your system's. |
| Step 1 | Maps show the outline and your points, with the right fonts. |
| Steps 1 and 2 | Areas you've viewed before show the full map; everywhere else shows the outline. |

To try it, open the site in Chrome, press F12, choose the Network tab and
set it to "Offline", then reload. Turning Wi-Fi off works too.

## 6. GPS tracks and place names

### GPS tracks (`.gpx`)

A track recorded during the trip (a `.gpx` file from a GPS watch, a
phone app such as Strava or OsmAnd, or a GPS logger) can be dropped into
the photos folder, or given with `--gpx`. Most watches and apps can
export an activity as GPX; several files (one per day, or from two
devices) are fine. Both scripts then:

- **Give a location to files that have none**, such as most cameras'
  photos: the track's position at the moment each was taken, marked
  "(from GPX track)" in the details panel. A file's own GPS always wins.
  Between two track points the position is interpolated, as long as
  they're at most 10 minutes apart; within 2 minutes before or after a
  stretch of track, its first or last point is used. Otherwise the file
  stays without a location: nothing is made up across a gap. Several
  tracks may overlap (a watch and a phone both recording): each is kept
  apart, and the one with a point closest in time is used.
- **Draw the way you went** on the day maps and the overview map, and
  measure each day's distance along it rather than from photo to photo.

Matching needs the moment each photo was taken in UTC, but cameras
record local clock time. The offset comes from the file where it's
recorded (the EXIF offset most phones and newer cameras write, a phone's
GPS time stamp, an iPhone video's local time). For a file that doesn't
say, the offset most of that day's other files state is used, else the
selected `--timezone` or `--tz-offset`, else the one most of the trip
states. If none of these is known, the startup message says so: pass
`--timezone` (recommended for trips with daylight-saving changes) or
`--tz-offset`. A camera whose
clock was simply set wrong will be placed wrongly; fix its clock (or the
files' times) first.

The track itself is shown as recorded, also on days where you've hidden
photos. Leave a `.gpx` file out of the folder (or pass `--no-gpx`) if
the way you went should stay private.

### Place names

Place names come from a list of about 160,000 towns shipped with
these scripts (GeoNames, every place with at least 1000 inhabitants), so
they work without internet. Each located photo gets a "Place" row in the
details panel ("Kyoto, Japan", or "near Kyoto, Japan" outside the town
itself; a big city counts from further out than a village). Each day gets
a line with its main towns in the order you visited them ("Kyoto ·
Nara") under its date. In the editor, a day without a title offers
those names as one ("Use 'Kyoto · Nara'"); nothing is filled in unless
you click it. On the live server, a visitor's day line only names towns
of photos they can see. `--no-places` turns place names off.

## What you get

- **Homepage:** the trip title, stats (days / photos / videos / km
  tracked: along the GPS track where there is one, otherwise the
  straight-line distance between each day's consecutive geotagged photos),
  an overview map with every geotagged photo as a point coloured by day
  (and each day's GPS track, if any), and a card per day showing the
  towns visited that day.
  Click a point or a card to jump to that day. A Card size control
  (S / M / L / XL) above the cards makes them bigger or smaller; see
  "Thumbnail and card size" below.
- **Day pages:** the description, the day's towns under its date, a map
  of that day's photo and video locations, a thumbnail grid, and
  previous/next day links.
  - The map shows the photos as points. With a GPS track it also draws
    the way you actually went; without one there's no connecting line,
    since photos aren't taken in a tidy walking order and a line through
    them gets messy fast.
  - Clicking a point shows a preview of that photo; clicking the preview
    opens it in the slideshow.
  - `←` / `→` (or a swipe on a touchscreen) go to the previous or next
    day.
  - `Esc` goes back to the homepage. It backs out one step at a time, so
    if a map popup, the viewer, its details panel or the title and
    description form is open, `Esc` closes that first and a further press
    leaves the page. Holding the key down, or pressing it together with
    Ctrl, Alt or Cmd, does not leave.
  - Favorites show a ★ in the grid, and captions are used as alt text.
  - Thumbnails can be reached with Tab, and Enter or Space opens one.
- **Slideshow:** click any thumbnail to open a full-screen viewer.
  - `←` / `→`: previous / next photo. Clicking the left or right edge of
    the screen, or swiping, also works.
  - **Autoplay** (`Space` or the Play button): see below.
  - Scroll or pinch to zoom, drag to pan once zoomed, double-click to
    toggle zoom. `+` / `-` / `0` work from the keyboard. The edge-click
    zones step aside while zoomed, so they don't fight with panning.
  - The caption, if there is one, appears at the bottom of the viewer
    along with the time the photo was taken and its position in the day.
  - `I`: toggle a details panel with camera, lens, aperture, shutter
    speed, ISO, focal length, resolution, file size, the place ("Kyoto,
    Japan"), and a small map of where the file was captured. Videos show duration, codec and frame
    rate instead of camera settings. The panel also has a "Download
    original file" link.
  - `F`: toggle true full-screen, with no bar, caption or on-screen
    controls. Everything else still works.
  - `Esc`: close the details panel if it's open, otherwise close the
    viewer. Once the viewer is closed, `Esc` on the day page goes back
    to the homepage.
  - `L` (or the Live button): play a Live Photo's motion clip. The
    button only appears on Live Photos, which are also marked LIVE in
    the grid. See "Live Photos" below.
  - Neighbouring photos preload in the background, so navigation feels
    instant. Videos play inline with normal video controls.
- **In a library** (see "Several trips"): an "All trips" link at the top
  of every page, and `Esc` on a trip's homepage goes back to the list.
- **Responsive layout:** the maps and thumbnail grid scale with the
  screen, from a big monitor down to a phone.
- **Thumbnail and card size:** day pages have a Thumbnail size control
  and the homepage has a Card size control, each with four steps: **S**,
  **M**, **L** and **XL**. M is the original layout of both. The browser
  remembers each choice separately, so a big thumbnail grid on day pages
  doesn't make the homepage cards big, and the choice carries over between
  days.

  **Sharp at every size.** A thumbnail is small (400 pixels by default),
  which is plenty for S and M but not for the big sizes: an XL tile, or a
  card that crops a portrait photo to a wide box, is stretched across more
  screen pixels than the thumbnail has, especially on a high-resolution
  display. So each tile checks its own size, the screen's pixel density and
  the thumbnail's real size, and where the thumbnail would be stretched it
  swaps in the larger version (`--large-thumb-size`, 1600 pixels on the live
  server). It does this only for tiles near the screen, so a long day
  doesn't fetch hundreds of large images, and it keeps the normal thumbnail
  if the download fails. Tiles that are already sharp, such as everything
  at S and M on an ordinary screen, never download the larger version. The
  live server needs nothing from you. For a **static site**, build it with
  `--large-thumb-size 1600`; without it, XL shows the normal thumbnails and
  looks soft on high-resolution screens. For the very sharpest result
  everywhere (a 4K screen scaled to 200%, portrait photos on XL cards) use
  `--large-thumb-size 2400`.

### Keyboard shortcuts

| Key | Where | Does |
|---|---|---|
| `←` `→` | day page | previous / next day |
| `Esc` | day page | back to the homepage (closes anything open first) |
| `←` `→` | slideshow | previous / next photo |
| `Space` | slideshow | play / pause autoplay |
| `+` `-` `0` | slideshow | zoom in / out / reset |
| `I` | slideshow | details panel |
| `F` | slideshow | true full-screen |
| `L` | slideshow | play a Live Photo's clip |
| `S` | slideshow, editing | favorite / unfavorite |
| `Esc` | slideshow | close details, then close the viewer |

Shortcuts are switched off while you're typing in a caption or title box,
except `Esc`, which leaves the box (or cancels the title and description
form) without leaving the page.

### Autoplay

Press **Play** (or `Space`) in the slideshow to have it advance by
itself. It never starts on its own, and closing the viewer stops it.

- A thin progress bar across the top shows the time left on the current
  photo. The speed button, which appears while playing, cycles through
  3, 5, 8 and 12 seconds; the choice is remembered by the browser.
- A video plays to the end before moving on.
- It loops back to the first photo after the last. With the Favorites
  filter on, it plays favorites only.
- Using the arrows or swiping keeps it playing and restarts the timer.
- It waits while the details panel is open, while you're zoomed in, while
  you're typing a caption, while a Live Photo clip plays, and while the
  tab is in the background, then continues with a fresh interval.
- In full-screen the buttons are hidden, so use `Space`.

### Links to a photo, and the Back button

Opening a photo adds `#p=...` to the address, and the address follows
you as you move between photos. That gives you:

- **Back closes the viewer** instead of leaving the page (and the
  back button on a phone does the same). Forward reopens it.
- **Links to a single photo.** Copy the address while a photo is open
  and anyone opening it lands straight on that photo. Closing it leaves
  them on the day page.

Things to know:

- On the static site the link names the photo's file in `media/`, so
  renaming or re-importing files breaks old links. On the live server it
  uses a short code worked out from the file's path in your photos
  folder, so links keep working across restarts and when other files are
  added; moving or renaming the file breaks them.
- A link to a photo that no longer exists is ignored.
- Links behave best when the site is served over http(s). When a page is
  opened straight from a folder (`file://`), some browsers don't allow
  the address to change; the viewer still works, it just doesn't update
  the address.

## How your photos are handled

**Your originals are never modified.** Neither script changes, moves or
renames your photos and videos. The scripts may create or update
`trip_content.json` in the photos folder (unless you pass `-c` to put it
elsewhere). The live server also updates it when you save an edit in the
page. A static build writes its generated site to the output folder you
choose; optional metadata and map-tile caches are written to their
configured locations.

**Full-screen viewing uses the real files.** For jpg, png, webp and bmp,
the slideshow shows your actual file, with no resizing or recompression.

- **Static site:** a byte-for-byte copy is placed in the output's
  `media/` folder.
- **Live server:** the file is streamed straight off disk.

Videos work the same way. This is meant for viewing on your own computer
or over a home network. On the open internet, visitors on slow
connections would be downloading 20 MB photos just to see them shrunk on
a phone.

**The copies keep their metadata.** Because the static site's copies are
byte for byte, they still contain everything embedded in the originals,
including GPS coordinates, and the details panel shows the location and
the original filename. That's fine for family and friends, but think
before putting the folder on a public website. Photos you've hidden are
not copied at all.

**HEIC/HEIF, TIFF and multi-image JPEGs are converted for display.**
Chrome, Firefox and Edge can't show HEIC/HEIF or TIFF at all, and some
multi-image JPEGs (written by certain portrait, panorama and HDR camera
modes) don't display correctly in browsers. For these files, the
slideshow shows a full-resolution, near-lossless JPEG version with the
same EXIF data (camera, lens, GPS, timestamp). It's shown in every
browser, including Safari, because some systems' HEIC decoders get the
orientation wrong.

- **Static site:** the JPEG version is saved in `media/`, next to an
  untouched copy of the original. These photos take up space twice in
  the output folder.
- **Live server:** the JPEG is built in memory on request.

Either way, the real original is what the "Download original file" link
in the details panel gives you.

**Live Photos.** An iPhone exports a Live Photo as a photo
(`IMG_1234.HEIC` or `.JPG`) plus a short `IMG_1234.MOV`. Instead of
listing that clip as a separate video, both scripts attach it to the
photo, so it plays from the slideshow with `L` or the Live button and
then returns to the photo. A video is treated as a Live Photo clip only
when all of these hold:

- A photo with the same name sits in the same folder. Identical copies
  count too: if duplicate detection kept a copy of the photo or the clip
  from another folder, they are still paired.
- The video carries Apple's Live Photo marker, or is at most 4 seconds
  long.

So a real video that just happens to share a photo's name is left alone.
The clips are HEVC, which Safari plays everywhere. Other browsers only
play them where the system has an HEVC decoder; where it doesn't,
pressing Live just stays on the photo. Use `--no-live-photos` to list
the clips as ordinary videos instead.

**Thumbnails** are small separate images (`--thumb-size`), since loading
full-resolution files just to show a preview would be slow. Where a bigger
size or a sharper screen would stretch them, the pages load a larger
version (`--large-thumb-size`) instead; see "Thumbnail and card size".

**Filenames in the static site are kept as close as possible to the
original.** `IMG_1234.HEIC` produces output files named `IMG_1234.*`,
with the same case, spaces and all. The live server doesn't copy files,
so none of this applies there. The exceptions:

- Characters that aren't allowed in Windows filenames (`< > : " / \ | ? *`)
  are replaced with `_`.
- On a collision, a suffix is added. Collisions are checked ignoring
  upper/lower case and the file extension, so `IMG_0001.jpg` from two
  phones, or `IMG_1.jpg` next to `IMG_1.heic`, count as one. The first
  file in alphabetical path order keeps the plain name, with photos
  going before videos (so in a Live Photo pair, the photo keeps it).
  The others get `_2`, `_3` and so on.
- Once a file has an output name, it keeps it on later runs, even if
  new files would now sort before it.
- For a multi-image JPEG, the converted display version is named
  `IMG_1234_display.jpg`, so it doesn't clash with the original's copy.

## Notes & limitations

- **Formats.**
  - **Photos:** jpg, png, heic/heif, tiff, bmp, webp. HEIC needs
    `pi-heif`, which is in `requirements.txt`; without it, HEIC files
    are skipped with a note.
  - **Videos:** mp4, mov, m4v, avi, mkv, 3gp. Whether a video *plays*
    inline depends on the browser supporting its container and codec.
    For example, HEVC files only play where the system has an HEVC
    decoder, and AVI generally doesn't play at all. Such a video still
    gets a preview tile, and "Download original file" still works. The
    files are never converted.
- **Skipped files.** These are ignored during the scan:
  - hidden files and folders (names starting with `.`, such as macOS's
    `._IMG_1234.JPG` companion files)
  - NAS and recycle-bin folders such as `@eaDir`, `#recycle` and
    `$RECYCLE.BIN`
  - files that can't actually be opened as an image or video (skipped
    with a warning, instead of showing up as broken tiles)
  - folders holding a site built by `generate_site.py`, recognized by
    its build record (`.trip_site_manifest.json`), so a generated site
    inside your photos folder is never mistaken for photos
- **Duplicates.** Files with identical content (the same photo imported
  from two devices, a folder synced twice) are detected and only the
  first is kept. Files are only treated as duplicates when they're
  identical byte for byte. Re-exported or re-compressed versions of a
  photo are different files, so they're kept. Use `--no-dedupe` to
  include everything. A caption, favorite or hidden flag on any copy
  applies to the one that's shown.
- **Day grouping.** Photos are placed by their EXIF capture date
  (`DateTimeOriginal`, falling back to the other EXIF date fields), and
  videos by their `creation_time`.
  - Files with no capture date at all are **not** placed by their file
    modified date. A copy, sync or export can reset that date, so trusting
    it could invent a day that never happened or misfile a photo.
  - These files go on a single "Unsorted" page at the end instead. It has
    the same grid and map, and it doesn't count toward the "days" total.
  - Both scripts tell you how many files this happened for.
  - Days run from midnight to midnight, so a photo taken at 1 a.m. belongs
    to the new day.
- **Dates that look wrong.** Some files do have a capture date, but it's
  plainly bad: a camera whose clock was never set (it counts up from
  2000-01-01), an editor that stamped a placeholder date, or a clock set
  into the future. Left alone, each would create a "day" far from the
  real trip. So by default a date is treated as wrong, and the file goes
  to "Unsorted" like a file with no date, when it is:
  - before 1995,
  - in the future, or
  - more than a year away from the rest of your files. This last check
    only applies when there are at least 5 files and at least 80% of
    them agree with each other, so a small folder, or a folder that
    genuinely spans several years, is left alone.

  Both scripts list the affected files and the reason when they start,
  and each one's details panel has a "Date" row saying which embedded
  date was ignored. If the dates are in fact right, say so:

  ```bash
  python generate_site.py -i photos --date-range 2024-08-01 2026-05-10
  ```

  `--date-range` replaces the guessing with your dates (both ends
  included); `--no-date-check` switches the check off. The check can't
  spot a wrong date that still looks plausible, such as a clock that's
  off by a few hours or a month.
- **Video timestamps.**
  - iPhone videos also record the local time where they were shot, and
    that's used as-is, just like a photo's EXIF time. No timezone
    setting is needed unless you choose a trip time zone.
  - Other videos only store `creation_time`, in UTC. By default it's
    converted using the timezone of the computer running the script,
    which is only right if that matches the trip's timezone.
  - To set the trip's time zone explicitly, use `--timezone Europe/Paris`
    (or another IANA name). It applies daylight-saving rules and overrides
    the recorded local time in iPhone video metadata too.
  - Alternatively, use `--tz-offset` with the trip's fixed UTC offset.
    This applies to every video, iPhone ones included.
  - Neither setting changes photo capture dates read from EXIF. A time
    zone can, however, affect matching a photo to a GPX track.
- **GPS.** For photos, the location comes from EXIF GPS data. For videos,
  it comes from the `location` / ISO-6709 tag (what iPhones write), when
  present. Coordinates that can't be real (out of range, or exactly
  0,0) are ignored. Files without GPS still appear in the grid, just not
  on the map.
- **Maps** use Leaflet with free OpenStreetMap tiles: no API key, no
  account. The dark look is a CSS filter over the standard tiles.
  - By default the map library, fonts and tiles load from the internet,
    so they need a connection; everything else works offline. See
    "Working without internet" to keep the first two locally and to
    cache tiles.
  - For a heavily visited public site, OpenStreetMap's tile usage policy
    asks you to use a dedicated provider (e.g. MapTiler, Stadia Maps,
    Mapbox). For a personal site shared with family and friends, the
    default is fine.
- **Re-running the static build.** The output folder holds a build
  record, `.trip_site_manifest.json`, so a re-run only redoes what's
  needed:
  - New photos, and photos whose size or modification time changed
    (edited or re-exported), get fresh thumbnails and copies. Unchanged
    files are reused, so regenerating a big trip is fast.
  - Changing `--thumb-size` rebuilds all thumbnails automatically.
  - Outputs of photos you removed, and pages for days that no longer
    have any photos, are deleted. The same goes for photos you've newly
    hidden. Only files the build record lists are ever deleted, and only
    inside the output folder.
  - Captions, favorites and covers don't touch the photo files, so
    re-running after changing only those is quick.
  - The build record also keeps every file's metadata, so unchanged
    files aren't even opened again; a re-run with nothing new takes
    well under a second for most trips.
  - The record also notes which AlbumMaker version made the site
    (`app_version`). It is informational: a site built by an older version
    is rebuilt by the same rules as before.
  - Deleting the build record is safe: the next run just regenerates
    thumbnails and converted images, and re-checks the copies. It can no
    longer tell which old files earlier runs made, though, so it cleans
    nothing up; it warns if the output folder holds files the new build
    didn't make (for example a photo you have hidden since). Delete the
    output folder for a clean result.
- **Editing is for the live server.** The static site only displays what
  `trip_content.json` says. Edits you make in the live server don't
  appear in a static site until you re-run `generate_site.py`.
- **Live server: new photos need a restart.** The folder is scanned once
  at startup (in parallel; with `--index-cache`, only new or changed
  files are read). Your `trip_content.json` is untouched by a restart;
  new days just get a blank entry added.
- **Live server: no login for viewing.** Anyone who can reach the server
  can look at the (non-hidden) photos. It's meant for your own computer
  or home network. Don't port-forward it to the internet. Editing needs
  the computer itself or the editor link (see section 4).
- **Live server: first view of a day is slower.** Thumbnails are built
  the first time a day is opened, which takes a moment for days with
  many photos. Revisiting is fast, thanks to the in-memory cache.
- **Upgrading from an older version.** A site built before the build
  record existed has no record of its files. The first run after
  upgrading regenerates thumbnails and converted images once; copies of
  originals are kept if they're still up to date. Files that only older
  versions made aren't in the record, so they are never cleaned up
  automatically. This includes the resized copies very old versions
  made (filenames ending in `-1280.jpg`, `-1920.jpg`, etc.), and
  leftovers from photos removed before upgrading. For a clean result,
  delete the output folder and regenerate.

## Troubleshooting

- **The packaged desktop app does something unexpected.** It has no console, so
  an unexpected error is shown in a message box and saved to
  `albummaker-gui-error.log` in the same folder as the app's settings. The
  **Activity** panel shows the command that was started and its output; include
  both when you report a problem.
- **Which version am I running?** Run any script with `--version` (for
  example `python album_server.py --version`), look at the desktop app's
  title bar or Help window, or at the footer of a generated page. Include it
  when you report a problem.
- **Photos ended up on "Unsorted".** They have no capture date, or one
  that looked wrong. Open a photo's details panel: a "Date" row says
  which embedded date was ignored and why. If the dates are right, pass
  `--date-range START END`. If a video has no date, check that ffmpeg is
  installed.
- **I can't see the editing buttons.** They only appear when you open the
  server from the computer running it, or in a browser that has opened
  the editor link printed at startup, and not with `--read-only`. Behind
  a proxy or tunnel, or with `--editor-link-only`, use the editor link.
  If you're looking at a page that has "Preview as visitor" applied, open
  the day again from the homepage.
- **"Nothing was saved" when editing.** `trip_content.json` currently
  has a syntax error. Fix it (the message says where), and edits work
  again. Nothing you typed in the file was touched.
- **A caption or favorite disappeared after I reorganised my folders.**
  They're attached to the file's path. The entry is still in
  `trip_content.json`; change its key to the new path.
- **A photo I hid is still on my published site.** Re-run
  `generate_site.py`; it removes the photo's files from the output folder.
  Then upload again, using a method that also deletes files that no
  longer exist locally (a plain copy leaves the old ones on the server).
- **XL (or L) thumbnails and cards look soft.** The thumbnail is being
  stretched, and the pages should swap in a larger one. On the live server
  check that you haven't passed `--large-thumb-size 0`, and that it is
  larger than your `--thumb-size` (the startup message says "Larger
  thumbnails (1600px)..."). A static site only has larger thumbnails if it
  was built with `--large-thumb-size`. If it is still not sharp enough,
  raise it (`--large-thumb-size 2400`). Raising `--thumb-size` instead
  makes every grid heavier, including the small sizes that don't need it.
- **The map is blank or grey.** By default the map library and tiles
  load from the internet, so check your connection. To be independent of
  it, see "Working without internet". With local offline assets in
  place you'll see a rough outline and a "tiles unavailable" notice
  instead of a blank box.
- **`--fetch-offline-assets` says it couldn't download.** It needs a
  connection to the npm registry (registry.npmjs.org); check your
  internet, proxy or firewall and run it again. Nothing was changed.
- **The fonts look different without internet.** The fonts load from the
  internet unless offline assets are present; run
  `--fetch-offline-assets` once. Until then the browser uses a fallback.
- **I use `--tile-cache` but tiles don't appear.** The server has to be
  able to reach the tile provider the first time it needs a tile. Watch
  the terminal for a message, and make sure the cache folder is writable.
- **Photos without GPS weren't placed on my GPX track.** The startup
  message says how many were placed. Check that the track covers the
  time the photos were taken (a track that ends before you took them
  can't place them). If the message says their time zone is unknown,
  pass `--timezone Asia/Tokyo` (or `--tz-offset 9` for a fixed offset)
  for Japan.
- **Photos were placed at the wrong spot on the track.** Usually the
  camera's clock was off (set to home time, or not changed for daylight
  saving). Placing uses the time each photo was taken, so fix the camera
  time (or `--timezone` / `--tz-offset` for a whole trip) and build again.
- **No place names appear, or an odd town is named.** Only photos with a
  location get one. The name comes from a list of every place with at
  least 1000 inhabitants, weighing distance against size, so in the
  countryside it can be a village you've never heard of; more than 50 km
  from any town, nothing is shown. `--no-places` turns them off.
- **A trip is missing from the library page.** The build or server
  messages say why: usually the folder isn't there (a drive that isn't
  plugged in) or its `trip_content.json` has a mistake. A static library
  keeps showing the version of a trip it built earlier.
- **The library shows a trip under its folder's name.** Its title in
  `trip_content.json` is still the default "My Trip". Give it a real one
  (`"title"` in that file).
- **A video shows a preview but won't play.** The browser can't decode
  that format. Use "Download original file" from the details panel, or
  play it with a desktop player.

## Running the tests

The `tests/` folder holds a test suite for the project. It builds small
photos (and, with ffmpeg, short videos) in temporary folders, runs the
generator and the live server exactly as you would, and checks what comes
out. It never touches your own photos, your `trip_content.json` or your
AlbumMaker user data. From the project folder:

```bash
python -m unittest discover -s tests -t .
```

`pytest tests` works too, if you prefer it. The suite's size and runtime
vary with optional dependencies and enabled browser tests. To run one
file or one test, name it:

```bash
python -m unittest tests.test_editing
python -m unittest tests.test_server.ServerBasicsTests.test_byte_ranges
python -m unittest tests.test_review_fixes
```

Tests that patch module-level names should patch them in the `albummaker`
module that uses them (for example `albummaker.offline._NPM_REGISTRY`).

Some parts are optional, and are skipped with a note when their
requirements are missing:

- **Video tests** need ffmpeg.
- **Browser tests** (the viewer's editing controls, links and Back button,
  autoplay, the day pages' arrow and Esc keys, the size controls, the
  sharper thumbnails, and the maps with the real Leaflet library, run in
  jsdom, a browser without a screen) need Node.js 18 or newer and a
  one-time `npm install` in `tests/js`.
- **A check of the real downloads**: set `TRIP_TEST_NETWORK=1` to also
  download the real packages for `--fetch-offline-assets` and verify them
  against their pinned checksums. It's off by default so the suite never
  needs the internet; it's the test to run if npm ever changes a package.

What's covered:

- The pure logic: GPS and date handling, the info-panel text, day
  grouping, the date check, duplicate detection, Live Photo pairing,
  output file naming, and every rule for `trip_content.json` (reading it,
  the per-photo and per-day edits, hidden photos, covers).
- The static build, run as a command: the files it makes, byte-for-byte
  copies, converted images, re-runs that only redo what changed, cleanup
  of what's no longer wanted, odd file names, and the command-line errors.
- The live server, run for real: pages, thumbnails, byte ranges, the Host
  check, bursts of simultaneous requests, and hot-reloading of the content
  file.
- Editing: what the API saves, everything it refuses, who may use it,
  hidden photos (including that they can't be fetched by working out
  their URL key), a content file with a mistake never being overwritten, and many
  edits at once losing nothing.
- Working offline: the download (against a fake npm registry, so no
  internet is used), checksum and failure handling, the `vendor` route and
  attempts to read files outside it, and the tile cache (against a fake
  tile provider that can go offline, hang, or refuse).
- GPS tracks (reading `.gpx` files, time zones, placing photos, the day
  tracks) and place names, and the library of trips (the list file, the
  static build and the live server).
- The version number: that `--version` works on every entry point, that
  generated page footers and the build record carry it, and that the
  release-tag check (`tools/check_version.py`) accepts only a matching tag.
- The viewer's behaviour in the browser tests above.

What isn't covered: a real browser (jsdom approximates one, with a few
stand-ins for what Leaflet needs), the real OpenStreetMap and npm
servers, and HEIC conversion, which needs `pi-heif`.

## Customizing the look

The whole design is shared by both scripts, so a change shows up in the
static site and the live server alike. It lives in ordinary files in the
`albummaker` folder:

- **`albummaker/static/style.css`.** Colours are CSS variables at the top
  (`--navy`, `--gold`, `--teal`, etc.), so they're easy to swap.
- **`DAY_COLORS` in `albummaker/model.py`.** This is the palette used for
  each day on the overview map.
- **`albummaker/templates/`.** The page layout: plain Jinja2 templates
  (`index.html`, `day.html`, and the `head.html` and `slideshow.html`
  parts they include).
- **`albummaker/static/world_land.json`.** The continent outline the maps
  fall back to offline (Natural Earth, public domain). The colours it's
  drawn in are in the `addBaseMap` function in `script.js`.
- **`albummaker/static/script.js`.** The slideshow, maps, autoplay and
  editing code.

The live server reads these files when it starts, so restart it after a
change. Its pages link to the CSS and JavaScript with a version code that
changes with their content, so browsers pick up the new files at once.

## License and third-party notices

AlbumMaker's source code is available under the
[PolyForm Noncommercial License 1.0.0](./LICENSE). In plain words: you may use,
modify and share it for **noncommercial purposes**, including personal and hobby
use, and use by charities, schools and public institutions. You may **not** sell
it or use it for commercial purposes without a separate written license from the
author. This is a *source-available* license, not an OSI-approved open source
license. Any earlier version that was published under the MIT License remains MIT
for those who received it.

For commercial licensing, contact the author via the repository.

Third-party software and data are not relicensed; their licenses,
attributions and binary-distribution notes are listed in
[THIRD_PARTY_NOTICES.md](./THIRD_PARTY_NOTICES.md). Read the notices for the
specific executable before redistributing it, especially the GPL source
notes for the bundled HEIF libraries. FFmpeg is not bundled.

## Support the project

AlbumMaker is free for noncommercial use. If it is useful to you, you can make
an optional, voluntary donation. A donation does not buy features, support or a
commercial license.

<!-- TODO: replace with your real donation link(s), e.g. GitHub Sponsors, Liberapay or Ko-fi -->
- Donate: *(link to be added)*
