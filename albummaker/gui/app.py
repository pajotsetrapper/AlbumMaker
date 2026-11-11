# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Pieter Remi Coppens
"""Qt desktop application entry point."""

from __future__ import annotations

import os
import platform
import re
import signal
import socket
import subprocess
import sys
import traceback
from datetime import datetime
from html import escape as _escape_html
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QEvent, QProcess, QProcessEnvironment, QTimer, Qt, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QFont, QDoubleValidator, QIcon, QPalette, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QAbstractButton,
    QButtonGroup,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QCompleter,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
    QComboBox,
)

from .. import __version__
from .commands import (
    build_command,
    ffmpeg_install_command,
    offline_assets_command,
    server_command,
)
from .settings import (LoadedSettings, SettingsError, default_settings, load_settings_or_recover,
                       prepare_metadata_cache, prepare_tile_cache, save_settings, settings_path,
                       validate_settings, write_library_file)
from ..timezones import timezone_search_choices
from ..offline import VENDOR_DIR, vendor_ready
from ..util import project_dir, read_resource_bytes

PROJECT_ROOT = project_dir()

def _dark_palette() -> QPalette:
    """Palette for the dark theme, so widgets the stylesheet does not cover
    (e.g. the Qt file dialog's views and checkboxes) are dark too."""
    palette = QPalette()
    window, base, alt = QColor("#171b24"), QColor("#1b222e"), QColor("#202633")
    text, muted, accent = QColor("#e6eaf2"), QColor("#788296"), QColor("#354c73")
    for role, color in (
        (QPalette.ColorRole.Window, window),
        (QPalette.ColorRole.WindowText, text),
        (QPalette.ColorRole.Base, base),
        (QPalette.ColorRole.AlternateBase, alt),
        (QPalette.ColorRole.Text, text),
        (QPalette.ColorRole.Button, QColor("#252e3d")),
        (QPalette.ColorRole.ButtonText, text),
        (QPalette.ColorRole.ToolTipBase, alt),
        (QPalette.ColorRole.ToolTipText, text),
        (QPalette.ColorRole.Highlight, accent),
        (QPalette.ColorRole.HighlightedText, QColor("#ffffff")),
        (QPalette.ColorRole.PlaceholderText, muted),
        (QPalette.ColorRole.Link, QColor("#a9c2ff")),
    ):
        palette.setColor(role, color)
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.WindowText,
                 QPalette.ColorRole.ButtonText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, muted)
    return palette


THEME_STYLES = {
    "light": """
        QWidget { color: #17243b; font-size: 13px; }
        QMainWindow, QDialog { background: #f3f6fb; }
        QWidget#mainRoot { background: #f3f6fb; }
        QWidget#scrollPage { background: transparent; }
        QScrollArea { background: transparent; border: 0; }
        QFrame#stepCard, QGroupBox, QTableWidget, QTabWidget::pane,
        QPlainTextEdit, QTextBrowser { background: #ffffff; border: 1px solid #dce4ef; border-radius: 12px; }
        QTabWidget::pane { background: #ffffff; border: 1px solid #dce4ef; border-radius: 10px; }
        QFrame#header { background: transparent; border: 0; }
        QFrame#serverLinks { background: #e8f4f1; border: 1px solid #cfe5df; border-radius: 10px; }
        QFrame#stepCard { border-radius: 14px; }
        QFrame#workflowChoice { background: #f8faff; border: 1px solid #e1e8f3; border-radius: 10px; }
        QGroupBox { margin-top: 12px; padding: 14px 12px 10px; font-weight: 600; }
        QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 5px; }
        QPushButton { background: #ffffff; border: 1px solid #d5deeb; border-radius: 8px;
            padding: 9px 14px; min-height: 18px; }
        QPushButton:hover { background: #edf3fd; border-color: #9bb6df; }
        QPushButton:disabled { color: #8a96a8; background: #edf0f5; }
        QPushButton#primary { color: #ffffff; background: #315fce; border-color: #315fce;
            font-weight: 650; padding: 11px 20px; }
        QPushButton#primary:hover { background: #254da9; }
        QPushButton#stop { color: #ffffff; background: #a63333; border-color: #a63333;
            font-weight: 650; }
        QPushButton#stop:hover { background: #872929; }
        QPushButton#serverLink { color: #ffffff; background: #237b68; border-color: #237b68;
            font-weight: 650; }
        QPushButton#serverLink:hover { background: #1b6657; }
        QPushButton#editorLink { color: #ffffff; background: #53627a; border-color: #53627a;
            font-weight: 650; }
        QPushButton#editorLink:hover { background: #3e4c62; }
        QPushButton#support { color: #b4325a; background: #fff3f6; border-color: #f2c4d2;
            font-weight: 650; }
        QPushButton#support:hover { background: #ffe4ec; border-color: #e58aa6; }
        QPushButton#kofi { color: #ffffff; background: #e0565b; border-color: #e0565b;
            font-weight: 650; padding: 11px 18px; }
        QPushButton#kofi:hover { background: #c94449; }
        QPushButton#sponsors { color: #ffffff; background: #8f3f97; border-color: #8f3f97;
            font-weight: 650; padding: 11px 18px; }
        QPushButton#sponsors:hover { background: #77327e; }
        QLabel#supportHeart { color: #e0565b; }
        QLineEdit, QSpinBox, QPlainTextEdit, QComboBox { background: #ffffff;
            border: 1px solid #cfd9e7; border-radius: 7px; padding: 7px;
            selection-background-color: #315fce; }
        QLineEdit:focus, QSpinBox:focus, QPlainTextEdit:focus, QComboBox:focus { border-color: #537ddd; }
        QTableWidget { gridline-color: #e6ebf2; alternate-background-color: #f7f9fc; }
        QHeaderView::section { background: #edf2f9; color: #33445f;
            border: 0; border-bottom: 1px solid #dce4ef; padding: 5px 8px; }
        QTableWidget::item:selected { background: #e7eefc; color: #173c88; }
        QTabBar::tab { background: #e9eef6; border-top-left-radius: 7px;
            border-top-right-radius: 7px; padding: 9px 14px; margin-right: 3px; }
        QTabBar::tab:selected { background: #ffffff; color: #234da8; font-weight: 600; }
        QRadioButton { spacing: 9px; }
        QCheckBox { spacing: 8px; }
        QScrollBar:vertical { width: 10px; background: transparent; margin: 0; }
        QScrollBar:horizontal { height: 10px; background: transparent; margin: 0; }
        QScrollBar::handle:vertical, QScrollBar::handle:horizontal {
            background: #c4cfdf; border-radius: 4px; min-height: 24px; min-width: 24px; }
        QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
        QLabel#muted { color: #66748a; }
        QLabel#status { color: #315fce; font-weight: 600; }
        QLabel#stepNumber { color: #315fce; font-size: 12px; font-weight: 700; }
    """,
    "dark": """
        QWidget { color: #e6eaf2; font-size: 13px; }
        QMainWindow, QDialog { background: #171b24; }
        QWidget#mainRoot { background: #171b24; }
        QWidget#scrollPage { background: transparent; }
        QScrollArea { background: transparent; border: 0; }
        QFrame#header { background: transparent; border: 0; }
        QFrame#serverLinks { background: #202f32; border: 1px solid #34534e; border-radius: 10px; }
        QAbstractItemView { background: #1b222e; alternate-background-color: #202633;
            color: #e6eaf2; border: 1px solid #343e4f; outline: 0;
            selection-background-color: #354c73; selection-color: #ffffff; }
        QAbstractItemView::item:hover { background: #2a3447; }
        QAbstractItemView::item:selected { background: #354c73; color: #ffffff; }
        QAbstractItemView::item:disabled { color: #788296; }
        QComboBox QAbstractItemView { border: 1px solid #3a4659; }
        QToolButton { background: #252e3d; color: #e6eaf2; border: 1px solid #3a4659;
            border-radius: 6px; padding: 4px; }
        QToolButton:hover { background: #303d51; border-color: #637fa8; }
        QToolButton:pressed, QToolButton:checked { background: #354c73; }
        QToolButton:disabled { color: #788296; background: #202633; }
        QSplitter::handle { background: #343e4f; }
        QMenu { background: #202633; color: #e6eaf2; border: 1px solid #3a4659; padding: 4px; }
        QMenu::item { padding: 6px 22px; border-radius: 4px; }
        QMenu::item:selected { background: #354c73; color: #ffffff; }
        QMenu::item:disabled { color: #788296; }
        QMenu::separator { height: 1px; background: #343e4f; margin: 4px 6px; }
        QToolTip { background: #202633; color: #e6eaf2; border: 1px solid #3a4659; }
        QFileDialog QLabel { background: transparent; }
        QFrame#stepCard, QGroupBox, QTableWidget, QTabWidget::pane, QPlainTextEdit, QTextBrowser {
            background: #202633; border: 1px solid #343e4f; border-radius: 12px; }
        QTabWidget::pane { background: #202633; border: 1px solid #343e4f; border-radius: 10px; }
        QFrame#stepCard { border-radius: 14px; }
        QFrame#workflowChoice { background: #242c39; border: 1px solid #3a4659; border-radius: 10px; }
        QGroupBox { margin-top: 12px; padding: 14px 12px 10px; font-weight: 600; }
        QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 5px; }
        QPushButton { background: #252e3d; border: 1px solid #3a4659; border-radius: 8px;
            padding: 9px 14px; min-height: 18px; }
        QPushButton:hover { background: #303d51; border-color: #637fa8; }
        QPushButton:disabled { color: #788296; background: #202633; }
        QPushButton#primary { color: #ffffff; background: #4a78dc; border-color: #4a78dc;
            font-weight: 650; padding: 11px 20px; }
        QPushButton#primary:hover { background: #5b88ec; }
        QPushButton#stop { color: #ffffff; background: #a8444b; border-color: #a8444b;
            font-weight: 650; }
        QPushButton#stop:hover { background: #913941; }
        QPushButton#serverLink { color: #ffffff; background: #287c6c; border-color: #287c6c;
            font-weight: 650; }
        QPushButton#serverLink:hover { background: #32917e; }
        QPushButton#editorLink { color: #ffffff; background: #46556e; border-color: #46556e;
            font-weight: 650; }
        QPushButton#editorLink:hover { background: #566783; }
        QPushButton#support { color: #ff9db8; background: #33222b; border-color: #5c3343;
            font-weight: 650; }
        QPushButton#support:hover { background: #432a36; border-color: #a85b78; }
        QPushButton#kofi { color: #ffffff; background: #e0565b; border-color: #e0565b;
            font-weight: 650; padding: 11px 18px; }
        QPushButton#kofi:hover { background: #ec6a6f; }
        QPushButton#sponsors { color: #ffffff; background: #9d4ba6; border-color: #9d4ba6;
            font-weight: 650; padding: 11px 18px; }
        QPushButton#sponsors:hover { background: #b05cb9; }
        QLabel#supportHeart { color: #ff7b80; }
        QLineEdit, QSpinBox, QPlainTextEdit, QComboBox { background: #1b222e;
            border: 1px solid #3a4659; border-radius: 7px; padding: 7px;
            selection-background-color: #4a78dc; }
        QLineEdit:focus, QSpinBox:focus, QPlainTextEdit:focus, QComboBox:focus { border-color: #7699e2; }
        QTableWidget { gridline-color: #343e4f; alternate-background-color: #242c39; }
        QHeaderView::section { background: #293344; color: #e6eaf2;
            border: 0; border-bottom: 1px solid #343e4f; padding: 5px 8px; }
        QTableWidget::item:selected { background: #354c73; color: #ffffff; }
        QTabBar::tab { background: #252e3d; border-top-left-radius: 7px;
            border-top-right-radius: 7px; padding: 9px 14px; margin-right: 3px; }
        QTabBar::tab:selected { background: #202633; color: #a9c2ff; font-weight: 600; }
        QRadioButton { spacing: 9px; }
        QCheckBox { spacing: 8px; }
        QScrollBar:vertical { width: 10px; background: transparent; margin: 0; }
        QScrollBar:horizontal { height: 10px; background: transparent; margin: 0; }
        QScrollBar::handle:vertical, QScrollBar::handle:horizontal {
            background: #48566c; border-radius: 4px; min-height: 24px; min-width: 24px; }
        QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
        QLabel#muted { color: #a2adbf; }
        QLabel#status { color: #a9c2ff; font-weight: 600; }
        QLabel#stepNumber { color: #a9c2ff; font-size: 12px; font-weight: 700; }
    """,
}

HELP_HTML = """
<h2>AlbumMaker help</h2>
<p>Follow the three steps in the main window: add trip folders, choose whether to run a live server
or create a static site, then start. Your choices are saved automatically. Most people can use the
defaults; less common settings are under <b>Options</b>. The General tab applies to both workflows;
the Live server and Static site tabs apply only to their named workflow. The version number is shown
under the app name and at the bottom of this window; please include it when reporting a problem
(see <b>Reporting a problem</b> at the end).</p>

<h3>Main window</h3>
<ul>
<li><b>Options:</b> opens the settings described below.</li>
<li><b>Help:</b> opens this window.</li>
<li><b>Install FFmpeg:</b> only shown while FFmpeg cannot be found; see <b>FFmpeg</b> below.</li>
<li><b>Status</b> (top right): what AlbumMaker is doing, such as <i>Ready</i>, <i>Server running</i>
or <i>Completed</i>.</li>
</ul>

<h3>Step 1: Choose your trip folders</h3>
<ul>
<li><b>Add folders:</b> choose one or more trip folders in the folder picker
(Ctrl/Command- or Shift-click to select several). Folders already in the list are skipped.
Each row is one trip.</li>
<li><b>Trip folder</b> column: the folder's name; hover over it to see the full path, or
<i>Folder not found</i> if it is missing.</li>
<li><b>Time zone</b> column: search by typing a region or city, or open the list, and choose a zone
such as <code>Europe/Paris</code>. It is saved with the folder and handles daylight-saving changes.
<b>Automatic</b> does not guess the trip's location. Photo EXIF times stay as recorded. For videos,
the <b>Default video UTC offset</b> (Options &rarr; General) is used if set; otherwise iPhone local
timestamps are kept as recorded and other UTC capture times use this computer's time zone. Choose a
named zone when you want a consistent trip-local time, especially for video. For GPS-track matching,
AlbumMaker uses offsets recorded in the files first, then the same day's media, then the trip zone or
default offset, and finally the trip's overall media offsets when available.</li>
<li><b>Remove:</b> removes the selected folders from the list; it never deletes photos or
generated files.</li>
</ul>

<h3>Step 2: Choose what to do</h3>
<ul>
<li><b>Live server:</b> scans your trips and serves the site to your web browser, where you can also
edit it (see <b>In the browser</b>). The server keeps running until you stop it or close AlbumMaker.
Its required setup is in Options &rarr; Live server.</li>
<li><b>Static site:</b> creates a standalone website folder you can copy, share or publish.
<b>Save site to</b> is the folder it is created in; <b>Browse&hellip;</b> chooses it. It does not
need the live-server setup. Its maps need the site to be served over http(s); see the note below.</li>
</ul>

<h3>Step 3: Run</h3>
<ul>
<li>The button says <b>Start live server</b> or <b>Create static site</b>, depending on Step 2. The text
next to it says what is still missing, if anything.</li>
<li>While a task runs, the button becomes <b>Stop server</b> (live server) or <b>Stop task</b> (anything
else, such as creating a site or downloading offline assets). Only one task runs at a time.</li>
<li>When the live server is ready, <b>Open trip site</b> opens it in your browser. When editing is
enabled, <b>Open editing link (private)</b> opens the private editor link, which lets any browser
edit, for example on another computer. Do not share that link: it grants editing access. It changes
every time the server starts.</li>
<li><b>Show activity</b> / <b>Hide activity</b> shows or hides the Activity log: progress, errors, the
commands AlbumMaker runs, and server requests. It opens by itself when a task starts.</li>
</ul>

<h3>Viewing a static site: maps need a web server</h3>
<p>Static sites load map tiles straight from OpenStreetMap. OpenStreetMap refuses tile requests that
carry no <code>Referer</code> header (its tile usage policy), and a browser sends none for a page opened
directly from disk (<code>file://</code>, for example by double-clicking <code>index.html</code>, or from a USB
drive). In that case the maps show "Access blocked" tiles or only the rough outline, while photos
and everything else still work. To get working maps:</p>
<ul>
<li><b>Publish the folder</b> on any web host (GitHub Pages, Netlify, your own server), so it is
opened through an <code>https://</code> address. Do not make the host send a
<code>Referrer-Policy: no-referrer</code> header.</li>
<li><b>Or test locally</b> by running <code>python -m http.server</code> inside the site folder and opening
<code>http://localhost:8000/</code>. OpenStreetMap may still reject <code>localhost</code>, so this is
only for testing.</li>
<li><b>Or use the live server</b>, which fetches and caches tiles itself, so it does not have this
limitation.</li>
</ul>
<p>OpenStreetMap's policy also expects light traffic. A heavily visited site should use a commercial
tile provider instead.</p>

<h3>Options &rarr; General</h3>
<p>These settings apply to both the live server and static sites.</p>
<ul>
<li><b>Appearance &rarr; Color theme:</b> light or dark, for this app.</li>
<li><b>Trip library page &rarr; Overview page title:</b> the heading and browser-tab title of the
page listing all trips. Defaults to <code>Trips</code>.</li>
<li><b>Video time handling &rarr; Default video UTC offset:</b> optional fixed fallback for videos in
trips whose time zone is Automatic. Enter hours east of UTC, such as <code>2</code> or
<code>-5.5</code>. A time zone chosen for a trip takes precedence. Leave blank to use automatic
handling.</li>
<li><b>Shared trip processing &rarr; Thumbnail size:</b> base thumbnail size in pixels (16&ndash;4096;
default 400).</li>
<li><b>Shared trip processing &rarr; Optional date range:</b> the first and last date (YYYY-MM-DD)
your files should fall between. Both dates are required, and the end must be on or after the start.
The same range is used for <b>every trip</b> in the list. Files outside it are treated as having a
suspicious date and are put under Unsorted. Leave both blank to use the normal date checks. Different
ranges per trip are only possible from the command line. Ignored (and greyed out) while
<b>Trust all captured dates</b> is on.</li>
<li><b>Shared import behavior:</b>
<ul>
<li><b>Keep exact duplicate files:</b> includes byte-identical copies that are otherwise
skipped.</li>
<li><b>Show Live Photo video clips separately:</b> lists paired clips as individual videos
instead of attaching them to their photos.</li>
<li><b>Trust all captured dates:</b> skips the suspicious-date checks; the optional date range is
ignored while this is on.</li>
<li><b>Ignore GPS tracks:</b> disables matching media to GPX tracks and drawing GPS routes.</li>
<li><b>Hide offline place names:</b> leaves out nearest-town names everywhere they appear: under
each day's date, on day and trip cards, in the photo details panel, and as suggested day titles
in the editor.</li>
</ul></li>
<li><b>Additional GPS tracks &rarr; GPX paths:</b> optional .gpx files or folders, one per line.
Tracks inside each trip folder are found automatically. Ignored (and greyed out) while
<b>Ignore GPS tracks</b> is on.</li>
</ul>

<h3>Options &rarr; Live server</h3>
<p>These settings only affect the live server.</p>
<p><b>One-time server setup.</b> The two cache folders are required and are created if needed.
Each has a <b>Browse&hellip;</b> button to choose it.</p>
<ul>
<li><b>Metadata cache folder:</b> stores a small JSON index per trip with each media file's size and
modification time plus metadata read from it, including capture date, GPS coordinates, dimensions,
camera details and video information. On a later server start, unchanged files can reuse these
results instead of being read again. It does not store photo or video copies. By default this is a
<code>metadata-cache</code> folder next to AlbumMaker's settings file.</li>
<li><b>Map tile cache folder:</b> stores map tiles as they are viewed, preventing repeat downloads
and allowing previously viewed map areas to work offline. By default this is a
<code>tile-cache</code> folder next to AlbumMaker's settings file.</li>
<li><b>Download offline assets</b> (<b>Refresh offline assets</b> once they are there): downloads local
copies of the map library and fonts, so those parts of the page work without internet. Missing
offline assets are also downloaded automatically when you start the server; an internet connection
is needed for that. They are stored in <code>offline-assets</code> beside AlbumMaker's settings file.
Map backgrounds (tiles) are separate: they come from the map provider unless they are in the tile
cache. If the offline assets are available, static sites also include them; they are not required
for static sites.</li>
</ul>
<p><b>Live server options.</b></p>
<ul>
<li><b>Port:</b> local web-server port (1&ndash;65535; default 8000).</li>
<li><b>Image cache:</b> maximum memory for generated thumbnails and converted photos (16&ndash;65536 MB;
default 512 MB).</li>
<li><b>High-resolution thumbnail:</b> a larger preview used for large gallery tiles and trip cards on
high-pixel-density screens, so small thumbnails do not look soft when enlarged. It is measured by its
longest side in pixels. The live-server default is 2400 pixels, a good choice for Full HD and 4K displays;
the server creates these previews on demand. It must be larger than the thumbnail size. Larger images
need more processing and memory; Off disables them.</li>
<li><b>Allow other devices on my network to connect:</b> lets devices on the local network view the
site. Off by default. There is no password, so use it only on a trusted network. Other devices can
look but not edit, unless they open the editor link.</li>
<li><b>Disable all in-page editing:</b> prevents edits to captions, favorites, hidden photos,
covers, titles and descriptions, for everyone. Off by default.</li>
<li><b>Require the editor link for editing:</b> editing is allowed only in a browser opened with the
private editor link (<b>Open editing link (private)</b>, also printed in Activity). Off by default;
useful behind a reverse proxy or tunnel.</li>
<li><b>Show every request in the activity log:</b> adds all HTTP requests to Activity; otherwise only
errors and slow requests are shown. On by default in the desktop app.</li>
</ul>
<p><b>Maps and network (optional).</b></p>
<ul>
<li><b>Custom map URL:</b> an optional map-tile provider URL starting with <code>http://</code> or
<code>https://</code> and containing <code>{z}</code>, <code>{x}</code> and <code>{y}</code>. Blank by
default, which uses OpenStreetMap. Static sites always use OpenStreetMap.</li>
<li><b>Extra allowed host names:</b> additional host names the server may answer to, one per line.
Blank by default; use only for a trusted custom local DNS name.</li>
</ul>

<h3>Options &rarr; Static site</h3>
<p>These settings only affect static-site generation (<b>Static site media</b>).</p>
<ul>
<li><b>High-resolution thumbnail:</b> optional larger version for large gallery tiles and trip cards on
high-pixel-density screens, so small thumbnails do not look soft when enlarged. It is measured by its
longest side in pixels. 2400 pixels is a good choice for Full HD and 4K displays; Off is the default for
static sites, and any other value must be larger than the thumbnail size. Larger images
increase build time and site size.</li>
<li><b>Parallel workers:</b> number of files processed at the same time. Automatic, the default, uses
the available CPU cores.</li>
<li><b>Regenerate all static site media:</b> rebuilds every thumbnail and media copy instead of reusing
unchanged generated files. Off by default.</li>
</ul>

<h3>FFmpeg</h3>
<p>AlbumMaker uses FFmpeg, a separate program, to read video dates and locations and to make video
previews. AlbumMaker does not include FFmpeg. Without it, photos work normally, but videos have no date
(so they end up under Unsorted), no location and a plain placeholder instead of a preview. Once FFmpeg
is installed, the missing previews are made the next time you start the server or create the site.</p>
<ul>
<li><b>When AlbumMaker starts</b> and cannot find FFmpeg, it offers to install it. If your system has a
package manager AlbumMaker can use (<code>winget</code> on Windows, Homebrew on macOS and Linux), the
<b>Install FFmpeg</b> button there runs it and shows the progress in Activity. Otherwise the dialog
offers <b>Open download page</b> and <b>Copy command</b>.</li>
<li><b>Not now</b> asks again at the next start. <b>Don't ask again</b> stops the question at
startup; the <b>Install FFmpeg</b> button in the main window still works.</li>
<li>The <b>Install FFmpeg</b> button in the main window is only shown while FFmpeg is missing. It
disappears once FFmpeg is found, including when you installed it some other way (AlbumMaker checks
again when you switch back to its window).</li>
<li><b>Install it yourself</b> instead, then restart AlbumMaker:
<code>winget install Gyan.FFmpeg</code> (Windows), <code>brew install ffmpeg</code> (macOS),
<code>sudo apt install ffmpeg</code> (Debian/Ubuntu), <code>sudo dnf install ffmpeg-free</code>
(Fedora) or <code>sudo pacman -S ffmpeg</code> (Arch). Or download a build from
<a href="https://ffmpeg.org/download.html">ffmpeg.org</a> and copy both <code>ffmpeg</code> and
<code>ffprobe</code> into this folder: <code>@FFMPEG_DIR@</code></li>
</ul>
<p>FFmpeg is made by the FFmpeg project and licensed by them (LGPL or GPL, depending on the build).</p>

<h3>In the browser</h3>
<p>What you can do on the site itself. Editing needs the live server; everything else works on both
the live server and a static site.</p>
<ul>
<li><b>Pages:</b> the trip list, with a card per trip and a map of all trips; each trip's
homepage, with an overview map and a card per day; and a page per day with its map and photos. Click a map point or a card to open it. Files without a usable date are on the
<i>Unsorted</i> page.</li>
<li><b>Sizes:</b> the <b>Card size</b> and <b>Thumbnail size</b> controls (<b>S</b>, <b>M</b>, <b>L</b>,
<b>XL</b>) make cards and thumbnails bigger or smaller. The browser remembers each choice.</li>
<li><b>Slideshow:</b> click a photo to open it full-screen. <b>Play</b> (or Space) advances by itself;
the speed button next to it changes the time per photo. The <b>Live</b> button plays a Live Photo's
clip.</li>
<li><b>Favorites:</b> once a photo is a favorite (&#9733;), a <b>&#9733; Favorites</b> button above the
thumbnails shows only the favorites, and the slideshow then steps through favorites only.</li>
<li><b>Locations:</b> each geotagged photo is a point on the maps, and <b>I</b> in the slideshow opens a
details panel with camera settings, the place, a small map of where it was taken, and a link to
download the original file. GPS tracks, if any, are drawn on the maps.</li>
<li><b>Links to one photo:</b> opening a photo adds <code>#p=&hellip;</code> to the address. Copy
the address to share a link that opens that photo; the browser's Back button closes the
viewer.</li>
</ul>
<p><b>Keyboard shortcuts.</b> On a day page: <b>&larr;</b> / <b>&rarr;</b> previous / next day,
<b>Esc</b> back to the trip homepage (it closes anything open first; on a trip's homepage it goes back
to the trip list). In the slideshow: <b>&larr;</b> / <b>&rarr;</b> previous / next photo, <b>Space</b>
play / pause, <b>+</b> / <b>-</b> / <b>0</b> zoom in / out / reset, <b>I</b> details panel, <b>F</b>
true full-screen, <b>L</b> play a Live Photo's clip, <b>S</b> favorite (when editing), <b>Esc</b> close
the details panel, then the viewer. Shortcuts are off while you type in a caption or title box.</p>
<p><b>Editing</b> (live server; on this computer, or in a browser opened with the editor link).
Changes are saved immediately to <code>trip_content.json</code> in the trip's photos folder.</p>
<ul>
<li><b>Day title and description:</b> <b>Edit title &amp; description</b> on a day page. A day without
a title offers its towns as a suggested title. <b>Preview as visitor</b> shows that page as others
see it.</li>
<li><b>Captions:</b> type in the caption box under a photo in the slideshow, then press Enter or click
away.</li>
<li><b>Favorite</b> (or <b>S</b>) marks a photo with &#9733;.</li>
<li><b>Set as cover</b> makes the photo that day's card on the trip homepage; <b>Set trip cover</b>
makes it the cover of the whole trip in the trip list.</li>
<li><b>Hide</b> / <b>Unhide:</b> hidden photos are never shown to visitors and are left out of a
static site entirely; you still see them, dimmed, so you can unhide them. Your files are never
changed or deleted.</li>
</ul>
<p><b>Trip title and subtitle</b> cannot be edited in the browser. They are stored, together with the
day texts, captions, favorites, hidden photos and covers, in <code>trip_content.json</code> in the
trip's photos folder. A new file starts with the folder's name as the title; edit the
<code>"title"</code> and <code>"subtitle"</code> lines with a text editor, then refresh the live
page or create the static site again.</p>

<h3>Only from the command line</h3>
<p>The app covers the common settings. These need the command-line scripts (see the README):</p>
<ul>
<li>Serving or building one trip folder on its own, without the trip list page (<code>-i</code>).</li>
<li>A different <code>trip_content.json</code> file (<code>-c</code> / <code>--content</code>).</li>
<li>One time zone for every trip at once (<code>--timezone</code>); in the app, choose it per trip.</li>
<li>Per-trip settings in a trip list (<code>--library</code>): the name a trip gets in web addresses,
and per-trip date ranges, UTC offsets, GPX tracks or import options.</li>
<li>The credit shown on the maps when you use a <b>Custom map URL</b> (<code>--tile-attribution</code>);
without it the maps credit the tile server's host name.</li>
</ul>

<h3>Settings file</h3>
<p>Your settings are saved automatically in <code>@SETTINGS_PATH@</code>. If that file is damaged,
AlbumMaker starts with the default settings, renames the damaged file to
<code>settings.json.bad-</code><i>date-time</i> in the same folder, and tells you what was wrong.</p>

<h3>Reporting a problem</h3>
<p>Report problems and suggestions at
<a href="https://github.com/pajotsetrapper/AlbumMaker/issues">github.com/pajotsetrapper/AlbumMaker/issues</a>.
Please include the version line at the bottom of this window and, if possible, the messages from
Activity. The packaged app also saves unexpected errors to <code>albummaker-gui-error.log</code> next to
the settings file.</p>

<h3>Supporting AlbumMaker</h3>
<p>AlbumMaker is free for noncommercial use. If you enjoy it, <b>♥ Support</b> in the top-right
corner opens a small window with two ways to donate: <b>☕ Buy me a coffee on Ko-fi</b>
(a one-off donation, no account needed) and <b>♥ Sponsor on GitHub</b> (one-off or monthly,
with your GitHub account). You can also go straight to
<a href="https://ko-fi.com/pietercoppens">ko-fi.com/pietercoppens</a> or
<a href="https://github.com/sponsors/pajotsetrapper">github.com/sponsors/pajotsetrapper</a>.
Donations are voluntary: they don't buy features, support or a commercial license, and
AlbumMaker works the same either way. The app never asks on its own.</p>

<h3>License</h3>
<p>AlbumMaker is licensed under the PolyForm Noncommercial License 1.0.0. It uses Qt (LGPL-3.0) and
other third-party software, which keep their own licenses. See the <code>LICENSE</code> and
<code>THIRD_PARTY_NOTICES</code> files next to the app in the download, or in the
<a href="https://github.com/pajotsetrapper/AlbumMaker">source repository</a>.</p>
"""


def _help_html() -> str:
    """HELP_HTML with this computer's folders filled in."""
    from ..ffmpeg_setup import user_tool_dir
    return (HELP_HTML
            .replace("@FFMPEG_DIR@", _escape_html(str(user_tool_dir())))
            .replace("@SETTINGS_PATH@", _escape_html(str(settings_path()))))


def _label(text: str, object_name: Optional[str] = None) -> QLabel:
    label = QLabel(text)
    if object_name:
        label.setObjectName(object_name)
    return label


LICENSE_TEXT = (
    "Licensed under PolyForm Noncommercial 1.0.0. Uses Qt (LGPL-3.0) and other "
    "third-party software; see THIRD_PARTY_NOTICES."
)


KOFI_URL = "https://ko-fi.com/pietercoppens"
GITHUB_SPONSORS_URL = "https://github.com/sponsors/pajotsetrapper"
SUPPORT_TEXT = (
    "AlbumMaker is made in spare time and is free for personal and other "
    "noncommercial use. If it helped you turn your trips into something you "
    "enjoy looking back on, you can say thanks with a small donation. It keeps "
    "the project going: time for new features, fixes and testing on every platform."
)
SUPPORT_FINE_PRINT = (
    "Donations are voluntary and always welcome, but they don't buy features, "
    "support or a commercial license. AlbumMaker works the same either way."
)


def _about_text() -> str:
    """The app version and what it runs on (handy in bug reports), then the license."""
    try:
        import PySide6
        qt = f"PySide6 {PySide6.__version__}"
    except Exception:  # pragma: no cover
        qt = "PySide6"
    return (f"AlbumMaker {__version__} \u00b7 Python {platform.python_version()} \u00b7 "
            f"{qt} \u00b7 {platform.platform()}\n{LICENSE_TEXT}")


# How a kind of background task is written in the status line and Activity,
# where it differs from the kind string itself (which the code compares against).
TASK_LABELS = {"ffmpeg setup": "FFmpeg setup"}


def _task_label(kind: str) -> str:
    """The task's name in the middle of a sentence."""
    return TASK_LABELS.get(kind, kind)


def _task_name(kind: str) -> str:
    """The task's name at the start of a sentence."""
    label = _task_label(kind)
    return label[:1].upper() + label[1:]


def _group(title: str) -> tuple[QGroupBox, QFormLayout]:
    box = QGroupBox(title)
    form = QFormLayout(box)
    form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
    form.setHorizontalSpacing(16)
    form.setVerticalSpacing(10)
    return box, form


def _scroll_page(*widgets: QWidget) -> QScrollArea:
    page = QWidget()
    page.setObjectName("scrollPage")
    layout = QVBoxLayout(page)
    layout.setContentsMargins(14, 14, 14, 14)
    layout.setSpacing(12)
    for widget in widgets:
        layout.addWidget(widget)
    layout.addStretch(1)
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setWidget(page)
    return scroll


def _application_icon() -> QIcon:
    image = QPixmap()
    if not image.loadFromData(read_resource_bytes("gui", "assets", "albummaker.ico")):
        raise RuntimeError("The AlbumMaker application icon could not be loaded.")
    return QIcon(image)


def _start_in_own_session(process: QProcess) -> bool:
    """On macOS and Linux, start the task as the leader of a new session (and so
    of its own process group), so stopping it can signal everything it started:
    the packaged helper is a PyInstaller launcher plus the real program, and a
    build runs worker processes. Needs PySide6 6.7 or later; returns False if
    that is not available, and the task then starts as before."""
    if sys.platform == "win32":
        return False
    flags = getattr(QProcess, "UnixProcessFlag", None)
    flag = getattr(flags, "CreateNewSession", None)
    if flag is None or not hasattr(process, "setUnixProcessParameters"):
        return False
    try:
        process.setUnixProcessParameters(flag)
    except (TypeError, ValueError, RuntimeError):  # pragma: no cover - older bindings
        return False
    return True


def settings_recovery_message(loaded: LoadedSettings) -> str:
    """What to tell the user when the settings file could not be used."""
    text = (
        "AlbumMaker could not use its settings file, so it started with the default "
        f"settings.\n\nWhat was wrong:\n{loaded.problem}\n\n"
    )
    if loaded.moved_to is not None:
        return text + (
            f"The old file was moved to:\n{loaded.moved_to}\n\n"
            "Your trip folders and other choices are still in that file; open it in a text "
            "editor if you want to copy them back. New settings are saved as usual."
        )
    return text + (
        f"The old file could not be moved aside ({loaded.move_error}), so it was left "
        f"unchanged at:\n{loaded.path}\n\n"
        "To avoid overwriting it, changes you make now will not be saved. Move, fix or "
        "delete that file, then restart AlbumMaker."
    )


class MainWindow(QMainWindow):
    def __init__(self, settings: dict, save_blocked_reason: str = ""):
        super().__init__()
        self.settings = settings
        # Set when a damaged settings file could not be moved aside: saving would
        # overwrite it, so settings are kept in memory only.
        self._save_blocked_reason = save_blocked_reason
        self._startup_settings = validate_settings(settings)
        self._process: Optional[QProcess] = None
        self._process_group = False
        self._process_kind = ""
        self._log_remainder = ""
        self._offline_asset_error = ""
        self._pending_server_start = False
        self._server_ready = False
        self._server_url = ""
        self._editor_url = ""
        self._stopping = False
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(500)
        self._save_timer.timeout.connect(self._save_from_ui)

        self.setWindowTitle(f"AlbumMaker {__version__}")
        self.setWindowIcon(_application_icon())
        self.setMinimumSize(680, 580)
        self.resize(920, 820)
        self._build_ui()
        self._load_ui()
        self._connect_persistence()
        self._apply_theme()
        self._update_actions()
        if save_blocked_reason:
            self.status_label.setText("Settings are not being saved")
            self.status_label.setToolTip(
                "The damaged settings file could not be moved aside, so it is not overwritten: "
                + save_blocked_reason
            )

    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("mainRoot")
        outer = QVBoxLayout(root)
        outer.setContentsMargins(28, 22, 28, 22)
        outer.setSpacing(14)

        header = QFrame()
        header.setObjectName("header")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 4)
        title_block = QVBoxLayout()
        title = _label("AlbumMaker")
        title.setFont(QFont(title.font().family(), 21, QFont.Weight.Bold))
        title_block.addWidget(title)
        title_block.addWidget(_label("A simple way to turn your trips into a photo site.", "muted"))
        version_label = _label(f"Version {__version__}", "muted")
        version_label.setToolTip(_about_text())
        title_block.addWidget(version_label)
        header_layout.addLayout(title_block)
        header_layout.addStretch(1)
        self.options_button = QPushButton("Options")
        self.options_button.setToolTip("Change appearance and advanced settings.")
        self.options_button.clicked.connect(lambda: self.options_dialog.exec())
        header_layout.addWidget(self.options_button)
        help_button = QPushButton("Help")
        help_button.setToolTip("Read plain-language help for the workflows and settings.")
        help_button.clicked.connect(self._show_help)
        header_layout.addWidget(help_button)
        self.ffmpeg_button = QPushButton("Install FFmpeg")
        self.ffmpeg_button.setToolTip(
            "Install FFmpeg, the separate program AlbumMaker uses for video dates and previews."
        )
        self.ffmpeg_button.clicked.connect(lambda: self._offer_ffmpeg_install(force=True))
        header_layout.addWidget(self.ffmpeg_button)
        self._update_ffmpeg_button()
        self.support_button = QPushButton("\u2665 Support")
        self.support_button.setObjectName("support")
        self.support_button.setToolTip(
            "Like AlbumMaker? Support its development with a donation on Ko-fi or GitHub Sponsors."
        )
        self.support_button.clicked.connect(self._show_support)
        header_layout.addWidget(self.support_button)
        self.status_label = _label("Ready", "status")
        header_layout.addWidget(self.status_label)
        outer.addWidget(header)

        self.server_links = QFrame()
        self.server_links.setObjectName("serverLinks")
        links_layout = QHBoxLayout(self.server_links)
        links_layout.setContentsMargins(12, 8, 12, 8)
        links_layout.addWidget(_label("Live server is ready", "status"))
        links_layout.addStretch(1)
        self.site_link_button = QPushButton("Open trip site")
        self.site_link_button.setObjectName("serverLink")
        self.site_link_button.setToolTip("Open the running trip site in your web browser.")
        self.site_link_button.clicked.connect(lambda: self._open_server_link(self._server_url))
        links_layout.addWidget(self.site_link_button)
        self.editor_link_button = QPushButton("Open editing link (private)")
        self.editor_link_button.setObjectName("editorLink")
        self.editor_link_button.setToolTip(
            "Open the private editing link. Do not share it; it grants editing access."
        )
        self.editor_link_button.clicked.connect(lambda: self._open_server_link(self._editor_url))
        self.editor_link_button.setVisible(False)
        links_layout.addWidget(self.editor_link_button)
        self.server_links.setVisible(False)
        outer.addWidget(self.server_links)

        outer.addWidget(self._build_library_panel(), 1)
        outer.addWidget(self._build_workflow_panel())
        outer.addWidget(self._build_run_panel())

        self.activity_toggle = QPushButton("Show activity")
        self.activity_toggle.setToolTip("Show or hide progress, errors, and server activity.")
        self.activity_toggle.setCheckable(True)
        self.activity_toggle.toggled.connect(self._toggle_activity)
        outer.addWidget(self.activity_toggle, alignment=Qt.AlignmentFlag.AlignLeft)
        self.activity_box = QGroupBox("Activity")
        activity_layout = QVBoxLayout(self.activity_box)
        activity_layout.setContentsMargins(12, 12, 12, 12)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(3000)
        self.log_view.setPlaceholderText(
            "Server requests, build progress and errors will appear here."
        )
        self.log_view.setToolTip("Read-only progress, error, and server activity messages.")
        self.log_view.setFont(QFont("monospace", 10))
        self.log_view.setMaximumHeight(150)
        activity_layout.addWidget(self.log_view)
        self.activity_box.setVisible(False)
        outer.addWidget(self.activity_box)
        scroll = QScrollArea()
        scroll.setObjectName("mainScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(root)
        self._main_scroll = scroll
        self.setCentralWidget(scroll)

        self._build_options_dialog()
        for button in self.findChildren(QAbstractButton):
            if not button.toolTip():
                button.setToolTip(button.text().replace("&", "") or "Activate this control.")
        self.run_button.clicked.connect(self._handle_run_button)
        self.offline_button.clicked.connect(self._fetch_offline_assets)
        self.index_cache.textChanged.connect(self._update_server_setup)
        self.tile_cache.textChanged.connect(self._update_server_setup)

    def _build_library_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("stepCard")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)
        layout.addWidget(_label("STEP 1  ·  Choose your trip folders", "stepNumber"))
        layout.addWidget(_label("Add one or more photo folders. You can set a time zone for each trip.", "muted"))
        self.trip_table = QTableWidget(0, 2)
        self.trip_table.setHorizontalHeaderLabels(["Trip folder", "Time zone"])
        self.trip_table.setToolTip(
            "Each row is one trip. Select its time zone in the second column."
        )
        self.trip_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.trip_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.trip_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.trip_table.setAlternatingRowColors(True)
        self.trip_table.verticalHeader().setVisible(False)
        header = self.trip_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        self.trip_table.setColumnWidth(1, 300)
        self.trip_table.setMinimumHeight(155)
        self.trip_table.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        layout.addWidget(self.trip_table, 1)

        buttons = QHBoxLayout()
        self.add_trip_button = QPushButton("Add folders")
        self.add_trip_button.setToolTip("Add one or more trip folders to the library.")
        self.remove_trip_button = QPushButton("Remove")
        self.remove_trip_button.setToolTip("Remove the selected folders from the library; files are not deleted.")
        self.trip_count = _label("0 folders", "muted")
        buttons.addWidget(self.add_trip_button)
        buttons.addWidget(self.remove_trip_button)
        buttons.addStretch(1)
        buttons.addWidget(self.trip_count)
        layout.addLayout(buttons)

        self.add_trip_button.clicked.connect(self._add_trip)
        self.remove_trip_button.clicked.connect(self._remove_trips)
        self.trip_table.itemSelectionChanged.connect(self._update_trip_selection)
        return panel

    def _build_workflow_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("stepCard")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)
        layout.addWidget(_label("STEP 2  ·  Choose what to do", "stepNumber"))

        choices = QHBoxLayout()
        choices.setSpacing(12)
        self.workflow_group = QButtonGroup(self)
        self.live_radio = QRadioButton("Run a live server")
        self.live_radio.setToolTip("Open and browse your trips in a web browser on this computer.")
        self.site_radio = QRadioButton("Create a static site")
        self.site_radio.setToolTip("Create a standalone website folder that you can share or copy.")
        for radio, title, description in (
            (self.live_radio, "Live server", "Browse your trips now; keep AlbumMaker running."),
            (self.site_radio, "Static site", "Create a folder you can share or publish."),
        ):
            card = QFrame()
            card.setObjectName("workflowChoice")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(14, 12, 14, 12)
            radio.setText(title)
            self.workflow_group.addButton(radio)
            card_layout.addWidget(radio)
            description_label = _label(description, "muted")
            description_label.setWordWrap(True)
            card_layout.addWidget(description_label)
            choices.addWidget(card, 1)
        self.live_radio.setChecked(True)
        layout.addLayout(choices)

        self.output_row = QWidget()
        output_layout = QHBoxLayout(self.output_row)
        output_layout.setContentsMargins(0, 0, 0, 0)
        self.output_dir = QLineEdit()
        self.output_dir.setToolTip("Folder where the static site will be created.")
        output_layout.addWidget(_label("Save site to"))
        output_layout.addWidget(self.output_dir, 1)
        browse_output = QPushButton("Browse…")
        browse_output.setToolTip("Choose the folder where the static site will be created.")
        browse_output.clicked.connect(self._browse_output)
        output_layout.addWidget(browse_output)
        layout.addWidget(self.output_row)
        self.output_row.setVisible(False)

        self.site_map_note = _label(
            "Note: the maps in a static site only work when the site is opened through a web server "
            "(http:// or https://). OpenStreetMap blocks map tiles when the page is opened directly "
            "from disk (file://), such as by double-clicking index.html. Publish the folder to a web "
            "host, or see Help for how to test locally.",
            "muted",
        )
        self.site_map_note.setWordWrap(True)
        self.site_map_note.setToolTip(
            "Browsers send no Referer header for file:// pages, and OpenStreetMap's tile servers "
            "refuse requests without one."
        )
        layout.addWidget(self.site_map_note)
        self.site_map_note.setVisible(False)

        self.live_radio.toggled.connect(self._update_workflow)
        self.site_radio.toggled.connect(self._update_workflow)
        return panel

    def _build_run_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("stepCard")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)
        layout.addWidget(_label("STEP 3  ·  Run", "stepNumber"))
        row = QHBoxLayout()
        self.run_button = QPushButton("Start live server")
        self.run_button.setObjectName("primary")
        self.run_button.setMinimumHeight(42)
        row.addWidget(self.run_button)
        row.addStretch(1)
        self.workflow_status = _label("Add a trip folder to get started.", "muted")
        row.addWidget(self.workflow_status, 1)
        layout.addLayout(row)
        return panel

    def _build_options_dialog(self) -> None:
        self.options_dialog = QDialog(self)
        self.options_dialog.setWindowTitle("AlbumMaker options")
        self.options_dialog.resize(760, 700)
        layout = QVBoxLayout(self.options_dialog)
        intro = _label("Optional settings. Most people can use the defaults.", "muted")
        layout.addWidget(intro)
        self.options_tabs = QTabWidget()
        self.options_tabs.addTab(self._general_tab(), "General")
        self.options_tabs.addTab(self._server_tab(), "Live server")
        self.options_tabs.addTab(self._build_tab(), "Static site")
        layout.addWidget(self.options_tabs, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.options_dialog.reject)
        buttons.accepted.connect(self.options_dialog.accept)
        layout.addWidget(buttons)

    def _general_tab(self) -> QWidget:
        appearance_box, appearance_form = _group("Appearance")
        self.theme_combo = QComboBox()
        self.theme_combo.addItem("Light", "light")
        self.theme_combo.addItem("Dark", "dark")
        self.theme_combo.setToolTip("Choose the color theme used by the desktop app.")
        self.theme_combo.currentIndexChanged.connect(self._apply_theme)
        self.theme_combo.currentIndexChanged.connect(self._schedule_save)
        appearance_form.addRow("Color theme", self.theme_combo)

        library_box, library_form = _group("Trip library page")
        self.library_title = QLineEdit()
        self.library_title.setToolTip(
            "Title shown on the all-trips overview page in both the live server and static sites."
        )
        self.library_title.setPlaceholderText("Trips")
        library_form.addRow("Overview page title", self.library_title)
        library_help = _label(
            "Shown as the heading and browser-tab title when the trip library is displayed.",
            "muted",
        )
        library_help.setWordWrap(True)
        library_form.addRow("", library_help)

        time_box, time_form = _group("Video time handling")
        self.tz_offset = QLineEdit()
        self.tz_offset.setPlaceholderText("Use automatic handling")
        self.tz_offset.setValidator(QDoubleValidator(-24.0, 24.0, 2, self.tz_offset))
        self.tz_offset.setToolTip(
            "Optional fixed UTC offset in hours for videos in trips set to Automatic. "
            "This applies to live server and static-site generation."
        )
        time_form.addRow("Default video UTC offset", self.tz_offset)
        time_help = _label(
            "Enter hours east of UTC (for example 2 or -5.5). A time zone set on a trip takes precedence.",
            "muted",
        )
        time_help.setWordWrap(True)
        time_form.addRow("", time_help)

        media_box, media_form = _group("Shared trip processing")
        self.thumb_size = QSpinBox()
        self.thumb_size.setRange(16, 4096)
        self.thumb_size.setSuffix(" px")
        self.thumb_size.setToolTip(
            "Base thumbnail size used by both the live server and static sites."
        )
        media_form.addRow("Thumbnail size", self.thumb_size)
        self.date_start = QLineEdit()
        self.date_start.setPlaceholderText("YYYY-MM-DD")
        self.date_start.setToolTip("Optional first date to include in every trip (YYYY-MM-DD).")
        self.date_end = QLineEdit()
        self.date_end.setPlaceholderText("YYYY-MM-DD")
        self.date_end.setToolTip("Optional last date to include in every trip (YYYY-MM-DD).")
        date_row = QWidget()
        dates = QHBoxLayout(date_row)
        dates.setContentsMargins(0, 0, 0, 0)
        dates.addWidget(self.date_start)
        dates.addWidget(_label("to", "muted"))
        dates.addWidget(self.date_end)
        media_form.addRow("Optional date range", date_row)

        behavior_box, behavior_form = _group("Shared import behavior")
        self.no_dedupe = QCheckBox("Keep exact duplicate files")
        self.no_dedupe.setToolTip("Include byte-identical copies that would otherwise be skipped.")
        behavior_form.addRow("", self.no_dedupe)
        self.no_live_photos = QCheckBox("Show Live Photo video clips separately")
        self.no_live_photos.setToolTip("List paired Live Photo clips as videos instead of attaching them to photos.")
        behavior_form.addRow("", self.no_live_photos)
        self.no_date_check = QCheckBox("Trust all captured dates")
        self.no_date_check.setToolTip(
            "Skip suspicious-date checks. The optional date range is ignored while this is enabled."
        )
        behavior_form.addRow("", self.no_date_check)
        self.no_gpx = QCheckBox("Ignore GPS tracks")
        self.no_gpx.setToolTip("Disable matching media to GPX tracks and drawing GPS routes.")
        behavior_form.addRow("", self.no_gpx)
        self.no_places = QCheckBox("Hide offline place names")
        self.no_places.setToolTip(
            "Leave out nearest-town names: under day dates, on day and trip cards, in the "
            "photo details panel and in suggested day titles."
        )
        behavior_form.addRow("", self.no_places)

        gpx_box, gpx_form = _group("Additional GPS tracks")
        self.gpx_files = QPlainTextEdit()
        self.gpx_files.setPlaceholderText("Optional: one .gpx file or folder path per line")
        self.gpx_files.setToolTip("These tracks are used by both workflows and ignored if GPS tracks are disabled.")
        self.gpx_files.setMaximumHeight(90)
        gpx_form.addRow("GPX paths", self.gpx_files)
        return _scroll_page(appearance_box, library_box, time_box, media_box, behavior_box, gpx_box)

    def _server_tab(self) -> QWidget:
        setup_box = QGroupBox("One-time server setup")
        setup_layout = QVBoxLayout(setup_box)
        setup_intro = _label(
            "The two cache folders are required for the live server and are created if needed. "
            "Missing offline assets download automatically the first time you start the server. "
            "Static sites do not require these server setup steps.",
            "muted",
        )
        setup_intro.setWordWrap(True)
        setup_layout.addWidget(setup_intro)
        cache_form = QFormLayout()
        self.index_cache = QLineEdit()
        self.index_cache.setPlaceholderText("Choose where AlbumMaker should store the metadata index")
        self.index_cache.setToolTip(
            "Required for the live server. Stores reusable metadata indexes, not copies of your media."
        )
        cache_form.addRow("Metadata cache folder", self._path_field(self.index_cache, directory=True))
        self.tile_cache = QLineEdit()
        self.tile_cache.setPlaceholderText("Choose where viewed map tiles should be saved")
        self.tile_cache.setToolTip(
            "Required for the live server. Saves viewed map tiles for faster repeat loads and offline use."
        )
        cache_form.addRow("Map tile cache folder", self._path_field(self.tile_cache, directory=True))
        setup_layout.addLayout(cache_form)
        self.metadata_cache_help = _label(
            "Stores a small index of file size, modified time and extracted metadata (capture date, "
            "GPS, camera and video details), not copies of your photos. Reusing it makes later starts "
            "faster when files have not changed.",
            "muted",
        )
        self.metadata_cache_help.setWordWrap(True)
        setup_layout.addWidget(self.metadata_cache_help)

        offline_row = QHBoxLayout()
        self.offline_status = _label("", "status")
        self.offline_status.setWordWrap(True)
        offline_row.addWidget(self.offline_status, 1)
        self.offline_button = QPushButton("Download offline assets")
        self.offline_button.setToolTip(
            "Download or refresh the local map library and fonts. Missing assets download automatically when the server starts."
        )
        offline_row.addWidget(self.offline_button)
        setup_layout.addLayout(offline_row)
        self.offline_location = _label(
            f"Offline map assets are stored in {VENDOR_DIR}.", "muted"
        )
        self.offline_location.setWordWrap(True)
        setup_layout.addWidget(self.offline_location)
        self.server_setup_status = _label("", "muted")
        self.server_setup_status.setWordWrap(True)
        setup_layout.addWidget(self.server_setup_status)

        server_box, form = _group("Live server options")
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setToolTip("TCP port used by the local web server.")
        form.addRow("Port", self.port)
        self.memory_cache = QSpinBox()
        self.memory_cache.setRange(16, 65536)
        self.memory_cache.setSuffix(" MB")
        self.memory_cache.setToolTip("Maximum memory used to cache generated thumbnails and converted photos.")
        form.addRow("Image cache", self.memory_cache)
        self.server_large_thumb_size = QSpinBox()
        self.server_large_thumb_size.setRange(0, 4096)
        self.server_large_thumb_size.setSpecialValueText("Off")
        self.server_large_thumb_size.setSuffix(" px")
        self.server_large_thumb_size.setToolTip(
            "Maximum width or height for larger previews. 2400 pixels is recommended for Full HD and 4K displays."
        )
        form.addRow("High-resolution thumbnail", self.server_large_thumb_size)

        self.lan = QCheckBox("Allow other devices on my network to connect")
        self.lan.setToolTip("Anyone on your local network can access the site; there is no login.")
        form.addRow("", self.lan)
        self.read_only = QCheckBox("Disable all in-page editing")
        self.read_only.setToolTip("Prevent changes to captions, favorites, hidden photos, covers, titles, and descriptions.")
        form.addRow("", self.read_only)
        self.editor_link_only = QCheckBox("Require the editor link for editing")
        self.editor_link_only.setToolTip(
            "Useful when using a reverse proxy or tunnel. Editing requires the link printed at startup."
        )
        form.addRow("", self.editor_link_only)
        self.verbose = QCheckBox("Show every request in the activity log")
        self.verbose.setToolTip("Show all HTTP requests in Activity instead of only errors and slow requests.")
        form.addRow("", self.verbose)
        network_note = _label(
            "LAN access has no login protection. Only allow other devices on a trusted home network.",
            "muted",
        )
        network_note.setWordWrap(True)

        maps_box, maps_form = _group("Maps and network (optional)")
        self.tile_url = QLineEdit()
        self.tile_url.setPlaceholderText("Use the default map provider")
        self.tile_url.setToolTip("Optional custom URL template must include {z}, {x}, and {y}.")
        maps_form.addRow("Custom map URL", self.tile_url)
        self.allow_hosts = QPlainTextEdit()
        self.allow_hosts.setPlaceholderText("Leave blank unless you use a custom local host name")
        self.allow_hosts.setToolTip("Optional additional host names the live server may answer to, one per line.")
        self.allow_hosts.setMaximumHeight(65)
        maps_form.addRow("Extra allowed host names", self.allow_hosts)

        return _scroll_page(setup_box, server_box, maps_box, network_note)

    def _build_tab(self) -> QWidget:
        media_box, media_form = _group("Static site media")
        self.large_thumb_size = QSpinBox()
        self.large_thumb_size.setRange(0, 4096)
        self.large_thumb_size.setSpecialValueText("Off")
        self.large_thumb_size.setSuffix(" px")
        self.large_thumb_size.setToolTip(
            "Maximum width or height for larger generated previews. 2400 pixels is recommended for Full HD and 4K displays."
        )
        media_form.addRow("High-resolution thumbnail", self.large_thumb_size)
        self.workers = QSpinBox()
        self.workers.setRange(0, 256)
        self.workers.setSpecialValueText("Automatic")
        self.workers.setToolTip("Number of files processed in parallel. Automatic uses available CPU cores.")
        media_form.addRow("Parallel workers", self.workers)
        self.force = QCheckBox("Regenerate all static site media")
        self.force.setToolTip(
            "Rebuild all generated thumbnails and media copies instead of reusing unchanged output."
        )
        media_form.addRow("", self.force)
        return _scroll_page(media_box)

    def _path_field(self, line_edit: QLineEdit, directory: bool) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(line_edit, 1)
        button = QPushButton("Browse…")
        button.setToolTip(
            "Choose a folder for this setting." if directory else "Choose a file for this setting."
        )
        if directory:
            button.clicked.connect(lambda: self._browse_directory(line_edit))
        else:
            button.clicked.connect(lambda: self._browse_file(line_edit))
        layout.addWidget(button)
        return row

    def _load_ui(self) -> None:
        for trip in self.settings["trips"]:
            self._add_trip_item(trip["path"], trip["timezone"])
        self.output_dir.setText(self.settings["output_dir"])
        self.theme_combo.setCurrentIndex(
            max(0, self.theme_combo.findData(self.settings["appearance"]["theme"]))
        )
        self.library_title.setText(self.settings["library"]["title"])
        server = self.settings["server"]
        self.port.setValue(int(server["port"]))
        self.memory_cache.setValue(int(server["memory_cache_mb"]))
        self.server_large_thumb_size.setValue(int(server["large_thumb_size"]))
        self.lan.setChecked(bool(server["lan"]))
        self.read_only.setChecked(bool(server["read_only"]))
        self.editor_link_only.setChecked(bool(server["editor_link_only"]))
        self.verbose.setChecked(bool(server["verbose"]))

        build = self.settings["build"]
        self.thumb_size.setValue(int(build["thumb_size"]))
        self.large_thumb_size.setValue(int(build["large_thumb_size"]))
        self.tz_offset.setText(str(build["tz_offset"]))
        self.workers.setValue(int(build["workers"] or 0))
        self.date_start.setText(str(build["date_range_start"]))
        self.date_end.setText(str(build["date_range_end"]))
        self.no_dedupe.setChecked(bool(build["no_dedupe"]))
        self.no_live_photos.setChecked(bool(build["no_live_photos"]))
        self.no_date_check.setChecked(bool(build["no_date_check"]))
        self.no_gpx.setChecked(bool(build["no_gpx"]))
        self.no_places.setChecked(bool(build["no_places"]))
        self.force.setChecked(bool(build["force"]))
        self.gpx_files.setPlainText("\n".join(build["gpx_files"]))

        advanced = self.settings["advanced"]
        self.tile_cache.setText(
            str(advanced["tile_cache"] or default_settings()["advanced"]["tile_cache"])
        )
        self.tile_url.setText(str(advanced["tile_url"]))
        self.index_cache.setText(
            str(advanced["index_cache"] or default_settings()["advanced"]["index_cache"])
        )
        self.allow_hosts.setPlainText("\n".join(advanced["allow_hosts"]))
        self._update_date_controls()
        self.gpx_files.setEnabled(not self.no_gpx.isChecked())
        self._update_trip_count()
        self._update_server_setup()
        self._update_workflow()

    def _connect_persistence(self) -> None:
        for widget in (
            self.output_dir, self.library_title, self.tz_offset, self.date_start, self.date_end,
            self.tile_cache, self.tile_url, self.index_cache,
        ):
            widget.textChanged.connect(self._schedule_save)
        self.output_dir.textChanged.connect(self._update_workflow)
        for widget in (
            self.port, self.memory_cache, self.server_large_thumb_size,
            self.thumb_size, self.large_thumb_size, self.workers,
        ):
            widget.valueChanged.connect(self._schedule_save)
        for widget in (
            self.lan, self.read_only, self.editor_link_only, self.verbose,
            self.no_dedupe, self.no_live_photos, self.no_date_check, self.no_gpx,
            self.no_places, self.force,
        ):
            widget.toggled.connect(self._schedule_save)
        self.gpx_files.textChanged.connect(self._schedule_save)
        self.allow_hosts.textChanged.connect(self._schedule_save)
        self.no_date_check.toggled.connect(self._update_date_controls)
        self.no_gpx.toggled.connect(lambda checked: self.gpx_files.setEnabled(not checked))

    def _update_date_controls(self, *_args) -> None:
        enabled = not self.no_date_check.isChecked()
        self.date_start.setEnabled(enabled)
        self.date_end.setEnabled(enabled)

    def _add_trip_item(self, path: str, timezone_name: str = "") -> None:
        self._insert_trip_item(self.trip_table.rowCount(), path, timezone_name)

    def _insert_trip_item(self, row: int, path: str, timezone_name: str = "") -> None:
        folder = Path(path).expanduser()
        self.trip_table.insertRow(row)
        trip_item = QTableWidgetItem(folder.name or str(folder))
        trip_item.setFlags(trip_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        trip_item.setData(Qt.ItemDataRole.UserRole, str(folder))
        trip_item.setToolTip(
            f"{folder}\nFolder not found" if not folder.is_dir() else str(folder)
        )
        self.trip_table.setItem(row, 0, trip_item)

        zone_combo = QComboBox()
        zone_combo.setEditable(True)
        zone_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        zone_combo.addItem("Automatic", "")
        for label, zone_name in timezone_search_choices():
            zone_combo.addItem(label, zone_name)
        if timezone_name:
            zone_combo.setCurrentIndex(zone_combo.findData(timezone_name))
        zone_combo.setProperty("timezoneValue", zone_combo.currentData() or "")
        zone_combo.setMaxVisibleItems(20)
        zone_combo.lineEdit().setPlaceholderText("Search or choose a time zone")
        completer = QCompleter(zone_combo.model(), zone_combo)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        zone_combo.setCompleter(completer)
        zone_combo.setToolTip(
            "Search by typing a region or city, or open the dropdown to browse all IANA time zones. "
            "Automatic does not infer a trip location."
        )
        zone_combo.currentIndexChanged.connect(
            lambda *_args, combo=zone_combo: self._on_timezone_changed(combo)
        )
        zone_combo.activated.connect(
            lambda *_args, combo=zone_combo: self._on_timezone_changed(combo)
        )
        self.trip_table.setCellWidget(row, 1, zone_combo)
        self.trip_table.setRowHeight(row, 34)

    def _on_timezone_changed(self, combo: QComboBox) -> None:
        if combo.currentIndex() < 0:
            exact_match = combo.findData(combo.currentText())
            if exact_match >= 0:
                combo.setCurrentIndex(exact_match)
        if combo.currentIndex() >= 0:
            combo.setProperty("timezoneValue", combo.currentData() or "")
        position = combo.mapTo(self.trip_table.viewport(), combo.rect().center())
        index = self.trip_table.indexAt(position)
        if index.isValid():
            self.trip_table.selectRow(index.row())
        self._schedule_save()

    def _trip_paths(self) -> list[str]:
        return [
            str(self.trip_table.item(row, 0).data(Qt.ItemDataRole.UserRole))
            for row in range(self.trip_table.rowCount())
        ]

    def _add_trip(self) -> None:
        dialog = QFileDialog(self, "Choose trip folders", str(Path.home()))
        dialog.setFileMode(QFileDialog.FileMode.Directory)
        dialog.setOption(QFileDialog.Option.ShowDirsOnly, True)
        dialog.setOption(QFileDialog.Option.DontUseNativeDialog, True)
        dialog.setLabelText(QFileDialog.DialogLabel.Accept, "Add selected folders")
        dialog.setLabelText(
            QFileDialog.DialogLabel.FileName,
            "Select folders (Ctrl/Command or Shift-click for multiple)",
        )
        for view in dialog.findChildren(QAbstractItemView):
            view.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        selected_paths = dialog.selectedFiles()
        existing = {str(Path(path).expanduser().resolve()) for path in self._trip_paths()}
        added = 0
        skipped = 0
        for selected in selected_paths:
            selected_path = str(Path(selected).resolve())
            if selected_path in existing:
                skipped += 1
                continue
            self._add_trip_item(selected_path)
            existing.add(selected_path)
            added += 1
        self._update_trip_count()
        self._schedule_save()
        if skipped:
            QMessageBox.information(
                self, "Folders already in library",
                f"Added {added} folder(s). Skipped {skipped} folder(s) already in the library.",
            )

    def _remove_trips(self) -> None:
        rows = sorted(
            {index.row() for index in self.trip_table.selectionModel().selectedRows()},
            reverse=True,
        )
        for row in rows:
            self.trip_table.removeRow(row)
        self._update_trip_count()
        self._schedule_save()

    def _update_trip_selection(self) -> None:
        self.remove_trip_button.setEnabled(bool(self.trip_table.selectionModel().selectedRows()))

    def _update_trip_count(self) -> None:
        count = self.trip_table.rowCount()
        self.trip_count.setText(f"{count} folder" + ("" if count == 1 else "s"))
        self._update_trip_selection()
        self._update_workflow()

    def _toggle_activity(self, visible: bool) -> None:
        self.activity_box.setVisible(visible)
        self.activity_toggle.setText("Hide activity" if visible else "Show activity")
        if visible:
            # Let the layout take the new box into account before measuring.
            QTimer.singleShot(0, self._fit_height_to_content)

    def _available_screen_rect(self):
        screen = self.screen() or QApplication.primaryScreen()
        return screen.availableGeometry() if screen is not None else None

    def _fit_to_screen(self) -> None:
        """Shrink the window (and move it) so it fits on the screen it is on."""
        available = self._available_screen_rect()
        if available is None or self.isMaximized() or self.isFullScreen():
            return
        frame = self.frameGeometry()
        extra_w = frame.width() - self.width()
        extra_h = frame.height() - self.height()
        width = min(self.width(), available.width() - extra_w)
        height = min(self.height(), available.height() - extra_h)
        if (width, height) != (self.width(), self.height()):
            self.resize(max(width, self.minimumWidth()), max(height, self.minimumHeight()))
        frame = self.frameGeometry()
        x = min(max(frame.left(), available.left()), available.right() - frame.width() + 1)
        y = min(max(frame.top(), available.top()), available.bottom() - frame.height() + 1)
        if (x, y) != (frame.left(), frame.top()):
            self.move(max(x, available.left()), max(y, available.top()))

    def _fit_height_to_content(self) -> None:
        """Grow the window so everything, including Activity, is visible
        without scrolling, as far as the screen allows."""
        if self.isMaximized() or self.isFullScreen():
            return
        content = self._main_scroll.widget()
        missing = content.sizeHint().height() - self._main_scroll.viewport().height()
        if missing <= 0:
            return
        self.resize(self.width(), self.height() + missing)
        self._fit_to_screen()
        if self.activity_box.isVisible():
            self._main_scroll.ensureWidgetVisible(self.activity_box, 0, 0)

    def _apply_theme(self, *_args) -> None:
        if not hasattr(self, "theme_combo"):
            return
        theme = self.theme_combo.currentData() or "light"
        application = QApplication.instance()
        if application is not None:
            if not hasattr(self, "_default_palette"):
                self._default_palette = QPalette(application.palette())
            application.setPalette(
                _dark_palette() if theme == "dark" else self._default_palette
            )
            application.setStyleSheet(THEME_STYLES[theme])

    def _offline_assets_ready(self) -> bool:
        self._offline_asset_error = ""
        try:
            return vendor_ready()
        except OSError as exc:
            self._offline_asset_error = str(exc)
            return False

    def _open_server_link(self, url: str) -> None:
        if url and not QDesktopServices.openUrl(QUrl(url)):
            self._append_log(f"Could not open this link in a browser: {url}")
            self.status_label.setText("Could not open browser link")

    def _set_run_button(self, text: str, mode: str, tooltip: str) -> None:
        changed_style = self.run_button.objectName() != mode
        self.run_button.setText(text)
        self.run_button.setToolTip(tooltip)
        if changed_style:
            self.run_button.setObjectName(mode)
            self.run_button.style().unpolish(self.run_button)
            self.run_button.style().polish(self.run_button)

    def _update_workflow(self, *_args) -> None:
        is_site = self.site_radio.isChecked()
        self.output_row.setVisible(is_site)
        self.site_map_note.setVisible(is_site)

        running = bool(
            self._process and self._process.state() != QProcess.ProcessState.NotRunning
        )
        if running:
            action = "Stop server" if self._process_kind == "server" else "Stop task"
            if self._stopping:
                self._set_run_button("Stopping…", "stop", "The running process is stopping.")
                self.run_button.setEnabled(False)
            else:
                self._set_run_button(
                    action, "stop", "Stop the live server." if self._process_kind == "server"
                    else "Stop the running task."
                )
                self.run_button.setEnabled(True)
            if self._process_kind == "server":
                status = (
                    "The live server is running."
                    if self._server_ready
                    else "Waiting for the live server to become ready…"
                )
            else:
                status = f"{_task_name(self._process_kind)} is in progress."
            self.workflow_status.setText(status)
            return

        self._set_run_button(
            "Create static site" if is_site else "Start live server",
            "primary",
            "Create the static site in the selected output folder."
            if is_site else "Start the live trip server."
        )
        has_trips = self.trip_table.rowCount() > 0
        if not has_trips:
            self.workflow_status.setText("Add a trip folder to get started.")
            self.run_button.setEnabled(False)
            return

        if is_site:
            ready = bool(self.output_dir.text().strip())
            self.workflow_status.setText(
                "Ready to create your site." if ready else "Choose where to save your site."
            )
        else:
            ready = bool(self.index_cache.text().strip()) and bool(self.tile_cache.text().strip())
            if not ready:
                self.workflow_status.setText("Choose the required cache folders in Options → Live server.")
            elif self._offline_assets_ready():
                self.workflow_status.setText("Live server setup is ready.")
            elif self._offline_asset_error:
                self.workflow_status.setText(self._offline_asset_error)
            else:
                self.workflow_status.setText(
                    "Offline assets will download automatically when you start the server."
                )
        self.run_button.setEnabled(ready)

    def _browse_output(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self, "Choose output folder", self.output_dir.text() or str(Path.home())
        )
        if selected:
            self.output_dir.setText(selected)

    def _browse_directory(self, target: QLineEdit) -> None:
        selected = QFileDialog.getExistingDirectory(self, "Choose a folder", target.text() or str(Path.home()))
        if selected:
            target.setText(selected)

    def _browse_file(self, target: QLineEdit) -> None:
        selected, _ = QFileDialog.getOpenFileName(self, "Choose a cache file", target.text())
        if selected:
            target.setText(selected)

    def _settings_from_ui(self) -> dict:
        settings = {
            "version": self.settings["version"],
            "appearance": {"theme": self.theme_combo.currentData()},
            "library": {"title": self.library_title.text().strip() or "Trips"},
            "trips": [
                {
                    "path": str(self.trip_table.item(row, 0).data(Qt.ItemDataRole.UserRole)),
                    "timezone": self.trip_table.cellWidget(row, 1).property("timezoneValue") or "",
                }
                for row in range(self.trip_table.rowCount())
            ],
            "output_dir": self.output_dir.text().strip(),
            "server": {
                "port": self.port.value(),
                "large_thumb_size": self.server_large_thumb_size.value(),
                "lan": self.lan.isChecked(),
                "read_only": self.read_only.isChecked(),
                "editor_link_only": self.editor_link_only.isChecked(),
                "memory_cache_mb": self.memory_cache.value(),
                "verbose": self.verbose.isChecked(),
            },
            "build": {
                "thumb_size": self.thumb_size.value(),
                "large_thumb_size": self.large_thumb_size.value(),
                "tz_offset": self.tz_offset.text().strip(),
                "workers": self.workers.value() or "",
                "no_dedupe": self.no_dedupe.isChecked(),
                "no_live_photos": self.no_live_photos.isChecked(),
                "no_date_check": self.no_date_check.isChecked(),
                "date_range_start": self.date_start.text().strip(),
                "date_range_end": self.date_end.text().strip(),
                "no_gpx": self.no_gpx.isChecked(),
                "no_places": self.no_places.isChecked(),
                "force": self.force.isChecked(),
                "gpx_files": [line.strip() for line in self.gpx_files.toPlainText().splitlines()
                              if line.strip()],
            },
            "advanced": {
                "tile_cache": self.tile_cache.text().strip(),
                "tile_url": self.tile_url.text().strip(),
                "index_cache": self.index_cache.text().strip(),
                "allow_hosts": [line.strip() for line in self.allow_hosts.toPlainText().splitlines()
                                if line.strip()],
            },
        }
        return settings

    def _show_help(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("AlbumMaker help")
        dialog.resize(720, 700)
        layout = QVBoxLayout(dialog)
        content = QTextBrowser()
        dark = self.theme_combo.currentData() == "dark"
        background, foreground, muted, code_background = (
            ("#202633", "#e6eaf2", "#a2adbf", "#293344")
            if dark
            else ("#ffffff", "#17243b", "#66748a", "#edf2f9")
        )
        content.setHtml(
            "<html><head><style>"
            f"body {{ background-color: {background}; color: {foreground}; "
            "font-family: sans-serif; line-height: 1.45; } "
            f"h2, h3, li, p {{ color: {foreground}; }} "
            f"code {{ background-color: {code_background}; color: {foreground}; "
            "padding: 2px 4px; border-radius: 3px; } "
            f".muted {{ color: {muted}; }}"
            "</style></head><body>"
            + _help_html()
            + '<p class="muted"><small>'
            + _escape_html(_about_text()).replace("\n", "<br>")
            + "</small></p>"
            + "</body></html>"
        )
        content.setOpenExternalLinks(True)
        layout.addWidget(content)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.setToolTip("Close the help window.")
        buttons.rejected.connect(dialog.reject)
        buttons.accepted.connect(dialog.accept)
        layout.addWidget(buttons)
        dialog.exec()

    def _open_link(self, url: str) -> None:
        if not QDesktopServices.openUrl(QUrl(url)):
            QApplication.clipboard().setText(url)
            QMessageBox.information(
                self, "Open this page",
                f"Your browser could not be opened. The address was copied to the "
                f"clipboard:\n{url}",
            )

    def _show_support(self) -> None:
        """A friendly page with the donation links. Never shown unless asked for."""
        dialog = QDialog(self)
        dialog.setWindowTitle("Support AlbumMaker")
        dialog.setMinimumWidth(460)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(28, 24, 28, 20)
        layout.setSpacing(12)

        heart = _label("\u2665", "supportHeart")
        heart.setStyleSheet("font-size: 40px;")   # the app style sheet sets label fonts
        heart.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(heart)
        title = _label("Enjoying AlbumMaker?")
        title.setStyleSheet("font-size: 20px; font-weight: 700;")
        title.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(title)
        text = _label(SUPPORT_TEXT)
        text.setWordWrap(True)
        text.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(text)

        buttons_row = QHBoxLayout()
        buttons_row.setSpacing(10)
        kofi = QPushButton("\u2615 Buy me a coffee on Ko-fi")
        kofi.setObjectName("kofi")
        kofi.setToolTip(f"One-off donation, no account needed: {KOFI_URL}")
        kofi.clicked.connect(lambda: self._open_link(KOFI_URL))
        sponsors = QPushButton("\u2665 Sponsor on GitHub")
        sponsors.setObjectName("sponsors")
        sponsors.setToolTip(f"One-off or monthly, with your GitHub account: {GITHUB_SPONSORS_URL}")
        sponsors.clicked.connect(lambda: self._open_link(GITHUB_SPONSORS_URL))
        buttons_row.addStretch(1)
        buttons_row.addWidget(kofi)
        buttons_row.addWidget(sponsors)
        buttons_row.addStretch(1)
        layout.addSpacing(4)
        layout.addLayout(buttons_row)
        layout.addSpacing(4)

        more = _label(
            "Not in a position to donate? A star on "
            '<a href="https://github.com/pajotsetrapper/AlbumMaker">GitHub</a>, a bug report or '
            "telling a friend helps too. Thank you!", "muted"
        )
        more.setWordWrap(True)
        more.setOpenExternalLinks(True)
        more.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(more)
        fine = _label(SUPPORT_FINE_PRINT, "muted")
        fine.setWordWrap(True)
        fine.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        fine.setStyleSheet("font-size: 12px;")
        layout.addWidget(fine)

        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.setToolTip("Close this window.")
        close.rejected.connect(dialog.reject)
        layout.addWidget(close)
        dialog.exec()

    def _update_ffmpeg_button(self) -> None:
        """Show the Install FFmpeg button only while FFmpeg cannot be found."""
        from ..ffmpeg_setup import ffmpeg_missing
        self.ffmpeg_button.setVisible(ffmpeg_missing())

    def changeEvent(self, event) -> None:
        # FFmpeg may have been installed outside AlbumMaker (for example in a
        # terminal), so check again whenever the window comes back to the front.
        if event.type() == QEvent.Type.ActivationChange and self.isActiveWindow():
            self._update_ffmpeg_button()
        super().changeEvent(event)

    def _offer_ffmpeg_install(self, force: bool = False) -> None:
        """Ask before installing FFmpeg, which AlbumMaker uses but does not include."""
        from ..ffmpeg_setup import (
            LICENSE_NOTE,
            FFMPEG_DOWNLOAD_URL,
            ffmpeg_missing,
            install_plan,
            manual_instructions,
            mark_declined,
            suggested_command,
            was_declined,
        )
        if not ffmpeg_missing():
            if force:
                QMessageBox.information(self, "FFmpeg", "FFmpeg is already installed.")
            return
        if not force and was_declined():
            return
        plan = install_plan()
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("Install FFmpeg?")
        text = (
            "AlbumMaker uses a separate program called FFmpeg to read video dates and "
            "make video previews. Without it, photos work normally but videos have no "
            "date, location or preview.\n\n" + LICENSE_NOTE
        )
        install_button = download_button = copy_button = None
        if plan is not None:
            text += "\n\nAlbumMaker will run your system's package manager:\n" + " ".join(plan)
            install_button = box.addButton("Install FFmpeg", QMessageBox.ButtonRole.AcceptRole)
        else:
            text += "\n\n" + manual_instructions()
            download_button = box.addButton(
                "Open download page", QMessageBox.ButtonRole.ActionRole
            )
            copy_button = box.addButton("Copy command", QMessageBox.ButtonRole.ActionRole)
        box.setText(text)
        not_now = box.addButton("Not now", QMessageBox.ButtonRole.RejectRole)
        never = box.addButton("Don't ask again", QMessageBox.ButtonRole.DestructiveRole)
        box.setDefaultButton(not_now)
        box.exec()
        clicked = box.clickedButton()
        if install_button is not None and clicked is install_button:
            self._launch("ffmpeg setup", ffmpeg_install_command(sys.executable))
        elif download_button is not None and clicked is download_button:
            if not QDesktopServices.openUrl(QUrl(FFMPEG_DOWNLOAD_URL)):
                QMessageBox.information(self, "FFmpeg", f"Open this page: {FFMPEG_DOWNLOAD_URL}")
        elif copy_button is not None and clicked is copy_button:
            QApplication.clipboard().setText(suggested_command())
            QMessageBox.information(
                self,
                "Command copied",
                f"Copied to the clipboard:\n{suggested_command()}\n\n"
                "Paste it into a terminal, then restart AlbumMaker.",
            )
        elif clicked is never:
            mark_declined()

    def _schedule_save(self, *_args) -> None:
        self._save_timer.start()

    def _save_from_ui(self) -> bool:
        settings = self._settings_from_ui()
        try:
            if self._save_blocked_reason:
                # check them as saving would, but leave the damaged file alone
                validate_settings(settings)
            else:
                save_settings(settings)
        except SettingsError as exc:
            self.log_view.appendPlainText(f"Settings error: {exc}")
            self.status_label.setText("Settings could not be saved")
            return False
        self.settings = settings
        return True

    def _prepare_library(self) -> Path:
        if not self._save_from_ui():
            raise SettingsError(f"Could not persist settings to {settings_path()}.")
        target = settings_path().parent / "gui-trip-library.txt"
        return write_library_file(self.settings["trips"], target)

    def _launch(self, kind: str, arguments: list[str]) -> None:
        if self._process and self._process.state() != QProcess.ProcessState.NotRunning:
            QMessageBox.warning(self, "A task is already running", "Stop the current task before starting another.")
            return
        self.activity_toggle.setChecked(True)
        process = QProcess(self)
        # No console-window tweak is needed here: Qt already starts the child
        # without a console window when this GUI program has none.
        frozen = getattr(sys, "frozen", False)
        # The packaged app's own folder is not a good place to run from; every
        # path the command uses is absolute, so any existing folder will do.
        working_dir = settings_path().parent if frozen else PROJECT_ROOT
        process.setWorkingDirectory(str(working_dir if working_dir.is_dir() else Path.home()))
        environment = QProcessEnvironment.systemEnvironment()
        environment.insert("PYTHONUNBUFFERED", "1")
        if frozen:
            # The helper is another PyInstaller program. Variables the packaged
            # app set for itself must not leak into it, or it can mistake itself
            # for part of this app and not start.
            for name in environment.keys():
                if name.startswith("_PYI_") or name == "_MEIPASS2":
                    environment.remove(name)
            environment.insert("PYINSTALLER_RESET_ENVIRONMENT", "1")
            if sys.platform.startswith("linux"):
                # Give the helper the library path this app was started with,
                # not the one PyInstaller set up for the app's own libraries.
                original = environment.value("LD_LIBRARY_PATH_ORIG", "")
                environment.remove("LD_LIBRARY_PATH_ORIG")
                if original:
                    environment.insert("LD_LIBRARY_PATH", original)
                else:
                    environment.remove("LD_LIBRARY_PATH")
        process.setProcessEnvironment(environment)
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self._process_group = _start_in_own_session(process)
        process.readyReadStandardOutput.connect(self._read_process_output)
        process.started.connect(lambda: self._on_started(kind))
        process.errorOccurred.connect(self._on_process_error)
        process.finished.connect(self._on_finished)
        self._process = process
        self._process_kind = kind
        self._stopping = False
        self._server_ready = False
        self._server_url = ""
        self._editor_url = ""
        self.site_link_button.hide()
        self.editor_link_button.hide()
        self.server_links.hide()
        self._log_remainder = ""
        self._append_log(f"Starting {_task_label(kind)}: {self._display_command(arguments)}")
        self.status_label.setText(f"Starting {_task_label(kind)}…")
        self._update_actions()
        process.start(arguments[0], arguments[1:])

    @staticmethod
    def _display_command(arguments: list[str]) -> str:
        return " ".join(f'"{arg}"' if " " in arg else arg for arg in arguments)

    def _start_server(self) -> None:
        try:
            cache_folder = prepare_metadata_cache(self.index_cache.text())
            self.index_cache.setText(str(cache_folder))
            tile_cache_folder = prepare_tile_cache(self.tile_cache.text())
            self.tile_cache.setText(str(tile_cache_folder))
            library = self._prepare_library()
            arguments = server_command(sys.executable, library, self.settings)
        except (OSError, SettingsError, ValueError, KeyError) as exc:
            QMessageBox.warning(self, "Cannot start server", str(exc))
            return
        port = int(self.settings["server"]["port"])
        if _port_in_use(port):
            QMessageBox.warning(
                self, "Port already in use",
                f"Something is already listening on port {port}. It may be an earlier AlbumMaker "
                f"server that is still running, and the browser would show that one instead of "
                f"this one.\n\nClose it (for example, end any 'albummaker-cli' process in Task "
                f"Manager) or choose another port in Options, then start again.",
            )
            return
        if not self._offline_assets_ready():
            self._pending_server_start = True
            self._append_log("Offline assets are missing; downloading them before server startup.")
            self._launch("offline assets", offline_assets_command(sys.executable))
            return
        self._launch("server", arguments)

    def _build_site(self) -> None:
        try:
            library = self._prepare_library()
            output = self.output_dir.text().strip()
            if not output:
                raise SettingsError("Choose an output folder before generating the site.")
            arguments = build_command(sys.executable, library, output, self.settings)
        except (OSError, SettingsError, ValueError, KeyError) as exc:
            QMessageBox.warning(self, "Cannot generate site", str(exc))
            return
        self._launch("build", arguments)

    def _run_selected_workflow(self) -> None:
        if self.site_radio.isChecked():
            self._build_site()
        else:
            self._start_server()

    def _handle_run_button(self) -> None:
        if self._process and self._process.state() != QProcess.ProcessState.NotRunning:
            self._stop_process()
        else:
            self._run_selected_workflow()

    def _fetch_offline_assets(self) -> None:
        self._launch("offline assets", offline_assets_command(sys.executable))

    def _read_process_output(self) -> None:
        if not self._process:
            return
        data = bytes(self._process.readAllStandardOutput()).decode("utf-8", errors="replace")
        lines = (self._log_remainder + data).splitlines(keepends=True)
        self._log_remainder = ""
        for line in lines:
            if line.endswith(("\n", "\r")):
                message = line.rstrip("\r\n")
                self._append_log(message)
                expected_url = f"http://127.0.0.1:{self.port.value()}/"
                editor_match = re.search(
                    r"Editor link \(keep it private; it changes every start\):\s*(https?://\S+)",
                    message,
                )
                if self._process_kind == "server" and editor_match:
                    self._editor_url = editor_match.group(1)
                if (
                    self._process_kind == "server"
                    and message.strip() == f"Serving at {expected_url}"
                ):
                    self._server_ready = True
                    self._server_url = expected_url
                    self.site_link_button.show()
                    self.editor_link_button.setVisible(bool(self._editor_url))
                    self.server_links.show()
                    self.status_label.setText("Server running")
                    self._update_workflow()
            else:
                self._log_remainder = line

    def _append_log(self, message: str) -> None:
        stamp = datetime.now().astimezone().strftime("%H:%M:%S")
        self.log_view.appendPlainText(f"{stamp}  {message}")

    def _on_started(self, kind: str) -> None:
        states = {
            "server": "Starting server",
            "build": "Generating site",
            "offline assets": "Downloading assets",
        }
        self.status_label.setText(states.get(kind, f"{_task_name(kind)} running"))
        self._update_actions()

    def _on_process_error(self, error: QProcess.ProcessError) -> None:
        if error != QProcess.ProcessError.FailedToStart and self._process and not self._stopping:
            self._append_log(f"Process error: {self._process.errorString()}")
        if error == QProcess.ProcessError.FailedToStart:
            message = self._process.errorString() if self._process else "Could not start the process."
            self._append_log(f"Error: {message}")
            self.status_label.setText("Could not start")
            self.server_links.hide()
            self.site_link_button.hide()
            self.editor_link_button.hide()
            self._server_ready = False
            self._server_url = ""
            self._editor_url = ""
            self._stopping = False
            if self._process_kind == "offline assets":
                self._pending_server_start = False
            self._cleanup_process()
            self._update_actions()

    def _on_finished(self, exit_code: int, _exit_status: QProcess.ExitStatus) -> None:
        self._read_process_output()
        if self._log_remainder:
            self._append_log(self._log_remainder)
            self._log_remainder = ""
        kind = self._process_kind
        user_stopped = self._stopping
        resume_server = (
            kind == "offline assets" and self._pending_server_start and not user_stopped
        )
        if kind == "offline assets":
            self._pending_server_start = False
        if kind == "server":
            label = "Server stopped" if user_stopped or exit_code == 0 else (
                "Server exited unexpectedly; check Activity for details."
            )
        elif user_stopped:
            label = "Task stopped"
        else:
            label = "Completed" if exit_code == 0 else "Task failed; check Activity for details."
        self.status_label.setText(label)
        if kind == "server":
            self.server_links.hide()
            self.site_link_button.hide()
            self.editor_link_button.hide()
            self._server_ready = False
            self._server_url = ""
            self._editor_url = ""
        self._stopping = False
        if kind == "offline assets":
            self._update_server_setup()
        if kind == "ffmpeg setup":
            self._update_ffmpeg_button()
        self._append_log(f"{_task_name(kind)} finished with exit code {exit_code}.")
        self._cleanup_process()
        self._update_actions()
        if resume_server:
            if exit_code == 0 and self._offline_assets_ready():
                self._append_log("Offline assets are ready; starting the live server.")
                self._start_server()
            else:
                self.status_label.setText("Could not prepare offline assets")
                self.workflow_status.setText(
                    "The live server was not started because offline assets could not be downloaded."
                )
                self._append_log(
                    "The live server was not started: offline asset download failed or validation did not pass."
                )

    def _cleanup_process(self) -> None:
        if self._process:
            self._process.deleteLater()
        self._process = None
        self._process_group = False
        self._process_kind = ""

    def _stop_process(self) -> None:
        if self._process and self._process.state() != QProcess.ProcessState.NotRunning:
            self._stopping = True
            self.status_label.setText("Stopping…")
            self._append_log("Requesting process shutdown…")
            self._terminate_process()
            QTimer.singleShot(3000, self._kill_if_running)
            self._update_actions()

    def _kill_if_running(self) -> None:
        if self._process and self._process.state() != QProcess.ProcessState.NotRunning:
            self._append_log("Process did not stop in time; terminating it.")
            self._kill_process_tree()

    def _signal_process_group(self, signal_number: int) -> bool:
        """Send a signal to the task's whole process group (macOS and Linux, when
        the task was started in its own session). False if that was not possible."""
        process = self._process
        if not self._process_group or process is None:
            return False
        if process.state() == QProcess.ProcessState.NotRunning:
            return False
        pid = process.processId()
        if pid <= 0:
            return False
        try:
            # the task is its session's leader, so its process group id is its pid
            os.killpg(pid, signal_number)
        except OSError:
            return False
        return True

    def _terminate_process(self) -> None:
        """Ask the running task, and everything it started, to stop."""
        if self._process is None:
            return
        if sys.platform == "win32":
            # terminate() does nothing to a program without a window, so end the
            # whole process tree at once (the helper runs as a launcher + child).
            self._kill_process_tree()
        elif not self._signal_process_group(signal.SIGTERM):
            self._process.terminate()

    def _kill_process_tree(self) -> None:
        """Kill the running task and everything it started. The packaged helper is a
        PyInstaller launcher plus a child; killing only the launcher would leave the
        real server running and holding its port."""
        process = self._process
        if process is None:
            return
        if sys.platform != "win32":
            # SIGKILL cannot be passed on by the launcher, so kill the whole group
            self._signal_process_group(signal.SIGKILL)
        elif process.processId():
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(process.processId()), "/T", "/F"],
                    capture_output=True, timeout=10,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except (OSError, subprocess.SubprocessError):
                pass
        process.kill()

    def _update_actions(self) -> None:
        running = bool(self._process and self._process.state() != QProcess.ProcessState.NotRunning)
        self.offline_button.setEnabled(not running)
        if not running:
            self._update_server_setup()
        self._update_workflow()

    def _update_server_setup(self, *_args) -> None:
        assets_ready = self._offline_assets_ready()
        cache_configured = bool(self.index_cache.text().strip())
        tile_cache_configured = bool(self.tile_cache.text().strip())
        self.offline_status.setText(
            f"Offline assets are ready in {VENDOR_DIR}."
            if assets_ready
            else (
                self._offline_asset_error
                or f"Offline assets are missing from {VENDOR_DIR}; they will download automatically when the live server starts."
            )
        )
        self.offline_button.setText(
            "Refresh offline assets" if assets_ready else "Download offline assets"
        )
        requirements = []
        if not cache_configured:
            requirements.append("Choose a metadata cache folder")
        if not tile_cache_configured:
            requirements.append("choose a map tile cache folder")
        if requirements:
            self.server_setup_status.setText(
                "To start the live server, " + " and ".join(requirements) + "."
            )
        else:
            status = "Server setup is complete. The cache folders will be created if needed."
            if not assets_ready and not self._offline_asset_error:
                status += " Missing offline assets will download automatically when you start the server."
            self.server_setup_status.setText(status)
        self._update_workflow()

    def _unsaved_changes(self) -> bool:
        """True when saving is blocked and the settings differ from those at startup."""
        if not self._save_blocked_reason:
            return False
        try:
            return validate_settings(self._settings_from_ui()) != self._startup_settings
        except SettingsError:
            return True

    def closeEvent(self, event) -> None:
        self._save_timer.stop()
        if self._unsaved_changes():
            answer = QMessageBox.question(
                self, "Settings not saved",
                "Your changes to the settings were not saved, because the damaged settings "
                f"file could not be moved aside:\n{self._save_blocked_reason}\n\nClose anyway?",
                QMessageBox.StandardButton.Close | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Close:
                event.ignore()
                return
        elif not self._save_from_ui():
            answer = QMessageBox.question(
                self, "Settings not saved",
                "The latest settings could not be saved. Close anyway?",
                QMessageBox.StandardButton.Close | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Close:
                event.ignore()
                return
        if self._process and self._process.state() != QProcess.ProcessState.NotRunning:
            self._terminate_process()
            if not self._process.waitForFinished(2500):
                self._kill_process_tree()
                self._process.waitForFinished(1000)
        event.accept()


def _port_in_use(port: int) -> bool:
    """True when something on this computer already accepts connections on the port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.5)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def _install_error_log() -> None:
    """The packaged app has no console, so an unexpected error would vanish.
    Save it to a log file next to the settings and show it in a message box."""
    log_path = settings_path().parent / "albummaker-gui-error.log"

    def hook(kind, value, trace) -> None:
        text = "".join(traceback.format_exception(kind, value, trace))
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("a", encoding="utf-8") as stream:
                stream.write(f"--- {datetime.now():%Y-%m-%d %H:%M:%S}  AlbumMaker {__version__}\n{text}\n")
        except OSError:
            pass
        try:
            QMessageBox.critical(
                None, "AlbumMaker error",
                f"An unexpected error occurred:\n\n{value}\n\nDetails were saved to:\n{log_path}",
            )
        except Exception:
            pass

    sys.excepthook = hook


def main() -> int:
    if "--version" in sys.argv[1:]:
        print(f"AlbumMaker {__version__}")
        return 0
    if getattr(sys, "frozen", False):
        _install_error_log()
    app = QApplication(sys.argv)
    app.setApplicationName("AlbumMaker")
    app.setOrganizationName("AlbumMaker")
    app.setWindowIcon(_application_icon())
    loaded = load_settings_or_recover()
    blocked = "" if loaded.can_save else (loaded.move_error or "unknown error")
    window = MainWindow(loaded.settings, save_blocked_reason=blocked)
    window.show()

    def after_show() -> None:
        # Only now is the window frame known (on X11 it is not right after show()).
        window._fit_to_screen()
        if loaded.recovered:
            message = settings_recovery_message(loaded)
            window._append_log("Settings problem: " + message.replace("\n\n", " ").replace("\n", " "))
            QMessageBox.warning(window, "AlbumMaker settings were reset", message)
        QTimer.singleShot(600, window._offer_ffmpeg_install)

    QTimer.singleShot(0, after_show)
    return app.exec()
