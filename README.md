# 🗺️ Static & Dynamic Trip Site Generator

A lightweight, high-performance Python toolkit to turn folders of trip photos and videos into fully interactive, browsable web galleries with route maps, responsive lightboxes, and EXIF metadata views.

Whether you want to build a completely self-contained static web bundle to host anywhere or dynamically serve a photos directory on the fly with **zero disk footprint**, this repository provides a full end-to-end solution.

---

## ✨ Key Features

- 📸 **Universal Format Support**: Automatically processes standard web formats (`JPG`, `PNG`, `WebP`, `BMP`), raw images (`TIFF`), videos (`MP4`, `MOV`, `MKV`, `AVI`, `3GP`), and Apple High-Efficiency formats (`HEIC`/`HEIF`).
- 🗺️ **Interactive Route Mapping**: Extracts embedded GPS coordinates via EXIF and `ffprobe` to render interactive OpenStreetMap route trails (via Leaflet.js) on both macro (trip summary) and micro (daily timeline) levels.
- 🎞️ **Full-Screen Keyboard Lightbox**: 
  - Rich slideshow experience supporting image panning, mouse/touch pinch-to-zoom, and autoplay video playback.
  - Interactive EXIF / metadata drawer showing camera gear, aperture, shutter speed, ISO, focal length, altitude, video codecs, and exact capture coordinates.
- 👯 **Smart De-duplication**: Uses multi-point byte hashing (`fast_fingerprint`) to spot exact file duplicates—even if renamed—without wasting time or resources hashing multi-gigabyte video files line-by-line.
- 🚨 **Multi-Stream & MPO Repair**: Detects dual-stream photos (such as iPhone portrait mode, 3D/MPO, or panorama files) and automatically builds web-native fallbacks so images never render half-loaded in modern browsers.
- ⚡ **Multi-Core Processing**: Scales thumbnail and derivative image processing across CPU cores using concurrent worker pools.
- 📝 **Additive Content Editing**: Custom day titles and trip descriptions live in an editable JSON file (`trip_content.json`). Re-running the generator or refreshing the server will **never overwrite** your custom text.

---

## 🛠️ Architecture & Workflow Options

This suite gives you two distinct workflows for presenting your media collections:

| Feature / Goal | `generate_site.py` (Static Builder) | `trip_server.py` (On-the-Fly Server) |
| :--- | :--- | :--- |
| **Output Type** | Generates flat static files (`HTML`, `CSS`, `JS`, converted media). | Fully in-memory rendering HTTP server. |
| **Disk Footprint** | Writes thumbnails and display JPEGs to disk. | **0 bytes written to disk**. |
| **Deployment** | Perfect for GitHub Pages, Netlify, S3, or offline USB drives. | Perfect for quick local browsing, NAS streaming, or instant checks. |
| **Dependencies** | Standard Python libraries + `Pillow` (+ optional `pillow-heif`). | Standard Python libraries + `Pillow` (+ optional `pillow-heif`). |

---

## 🚀 Installation & Prerequisites

### 1. System Dependencies (Required for Videos & Audio)
Video timestamp extraction, frame grabbing, and duration indexing rely on `ffmpeg` and `ffprobe`.

- **macOS** (via Homebrew):
  `brew install ffmpeg`
- **Ubuntu/Debian**:
  `sudo apt update && sudo apt install -y ffmpeg`
- **Windows** (via Winget or Chocolatey):
  `winget install FFmpeg`

### 2. Python Dependencies
Install `Pillow` (for image manipulation) and `pillow-heif` (for Apple `.heic`/`.heif` decoding):

`pip install Pillow pillow-heif jinja2`

---

## 🖥️ Usage & Command Line Options

### Option A: Static Site Builder (`generate_site.py`)

Scans an input directory, processes all derivatives into an output directory, and exports ready-to-publish `.html` pages.

`python generate_site.py -i /path/to/photos -o /path/to/site_output`

#### CLI Options for `generate_site.py`:
- `-i, --input PATH`: [Required] Directory containing input photos and videos.
- `-o, --output PATH`: Output directory for the static site (default: `./site`).
- `-c, --content PATH`: JSON metadata file for trip titles/descriptions (default: `./trip_content.json`).
- `--thumb-size INT`: Max height/width in pixels for generated thumbnails (default: `400`).
- `--force`: Force re-generation of existing thumbnails/derivatives.
- `--tz-offset FLOAT`: Fixed UTC hour offset (e.g., `2` or `-5.5`) for video timestamps.
- `--workers INT`: Limit worker process count (defaults to total CPU cores).
- `--no-dedupe`: Disable automatic exact-duplicate file filtering.

---

### Option B: Zero-Disk On-the-Fly Server (`trip_server.py`)

Run an instant HTTP server directly against your photos folder. Thumbnails, responsive display conversions, and page structures are calculated purely on demand in-memory.

`python trip_server.py -i /path/to/photos -p 8000`

Once running, open your browser to `http://127.0.0.1:8000/`.

#### CLI Options for `trip_server.py`:
- `-i, --input PATH`: [Required] Directory containing photos and videos to serve.
- `-p, --port INT`: Port number to bind the HTTP server to (default: `8000`).
- `-b, --bind ADDRESS`: IP address to bind the server to (default: `127.0.0.1`; set to `0.0.0.0` for network sharing).
- `-c, --content PATH`: JSON metadata file for trip titles/descriptions (default: `./trip_content.json`).
- `--thumb-size INT`: Max height/width in pixels for dynamically generated thumbnails (default: `400`).
- `--tz-offset FLOAT`: Fixed UTC hour offset (e.g., `2` or `-5.5`) for video timestamps.
- `--workers INT`: Limit worker process count for dynamic image decoding (defaults to total CPU cores).
- `--no-dedupe`: Disable automatic exact-duplicate file filtering.

---

## ✍️ Customizing Trip Titles & Day Descriptions

When either script runs for the first time, it scans all EXIF dates and generates a `trip_content.json` file structured like this:

{
  "title": "My European Adventure",
  "subtitle": "2,400km across mountain passes and coastal highways.",
  "days": {
    "2026-05-10": {
      "title": "Arrival in Zurich",
      "description": "Landed early in the morning. Picked up the rental car and drove toward the lakeshore.\n\nStopped for lunch near the water."
    },
    "2026-05-11": {
      "title": "Crossing the Pass",
      "description": "Drove up into the mountains through dense morning fog."
    }
  }
}

### How to use `trip_content.json`:
1. Open `trip_content.json` in any text editor.
2. Edit the main `"title"` and `"subtitle"`.
3. Add custom titles and descriptions for each day key (`YYYY-MM-DD`). Separate paragraphs using double line breaks (`\n\n`).
4. **Re-run or refresh**: The script reads your edits, updates the site display, and safe-appends any newly discovered dates without clearing existing descriptions.

---

## 📁 Directory Structures

### Static Site Output (`generate_site.py`)
site_output/
├── index.html            # Main trip overview, stats, and complete route map
├── trip_content.json     # Editable metadata source file
├── assets/
│   ├── style.css         # Dark-themed custom styles
│   └── script.js         # Map renderers & interactive slideshow engine
├── days/
│   ├── 2026-05-10.html   # Dedicated daily log with day route map & media grid
│   └── 2026-05-11.html
├── media/                # Full-size browser-compatible images & videos
└── thumbs/               # Low-bandwidth grid thumbnails

---

## ⌨️ Interactive Slideshow Controls

When viewing any image grid, click a thumbnail to open the lightbox viewer:

- **Next / Previous Slide**: `Right Arrow` / `Left Arrow` or Horizontal Touch Swipe
- **Toggle Details Drawer**: Press `I` or click the **Info** button
- **Toggle Fullscreen Mode**: Press `F` or click **Fullscreen**
- **Zoom In / Out**: Mouse Wheel scroll, Touch Pinch, or Press `+` / `-`
- **Reset Zoom Scale**: Double-click media frame or press `0`
- **Pan Zoomed Photo**: Click & Drag or Single-finger touch pan
- **Close Lightbox**: Press `Escape` or click **Close**


# Limitations worth knowing

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
