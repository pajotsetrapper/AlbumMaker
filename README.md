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
| After adding photos | Re-run the script | Restart the server |
| After editing descriptions | Re-run the script | Save the file and refresh the page |
| Cost | A build step | A little CPU on first view of each day |

Both use the same `requirements.txt` and the same `trip_content.json`
format, so you can switch between them, or use both, at any time.

## 1. Install

You need Python 3.9+, plus:

```bash
pip install -r requirements.txt
```

You also need **ffmpeg** installed and on your PATH. It's used to read
video metadata and grab a thumbnail frame from each video:

- macOS: `brew install ffmpeg`
- Ubuntu/Debian: `sudo apt install ffmpeg`
- Windows: [ffmpeg.org/download](https://ffmpeg.org/download.html), or `winget install ffmpeg`

Without ffmpeg, videos still appear, but with no date (so they land
on the "Unsorted" page) and no location or thumbnail. The static site
shows a broken-image icon in place of their thumbnails.

## 2a. Build a static site: `generate_site.py`

```bash
python generate_site.py -i /path/to/your/photos -o /path/to/site_output
```

Then open `site_output/index.html` in a browser. No server is needed.

Options:

- `-i / --input`: folder with your photos and videos. Subfolders are
  scanned too. Required.
- `-o / --output`: where the website is written (default: `./site`).
  **Keep this outside your photos folder.** Otherwise the next run scans
  the generated thumbnails as if they were photos.
- `-c / --content`: where titles and descriptions are stored (default:
  `./trip_content.json`). See section 3.
- `--thumb-size`: maximum thumbnail dimension in px (default 400). For
  video thumbnails this sets the width, so portrait videos get taller
  thumbnails.
- `--force`: regenerate all thumbnails and media copies, even ones that
  already exist. See "Re-running the static build" below for when you
  need this.
- `--tz-offset`: fixed UTC offset in hours (e.g. `2` or `-5.5`) for video
  timestamps. See "Video timestamps" below.
- `--workers`: how many files to process in parallel (default: one per
  CPU core). Lower it if you want to keep using the computer while it
  runs.
- `--no-dedupe`: include exact-duplicate files instead of skipping them.
  See "Duplicates" below.

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
    rate instead of camera settings. On the live server, this panel also
    has a "Download original file" link.
  - `F`: toggle true full-screen, with no bar, caption or on-screen
    controls. Everything else still works.
  - `Esc`: close the details panel if it's open, otherwise close the
    viewer.
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
  untouched copy of the original. That original copy is currently not
  linked from the site, so these photos take up space twice in the
  output folder.
- **Live server:** the JPEG is built in memory on request. The real
  original is available through the "Download original file" link.

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
  file in alphabetical path order keeps the plain name; the others get
  `_2`, `_3` and so on.
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
- **Duplicates.** Files with identical content (the same photo imported
  from two devices, a folder synced twice) are detected and only the
  first is kept. Detection compares file size plus the first and last
  64 KB of each file. Re-exported or re-compressed versions of a photo
  are not detected. Use `--no-dedupe` to include everything.
- **Day grouping.** Photos are placed by their EXIF capture date
  (`DateTimeOriginal`, falling back to the other EXIF date fields), and
  videos by their `creation_time`.
  - Files with no capture date at all are **not** placed by their file
    modified date. A copy, sync or export can reset that date, so trusting
    it could invent a day that never happened or misfile a photo.
  - These files go on a single "Unsorted" page at the end instead. It has
    the same grid and map, and it doesn't count toward the "days" total.
  - Both scripts tell you how many files this happened for.
- **Video timestamps.** A video's `creation_time` is stored in UTC.
  - By default it's converted using the timezone of the computer running
    the script, which is only right if that matches the trip's timezone.
  - If a day's videos land on the wrong side of midnight, pass
    `--tz-offset` with the trip's UTC offset.
  - Photos are unaffected, since camera EXIF times are already local.
- **GPS.** For photos, the location comes from EXIF GPS data. For videos,
  it comes from the `location` / ISO-6709 tag (what iPhones write), when
  present. Files without GPS still appear in the grid, just not on the
  map.
- **Maps** use Leaflet with free OpenStreetMap tiles: no API key, no
  account. The dark look is a CSS filter over the standard tiles.
  - The maps and fonts load from the internet, so they need a connection.
    Everything else works offline.
  - For a heavily visited public site, OpenStreetMap's tile usage policy
    asks you to use a dedicated provider (e.g. MapTiler, Stadia Maps,
    Mapbox). For a personal site shared with family and friends, the
    default is fine.
- **Re-running the static build.** Without `--force`, files whose
  thumbnail and media copy already exist are skipped, so adding a few
  photos to a big trip and regenerating is fast. The flip side:
  - Changing `--thumb-size` only affects newly processed files until you
    run once with `--force`.
  - The same goes for a photo you edited after it was first processed.
  - Photos you remove from the input folder leave their old copies
    behind in the output folder. Day pages for days that no longer have
    any photos stay behind too. Delete the output folder for a clean
    rebuild.
- **Live server: new photos need a restart.** The folder is scanned once
  at startup. Your `trip_content.json` is untouched by a restart; new
  days just get a blank entry added.
- **Live server: no authentication.** It's meant for your own computer or
  home network. Don't port-forward it to the internet.
- **Live server: first view of a day is slower.** Thumbnails are built
  the first time a day is opened, which takes a moment for days with
  many photos. Revisiting is fast, thanks to the in-memory cache.
- **Upgrading from an older version.** Earlier versions of
  `generate_site.py` made several resized copies per photo (filenames
  ending in `-1280.jpg`, `-1920.jpg`, etc.). These are no longer used
  and aren't cleaned up automatically. Delete the output folder before
  regenerating so they don't linger.

## Customizing the look

The design lives in two places in each script:

- **The `CSS` string.** Colours are CSS variables at the top (`--navy`,
  `--gold`, `--teal`, etc.), so they're easy to swap.
- **The `DAY_COLORS` list.** This is the palette used for each day on the
  overview map.

The page layout is plain Jinja2 templates in the same files. Both scripts
currently contain their own copy of the CSS, JavaScript and templates.
If you change the look and want both to match, make the same change in
both files.
