"""
albummaker: turns a folder of trip photos and videos into a website.

Two front ends share this package:
  - albummaker.build   builds a static folder (generate_site.py)
  - albummaker.server  serves the same site live (album_server.py)

Modules:
  util       atomic writes, safe JSON for <script>, package resources
  model      MediaItem / DayGroup and the file-type constants
  metadata   EXIF and ffprobe reading, plus the metadata cache format
  scan       finding files, duplicates, Live Photos, days, the date check
  content    trip_content.json: reading, the per-photo/per-day edits
  imaging    thumbnails, display JPEGs and video frames (shared by both)
  render     page templates, CSS/JS and the data that fills them
  offline    local copies of Leaflet and the fonts (--fetch-offline-assets)
  cli        argument helpers shared by both scripts
"""

__version__ = "3.0.0"
