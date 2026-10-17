# Trip Site

Turns a folder of trip photos and videos into a website. It has one
chapter per day, a route map per day, an overview map and title on the
homepage, and a full-screen slideshow you can drive with the keyboard.

It reads EXIF (for photos) and container metadata (for videos) to work
out *when* and *where* each file was captured. You don't type anything
in by hand except the titles and descriptions, if you want them. With the
live server you can also add captions, mark favorites, hide photos and
pick each day's cover photo right in the page.

## Quick start

```bash
pip install -r requirements.txt

# Live and editable (open http://127.0.0.1:8000/):
python trip_server.py -i /path/to/your/photos

# Or build a standalone folder you can copy anywhere:
python generate_site.py -i /path/to/your/photos -o site

# Optional, once, while online: so the pages work without internet
# (see "Working without internet"):
python trip_server.py --fetch-offline-assets
```

You also need ffmpeg (see "Install"). Everything below explains the
details.

There are two scripts. Both produce the same site with the same look and
features; they differ in how it's delivered, and in whether you can edit
in the page:

| | `generate_site.py` | `trip_server.py` |
|---|---|---|
| What it does | Builds a folder of static HTML, thumbnails and media copies | Runs a small local web server that builds every page and image on request |
| Python needed while viewing? | No. Open `index.html`, copy the folder to a USB drive, etc. | Yes, the whole time |
| Disk space | Thumbnails plus a copy of every photo and video | Nothing, except `trip_content.json` (and the map tiles, if you turn on the tile cache) |
| Without internet | Everything works except the map tiles; the map library and fonts can be bundled into the site | The same, and map tiles you have already viewed can be kept in a local cache |
| After adding or changing photos | Re-run the script (only new or changed files are processed) | Restart the server |
| Editing titles, descriptions, captions, favorites, hidden photos, covers | Edit `trip_content.json`, then re-run the script | In the page itself (from the computer running the server), or edit the file and refresh |
| Cost | A build step | A little CPU on first view of each day |

Both use the same `requirements.txt` and the same `trip_content.json`
format, so you can switch between them, or use both, at any time. A
typical workflow is to curate in the live server, then run
`generate_site.py` to produce the version you share.

## 1. Install

You need Python 3.9+, plus:

```bash
pip install -r requirements.txt
```

Keep the three Python files together in one folder: `generate_site.py`,
`trip_server.py`, and `trip_common.py`. The last one holds the code both
scripts share, and neither runs without it. Optionally, a `vendor` folder
next to them holds local copies of the map library and fonts; see
"Working without internet".

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
- `-c / --content`: where titles, descriptions and photo notes are
  stored (default: `./trip_content.json`). See section 3.
- `--thumb-size`: maximum thumbnail dimension in px (default 400,
  allowed 16–4096).
- `--force`: regenerate all thumbnails and media copies, even for files
  that haven't changed. Normally not needed; see "Re-running the static
  build" below.
- `--tz-offset`: fixed UTC offset in hours (e.g. `2` or `-5.5`) for video
  timestamps. See "Video timestamps" below.
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
- `--fetch-offline-assets`: download the map library and fonts into a
  `vendor` folder (needs internet, once), then exit. See "Working
  without internet".

Photos you've marked **hidden** in `trip_content.json` are left out of
the build entirely, including their files. See "Hidden photos" below.

If a `vendor` folder is present, it is copied into the site
(`assets/vendor`), so the site doesn't need the internet for its map
library and fonts.

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
- `-c / --content`: where titles, descriptions and photo notes are
  stored (default: `./trip_content.json`). See section 3.
- `--port`: default `8000`.
- `--lan`: let other devices on your network open the site too. **There
  is no login of any kind**: anyone on your Wi-Fi or network can browse
  your photos while the server runs with this on. Leave it off unless you
  want that. Other devices can look but never edit; see section 4.
- `--read-only`: turn off in-page editing completely, even on the
  computer running the server. See section 4.
- `--allow-host NAME`: an extra host name the server should answer to
  (repeatable). The server only responds to requests addressed to an IP
  address, `localhost`, or this computer's own name (also with
  `.local`); anything else gets a 403. That blocks "DNS rebinding", a
  trick where a web page you visit tries to read your local server
  through its own domain name. You only need this if you reach the
  computer through a custom local DNS name.
- `--thumb-size`, `--tz-offset`, `--date-range`, `--no-date-check`,
  `--no-dedupe`, `--no-live-photos`, `--fetch-offline-assets`: same as
  for `generate_site.py`.
- `--tile-cache DIR`: keep the map tiles you look at in this folder and
  serve them from there, so maps you've already viewed still work
  without internet. See "Working without internet".
- `--tile-url TEMPLATE`: where map tiles come from, with `{z}`, `{x}` and
  `{y}` in it (default: OpenStreetMap). For a tile provider you have an
  account with.

Apart from `trip_content.json` (and the tile folder, if you use
`--tile-cache`), the server writes nothing to disk, and it writes to the
JSON file only when you save an edit in the page.
Thumbnails and converted images are built in memory and kept in a small
in-memory cache, so revisiting a day is fast. The cache is gone when the
server stops.

## 3. Titles, descriptions and photo notes: `trip_content.json`

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
- **Hide** / **Unhide**: see below.

**On the day page**, **Edit title & description** opens a small form.
Saving reloads the page with your text in place. **Preview as visitor**
reloads the page exactly as someone else would see it, with no editing
controls and hidden photos gone. (It applies to that one page; navigating
elsewhere puts you back in the editor.)

Who can edit:

- Only the computer running the server, and that holds whether or not
  `--lan` is on. With `--lan`, you can still edit from the host computer
  while other devices on your network can look but never change
  anything. Using the machine's own network address from the machine
  itself still counts as local.
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
  publish. A Live Photo's motion clip goes with its photo.

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
python trip_server.py --fetch-offline-assets
```

(`generate_site.py --fetch-offline-assets` does the same thing.) It saves
Leaflet and the three fonts, about 0.5 MB in 28 files, into a `vendor`
folder next to the scripts, and from then on both scripts use those local
copies automatically. Details:

- The files come from the npm registry, in exact pinned versions, and
  each download is checked against its published SHA-512 checksum. One
  that doesn't match is refused.
- If anything fails (no connection, a bad checksum, a full disk), it says
  so and your existing `vendor` folder is left as it was. A half-finished
  download is never used.
- The live server picks the folder up when it starts; it prints whether
  it found it. The static build copies it into the site
  (`assets/vendor`), so the site keeps working when moved to another
  computer or a USB drive.
- Licenses: Leaflet is BSD-2-Clause and the fonts (Fraunces, IBM Plex
  Sans, IBM Plex Mono) are under the SIL Open Font License. The licenses
  are saved in `vendor/licenses`, and the static build copies them along.
- To go back to loading everything from the internet, delete the
  `vendor` folder. To refresh the files, run the command again.

### Step 2: keep the map tiles you've looked at (live server)

```bash
python trip_server.py -i /path/to/your/photos --tile-cache tile_cache
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
- The server introduces itself to the provider as "TripSite". OpenStreetMap's
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

## What you get

- **Homepage:** the trip title, stats (days / photos / videos / km
  tracked, where km is the straight-line distance between each day's
  consecutive geotagged photos, Unsorted included), an overview map with
  every geotagged photo as a point coloured by day, and a card per day.
  Click a point or a card to jump to that day.
- **Day pages:** the description, a map of that day's photo and video
  locations, a thumbnail grid, and previous/next day links.
  - The map shows points only, with no connecting line. Photos aren't
    taken in a tidy walking order, so a line through them gets messy
    fast.
  - Clicking a point shows a preview of that photo; clicking the preview
    opens it in the slideshow.
  - `←` / `→` (or a swipe on a touchscreen) go to the previous or next
    day.
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

### Keyboard shortcuts

| Key | Where | Does |
|---|---|---|
| `←` `→` | day page | previous / next day |
| `←` `→` | slideshow | previous / next photo |
| `Space` | slideshow | play / pause autoplay |
| `+` `-` `0` | slideshow | zoom in / out / reset |
| `I` | slideshow | details panel |
| `F` | slideshow | true full-screen |
| `L` | slideshow | play a Live Photo's clip |
| `S` | slideshow, editing | favorite / unfavorite |
| `Esc` | slideshow | close details, then close the viewer |

Shortcuts are switched off while you're typing in a caption or title box.

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
  uses a number that can shift when files are added, so treat those links
  as being for yourself.
- A link to a photo that no longer exists is ignored.
- Links behave best when the site is served over http(s). When a page is
  opened straight from a folder (`file://`), some browsers don't allow
  the address to change; the viewer still works, it just doesn't update
  the address.

## How your photos are handled

**Your originals are never modified.** Neither script ever changes,
moves or renames your photos and videos. The only file either script
may create in your photos folder is `trip_content.json`, and only if you
run the script from inside that folder. The live server later updates it
when you save an edit in the page.

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
  - The scan still reads every file's metadata on each run, which takes
    a moment on large trips.
  - Deleting the build record is safe: the next run just regenerates
    thumbnails and converted images, and re-checks the copies.
- **Editing is for the live server.** The static site only displays what
  `trip_content.json` says. Edits you make in the live server don't
  appear in a static site until you re-run `generate_site.py`.
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

## Troubleshooting

- **Photos ended up on "Unsorted".** They have no capture date, or one
  that looked wrong. Open a photo's details panel: a "Date" row says
  which embedded date was ignored and why. If the dates are right, pass
  `--date-range START END`. If a video has no date, check that ffmpeg is
  installed.
- **I can't see the editing buttons.** They only appear when you open the
  server from the computer running it, and not with `--read-only`. If
  you're looking at a page that has "Preview as visitor" applied, open
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
- **The map is blank or grey.** By default the map library and tiles
  load from the internet, so check your connection. To be independent of
  it, see "Working without internet". With the local `vendor` folder in
  place you'll see a rough outline and a "tiles unavailable" notice
  instead of a blank box.
- **`--fetch-offline-assets` says it couldn't download.** It needs a
  connection to the npm registry (registry.npmjs.org); check your
  internet, proxy or firewall and run it again. Nothing was changed.
- **The fonts look different without internet.** The fonts load from the
  internet unless the `vendor` folder is present; run
  `--fetch-offline-assets` once. Until then the browser uses a fallback.
- **I use `--tile-cache` but tiles don't appear.** The server has to be
  able to reach the tile provider the first time it needs a tile. Watch
  the terminal for a message, and make sure the cache folder is writable.
- **A video shows a preview but won't play.** The browser can't decode
  that format. Use "Download original file" from the details panel, or
  play it with a desktop player.

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
- **The continent outline.** The offline fallback map is generated data
  (Natural Earth, public domain) at the very end of `trip_common.py`; the
  colours it's drawn in are in the `addBaseMap` function in the `JS`
  string.
- **The `JS` string.** The slideshow, maps, autoplay and editing code.
  Like `CSS`, it's a raw string, so backslashes in it are passed to the
  browser unchanged.
