# Trip Site

Turns a folder of trip photos and videos into a website. It has one
chapter per day, a route map per day, an overview map and title on the
homepage, and a full-screen slideshow you can drive with the keyboard.

It reads EXIF (for photos) and container metadata (for videos) to work
out *when* and *where* each file was captured. You don't type anything
in by hand except the titles and descriptions, if you want them.

There are two scripts. Both produce the same site with the same look and
features; they differ only in how it's delivered:

| | `generate_site.py` | `trip_server.py` |
|---|---|---|
| What it does | Builds a folder of static HTML, thumbnails and media copies | Runs a small local web server that builds every page and image on request |
| Python needed while viewing? | No. Open `index.html`, copy the folder to a USB drive, etc. | Yes, the whole time |
| Disk space | Thumbnails plus a copy of every photo and video | Nothing, except `trip_content.json` |
| After adding or changing photos | Re-run the script (only new or changed files are processed) | Restart the server |
| After editing descriptions | Re-run the script | Save the file and refresh the page |
| Cost | A build step | A little CPU on first view of each day |

Both use the same `requirements.txt` and the same `trip_content.json`
format, so you can switch between them, or use both, at any time.

## 1. Install

You need Python 3.9+, plus:

```bash
pip install -r requirements.txt
```

Keep the three Python files together in one folder: `generate_site.py`,
`trip_server.py`, and `trip_common.py`. The last one holds the code both
scripts share, and neither runs without it.

You also need **ffmpeg** installed and on your PATH. It's used to read
video metadata and grab a thumbnail frame from each video:

- macOS: `brew install ffmpeg`
- Ubuntu/Debian: `sudo apt install ffmpeg`
- Windows: [ffmpeg.org/download](https://ffmpeg.org/download.html), or `winget install ffmpeg`

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
  scanned too. Required.
- `-o / --output`: where the website is written (default: `./site`).
  It can be inside your photos folder; it's excluded from the scan. It
  can't be the photos folder itself, or a folder that contains it.
- `-c / --content`: where titles and descriptions are stored (default:
  `./trip_content.json`). See section 3.
- `--thumb-size`: maximum thumbnail dimension in px (default 400,
  allowed 16–4096).
- `--force`: regenerate all thumbnails and media copies, even for files
  that haven't changed. Normally not needed; see "Re-running the static
  build" below.
- `--tz-offset`: fixed UTC offset in hours (e.g. `2` or `-5.5`) for video
  timestamps. See "Video timestamps" below.
- `--workers`: how many files to process in parallel (default: one per
  CPU core). Lower it if you want to keep using the computer while it
  runs.
- `--no-dedupe`: include exact-duplicate files instead of skipping them.
  See "Duplicates" below.
- `--no-live-photos`: show iPhone Live Photo clips as separate videos
  instead of attaching them to their photos. See "Live Photos" below.

## 2b. Serve it live: `trip_server.py`

```bash
python trip_server.py -i /path/to/your/photos
```

Then open the URL it prints, by default **http://127.0.0.1:8000/**. Stop
the server with Ctrl+C.

Options:

- `-i / --input`: folder with your photos and videos (default: the
  current folder, so you can also drop the script into your photos
  folder and run it with no arguments).
- `-c / --content`: where titles and descriptions are stored (default:
  `./trip_content.json`). See section 3.
- `--port`: default `8000`.
- `--lan`: let other devices on your network open the site too. **There
  is no login of any kind**: anyone on your Wi-Fi or network can browse
  your photos while the server runs with this on. Leave it off unless you
  want that.
- `--allow-host NAME`: an extra host name the server should answer to
  (repeatable). The server only responds to requests addressed to an IP
  address, `localhost`, or this computer's own name (also with
  `.local`); anything else gets a 403. That blocks "DNS rebinding", a
  trick where a web page you visit tries to read your local server
  through its own domain name. You only need this if you reach the
  computer through a custom local DNS name.
- `--thumb-size`: same as for `generate_site.py`.
- `--tz-offset`: same as for `generate_site.py`.
- `--no-dedupe`: same as for `generate_site.py`.
- `--no-live-photos`: same as for `generate_site.py`.

The server writes nothing to disk except `trip_content.json`.
Thumbnails and converted images are built in memory and kept in a small
in-memory cache, so revisiting a day is fast. The cache is gone when the
server stops.

## 3. Titles and descriptions

The first run of either script creates `trip_content.json` in the folder
you run it from, with an entry for every day it found:

```json
{
  "title": "My Trip",
  "subtitle": "",
  "days": {
    "2026-05-01": { "title": "", "description": "" },
    "2026-05-02": { "title": "", "description": "" },
    "unknown": { "title": "", "description": "" }
  }
}
```

Edit `title` and `subtitle` for the whole trip, and `title` and
`description` for each day. A blank line in a description starts a new
paragraph. The `"unknown"` entry only appears if some files had no real
capture date; it's the "Unsorted" page (see "Day grouping" below).

To see your changes:

- **Static site:** re-run the same `generate_site.py` command.
- **Live server:** just save the file and refresh the page.

Your text is safe:

- **Entries are only ever added.** Later runs add blank entries for new
  days and never change text you've written. When entries are added,
  the file is saved with standard 2-space indentation, and a missing
  `title` or `subtitle` is filled in with the default.
- **A broken file is never rewritten.** If the file has a mistake (a
  trailing comma is the classic), the script stops and tells you what's
  wrong, including the line and column for syntax errors. A running server prints a warning and
  keeps showing the last version that loaded fine until you save a fixed
  one.
- **Titles and descriptions are plain text.** Anything that looks like
  HTML is shown as-is rather than interpreted.

## What you get

- **Homepage:** the trip title, stats (days / photos / videos / km
  tracked, where km is the straight-line distance between each day's
  consecutive geotagged photos, Unsorted included), an overview map with every geotagged photo as a point
  coloured by day, and a card per day. Click a point or a card to jump to
  that day.
- **Day pages:** the description, a map of that day's photo and video
  locations, a thumbnail grid, and previous/next day links.
  - The map shows points only, with no connecting line. Photos aren't
    taken in a tidy walking order, so a line through them gets messy
    fast.
  - Clicking a point shows a preview of that photo; clicking the preview
    opens it in the slideshow.
  - `←` / `→` (or a swipe on a touchscreen) go to the previous or next
    day.
- **Slideshow:** click any thumbnail to open a full-screen viewer.
  - `←` / `→`: previous / next photo. Clicking the left or right edge of
    the screen, or swiping, also works.
  - Scroll or pinch to zoom, drag to pan once zoomed, double-click to
    toggle zoom. `+` / `-` / `0` work from the keyboard. The edge-click
    zones step aside while zoomed, so they don't fight with panning.
  - `I`: toggle a details panel with camera, lens, aperture, shutter
    speed, ISO, focal length, resolution, file size, and a small map of
    where the file was captured. Videos show duration, codec and frame
    rate instead of camera settings. The panel also has a "Download
    original file" link.
  - `F`: toggle true full-screen, with no bar, caption or on-screen
    controls. Everything else still works.
  - `Esc`: close the details panel if it's open, otherwise close the
    viewer.
  - `L` (or the Live button): play a Live Photo's motion clip. The
    button only appears on Live Photos, which are also marked LIVE in
    the grid. See "Live Photos" below.
  - Neighbouring photos preload in the background, so navigation feels
    instant. Videos play inline with normal video controls.
- **Responsive layout:** the maps and thumbnail grid scale with the
  screen, from a big monitor down to a phone.
- **Thumbnail size:** each day page has a Small / Medium / Large toggle.
  Your choice is remembered by the browser and carries over between days.

## How your photos are handled

**Your originals are never modified.** Neither script ever changes,
moves or renames your photos and videos. The only file either script
may create in your photos folder is `trip_content.json`, and only if you
run the script from inside that folder.

**Full-screen viewing uses the real files.** For jpg, png, webp and bmp,
the slideshow shows your actual file, with no resizing or recompression.

- **Static site:** a byte-for-byte copy is placed in the output's
  `media/` folder.
- **Live server:** the file is streamed straight off disk.

Videos work the same way. This is meant for viewing on your own computer
or over a home network. On the open internet, visitors on slow
connections would be downloading 20 MB photos just to see them shrunk on
a phone.

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

- A photo with the same name sits in the same folder.
- The video carries Apple's Live Photo marker, or is at most 4 seconds
  long.

So a real video that just happens to share a photo's name is left alone.
The clips are HEVC, which Safari plays everywhere. Other browsers only
play them where the system has an HEVC decoder; where it doesn't,
pressing Live just stays on the photo. Use `--no-live-photos` to list
the clips as ordinary videos instead.

**Thumbnails** are small separate images (`--thumb-size`), since loading
full-resolution files just to show a preview would be slow.

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
    `pillow-heif`, which is in `requirements.txt`; without it, HEIC files
    are skipped with a note.
  - **Videos:** mp4, mov, m4v, avi, mkv, 3gp.
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
  include everything.
- **Day grouping.** Photos are placed by their EXIF capture date
  (`DateTimeOriginal`, falling back to the other EXIF date fields), and
  videos by their `creation_time`.
  - Files with no capture date at all are **not** placed by their file
    modified date. A copy, sync or export can reset that date, so trusting
    it could invent a day that never happened or misfile a photo.
  - These files go on a single "Unsorted" page at the end instead. It has
    the same grid and map, and it doesn't count toward the "days" total.
  - Both scripts tell you how many files this happened for.
- **Video timestamps.**
  - iPhone videos also record the local time where they were shot, and
    that's used as-is, just like a photo's EXIF time. No timezone
    setting is needed for them.
  - Other videos only store `creation_time`, in UTC. By default it's
    converted using the timezone of the computer running the script,
    which is only right if that matches the trip's timezone.
  - If a day's videos land on the wrong side of midnight, pass
    `--tz-offset` with the trip's UTC offset. This then applies to
    every video, iPhone ones included.
  - Photos are unaffected, since camera EXIF times are already local.
- **GPS.** For photos, the location comes from EXIF GPS data. For videos,
  it comes from the `location` / ISO-6709 tag (what iPhones write), when
  present. Coordinates that can't be real (out of range, or exactly
  0,0) are ignored. Files without GPS still appear in the grid, just not
  on the map.
- **Maps** use Leaflet with free OpenStreetMap tiles: no API key, no
  account. The dark look is a CSS filter over the standard tiles.
  - The maps and fonts load from the internet, so they need a connection.
    Everything else works offline.
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
    have any photos, are deleted. Only files the build record lists are
    ever deleted, and only inside the output folder.
  - The scan still reads every file's metadata on each run, which takes
    a moment on large trips.
  - Deleting the build record is safe: the next run just regenerates
    thumbnails and converted images, and re-checks the copies.
- **Live server: new photos need a restart.** The folder is scanned once
  at startup. Your `trip_content.json` is untouched by a restart; new
  days just get a blank entry added.
- **Live server: no authentication.** It's meant for your own computer or
  home network. Don't port-forward it to the internet.
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

## Running the tests

The `tests/` folder has a test suite covering both scripts. It builds
small photos and videos on the fly, runs the generator, and starts the
server on a free port. It needs nothing beyond `requirements.txt`. From
the project folder:

```bash
python -m unittest discover -s tests -t .
```

Video tests are skipped automatically if ffmpeg isn't installed. pytest
runs the same tests too, if you prefer it (`pytest tests`).

## Customizing the look

The whole design lives in `trip_common.py` and is shared by both
scripts, so a change there shows up in the static site and the live
server alike:

- **The `CSS` string.** Colours are CSS variables at the top (`--navy`,
  `--gold`, `--teal`, etc.), so they're easy to swap.
- **The `DAY_COLORS` list.** This is the palette used for each day on the
  overview map.
- **The templates.** The page layout is plain Jinja2 templates
  (`INDEX_TEMPLATE`, `DAY_TEMPLATE`) in the same file.
