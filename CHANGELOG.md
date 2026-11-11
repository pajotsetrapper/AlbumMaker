# Changelog

All notable changes to AlbumMaker are listed here. The release workflow uses
the section for a version as that GitHub Release's notes.

## [3.0.0] - 2026-11-11

The first public release with ready-to-run desktop apps for Windows, macOS
(Apple silicon and Intel) and Linux.

### Downloads

Pick the file for your computer below, unpack it and open AlbumMaker. The
apps are not code-signed: the first time, macOS asks you to allow it in
**System Settings → Privacy & Security → Open Anyway**, and Windows shows
"Windows protected your PC" (choose **More info → Run anyway**). See
[Download a standalone app](https://github.com/pajotsetrapper/AlbumMaker#download-a-standalone-app)
for the details. `SHA256SUMS.txt` lists each file's checksum.

### Support AlbumMaker ♥

AlbumMaker is free for personal and other noncommercial use. If you enjoy it,
you can support its development on
[Ko-fi](https://ko-fi.com/pietercoppens) or
[GitHub Sponsors](https://github.com/sponsors/pajotsetrapper). Thank you!

### What AlbumMaker does

- Turns a folder of trip photos and videos into a website: one chapter per
  day, a route map per day, an overview map, and a full-screen slideshow
  with keyboard shortcuts, autoplay, zoom and Live Photo playback.
- Works as a static site you can copy anywhere, or as a live local server
  where you edit titles, descriptions, captions, favorites, hidden photos
  and day and trip covers right in the page.
- Gathers several trips, wherever their folders are, on one all-trips page.
- Names the places you visited offline, uses GPS tracks (`.gpx`) to place
  camera photos and draw your route, and works without internet once the
  map library and fonts are downloaded.
- Desktop app to manage the trip list, run the live server and build static
  sites, with in-app help for every control.

### Changed in this release

- **License:** AlbumMaker is now under the PolyForm Noncommercial License
  1.0.0: free for personal and other noncommercial use. Versions published
  earlier under the MIT License stay MIT for those who received them.
- **FFmpeg is no longer bundled.** The app offers to install it with your
  system's package manager (winget or Homebrew), or you can install it
  yourself; `python -m albummaker install-ffmpeg` does the same from a
  terminal. Photos work without it.
- HEIC photos are read with the decode-only `pi-heif` in the downloadable
  apps.
- The downloads include the full third-party license notices next to the
  app, and the app's Help window states the license.
- The Install FFmpeg button disappears once FFmpeg is found, and the window
  grows to show the Activity panel when it opens.
- A new sample trip, "A Weekend at Lake Lucerne", on the product page: day
  stories, captions and favorites, and a GPS track that places the photos
  and draws both days' routes. It is rebuilt from `docs/demo-source` with
  `tools/build_demo.py`.
- New **♥ Support** button in the desktop app, a support section on the
  product page and in the README, and a Sponsor button on the repository,
  all with the Ko-fi and GitHub Sponsors links. The app never asks by itself.
- A damaged settings file no longer stops the desktop app from starting: it
  is set aside and the app starts with default settings.
- The unfinished alternative page templates (`"template"` in
  `trip_content.json`) were removed; the key is ignored if present.

### Fixed

- A day or trip cover could pick a different photo with the same file name
  in another subfolder.
- The all-trips page showed no cover for a trip whose chosen cover photo was
  hidden or removed.
- Rebuilding a static site without HEIC support no longer deletes the HEIC
  photos it built before.
- Videos whose details could not be read (FFmpeg missing or too slow) are
  read again on the next run instead of staying undated.
- 16-bit grayscale TIFF and PNG images no longer come out white.
- Saving a day's title in the page no longer overwrites a description that
  was changed in the file in the meantime.
- Trip titles on the all-trips map are shown as text, never as HTML.
- On Linux, the downloaded app no longer passes its own libraries to the
  system's FFmpeg.
- Downloading the map library and fonts (at the first start of the live
  server) no longer hangs on a slow or stalling connection: it shows
  progress, gives up on a stalled try after a time limit and tries again,
  up to three times.
