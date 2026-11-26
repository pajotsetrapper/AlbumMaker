# Changelog

All notable changes to AlbumMaker (formerly TripServer) are documented in this file.

## [Unreleased]

### Fixed
- **Typing this computer's time zone in the desktop app:** This computer's zone is listed as e.g. `Brussels · UTC+01:00 · Europe · this computer`, so typing its plain label matched nothing and the edit was silently dropped. A typed label now also matches without that suffix, and a city on its own (such as `brussels`) is accepted when only one zone has it.
- **Cinema homepage blank without the map:** When Leaflet could not load (a site without the offline files opened with no internet, or a blocked CDN), every day on the Cinema homepage stayed invisible, because the scenes were only faded in by code that ran after the map was created. The scenes are now set up separately from the map, and the empty map corner is hidden.
- **Photos with very large EXIF were left out:** A TIFF, HEIC or multi-image JPEG whose EXIF was over 64 KB (Lightroom and Photoshop TIFF exports often carry 100 KB of XMP) failed to convert, because a JPEG cannot hold that much EXIF, so the photo was missing from the site. The display copy now leaves out the bulky blocks (XMP, IPTC, Photoshop data, ICC profile, maker note) and keeps the camera, time and GPS; if it still does not fit, it is written without EXIF.

### Development
- **Map tests follow the 250 km long-hop limit:** The `splitLongHops` browser tests still used hops (Bangkok to Phuket, Los Angeles to Las Vegas) that the 3.2.0 change from 1000 km to 250 km turned into jumps, so two of them failed.
- **CI runs every test suite before a release:** The test job now installs Node.js and the `tests/js` packages, so the browser tests run instead of being skipped; installs FFmpeg on one leg, so the video tests run; adds an Intel macOS leg (that build is released) and a Python 3.9 leg (the oldest version the README supports); and checks that `CHANGELOG.md` has a section for the tagged version before anything is built.

## [3.2.1] - 2026-11-25

Maintenance release, focussing on GUI performance improvements

### Added
- **Optional GUI startup timing log:** Setting an environment variable `ALBUMMAKER_PROFILE_STARTUP=1` writes an `albummaker-startup-timing.txt` profile alongside the settings file.
- **Time zone documentation:** Added a *"Time zones: do I need to set one?"* section to the desktop app Help window and `README` explaining how Automatic UTC offset detection works and when to set a manual zone.

### Changed
- **Faster place-name lookups and warm starts:** Cached `Gazetteer.nearest` results per position in `<index-cache>.places.json` (invalidated when the town list changes), precomputed town reach bounds with early distance rejection, memoized exact-coordinate lookups, and sliced relative paths instead of `Path.relative_to` (reducing warm start on a 20,000-photo trip from ~14 s to ~1.6 s).
- **Faster desktop GUI launch:**
  - Switched the trip table's Time zone column to plain text with on-demand searchable dropdown editors (`QStyledItemDelegate`), cached timezone choices, and disabled table repainting during initial row population.
  - Moved trip folder existence checks and FFmpeg detection to background threads so sleeping network drives or tool lookups do not block the window from appearing.
  - Deferred importing download-only modules (`urllib`, `hashlib`, `tarfile`, `base64`) and Pillow during startup, checked the per-user offline-assets folder first, decoded the app icon once, built the version tooltip on first hover, and blocked signals while loading settings to prevent redundant status refreshes and file saves.
  - Used `QFontDatabase`'s system `FixedFont` for the activity log to avoid slow Windows font-substitution searches.
- **Easier time-zone dropdown:** The list now shows only real places (about 420 zones, plus UTC) instead of ~500 entries: old aliases, `Etc/GMT+N` (whose signs are inverted), `Factory`, `localtime` and POSIX-style names such as `EST5EDT` are left out, though a zone already set on a trip, or typed in full, is still accepted. Labels show the UTC offset, like `Brussels · UTC+01:00 · Europe` (offsets are today's and change with daylight saving time); the IANA name is shown when hovering. Automatic comes first, then a group with this computer's zone (where it can be told: from `TZ`, `/etc/localtime`, or the optional `tzlocal` package), the five most recently used zones and UTC, then every other zone from A to Z, with separators between the groups. Recently used zones are saved in the settings file as `recent_time_zones`.

- **Editor link visibility:** Displayed the private editor link in the server console and GUI ready window only when `--lan` or `--editor-link-only` is active, and renamed the GUI setting to *"Only browsers with the editor link can edit (not this computer)"*.

## [3.2.0] - 2026-11-22 

### Added
- **Edit the trip title and subtitle from the main page:** The live server's main trip page (in every template) now has an "Edit title & subtitle" button next to "Preview as visitor", like the day pages. They are saved as the top-level `title` and `subtitle` in that trip's `trip_content.json` (via the new `/api/trip` endpoint), the rest of the file is left untouched, and a save is refused if the text was changed elsewhere since the page was loaded. The subtitle may be left empty.
- **Social link previews:** Added shared Open Graph and Twitter card metadata (`templates/social_meta.html`) for trip and day pages, including 1200 px cover preview thumbnails and live-server URL generation from `Host` and `X-Forwarded-Proto` headers.
- **GUI progress, completion, and error modals:** Added a blocking "Please wait" progress modal (with progress bar, current step, elapsed timer, keep-open note, and `Cancel` option) during server startup and static builds, followed by completion dialogs ("Your trips are ready!" / "Your site is ready!") or a red error dialog with a "Show activity" action on failure.
- **Taskbar/Dock attention alerts:** Flashed the taskbar or Dock icon when background server startup or build tasks complete or fail while the window is unfocused.
- **Optional Ken Burns slideshow effect:** Added a toggle button and `K` shortcut in the viewer (`tripKenBurns`) with subtle scale and pan animations across templates, pausing on pause, stopping on manual zoom, skipping videos, and respecting `prefers-reduced-motion`.
- **Thumbnail and card size controls:** Added `S`/`M`/`L`/`XL` thumbnail size controls to Journal, Scrapbook, and Cinema day pages (reusing `tripThumbSize`), as well as `S`/`M`/`L`/`XL` card size controls to Journal, Scrapbook, and Cinema library pages (reusing `tripLibrarySize`).
- **Long-hop line splitting:** Added `splitLongHops` in `script.js` to split route and map lines between distant points instead of drawing straight lines across continents.

### Changed
- **Default template set to `cinema`:** Updated default template configuration and fallbacks to `cinema` for new installations while preserving existing `classic` library and GUI settings.
- **Long-hop threshold lowered:** Lowered the distance threshold for long hops from 1000 km to 250 km.
- **Fluid template widths and Cinema polish:** Expanded container widths up to 94% of the viewport with upper caps in Journal, Scrapbook, and Cinema, replayed Cinema day entrance animations on revisit, fixed library intro visibility/height, and redesigned Journal `L` and `S` layouts and favourites-only grid behavior.
- **Server ready window:** It now shows the private editing link (when editing is on) next to the site address, and uses this computer's network address instead of 127.0.0.1 when other devices may connect.
- **Template menu always visible:** The Template menu on the live library page is now shown to every visitor (it was hidden from anyone without the editor link, such as a phone on your network). It stays greyed out, with a short note, unless the browser may edit.
- **Product page:** The demo buttons now lead to a picker with a live sample of every template (Cinema, Journal, Scrapbook and Classic) instead of only the Classic one. Cinema is shown first as the default, and the page now covers link previews, the Ken Burns slideshow, thumbnail sizes, GPS route cleanup and the desktop progress window.
- **Dark GUI theme by default:** The desktop app now starts in the dark theme for new installs; saved settings keep their theme.
- **GUI controls and demo site:** Updated run button labels (`Cancel` while preparing, `Stop server` when ready), status copy, help text, tests, `README`, and demo outputs.

### Fixed
- **Asset cache versioning:** Fixed `render.py` cache version calculation when using non-`classic` default templates so updated `cinema` CSS and JS assets refresh properly in browsers.
- **GPX jump splitting:** Split GPX recordings at stray jumps or signal gaps (hops of at least 1.5 km exceeding 1000 km/h or 25x surrounding hops), dropping isolated stray points, preventing unlocated photos from being placed on jumps, and reporting cut jumps during builds.

## [3.1.0] - 2026-11-20

### Added
- **Site template system and additional templates:** Added `albummaker/site_templates.py`, `--template` CLI support, library-level template selection, per-trip template selection/persistence, and the `journal`, `scrapbook`, and `cinema` templates alongside `classic`.
- **Bundled offline assets in desktop builds:** Downloaded Leaflet and fonts at build time (verified with SHA-512), recorded third-party notices, and copied assets into the offline-assets folder on first start.
- **Support and sponsorship links:** Added a Support window in the desktop app, GitHub Sponsors configuration, and support sections in the docs and product page.

### Changed
- **Offline asset download resilience:** Downloaded packages in chunks with periodic progress output, per-step and per-attempt timeouts, and retries.
- **Documentation and demo trip:** Added the Lake Lucerne demo trip and GPX track, updated `README` and product page documentation, and aligned help tests with UI labels.

## [3.0.0] - 2026-11-11

### Added
- **Trip libraries (`--library`):** Added multi-trip library support for static site generation (cards newest first, library map, per-trip fallback on error) and live serving under `/<name>/` with a library root `/`.
- **GPX track import and offline place names:** Added streaming GPX track matching and route/distance rendering (with DTD rejection), plus bundled offline place names (`--no-gpx`, `--no-places`).
- **In-page editing and deep links:** Added in-page editing for captions, favorites, hiding, covers, and day text saved to `trip_content.json`, along with photo deep links, Back support, slideshow autoplay, and date sanity checks.
- **Offline assets, `tripsite` package, and zipapp:** Split shared logic into `tripsite/`, added `tools/build_zipapp.py`, added local Leaflet/fonts and map tile caching with outline fallback, and added `--large-thumb-size`.
- **Desktop GUI and FFmpeg setup:** Added the desktop GUI with per-trip searchable IANA time zones, configurable library overview title, and `albummaker install-ffmpeg` / GUI FFmpeg installation helpers.
- **Multi-platform packaging and release workflow:** Added native builds and release workflows for Windows, macOS, and Linux, plus fallback image formats (HEIC, TIFF, MPO) and keyboard day navigation.

### Changed
- **License and FFmpeg bundling:** Switched to PolyForm Noncommercial 1.0.0, replaced `pillow-heif` with `pi-heif` (while still accepting `pillow-heif` if installed), and stopped bundling FFmpeg inside executables.
- **Renamed project to AlbumMaker:** Migrated package, launchers, per-user storage paths, and documentation from TripServer to AlbumMaker.

### Fixed
- **Build, server, and viewer hardening:** Fixed duplicate/hidden photo and Live Photo clip handling, atomic file writes, byte-capped memory caches, concurrent render limits, editor authentication/cookie handling, optimistic edit conflict checks (`409`), trip cover matching, and path normalization across platforms.