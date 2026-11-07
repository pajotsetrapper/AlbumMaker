"""Qt desktop application entry point."""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QProcess, QProcessEnvironment, QSize, QTimer, Qt
from PySide6.QtGui import QFont, QDoubleValidator
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from .commands import build_command, offline_assets_command, server_command
from .settings import SettingsError, load_settings, save_settings, settings_path, write_library_file
from ..timezones import get_timezone, timezone_choices
from ..util import project_dir

PROJECT_ROOT = project_dir()

APP_STYLE = """
QMainWindow, QWidget { background: #f4f6f9; color: #172033; font-size: 13px; }
QFrame#header, QGroupBox, QListWidget, QTabWidget::pane, QPlainTextEdit {
    background: #ffffff; border: 1px solid #dfe4ec; border-radius: 9px;
}
QFrame#header { border: 0; }
QGroupBox { margin-top: 12px; padding: 14px 12px 10px 12px; font-weight: 600; }
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 5px; }
QPushButton {
    background: #ffffff; border: 1px solid #d5dce7; border-radius: 7px;
    padding: 8px 13px; min-height: 18px;
}
QPushButton:hover { background: #edf2fb; border-color: #9eb4da; }
QPushButton:disabled { color: #8992a2; background: #f1f3f6; }
QPushButton#primary { color: #ffffff; background: #315fce; border-color: #315fce; font-weight: 600; }
QPushButton#primary:hover { background: #254da9; }
QPushButton#stop { color: #a63333; }
QLineEdit, QSpinBox, QPlainTextEdit {
    background: #ffffff; border: 1px solid #cfd7e3; border-radius: 6px; padding: 7px;
    selection-background-color: #315fce;
}
QLineEdit:focus, QSpinBox:focus, QPlainTextEdit:focus { border-color: #537ddd; }
QListWidget { padding: 5px; }
QListWidget::item { border-radius: 5px; padding: 3px 6px; }
QListWidget::item:selected { background: #e7eefc; color: #173c88; }
QTabBar::tab {
    background: #e9edf3; border-top-left-radius: 7px; border-top-right-radius: 7px;
    padding: 9px 14px; margin-right: 3px;
}
QTabBar::tab:selected { background: #ffffff; color: #234da8; font-weight: 600; }
QCheckBox { spacing: 8px; }
QLabel#muted { color: #657187; }
QLabel#status { color: #315fce; font-weight: 600; }
"""

HELP_HTML = """
<h2>TripServer help</h2>
<p>Add one or more photo folders to the trip library. The selected trips are
processed together. Settings are saved automatically on this computer.</p>
<h3>Trip library</h3>
<ul>
<li><b>Add folders:</b> choose multiple trip folders in the folder picker.
Existing entries are skipped.</li>
<li><b>Time zone:</b> select one or more trips and choose an IANA time zone,
such as <code>Europe/Paris</code> or <code>Asia/Tokyo</code>. This is saved
with each folder and handles daylight-saving changes. Automatic leaves
timestamps to the existing metadata-based behavior.</li>
<li><b>Remove:</b> removes selected folders from the library; it never
deletes photos or generated files.</li>
<li><b>Folder order:</b> drag trips to change their order on the library page.</li>
<li><b>Output folder:</b> destination for the static site. It is not used by
the live server.</li>
</ul>
<h3>Server settings</h3>
<ul>
<li><b>Port:</b> local web-server port (1–65535; default 8000); open the shown URL in a browser.</li>
<li><b>Image cache:</b> maximum memory for generated thumbnails and converted photos (16–65536 MB;
default 512 MB).</li>
<li><b>High-resolution thumbnail:</b> larger preview size in pixels (default 1600); it must be larger than the
normal thumbnail size, or Off disables it.</li>
<li><b>Allow other devices on my network:</b> lets devices on the local network view the site.
Off by default. There is no password, so use only a trusted network.</li>
<li><b>Disable all in-page editing:</b> prevents edits to captions, favorites, hidden photos,
covers, titles and descriptions. Off by default.</li>
<li><b>Require the editor link:</b> editing is allowed only in a browser opened with the
private editor link printed in the activity log. Off by default; useful behind a proxy or tunnel.</li>
<li><b>Show every request:</b> adds all HTTP requests to the activity log; otherwise errors
and slow requests are shown. On by default in the desktop app.</li>
</ul>
<h3>Build settings</h3>
<ul>
<li><b>Thumbnail size:</b> maximum thumbnail dimension in pixels (16–4096; default 400).</li>
<li><b>High-resolution thumbnail:</b> optional larger version in pixels; Off by default and otherwise it must
be larger than the normal thumbnail size. The live server uses 1600 by default.</li>
<li><b>Video UTC offset:</b> optional fixed offset in hours (for example 2 or -5.5; blank by default) for video
timestamps when no trip-specific time zone is set. If blank, iPhone videos use their recorded local time and
other videos use this computer's time zone. Per-trip time zones take precedence.</li>
<li><b>Parallel workers:</b> number of files processed simultaneously. Automatic uses
available CPU cores and is the default.</li>
<li><b>Optional date range:</b> include the start and end dates (YYYY-MM-DD) for every trip;
both dates are required, and the end must be on or after the start. Files outside this inclusive range are put
under Unsorted. Leave both blank (the default) to use date checks.</li>
<li><b>Keep exact duplicate files:</b> includes byte-identical copies that are otherwise skipped. Off by default.</li>
<li><b>Show Live Photo video clips separately:</b> lists paired clips as individual videos
instead of attaching them to their photos. Off by default.</li>
<li><b>Trust all captured dates:</b> skips suspicious-date checks. The optional date range
is ignored when enabled. Off by default.</li>
<li><b>Ignore GPS tracks:</b> disables GPX track matching and route drawing. Off by default.</li>
<li><b>Hide offline place names:</b> omits nearest-town names. Off by default.</li>
<li><b>Regenerate all static site media:</b> rebuilds every thumbnail and media copy. Only
applies to Generate site and is off by default.</li>
<li><b>GPX paths:</b> optional .gpx files or folders, one per line. Tracks inside each trip
folder are found automatically. No extra paths are used by default. These paths are ignored
when GPS tracks are disabled.</li>
</ul>
<h3>Advanced settings</h3>
<ul>
<li><b>Tile cache folder:</b> for the live server, stores map tiles as they are viewed, so they can be reused offline.
Blank by default.</li>
<li><b>Tile URL template:</b> for the live server, an optional map-tile provider URL containing <code>{z}</code>,
<code>{x}</code> and <code>{y}</code>. Blank by default; the provider is OpenStreetMap.</li>
<li><b>Metadata index cache folder:</b> for the live server, an optional folder for caching file dates, GPS and
camera metadata between server starts. TripServer keeps one cache file per trip in this folder. Off by default.</li>
<li><b>Allowed host names:</b> for the live server, additional host names the server may answer to, one per line.
Blank by default; use only for a trusted custom local DNS name.</li>
</ul>
<h3>Actions and activity</h3>
<ul>
<li><b>Start server:</b> opens the live, editable site for all listed trips.</li>
<li><b>Generate site:</b> creates a static copy in the output folder.</li>
<li><b>Download offline assets:</b> downloads the map library and fonts needed for offline pages;
map tiles are cached only when viewed.</li>
<li><b>Stop:</b> stops the currently running operation. Progress and errors appear in Activity.</li>
</ul>
"""


def _label(text: str, object_name: Optional[str] = None) -> QLabel:
    label = QLabel(text)
    if object_name:
        label.setObjectName(object_name)
    return label


def _group(title: str) -> tuple[QGroupBox, QFormLayout]:
    box = QGroupBox(title)
    form = QFormLayout(box)
    form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
    form.setHorizontalSpacing(16)
    form.setVerticalSpacing(10)
    return box, form


def _scroll_page(*widgets: QWidget) -> QScrollArea:
    page = QWidget()
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


class MainWindow(QMainWindow):
    def __init__(self, settings: dict):
        super().__init__()
        self.settings = settings
        self._process: Optional[QProcess] = None
        self._process_kind = ""
        self._log_remainder = ""
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(500)
        self._save_timer.timeout.connect(self._save_from_ui)

        self.setWindowTitle("TripServer")
        self.setMinimumSize(780, 620)
        self.resize(1120, 800)
        self.setStyleSheet(APP_STYLE)
        self._build_ui()
        self._load_ui()
        self._connect_persistence()
        self._update_actions()

    def _build_ui(self) -> None:
        root = QWidget()
        outer = QVBoxLayout(root)
        outer.setContentsMargins(18, 14, 18, 14)
        outer.setSpacing(12)

        header = QFrame()
        header.setObjectName("header")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(3, 0, 3, 0)
        title_block = QVBoxLayout()
        title = _label("TripServer")
        title.setFont(QFont(title.font().family(), 21, QFont.Weight.Bold))
        title_block.addWidget(title)
        title_block.addWidget(_label("Your trips, ready to browse or publish.", "muted"))
        header_layout.addLayout(title_block)
        header_layout.addStretch(1)
        help_button = QPushButton("Help")
        help_button.clicked.connect(self._show_help)
        header_layout.addWidget(help_button)
        self.status_label = _label("Ready", "status")
        header_layout.addWidget(self.status_label)
        outer.addWidget(header)

        actions = QHBoxLayout()
        self.start_button = QPushButton("Start server")
        self.start_button.setObjectName("primary")
        self.stop_button = QPushButton("Stop")
        self.stop_button.setObjectName("stop")
        self.build_button = QPushButton("Generate site")
        self.build_button.setObjectName("primary")
        self.offline_button = QPushButton("Download offline assets")
        for button in (self.start_button, self.stop_button, self.build_button, self.offline_button):
            actions.addWidget(button)
        actions.addStretch(1)
        outer.addLayout(actions)

        panes = QSplitter(Qt.Orientation.Horizontal)
        panes.setChildrenCollapsible(False)
        panes.addWidget(self._build_library_panel())
        panes.addWidget(self._build_settings_panel())
        panes.setStretchFactor(0, 1)
        panes.setStretchFactor(1, 3)
        panes.setSizes([280, 780])

        activity_box = QGroupBox("Activity & requests")
        activity_layout = QVBoxLayout(activity_box)
        activity_layout.setContentsMargins(10, 10, 10, 10)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(3000)
        self.log_view.setPlaceholderText(
            "Server requests, build progress and errors will appear here."
        )
        self.log_view.setFont(QFont("monospace", 10))
        activity_layout.addWidget(self.log_view)
        self.server_url = _label("Server URL: not running", "muted")
        activity_layout.addWidget(self.server_url)

        vertical = QSplitter(Qt.Orientation.Vertical)
        vertical.setChildrenCollapsible(False)
        vertical.addWidget(panes)
        vertical.addWidget(activity_box)
        vertical.setStretchFactor(0, 3)
        vertical.setStretchFactor(1, 2)
        vertical.setSizes([500, 250])
        outer.addWidget(vertical, 1)
        self.setCentralWidget(root)

        self.start_button.clicked.connect(self._start_server)
        self.stop_button.clicked.connect(self._stop_process)
        self.build_button.clicked.connect(self._build_site)
        self.offline_button.clicked.connect(self._fetch_offline_assets)

    def _build_library_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(9)
        layout.addWidget(_label("Trip library"))
        layout.addWidget(_label("Add folders in bulk; select trips to set their time zone.",
                                "muted"))
        self.trip_list = QListWidget()
        self.trip_list.setToolTip(
            "Drag folders to change their order. Select one or more trips to set their time zone."
        )
        self.trip_list.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        self.trip_list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.trip_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.trip_list.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout.addWidget(self.trip_list, 1)

        buttons = QHBoxLayout()
        self.add_trip_button = QPushButton("Add folders")
        self.timezone_button = QPushButton("Set time zone…")
        self.remove_trip_button = QPushButton("Remove")
        buttons.addWidget(self.add_trip_button)
        buttons.addWidget(self.remove_trip_button)
        layout.addLayout(buttons)
        layout.addWidget(self.timezone_button)
        self.trip_count = _label("0 folders", "muted")
        layout.addWidget(self.trip_count)

        output_box, output_form = _group("Generated site")
        self.output_dir = QLineEdit()
        output_row = QWidget()
        output_layout = QHBoxLayout(output_row)
        output_layout.setContentsMargins(0, 0, 0, 0)
        output_layout.addWidget(self.output_dir, 1)
        browse_output = QPushButton("Browse…")
        browse_output.clicked.connect(self._browse_output)
        output_layout.addWidget(browse_output)
        output_form.addRow("Output folder", output_row)
        layout.addWidget(output_box)

        self.add_trip_button.clicked.connect(self._add_trip)
        self.timezone_button.clicked.connect(self._set_trip_timezone)
        self.remove_trip_button.clicked.connect(self._remove_trips)
        self.trip_list.itemSelectionChanged.connect(self._update_trip_selection)
        self.trip_list.model().rowsMoved.connect(self._schedule_save)
        return panel

    def _build_settings_panel(self) -> QTabWidget:
        tabs = QTabWidget()
        tabs.addTab(self._server_tab(), "Server")
        tabs.addTab(self._build_tab(), "Build")
        tabs.addTab(self._advanced_tab(), "Advanced")
        return tabs

    def _server_tab(self) -> QWidget:
        server_box, form = _group("Live server")
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setToolTip("TCP port used by the local web server.")
        form.addRow("Port", self.port)
        self.memory_cache = QSpinBox()
        self.memory_cache.setRange(16, 65536)
        self.memory_cache.setSuffix(" MB")
        form.addRow("Image cache", self.memory_cache)
        self.server_large_thumb_size = QSpinBox()
        self.server_large_thumb_size.setRange(0, 4096)
        self.server_large_thumb_size.setSpecialValueText("Off")
        self.server_large_thumb_size.setSuffix(" px")
        form.addRow("High-resolution thumbnail", self.server_large_thumb_size)

        self.lan = QCheckBox("Allow other devices on my network to connect")
        self.lan.setToolTip("Anyone on your local network can access the site; there is no login.")
        form.addRow("", self.lan)
        self.read_only = QCheckBox("Disable all in-page editing")
        form.addRow("", self.read_only)
        self.editor_link_only = QCheckBox("Require the editor link for editing")
        self.editor_link_only.setToolTip(
            "Useful when using a reverse proxy or tunnel. Editing requires the link printed at startup."
        )
        form.addRow("", self.editor_link_only)
        self.verbose = QCheckBox("Show every request in the activity log")
        form.addRow("", self.verbose)
        note = _label(
            "LAN access has no login protection. Use it only on a trusted network.",
            "muted",
        )
        note.setWordWrap(True)
        return _scroll_page(server_box, note)

    def _build_tab(self) -> QWidget:
        media_box, media_form = _group("Media & dates")
        self.thumb_size = QSpinBox()
        self.thumb_size.setRange(16, 4096)
        self.thumb_size.setSuffix(" px")
        media_form.addRow("Thumbnail size", self.thumb_size)
        self.large_thumb_size = QSpinBox()
        self.large_thumb_size.setRange(0, 4096)
        self.large_thumb_size.setSpecialValueText("Off")
        self.large_thumb_size.setSuffix(" px")
        media_form.addRow("High-resolution thumbnail", self.large_thumb_size)
        self.tz_offset = QLineEdit()
        self.tz_offset.setPlaceholderText("Automatic")
        self.tz_offset.setValidator(QDoubleValidator(-24.0, 24.0, 2, self.tz_offset))
        self.tz_offset.setToolTip("Optional UTC offset in hours for video timestamps, e.g. 2 or -5.5.")
        media_form.addRow("Video UTC offset (hours)", self.tz_offset)
        self.workers = QSpinBox()
        self.workers.setRange(0, 256)
        self.workers.setSpecialValueText("Automatic")
        self.workers.setToolTip("Number of files processed in parallel. Automatic uses available CPU cores.")
        media_form.addRow("Parallel workers", self.workers)

        self.date_start = QLineEdit()
        self.date_start.setPlaceholderText("YYYY-MM-DD")
        self.date_end = QLineEdit()
        self.date_end.setPlaceholderText("YYYY-MM-DD")
        date_row = QWidget()
        dates = QHBoxLayout(date_row)
        dates.setContentsMargins(0, 0, 0, 0)
        dates.addWidget(self.date_start)
        dates.addWidget(_label("to", "muted"))
        dates.addWidget(self.date_end)
        media_form.addRow("Optional date range", date_row)

        behavior_box, behavior_form = _group("Import behavior")
        self.no_dedupe = QCheckBox("Keep exact duplicate files")
        behavior_form.addRow("", self.no_dedupe)
        self.no_live_photos = QCheckBox("Show Live Photo video clips separately")
        behavior_form.addRow("", self.no_live_photos)
        self.no_date_check = QCheckBox("Trust all captured dates")
        self.no_date_check.setToolTip(
            "Skip suspicious-date checks. The optional date range is ignored while this is enabled."
        )
        behavior_form.addRow("", self.no_date_check)
        self.no_gpx = QCheckBox("Ignore GPS tracks")
        behavior_form.addRow("", self.no_gpx)
        self.no_places = QCheckBox("Hide offline place names")
        behavior_form.addRow("", self.no_places)
        self.force = QCheckBox("Regenerate all static site media")
        behavior_form.addRow("", self.force)

        gpx_box, gpx_form = _group("Additional GPS tracks")
        self.gpx_files = QPlainTextEdit()
        self.gpx_files.setPlaceholderText("Optional: one .gpx file or folder path per line")
        self.gpx_files.setToolTip("These additional tracks are ignored when 'Ignore GPS tracks' is enabled.")
        self.gpx_files.setMaximumHeight(90)
        gpx_form.addRow("GPX paths", self.gpx_files)
        return _scroll_page(media_box, behavior_box, gpx_box)

    def _advanced_tab(self) -> QWidget:
        map_box, map_form = _group("Maps")
        self.tile_cache = QLineEdit()
        map_form.addRow("Tile cache folder", self._path_field(self.tile_cache, directory=True))
        self.tile_url = QLineEdit()
        self.tile_url.setPlaceholderText("Default OpenStreetMap tile server")
        self.tile_url.setToolTip("Custom URL template must include {z}, {x}, and {y}.")
        map_form.addRow("Tile URL template", self.tile_url)
        self.index_cache = QLineEdit()
        map_form.addRow("Metadata index cache folder", self._path_field(self.index_cache, directory=True))

        hosts_box, hosts_form = _group("Network")
        self.allow_hosts = QPlainTextEdit()
        self.allow_hosts.setPlaceholderText("One extra host name per line")
        self.allow_hosts.setMaximumHeight(100)
        hosts_form.addRow("Allowed host names", self.allow_hosts)
        note = _label(
            "The offline-assets button downloads Leaflet and fonts into the project's vendor folder. "
            "Map tiles are cached only when viewed.",
            "muted",
        )
        note.setWordWrap(True)
        return _scroll_page(map_box, hosts_box, note)

    def _path_field(self, line_edit: QLineEdit, directory: bool) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(line_edit, 1)
        button = QPushButton("Browse…")
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
        self.tile_cache.setText(str(advanced["tile_cache"]))
        self.tile_url.setText(str(advanced["tile_url"]))
        self.index_cache.setText(str(advanced["index_cache"]))
        self.allow_hosts.setPlainText("\n".join(advanced["allow_hosts"]))
        self._update_date_controls()
        self.gpx_files.setEnabled(not self.no_gpx.isChecked())
        self._update_trip_count()
        self._update_trip_selection()

    def _connect_persistence(self) -> None:
        for widget in (
            self.output_dir, self.tz_offset, self.date_start, self.date_end,
            self.tile_cache, self.tile_url, self.index_cache,
        ):
            widget.textChanged.connect(self._schedule_save)
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
        folder = Path(path).expanduser()
        item = QListWidgetItem()
        item.setData(Qt.ItemDataRole.UserRole, str(folder))
        item.setData(Qt.ItemDataRole.UserRole + 1, timezone_name)
        self._refresh_trip_item(item)
        self.trip_list.addItem(item)

    def _refresh_trip_item(self, item: QListWidgetItem) -> None:
        folder = Path(item.data(Qt.ItemDataRole.UserRole)).expanduser()
        timezone_name = item.data(Qt.ItemDataRole.UserRole + 1) or ""
        zone_label = timezone_name or "Automatic"
        item.setText(f"{folder.name or str(folder)}  —  {zone_label}")
        item.setToolTip(f"{folder}\nTime zone: {zone_label}")
        if not folder.is_dir():
            item.setToolTip(f"{folder}\nFolder not found\nTime zone: {zone_label}")
        item.setSizeHint(QSize(0, 31))

    def _trip_paths(self) -> list[str]:
        return [
            str(self.trip_list.item(index).data(Qt.ItemDataRole.UserRole))
            for index in range(self.trip_list.count())
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

    def _set_trip_timezone(self) -> None:
        selected = self.trip_list.selectedItems()
        if not selected:
            QMessageBox.information(self, "Select trips", "Select one or more trips before setting a time zone.")
            return
        selected_zones = {
            item.data(Qt.ItemDataRole.UserRole + 1) or "Automatic"
            for item in selected
        }
        mixed = len(selected_zones) > 1
        zones = ["Automatic"] + (["Multiple time zones"] if mixed else []) + timezone_choices()
        current = "Multiple time zones" if mixed else next(iter(selected_zones))
        current_index = zones.index(current) if current in zones else 0
        chosen, accepted = QInputDialog.getItem(
            self, "Set trip time zone", "Time zone for the selected trip folder(s):",
            zones, current_index, True,
        )
        if not accepted or chosen == "Multiple time zones":
            return
        timezone_name = "" if chosen.strip().lower() == "automatic" else chosen.strip()
        try:
            if timezone_name:
                get_timezone(timezone_name)
        except ValueError as exc:
            QMessageBox.warning(self, "Unknown time zone", str(exc))
            return
        for item in selected:
            item.setData(Qt.ItemDataRole.UserRole + 1, timezone_name)
            self._refresh_trip_item(item)
        self._schedule_save()

    def _update_trip_selection(self) -> None:
        self.timezone_button.setEnabled(bool(self.trip_list.selectedItems()))

    def _remove_trips(self) -> None:
        for item in self.trip_list.selectedItems():
            self.trip_list.takeItem(self.trip_list.row(item))
        self._update_trip_count()
        self._schedule_save()

    def _update_trip_count(self) -> None:
        count = self.trip_list.count()
        self.trip_count.setText(f"{count} folder" + ("" if count == 1 else "s"))

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
            "trips": [
                {
                    "path": str(self.trip_list.item(index).data(Qt.ItemDataRole.UserRole)),
                    "timezone": self.trip_list.item(index).data(Qt.ItemDataRole.UserRole + 1) or "",
                }
                for index in range(self.trip_list.count())
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
        dialog.setWindowTitle("TripServer help")
        dialog.resize(720, 700)
        layout = QVBoxLayout(dialog)
        content = QTextBrowser()
        content.setHtml(HELP_HTML)
        content.setOpenExternalLinks(True)
        layout.addWidget(content)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        buttons.accepted.connect(dialog.accept)
        layout.addWidget(buttons)
        dialog.exec()

    def _schedule_save(self, *_args) -> None:
        self._save_timer.start()

    def _save_from_ui(self) -> bool:
        settings = self._settings_from_ui()
        try:
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
        process = QProcess(self)
        process.setWorkingDirectory(str(PROJECT_ROOT))
        environment = QProcessEnvironment.systemEnvironment()
        environment.insert("PYTHONUNBUFFERED", "1")
        process.setProcessEnvironment(environment)
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        process.readyReadStandardOutput.connect(self._read_process_output)
        process.started.connect(lambda: self._on_started(kind))
        process.errorOccurred.connect(self._on_process_error)
        process.finished.connect(self._on_finished)
        self._process = process
        self._process_kind = kind
        self._log_remainder = ""
        self._append_log(f"Starting {kind}: {self._display_command(arguments)}")
        self.status_label.setText(f"Starting {kind}…")
        self._update_actions()
        process.start(arguments[0], arguments[1:])

    @staticmethod
    def _display_command(arguments: list[str]) -> str:
        return " ".join(f'"{arg}"' if " " in arg else arg for arg in arguments)

    def _start_server(self) -> None:
        try:
            library = self._prepare_library()
            arguments = server_command(sys.executable, library, self.settings)
        except (OSError, SettingsError, ValueError, KeyError) as exc:
            QMessageBox.warning(self, "Cannot start server", str(exc))
            return
        self.server_url.setText(f"Server URL: http://127.0.0.1:{self.port.value()}/")
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
                self._append_log(line.rstrip("\r\n"))
            else:
                self._log_remainder = line

    def _append_log(self, message: str) -> None:
        stamp = datetime.now().astimezone().strftime("%H:%M:%S")
        self.log_view.appendPlainText(f"{stamp}  {message}")

    def _on_started(self, kind: str) -> None:
        states = {
            "server": "Server running",
            "build": "Generating site",
            "offline assets": "Downloading assets",
        }
        self.status_label.setText(states.get(kind, f"{kind.capitalize()} running"))
        if kind == "server":
            self._append_log(f"Server running at http://127.0.0.1:{self.port.value()}/")
        self._update_actions()

    def _on_process_error(self, error: QProcess.ProcessError) -> None:
        if error == QProcess.ProcessError.FailedToStart:
            message = self._process.errorString() if self._process else "Could not start the process."
            self._append_log(f"Error: {message}")
            self.status_label.setText("Could not start")
            self.server_url.setText("Server URL: not running")
            self._cleanup_process()
            self._update_actions()

    def _on_finished(self, exit_code: int, _exit_status: QProcess.ExitStatus) -> None:
        self._read_process_output()
        if self._log_remainder:
            self._append_log(self._log_remainder)
            self._log_remainder = ""
        kind = self._process_kind
        label = "Server stopped" if kind == "server" and exit_code == 0 else (
            "Completed" if exit_code == 0 else f"Failed (exit code {exit_code})"
        )
        self.status_label.setText(label)
        if kind == "server":
            self.server_url.setText("Server URL: not running")
        self._append_log(f"{kind.capitalize()} finished with exit code {exit_code}.")
        self._cleanup_process()
        self._update_actions()

    def _cleanup_process(self) -> None:
        if self._process:
            self._process.deleteLater()
        self._process = None
        self._process_kind = ""

    def _stop_process(self) -> None:
        if self._process and self._process.state() != QProcess.ProcessState.NotRunning:
            self.status_label.setText("Stopping…")
            self._append_log("Requesting process shutdown…")
            self._process.terminate()
            QTimer.singleShot(3000, self._kill_if_running)
            self._update_actions()

    def _kill_if_running(self) -> None:
        if self._process and self._process.state() != QProcess.ProcessState.NotRunning:
            self._append_log("Process did not stop in time; terminating it.")
            self._process.kill()

    def _update_actions(self) -> None:
        running = bool(self._process and self._process.state() != QProcess.ProcessState.NotRunning)
        self.start_button.setEnabled(not running)
        self.build_button.setEnabled(not running)
        self.offline_button.setEnabled(not running)
        self.stop_button.setEnabled(running)

    def closeEvent(self, event) -> None:
        self._save_timer.stop()
        if not self._save_from_ui():
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
            self._process.terminate()
            if not self._process.waitForFinished(2500):
                self._process.kill()
                self._process.waitForFinished(1000)
        event.accept()


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("TripServer")
    app.setOrganizationName("TripServer")
    try:
        settings = load_settings()
    except SettingsError as exc:
        QMessageBox.critical(None, "TripServer settings error", str(exc))
        return 1
    window = MainWindow(settings)
    window.show()
    return app.exec()
