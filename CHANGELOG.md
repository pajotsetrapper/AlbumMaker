# Changelog

All notable changes to AlbumMaker (formerly TripServer) are documented in this file.

## [Unreleased]

### Changed
- **Download instructions match the releases:** The README and product page listed a Linux x64 download that releases no longer contain (only Windows and macOS are built). They now say that on Linux AlbumMaker runs from source.

### Fixed
- **Typing this computer's time zone in the desktop app:** This computer's zone is listed as e.g. `Brussels · UTC+01:00 · Europe · this computer`, so typing its plain label matched nothing and the edit was silently dropped. A typed label now also matches without that suffix, and a city on its own (such as `brussels`) is accepted when only one zone has it.
- **Cinema homepage blank without the map:** When Leaflet could not load (a site without the offline files opened with no internet, or a blocked CDN), every day on the Cinema homepage stayed invisible, because the scenes were only faded in by code that ran after the map was created. The scenes are now set up separately from the map, and the empty map corner is hidden.
- **Photos with very large EXIF were left out:** A TIFF, HEIC or multi-image JPEG whose EXIF was over 64 KB (Lightroom and Photoshop TIFF exports often carry 100 KB of XMP) failed to convert, because a JPEG cannot hold that much EXIF, so the photo was missing from the site. The display copy now leaves out the bulky blocks (XMP, IPTC, Photoshop data, ICC profile, maker note) and keeps the camera, time and GPS; if it still does not fit, it is written without EXIF.
- **Desktop app started from `albummaker.pyz`:** `python albummaker.pyz gui` started every task (server, build, offline files, FFmpeg) as `python -m albummaker`, which fails next to the `.pyz`. The app now tells it runs from a zipapp by where its own code was loaded from.
- **Non-Latin folder names on Windows:** A build, server or FFmpeg setup started from the desktop app on Windows wrote its output in the ANSI code page, so a trip folder such as `Москва 2024` or `東京` stopped it with `UnicodeEncodeError`, and names like `Café` showed garbled in the activity log. Output to a pipe or log file is now written as UTF-8, and a character that cannot be shown is escaped instead of raising.
- **Restarting the server right after stopping it:** The desktop app's Stop button set a 3-second "kill if still running" timer that checked whichever task was running when it fired, so starting the server again within those 3 seconds killed the new server, logged "did not stop in time" and reported that the server could not start. The timer now only acts on the task it was set for.
- **Closing the desktop app while a task runs:** The running task was stopped as if it had failed by itself, so a "could not be created" or "stopped unexpectedly" pop-up flashed up while the window closed, and an offline-files download that finished during the wait could start a server as the app quit. Closing now stops the task as the Stop button does.
- **Hand-edited settings could stop the app from starting:** A `"template"` value that was not text (in the desktop app's settings or a library's `library_settings.json`) crashed the desktop app or the live server at startup, and a trip folder written as `~user/...` for a user that does not exist on this computer crashed the desktop app's window. The desktop app now treats such a settings file like any damaged one (moved aside, defaults used), the server reports the bad template and uses the default, and an unknown `~user` is kept as written.
- **Videos that record only a UTC time landed on the wrong day:** Under Automatic, a video from an Android phone, GoPro or drone was converted with the time zone of the computer doing the build, so a video shot at 08:00 in Tokyo and built in New York showed up the evening before. Such videos now take the UTC offset the trip's own photos state (that day's, else the trip's most common one); this computer's zone is only used when no photo states one. `--timezone` and `--tz-offset` still take precedence.
- **A folder that could not be read lost its photos from the site:** When a subfolder could not be listed during a build (no permission, a network drive dropping out), it was skipped silently and the cleanup then deleted its photos' thumbnails and copies. The build now says which folder it could not read and keeps what the last build made of it.
- **Duller colours in thumbnails and converted photos:** Thumbnails, and the display copies of HEIC, TIFF and multi-image JPEG photos, were saved without the photo's colour profile, so browsers showed wide-gamut photos (iPhone photos are Display P3) as if they were sRGB, visibly duller than the original. An RGB colour profile is now kept.
- **Two photos with the same accented name could share one file:** `a/Café.jpg` and `b/Café.jpg` whose "é" is spelled differently (one copied from an older Mac disk or over SMB, as "e" plus a combining accent) were given names macOS and Windows treat as the same file, so one photo showed the other's picture. Output names are now compared the way those disks compare them, and new names use the composed spelling.
- **Transparent images had black thumbnails:** The transparent parts of a PNG or WebP (a logo, a screenshot with rounded corners) came out black in thumbnails and converted copies. They are now shown on white.
- **Text typed in the day and trip editors was easy to lose:** Esc threw away changed text without asking, also when it was only meant to cancel an input method's composition (Japanese, Chinese ...); the arrow keys went to another day while Save or Cancel had the focus; Enter confirming a composition saved half-typed text; leaving the page with changed text gave no warning; and a double click on Save sent the edit twice, flashing a "changed elsewhere" error. Esc now asks before discarding changed text, keys that belong to an input method are left alone, the arrows do nothing while the editor is open, the browser asks before leaving with unsaved text, and Save works once at a time.
- **Esc on an enlarged Cinema map left the trip:** On a Cinema trip homepage in a library, pressing Esc to close the enlarged map went back to the list of trips instead. Esc now closes the map first, and only a second Esc leaves the page.
- **Pinch-zooming could change the day or the photo:** On a phone, pinch-zooming the thumbnails on a day page could jump to the next day, and pinching back out in the viewer could skip a photo or two, because the finger lifted last was taken for a swipe. Only a one-finger gesture now counts as a swipe, and a day page the browser has zoomed in on can be panned without changing day.
- **The photo viewer for keyboard and screen-reader users:** Opening the viewer left the focus on the page behind it, so screen readers did not announce it and Tab walked through the hidden page; the previous/next arrows could not be reached by keyboard or named by a screen reader; the closed details panel's buttons could still be tabbed to while off screen; and a new photo's caption was not announced. The viewer now takes the focus when it opens (and gives it back when it closes), keeps Tab inside, has labelled previous/next buttons, hides the closed details panel completely, announces captions, and uses a photo's caption as its text alternative.
- **Quick repeated edits in the viewer:** Pressing S (or Hide) twice before the first save had answered sent the same value twice, so the second press did not undo the first, and two quick caption edits could be refused as "changed elsewhere". A photo's saves now go out one after another, each starting from what the previous one saved.
- **Author instructions on the Classic template's published pages:** A day without a description showed visitors "No description yet — add one … in trip_content.json and re-run the generator." Like the other templates, Classic now shows that note only to someone who can edit, and points to the Edit button.
- **Long day descriptions in non-Latin scripts could not be saved:** The live server refused edits over 64 KB as "Request too large", but a day description may be 20,000 characters and is sent twice (the new text and the text it replaces), so editing an 11,000-character Chinese or Japanese description failed although it was within the limit. The size limit is now worked out from the field limits.

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