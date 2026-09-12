# Trip Site Generator

Turns a folder of trip photos/videos into a static website: one chapter per
day, thumbnails, a route map per day, an overview map + title on the
homepage, and a full-screen slideshow you drive with the keyboard.

It reads EXIF (for photos) and container metadata (for video) to figure out
*when* and *where* each file was captured — you don't type anything in by
hand except the descriptions, if you want them.

## 1. Install

You need Python 3.9+, plus:

```bash
pip install -r requirements.txt
```

You also need **ffmpeg** installed and on your PATH (used to grab a video
thumbnail frame and read video metadata):

- macOS: `brew install ffmpeg`
- Ubuntu/Debian: `sudo apt install ffmpeg`
- Windows: [ffmpeg.org/download](https://ffmpeg.org/download.html), or `winget install ffmpeg`

## 2. Run it

```bash
python generate_site.py -i /path/to/your/photos -o /path/to/site_output
```

- `-i / --input` — folder with your photos & videos (subfolders are scanned too)
- `-o / --output` — where the generated website is written (default: `./site`)
- `-c / --content` — where titles/descriptions are stored (default: `./trip_content.json`)
- `--thumb-size` — max thumbnail dimension in px (default 400). This is the
  only resizing the script ever does — see "Your originals, untouched"
  below for the full-screen view.
- `--force` — regenerate thumbnails/full-size images even if they already exist
- `--tz-offset` — fixed UTC offset in hours (e.g. `2` or `-5.5`) for converting
  **video** timestamps to your trip's local time. Video files store an
  absolute UTC creation time; without this flag it's converted using
  whatever timezone the computer running the script happens to be in, which
  is only correct if that's also the trip's timezone. Photos are unaffected
  — camera EXIF timestamps are already local, with no conversion involved.
- `--workers` — how many files to process in parallel (default: one per CPU
  core). Lower it if you want to keep using the computer for other things
  while it runs.
- `--no-dedupe` — by default, exact-duplicate files (same content found
  twice, e.g. the same photo imported from two devices) are detected and
  skipped automatically. Pass this to disable that and include everything.

Open `site_output/index.html` in a browser. That's it — no server needed to
view it locally (the map tiles and fonts do need an internet connection,
since they load from OpenStreetMap and Google Fonts).

## 3. Add descriptions whenever you like

The first run creates `trip_content.json` next to wherever you run the
script, pre-filled with an entry for every day it found:

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

An `"unknown"` entry shows up here too if any files had no real capture-date
metadata — that's the "Unsorted" page (see "Day grouping" below), and you
can give it a title/description exactly like any other day.

Edit `title`/`subtitle` for the whole trip, and `title`/`description` per
day. Blank lines in a description start a new paragraph. Then just re-run
the same command — **the script only ever adds new day entries to this
file, it never overwrites text you've already written**, so it's safe to
add more photos later and regenerate.

## What you get

- **Homepage**: trip title, stats (days / photos / videos / km tracked),
  an overview map with the full route (one colour per day, click a line or
  a day card to jump to that day), and a card per day.
- **Day pages**: a description block, a map of just that day's photo/video
  locations (points only — no connecting line, since photos aren't taken in
  a tidy walking order and a line through them gets messy fast), a
  thumbnail grid, and prev/next day links. Clicking a point on the map
  shows a preview of that photo; clicking the preview opens it straight in
  the full-screen slideshow.
- **Slideshow**: click any thumbnail to open a full-screen viewer.
  - `→` / `←` next / previous
  - Scroll/pinch to zoom in on a photo, drag to pan around once zoomed,
    double-click to toggle zoom, `+`/`-`/`0` also work from the keyboard
  - `I` toggle a details panel: camera, lens, aperture, shutter speed, ISO,
    focal length, resolution, file size, and a small map of exactly where
    that file was captured (video files show duration/codec/frame rate
    instead of camera settings)
  - `Esc` close the details panel if it's open, otherwise close the viewer
  - `F` toggle true full-screen (browser Fullscreen API) — in true
    full-screen the photo fills the entire screen with no bar, caption, or
    on-screen controls at all; everything above still works the same, it's
    just not visibly cluttering the screen
  - Click the left/right edge of the screen also works, for mouse/touch
    (this steps aside automatically while zoomed in, so it doesn't fight
    with dragging to pan)
  - Swipe left/right on a touchscreen also navigates
  - Neighboring photos preload in the background so navigation feels instant
  - Videos play inline with normal video controls
- **Responsive layout**: the map and thumbnail grid scale with the
  browser/screen width — more (and slightly larger) thumbnails per row on
  a big monitor, everything reflows sensibly down to a phone.
- **Thumbnail size**: each day page has a Small/Medium/Large toggle above
  the thumbnail grid. Your choice is remembered (via the browser's local
  storage) and carries over as you move between day pages.

## Notes & limitations

- **Your originals, untouched.** The full-screen viewer uses your actual
  photo and video files directly — for jpg, png, webp, and bmp, that's a
  byte-for-byte copy, no resizing or recompression at all. This is meant
  for viewing the site from disk or over a LAN, not for hosting it on the
  open internet to strangers on slow connections, where you'd normally want
  a few resized versions so people aren't downloading a 20MB photo just to
  see it shrunk on a phone. If you ever do want to publish it publicly,
  say so and that can be added back in as an option.
  HEIC/HEIF and TIFF get the same treatment — the original file is copied
  completely untouched, nothing is thrown away — but since Chrome, Firefox,
  and Edge can't decode those formats at all (Safari can, for HEIC), a
  JPEG copy is generated alongside it purely so the photo is actually
  viewable everywhere else. The site automatically offers the real
  original to any browser that can use it and only serves the JPEG to the
  ones that can't, so nobody has to think about which one they're getting.
  The JPEG carries the same EXIF data as the original too — camera, lens,
  GPS, timestamp, everything. The tradeoff is disk space: a HEIC/TIFF photo
  now exists on disk twice (original + JPEG copy) instead of once.
  Thumbnails in the grid are still a small separate derived image, since
  loading full-resolution originals just to show a postage-stamp preview
  would be slow even from local disk.
- **Filenames are kept as-is.** `IMG_1234.HEIC` produces output files named
  `IMG_1234.*` — same name, same case, spaces and parentheses and all.
  The only time a file gets renamed is a genuine collision: if two
  different source files (anywhere in the folder, including subfolders)
  would otherwise produce the same output name — two phones that both
  started their own `IMG_0001.jpg` numbering, for instance — the second one
  gets a plain `_2`, `_3`, etc. appended, nothing more elaborate than that.
- **Formats**: photos — jpg, png, heic/heif, tiff, bmp, webp. Videos — mp4,
  mov, m4v, avi, mkv, 3gp. HEIC needs `pillow-heif` (in requirements.txt).
- **Day grouping**: uses the photo's `DateTimeOriginal` EXIF tag, or a
  video's `creation_time`. Files with neither — no real capture-date
  metadata at all — are **not** placed on a day based on their file
  modified/created date, since that often has nothing to do with when they
  were actually taken (a copy, a sync, an export can all reset it, and
  trusting it could silently invent a day that never happened on the trip,
  or misfile a photo onto the wrong one). They're collected instead into a
  single "Unsorted" page at the end of the trip — same thumbnail grid, same
  map if any of them happen to have GPS, just without a date attached, and
  it doesn't count toward the "days" total on the homepage. The generator
  tells you how many files this happened for.
- **Video timestamps**: read from the container's `creation_time`, which is
  UTC. By default that's converted using your computer's local timezone at
  generation time — not necessarily the trip's. If a day's videos land on
  the wrong side of midnight, pass `--tz-offset` with the trip's UTC offset
  to fix it (see above).
- **GPS**: pulled from EXIF `GPSInfo` for photos and, when present, the
  `location`/ISO-6709 tag for videos (this is what iPhones write). Not
  every camera/app writes GPS — files without it still show up in the
  thumbnail grid, just not on the map.
- **Maps**: uses Leaflet + free OpenStreetMap tiles — no API key, no account,
  no signup, ever. The dark "night map" look is faked with a CSS filter on
  top of the standard OSM tiles rather than a paid/gated dark tile style, so
  it keeps working regardless of what any tile provider does with their
  pricing. This does mean the *generated site* needs an internet connection
  to show the maps (your photos/videos themselves are all local files, so
  everything else works offline). If you expect heavy traffic (a public
  site with lots of visitors), OSM's tile usage policy asks you to consider
  a dedicated provider (e.g. MapTiler, Stadia Maps, Mapbox) instead of their
  free shared tiles — for a personal trip site shared with family/friends,
  the default is fine.
- **Large trips**: re-running without `--force` skips images/videos whose
  thumbnail + display copies already exist, so adding a few more photos to
  a big trip and regenerating is fast. This also means if you change
  `--thumb-size` after already generating a site, previously-processed
  files won't be resized to match until you add `--force` — new photos you
  add later will use the new setting automatically, but existing ones need
  that one forced regeneration to catch up.
- **Upgrading from an older version of this script**: earlier versions
  generated multiple resized copies per photo (filenames ending in
  `-1280.jpg`, `-1920.jpg`, etc.). Those are no longer used and won't be
  cleaned up automatically — if you're regenerating a site that already
  exists, it's worth deleting the output folder first so those old files
  don't just sit there unused.

## Customizing the look

All the design lives in two spots inside `generate_site.py`: the `CSS`
string (colors are CSS variables at the top — `--navy`, `--gold`, `--teal`,
etc. — easy to swap) and the `DAY_COLORS` list (the palette used for each
day's route on the overview map). Everything else is plain Jinja2 templates
in the same file if you want to change the layout.
