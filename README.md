# TripServer
Hosts a trip website, rendering all content on demand

# Trip Server — the same trip site, served live

`trip_server.py` is a sister script to `generate_site.py`. Same look, same
day-by-day layout, same maps, same full-screen slideshow — but instead of
building a folder of HTML/thumbnail files, it runs a small local web server
that generates every page and every image **on the fly**, per request, and
writes nothing to disk except the one file you'd want it to keep:
`trip_content.json`, your titles and descriptions.

Pick whichever fits how you want to view your trip:

- **`generate_site.py`** — builds a folder of static files you can open
  without Python running, copy to a USB drive, or put anywhere. Costs disk
  space (thumbnails + copies of your media) and a build step whenever your
  photos change.
- **`trip_server.py`** (this one) — nothing is written to disk at all. Run
  it whenever you want to browse; stop it and there's no trace. Costs a bit
  of CPU per page load instead (generating thumbnails on the spot) and
  needs Python running the whole time you're viewing it.

Both scripts share the same `requirements.txt` — if you already set up
`generate_site.py`, you don't need to install anything new for this one.

## Run it

```bash
python trip_server.py -i /path/to/your/photos
```

Then open the URL it prints — by default **http://127.0.0.1:8000/**.

That's it. No output folder, no `--force`, no regenerating anything when
you add more photos — just stop the server (Ctrl+C) and start it again.

## Options

- `-i / --input` — folder with your photos & videos (default: current
  folder, so you can also just drop the script *in* your photos folder and
  run it with no arguments)
- `-c / --content` — where titles/descriptions are stored (default:
  `./trip_content.json`) — see below
- `--port` — default `8000`
- `--lan` — let other devices on your network open the site too (binds to
  all network interfaces instead of just your own computer). **There's no
  login of any kind** — anyone on your Wi-Fi/network can browse your
  photos while the server is running with this on. Leave it off unless you
  want that.
- `--thumb-size` — max thumbnail dimension in px (default 400)
- `--tz-offset` — fixed UTC offset in hours (e.g. `2` or `-5.5`) for
  correcting **video** timestamps to your trip's local time — same
  reasoning as in `generate_site.py`'s README
- `--no-dedupe` — include exact-duplicate files instead of skipping them

## Descriptions, and the one file it does write

Exactly like `generate_site.py`: the first run creates `trip_content.json`
with a blank entry for every day it found, and only ever *adds* new entries
to it — your text is never overwritten. The nice part of the live-server
version: **just save the file and refresh the page.** No re-running
anything; the server checks whether the file changed and picks up edits on
the next request.

## What's different from generate_site.py, under the hood

Everything you see is identical — I reused the same design, CSS, and
JavaScript — but a few things work differently because nothing is
pre-built:

- **Your original files are used directly**, streamed straight off disk on
  request — not copied anywhere, not renamed. Full-size photo and video
  requests support HTTP range requests, so video seeking works properly in
  the browser.
- **HEIC/HEIF photos** work the same clever way: Safari gets the real
  original file streamed directly (via `<picture>`, the same
  progressive-enhancement trick as the static version), and a JPEG version
  is generated *in memory* — never written to disk — for browsers that
  can't decode HEIC (Chrome, Firefox, Edge).
- **Thumbnails are generated per-request** the first time they're needed,
  then kept briefly in an in-memory cache (not a file — it's gone the
  moment you stop the server) so scrolling back to a day you've already
  visited doesn't redo the work.
- **A bonus the live version can do that the static one can't:** every
  photo/video has a "Download original file" link in its details panel
  (press `I` on a full-screen photo), since there's no downside to
  offering the raw file on request when nothing has to be pre-copied for it.
- One small architecture simplification: since URLs use a plain internal ID
  (`/thumb/42`, `/media/42`) rather than filenames, there's no need to
  rename files to avoid collisions the way the static version sometimes
  does — that whole concern doesn't exist here.

## Limitations worth knowing

- **New photos need a restart.** The folder is scanned once when the
  server starts. Add more photos, restart the server (`trip_content.json`
  and your descriptions are untouched — only new days there get a blank
  skeleton entry added).
- **No authentication.** This is meant for your own computer or your own
  home network, not the open internet. Don't port-forward this to the
  public internet.
- **Slower first view than a static site.** Since images are generated on
  request instead of pre-built, the very first time you open a day with a
  lot of photos will take a bit of CPU work up front (still fast — well
  under a second per thumbnail — but noticeable compared to a static
  file). Revisiting the same day is fast, thanks to the in-memory cache.
- Everything else — formats supported, EXIF/GPS extraction, day grouping,
  the "Unsorted" bucket for undated files, zoom/pan, fullscreen — works
  exactly like `generate_site.py`. See that README for the details; they
  all apply here too.
