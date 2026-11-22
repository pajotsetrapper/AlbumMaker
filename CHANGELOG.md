# Changelog

All notable changes to AlbumMaker (formerly TripServer) are documented in this file.

## [3.2.0] - TBD 

### Added
- **Social link previews:** Added shared Open Graph and Twitter card metadata (`templates/social_meta.html`) for trip and day pages, including 1200 px cover preview thumbnails and live-server URL generation from `Host` and `X-Forwarded-Proto` headers[cite: 1].
- **GUI progress, completion, and error modals:** Added a blocking "Please wait" progress modal (with progress bar, current step, elapsed timer, keep-open note, and `Cancel` option) during server startup and static builds, followed by completion dialogs ("Your trips are ready!" / "Your site is ready!") or a red error dialog with a "Show activity" action on failure[cite: 1].
- **Taskbar/Dock attention alerts:** Flashed the taskbar or Dock icon when background server startup or build tasks complete or fail while the window is unfocused[cite: 1].
- **Optional Ken Burns slideshow effect:** Added a toggle button and `K` shortcut in the viewer (`tripKenBurns`) with subtle scale and pan animations across templates, pausing on pause, stopping on manual zoom, skipping videos, and respecting `prefers-reduced-motion`[cite: 1].
- **Thumbnail and card size controls:** Added `S`/`M`/`L`/`XL` thumbnail size controls to Journal, Scrapbook, and Cinema day pages (reusing `tripThumbSize`), as well as `S`/`M`/`L`/`XL` card size controls to Journal, Scrapbook, and Cinema library pages (reusing `tripLibrarySize`)[cite: 1].
- **Long-hop line splitting:** Added `splitLongHops` in `script.js` to split route and map lines between distant points instead of drawing straight lines across continents[cite: 1].

### Changed
- **Default template set to `cinema`:** Updated default template configuration and fallbacks to `cinema` for new installations while preserving existing `classic` library and GUI settings[cite: 1].
- **Long-hop threshold lowered:** Lowered the distance threshold for long hops from 1000 km to 250 km[cite: 1].
- **Fluid template widths and Cinema polish:** Expanded container widths up to 94% of the viewport with upper caps in Journal, Scrapbook, and Cinema, replayed Cinema day entrance animations on revisit, fixed library intro visibility/height, and redesigned Journal `L` and `S` layouts and favourites-only grid behavior[cite: 1].
- **GUI controls and demo site:** Updated run button labels (`Cancel` while preparing, `Stop server` when ready), status copy, help text, tests, `README`, and demo outputs[cite: 1].

### Fixed
- **Asset cache versioning:** Fixed `render.py` cache version calculation when using non-`classic` default templates so updated `cinema` CSS and JS assets refresh properly in browsers[cite: 1].
- **GPX jump splitting:** Split GPX recordings at stray jumps or signal gaps (hops of at least 1.5 km exceeding 1000 km/h or 25x surrounding hops), dropping isolated stray points, preventing unlocated photos from being placed on jumps, and reporting cut jumps during builds[cite: 1].

## [3.1.0] - 2026-11-20

### Added
- **Site template system and additional templates:** Added `albummaker/site_templates.py`, `--template` CLI support, library-level template selection, per-trip template selection/persistence, and the `journal`, `scrapbook`, and `cinema` templates alongside `classic`[cite: 1].
- **Bundled offline assets in desktop builds:** Downloaded Leaflet and fonts at build time (verified with SHA-512), recorded third-party notices, and copied assets into the offline-assets folder on first start[cite: 1].
- **Support and sponsorship links:** Added a Support window in the desktop app, GitHub Sponsors configuration, and support sections in the docs and product page[cite: 1].

### Changed
- **Offline asset download resilience:** Downloaded packages in chunks with periodic progress output, per-step and per-attempt timeouts, and retries[cite: 1].
- **Documentation and demo trip:** Added the Lake Lucerne demo trip and GPX track, updated `README` and product page documentation, and aligned help tests with UI labels[cite: 1].

## [3.0.0] - 2026-11-11

### Added
- **Trip libraries (`--library`):** Added multi-trip library support for static site generation (cards newest first, library map, per-trip fallback on error) and live serving under `/<name>/` with a library root `/`[cite: 1].
- **GPX track import and offline place names:** Added streaming GPX track matching and route/distance rendering (with DTD rejection), plus bundled offline place names (`--no-gpx`, `--no-places`)[cite: 1].
- **In-page editing and deep links:** Added in-page editing for captions, favorites, hiding, covers, and day text saved to `trip_content.json`, along with photo deep links, Back support, slideshow autoplay, and date sanity checks[cite: 1].
- **Offline assets, `tripsite` package, and zipapp:** Split shared logic into `tripsite/`, added `tools/build_zipapp.py`, added local Leaflet/fonts and map tile caching with outline fallback, and added `--large-thumb-size`[cite: 1].
- **Desktop GUI and FFmpeg setup:** Added the desktop GUI with per-trip searchable IANA time zones, configurable library overview title, and `albummaker install-ffmpeg` / GUI FFmpeg installation helpers[cite: 1].
- **Multi-platform packaging and release workflow:** Added native builds and release workflows for Windows, macOS, and Linux, plus fallback image formats (HEIC, TIFF, MPO) and keyboard day navigation[cite: 1].

### Changed
- **License and FFmpeg bundling:** Switched to PolyForm Noncommercial 1.0.0, replaced `pillow-heif` with `pi-heif` (while still accepting `pillow-heif` if installed), and stopped bundling FFmpeg inside executables[cite: 1].
- **Renamed project to AlbumMaker:** Migrated package, launchers, per-user storage paths, and documentation from TripServer to AlbumMaker[cite: 1].

### Fixed
- **Build, server, and viewer hardening:** Fixed duplicate/hidden photo and Live Photo clip handling, atomic file writes, byte-capped memory caches, concurrent render limits, editor authentication/cookie handling, optimistic edit conflict checks (`409`), trip cover matching, and path normalization across platforms[cite: 1].