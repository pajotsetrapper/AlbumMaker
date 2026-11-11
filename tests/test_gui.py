# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Pieter Remi Coppens
import html
import json
import os
import re
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from albummaker.gui.commands import (
    _command_prefix,
    build_command,
    ffmpeg_install_command,
    offline_assets_command,
    server_command,
)
from albummaker.gui.settings import (
    SettingsError,
    default_settings,
    load_settings,
    load_settings_or_recover,
    prepare_metadata_cache,
    prepare_tile_cache,
    save_settings,
    settings_path,
    write_library_file,
)
from albummaker.app_paths import config_dir, offline_assets_dir
from albummaker.library import read_trip_list, trip_args
from albummaker.timezones import get_timezone, timezone_choices, timezone_search_choices


class GuiSettingsTests(unittest.TestCase):
    def test_timezone_choices_include_bundled_database_without_system_zoneinfo(self):
        with mock.patch("albummaker.timezones.available_timezones", return_value=set()):
            choices = timezone_choices()
        self.assertIn("Europe/Paris", choices)
        self.assertIn("America/Los_Angeles", choices)
        self.assertGreater(len(choices), 100)
        self.assertEqual(get_timezone("Europe/Paris").key, "Europe/Paris")
        self.assertEqual(choices, sorted(set(choices)))

    def test_timezone_search_choices_match_city_and_region_names(self):
        suggestions = dict((label, zone) for label, zone in timezone_search_choices())
        self.assertIn("Brussels (Europe/Brussels)", suggestions)
        self.assertEqual(suggestions["Brussels (Europe/Brussels)"], "Europe/Brussels")
        self.assertIn("Bangkok (Asia/Bangkok)", suggestions)
        self.assertEqual(suggestions["Bangkok (Asia/Bangkok)"], "Asia/Bangkok")

    def test_metadata_cache_folder_is_created_and_validated(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "cache" / "metadata"
            self.assertEqual(prepare_metadata_cache(str(cache)), cache.resolve())
            self.assertTrue(cache.is_dir())

            existing_file = Path(tmp) / "not-a-folder"
            existing_file.write_text("data", encoding="utf-8")
            with self.assertRaisesRegex(SettingsError, "Could not create the metadata cache folder"):
                prepare_metadata_cache(str(existing_file))

    def test_metadata_cache_folder_is_required(self):
        with self.assertRaisesRegex(SettingsError, "Choose a metadata cache folder"):
            prepare_metadata_cache("  ")

    def test_map_tile_cache_folder_is_created_and_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "cache" / "tiles"
            self.assertEqual(prepare_tile_cache(str(cache)), cache.resolve())
            self.assertTrue(cache.is_dir())
        with self.assertRaisesRegex(SettingsError, "Choose a map tile cache folder"):
            prepare_tile_cache("")

    def test_settings_round_trip_and_default_merge(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config" / "settings.json"
            settings = default_settings()
            settings["trips"] = [{"path": "/photos/Japan", "timezone": "Asia/Tokyo"}]
            settings["server"]["editor_link_only"] = True
            settings["library"]["title"] = "Family Holidays"
            save_settings(settings, path)

            loaded = load_settings(path)
            self.assertEqual(
                loaded["trips"],
                [{"path": "/photos/Japan", "timezone": "Asia/Tokyo"}],
            )
            self.assertTrue(loaded["server"]["editor_link_only"])
            self.assertEqual(loaded["build"]["thumb_size"], 400)
            self.assertEqual(loaded["server"]["large_thumb_size"], 2400)
            self.assertTrue(loaded["advanced"]["tile_cache"])
            self.assertEqual(loaded["appearance"]["theme"], "light")
            self.assertEqual(loaded["library"]["title"], "Family Holidays")

            settings["appearance"]["theme"] = "dark"
            save_settings(settings, path)
            self.assertEqual(load_settings(path)["appearance"]["theme"], "dark")

    def test_theme_values_are_validated(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = default_settings()
            settings["appearance"]["theme"] = "midnight"
            with self.assertRaisesRegex(SettingsError, "appearance.theme"):
                save_settings(settings, Path(tmp) / "settings.json")

    def test_library_title_must_be_nonempty_and_reasonably_short(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = default_settings()
            settings["library"]["title"] = " "
            with self.assertRaisesRegex(SettingsError, "library.title"):
                save_settings(settings, Path(tmp) / "settings.json")
            settings["library"]["title"] = "x" * 201
            with self.assertRaisesRegex(SettingsError, "library.title"):
                save_settings(settings, Path(tmp) / "settings.json")

    def test_default_cache_folders_live_beside_settings_file(self):
        settings = default_settings()
        settings_file_folder = settings_path().parent
        self.assertEqual(
            Path(settings["advanced"]["index_cache"]),
            settings_file_folder / "metadata-cache",
        )
        self.assertEqual(
            Path(settings["advanced"]["tile_cache"]),
            settings_file_folder / "tile-cache",
        )
        self.assertEqual(offline_assets_dir(), settings_file_folder / "offline-assets")

    def test_application_data_directory_uses_platform_conventions(self):
        with mock.patch("albummaker.app_paths.sys.platform", "darwin"), mock.patch(
            "albummaker.app_paths.Path.home", return_value=Path("/home/example")
        ), mock.patch.dict(
            "os.environ", {"ALBUMMAKER_CONFIG_DIR": ""}, clear=False
        ):
            self.assertEqual(
                config_dir(), Path("/home/example/Library/Application Support/AlbumMaker")
            )

        with mock.patch("albummaker.app_paths.sys.platform", "win32"), mock.patch.dict(
            "os.environ",
            {"APPDATA": "/Users/example/AppData/Roaming", "ALBUMMAKER_CONFIG_DIR": ""},
            clear=False,
        ):
            self.assertEqual(config_dir(), Path("/Users/example/AppData/Roaming/AlbumMaker"))

        with mock.patch("albummaker.app_paths.sys.platform", "linux"), mock.patch.dict(
            "os.environ",
            {"XDG_CONFIG_HOME": "/home/example/.config", "ALBUMMAKER_CONFIG_DIR": ""},
            clear=False,
        ):
            self.assertEqual(config_dir(), Path("/home/example/.config/albummaker"))

    def test_trip_paths_are_saved_and_time_zones_are_validated(self):
        settings = default_settings()
        settings["trips"] = [{"path": "/photos/Italy", "timezone": ""}]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            save_settings(settings, path)
            self.assertEqual(
                load_settings(path)["trips"],
                [{"path": "/photos/Italy", "timezone": ""}],
            )
            settings["trips"] = [{"path": "/photos/Italy", "timezone": "Not/AZone"}]
            with self.assertRaisesRegex(SettingsError, "Unknown IANA time zone"):
                save_settings(settings, path)

    def test_rejects_invalid_settings_version_and_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text('{"version": 99}', encoding="utf-8")
            with self.assertRaises(SettingsError):
                load_settings(path)

            settings = default_settings()
            settings["server"]["port"] = "8000"
            with self.assertRaises(SettingsError):
                save_settings(settings, path)
            settings["server"]["port"] = 70000
            with self.assertRaises(SettingsError):
                save_settings(settings, path)

    def test_writes_library_file_compatible_with_paths_containing_spaces(self):
        with tempfile.TemporaryDirectory() as tmp:
            trip = Path(tmp) / "Japan Family Trip"
            trip.mkdir()
            library = write_library_file([str(trip)], Path(tmp) / "gui-trip-library.txt")

            entries = read_trip_list(library)
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0].path.resolve(), trip.resolve())

    def test_writes_each_trip_time_zone_into_library_options(self):
        with tempfile.TemporaryDirectory() as tmp:
            japan = Path(tmp) / "Japan Family Trip"
            japan.mkdir()
            france = Path(tmp) / "France"
            france.mkdir()
            library = write_library_file(
                [
                    {"path": str(japan), "timezone": "Asia/Tokyo"},
                    {"path": str(france), "timezone": "Europe/Paris"},
                ],
                Path(tmp) / "gui-trip-library.txt",
            )
            entries = read_trip_list(library)
            self.assertEqual(len(entries), 2)
            self.assertEqual(entries[0].options, ["--timezone", "Asia/Tokyo"])
            self.assertEqual(entries[1].options, ["--timezone", "Europe/Paris"])
            args = __import__("argparse").Namespace(
                input=None, content=None, tz_offset=2.0, timezone=None, no_dedupe=False,
                no_live_photos=False, date_range=None, no_date_check=False, gpx=[],
                no_gpx=False, no_places=False, thumb_size=400,
            )
            trip_args_out, _ = trip_args(args, entries[0], library)
            self.assertEqual(trip_args_out.timezone, "Asia/Tokyo")
            self.assertIsNone(trip_args_out.tz_offset)

    def test_library_path_delimiters_are_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            # '|' cannot be used in a Windows file name, so the folder is not
            # created; the check under test only looks at the path text
            trip = Path(tmp) / "Trip|Name"
            with mock.patch.object(Path, "is_dir", return_value=True):
                with self.assertRaisesRegex(SettingsError, "cannot contain"):
                    write_library_file([str(trip)], Path(tmp) / "library.txt")


class PackagedLaunchTests(unittest.TestCase):
    """The GUI cannot run here without PySide6, so guard the launch code by source."""

    def test_launch_has_no_windows_only_process_hook(self):
        source = (Path(__file__).resolve().parent.parent / "albummaker" / "gui" / "app.py").read_text(encoding="utf-8")
        # An exception inside this hook is invisible in the windowed app and stops the helper starting.
        self.assertNotIn("setCreateProcessArgumentsModifier", source)
        self.assertIn("PYINSTALLER_RESET_ENVIRONMENT", source)

    def test_frozen_cli_supports_worker_processes(self):
        source = (Path(__file__).resolve().parent.parent / "tools" / "frozen_cli.py").read_text(encoding="utf-8")
        self.assertIn("multiprocessing.freeze_support()", source)


class GuiCommandTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.trip = self.root / "Trip With Spaces"
        self.trip.mkdir()
        self.library = write_library_file([str(self.trip)], self.root / "library.txt")
        self.settings = default_settings()
        self.settings["build"]["date_range_start"] = "2024-05-01"
        self.settings["build"]["date_range_end"] = "2024-05-03"

    def test_ffmpeg_install_command_asks_the_cli_to_install_without_prompting(self):
        command = ffmpeg_install_command(sys.executable)
        self.assertEqual(command[-2:], ["install-ffmpeg", "--yes"])

    def test_server_command_includes_editor_link_and_server_settings(self):
        self.settings["library"]["title"] = "Family Holidays"
        self.settings["server"].update({
            "port": 8123,
            "large_thumb_size": 0,
            "lan": True,
            "editor_link_only": True,
            "read_only": True,
            "verbose": True,
        })
        command = server_command("python", self.library, self.settings)

        self.assertIn("--editor-link-only", command)
        self.assertIn("--read-only", command)
        self.assertIn("--lan", command)
        self.assertEqual(command[command.index("--library-title") + 1], "Family Holidays")
        self.assertEqual(command[command.index("--port") + 1], "8123")
        self.assertEqual(
            command[command.index("--tile-cache") + 1],
            self.settings["advanced"]["tile_cache"],
        )
        from albummaker.server import build_parser
        args = build_parser().parse_args(command[command.index("serve") + 1:])
        self.assertEqual(args.large_thumb_size, 0)
        entry = read_trip_list(self.library)[0]
        self.assertEqual(
            entry.options,
            ["--date-range", "2024-05-01", "2024-05-03"],
        )
        _, date_range = trip_args(args, entry, self.library)
        self.assertEqual(date_range[0].strftime("%Y-%m-%d"), "2024-05-01")
        self.assertEqual(date_range[1].strftime("%Y-%m-%d"), "2024-05-04")

    def test_server_command_requires_a_tile_cache_folder(self):
        self.settings["advanced"]["tile_cache"] = ""
        with self.assertRaisesRegex(ValueError, "map tile cache folder"):
            server_command("python", self.library, self.settings)

    def test_build_command_maps_media_options_and_output(self):
        self.settings["library"]["title"] = "Summer Trips"
        self.settings["build"].update({
            "no_dedupe": True,
            "no_live_photos": True,
            "workers": 3,
            "force": True,
            "gpx_files": ["/tracks/route.gpx"],
        })
        output = str(self.root / "Generated Site")
        command = build_command("python", self.library, output, self.settings)

        from albummaker.build import build_parser
        build_parser().parse_args(command[command.index("build") + 1:])
        self.assertIn("--no-dedupe", command)
        self.assertIn("--no-live-photos", command)
        self.assertIn("--force", command)
        self.assertEqual(command[command.index("--workers") + 1], "3")
        self.assertEqual(command[command.index("--gpx") + 1], str(Path("/tracks/route.gpx")))
        self.assertEqual(command[command.index("--output") + 1], output)
        self.assertEqual(command[command.index("--library-title") + 1], "Summer Trips")
        self.assertEqual(
            read_trip_list(self.library)[0].options[-3:],
            ["--date-range", "2024-05-01", "2024-05-03"],
        )

    def test_date_range_appends_to_per_trip_timezone(self):
        timezone_library = write_library_file(
            [{"path": str(self.trip), "timezone": "Asia/Tokyo"}],
            self.root / "timezone-library.txt",
        )
        build_command("python", timezone_library, str(self.root / "out"), self.settings)
        self.assertEqual(
            read_trip_list(timezone_library)[0].options,
            ["--timezone", "Asia/Tokyo", "--date-range", "2024-05-01", "2024-05-03"],
        )

    def test_invalid_date_range_and_thumbnail_sizes_are_rejected(self):
        self.settings["build"]["date_range_end"] = ""
        with self.assertRaisesRegex(ValueError, "both the start and end"):
            build_command("python", self.library, str(self.root / "out"), self.settings)

        self.settings["build"]["date_range_end"] = "2024-05-03"
        self.settings["build"]["large_thumb_size"] = 400
        with self.assertRaisesRegex(ValueError, "larger than"):
            build_command("python", self.library, str(self.root / "out"), self.settings)

        self.settings["build"]["large_thumb_size"] = 0
        self.settings["build"]["date_range_start"] = "2024-0501"
        with self.assertRaisesRegex(ValueError, "YYYY-MM-DD"):
            build_command("python", self.library, str(self.root / "out"), self.settings)

    def test_offline_assets_command_uses_cli_subcommand(self):
        self.assertEqual(
            offline_assets_command("python"),
            ["python", "-u", "-m", "albummaker", "serve", "--fetch-offline-assets"],
        )

    def test_zipapp_launches_its_bundled_cli(self):
        with mock.patch("albummaker.gui.commands.sys.argv", ["/tmp/albummaker.pyz"]):
            command = offline_assets_command("python")
        self.assertEqual(
            command,
            ["python", "-u", str(Path("/tmp/albummaker.pyz")), "serve", "--fetch-offline-assets"],
        )

    def test_frozen_gui_launches_its_bundled_cli_helper(self):
        with tempfile.TemporaryDirectory() as tmp:
            helper_dir = Path(tmp) / "bin"
            helper_dir.mkdir()
            helper = helper_dir / (
                "albummaker-cli.exe" if sys.platform == "win32" else "albummaker-cli"
            )
            helper.touch()
            with mock.patch("albummaker.gui.commands.sys.frozen", True, create=True), mock.patch(
                "albummaker.gui.commands.sys._MEIPASS", tmp, create=True
            ):
                self.assertEqual(_command_prefix("ignored-python", "serve"), [str(helper), "serve"])

    def test_explicit_ignore_options_take_precedence(self):
        self.settings["build"]["no_date_check"] = True
        self.settings["build"]["no_gpx"] = True
        self.settings["build"]["gpx_files"] = ["/tracks/ignored.gpx"]
        command = build_command("python", self.library, str(self.root / "out"), self.settings)

        self.assertIn("--no-date-check", command)
        self.assertIn("--no-gpx", command)
        self.assertNotIn("--date-range", command)
        self.assertNotIn("--gpx", command)


class SettingsRecoveryTests(unittest.TestCase):
    """A damaged settings file must not stop the desktop app."""

    NOW = datetime(2026, 11, 11, 9, 30, 5)

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.folder = Path(tmp.name)
        self.path = self.folder / "settings.json"

    def test_missing_file_gives_defaults_without_a_warning(self):
        loaded = load_settings_or_recover(self.path)
        self.assertEqual(loaded.settings, default_settings())
        self.assertFalse(loaded.recovered)
        self.assertTrue(loaded.can_save)

    def test_valid_file_loads_normally(self):
        settings = default_settings()
        settings["library"]["title"] = "Holidays"
        save_settings(settings, self.path)
        loaded = load_settings_or_recover(self.path)
        self.assertFalse(loaded.recovered)
        self.assertEqual(loaded.settings["library"]["title"], "Holidays")
        self.assertTrue(self.path.exists())

    def test_damaged_file_is_moved_aside_and_defaults_are_used(self):
        self.path.write_text('{"version": 1, "trips": [', encoding="utf-8")
        loaded = load_settings_or_recover(self.path, now=self.NOW)
        backup = self.folder / "settings.json.bad-20261111-093005"
        self.assertTrue(loaded.recovered)
        self.assertTrue(loaded.can_save)
        self.assertEqual(loaded.moved_to, backup)
        self.assertEqual(loaded.settings, default_settings())
        self.assertIn("Could not read settings", loaded.problem)
        self.assertFalse(self.path.exists())
        self.assertEqual(backup.read_text(encoding="utf-8"), '{"version": 1, "trips": [')
        # the defaults can now be saved where the damaged file was
        save_settings(loaded.settings, self.path)
        self.assertEqual(load_settings(self.path), default_settings())

    def test_invalid_values_are_reported_and_an_earlier_backup_is_kept(self):
        earlier = self.folder / "settings.json.bad-20261111-093005"
        earlier.write_text("earlier", encoding="utf-8")
        self.path.write_text(json.dumps({"version": 99}), encoding="utf-8")
        loaded = load_settings_or_recover(self.path, now=self.NOW)
        self.assertIn("Unsupported settings version", loaded.problem)
        self.assertEqual(loaded.moved_to, self.folder / "settings.json.bad-20261111-093005-1")
        self.assertEqual(earlier.read_text(encoding="utf-8"), "earlier")
        self.assertEqual(json.loads(loaded.moved_to.read_text(encoding="utf-8")), {"version": 99})

    def test_file_that_cannot_be_moved_is_left_alone_and_saving_is_blocked(self):
        self.path.write_text("not json", encoding="utf-8")
        with mock.patch("albummaker.gui.settings.os.rename", side_effect=PermissionError("denied")):
            loaded = load_settings_or_recover(self.path, now=self.NOW)
        self.assertTrue(loaded.recovered)
        self.assertIsNone(loaded.moved_to)
        self.assertFalse(loaded.can_save)
        self.assertIn("denied", loaded.move_error)
        self.assertEqual(loaded.settings, default_settings())
        self.assertEqual(self.path.read_text(encoding="utf-8"), "not json")


try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
except Exception:  # PySide6 is optional; the GUI tests above do not need it
    QApplication = None


@unittest.skipIf(QApplication is None, "PySide6 is not installed")
class MainWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_window(self, **kwargs):
        from albummaker.gui.app import MainWindow
        window = MainWindow(default_settings(), **kwargs)
        self.addCleanup(window.deleteLater)
        return window

    def test_settings_are_not_written_while_the_damaged_file_is_in_place(self):
        window = self.make_window(save_blocked_reason="denied")
        with mock.patch("albummaker.gui.app.save_settings") as save:
            window.library_title.setText("Changed")
            self.assertTrue(window._save_from_ui())
            save.assert_not_called()
        self.assertEqual(window.settings["library"]["title"], "Changed")
        self.assertTrue(window._unsaved_changes())
        self.assertEqual(window.status_label.text(), "Settings are not being saved")

    def test_settings_are_saved_normally(self):
        window = self.make_window()
        with mock.patch("albummaker.gui.app.save_settings") as save:
            self.assertTrue(window._save_from_ui())
            save.assert_called_once()
        self.assertFalse(window._unsaved_changes())

    def test_recovery_message_says_what_was_wrong_and_where_the_file_went(self):
        from albummaker.gui.app import settings_recovery_message
        from albummaker.gui.settings import LoadedSettings
        moved = LoadedSettings(default_settings(), Path("/cfg/settings.json"), "bad JSON",
                               Path("/cfg/settings.json.bad-20261111-093005"))
        text = settings_recovery_message(moved)
        self.assertIn("bad JSON", text)
        self.assertIn(str(Path("/cfg/settings.json.bad-20261111-093005")), text)
        kept = LoadedSettings(default_settings(), Path("/cfg/settings.json"), "bad JSON",
                              None, "denied")
        text = settings_recovery_message(kept)
        self.assertIn("denied", text)
        self.assertIn("will not be saved", text)

    def test_task_names_are_written_properly(self):
        from albummaker.gui.app import _task_label, _task_name
        self.assertEqual(_task_name("ffmpeg setup"), "FFmpeg setup")
        self.assertEqual(_task_label("ffmpeg setup"), "FFmpeg setup")
        self.assertEqual(_task_name("offline assets"), "Offline assets")
        self.assertEqual(_task_label("build"), "build")

    def test_help_names_every_control_by_its_on_screen_label(self):
        from PySide6.QtWidgets import QAbstractButton, QDialogButtonBox, QFormLayout, QLabel
        from albummaker.gui.app import _about_text, _help_html
        window = self.make_window()
        text = html.unescape(re.sub(r"<[^>]+>", "", _help_html()))
        text = re.sub(r"\s+", " ", text)
        labels = set()
        for root in (window, window.options_dialog):
            for button in root.findChildren(QAbstractButton):
                # (not the dialog's standard Close button)
                if button.text() and not isinstance(button.parent(), QDialogButtonBox):
                    labels.add(button.text())
            for form in root.findChildren(QFormLayout):
                for row in range(form.rowCount()):
                    item = form.itemAt(row, QFormLayout.ItemRole.LabelRole)
                    if item is not None and isinstance(item.widget(), QLabel) and item.widget().text():
                        labels.add(item.widget().text())
        labels |= {"Refresh offline assets", "Hide activity", "Stop server", "Stop task",
                   "Create static site", "Open trip site", "Open editing link (private)"}
        labels |= {window.options_tabs.tabText(i) for i in range(window.options_tabs.count())}
        missing = sorted(label for label in labels
                         if label.replace("&", "").replace("\u2026", "&hellip;") not in text
                         and label.replace("&", "") not in text)
        self.assertEqual(missing, [])
        self.assertIn("https://github.com/pajotsetrapper/AlbumMaker/issues", _help_html())
        self.assertIn("PolyForm Noncommercial 1.0.0", _about_text())

    def test_support_dialog_offers_both_donation_links(self):
        from unittest import mock
        from PySide6.QtWidgets import QDialog, QPushButton
        from albummaker.gui import app as gui_app
        window = self.make_window()
        self.assertEqual(window.support_button.objectName(), "support")
        self.assertTrue(window.support_button.toolTip())
        shown = []
        with mock.patch.object(QDialog, "exec", lambda dialog: shown.append(dialog) or 0):
            window.support_button.click()
        self.assertEqual(len(shown), 1)
        buttons = {b.objectName(): b for b in shown[0].findChildren(QPushButton)}
        opened = []
        with mock.patch.object(gui_app.QDesktopServices, "openUrl",
                               side_effect=lambda url: opened.append(url.toString()) or True):
            buttons["kofi"].click()
            buttons["sponsors"].click()
        self.assertEqual(opened, ["https://ko-fi.com/pietercoppens",
                                  "https://github.com/sponsors/pajotsetrapper"])
        for name in ("kofi", "sponsors"):
            self.assertTrue(buttons[name].toolTip())
        help_text = gui_app._help_html()
        for label in ("\u2665 Support", "Buy me a coffee on Ko-fi", "Sponsor on GitHub"):
            self.assertIn(label, help_text)

    def test_a_link_that_cannot_open_is_copied_instead(self):
        from unittest import mock
        from PySide6.QtWidgets import QApplication
        from albummaker.gui import app as gui_app
        window = self.make_window()
        with mock.patch.object(gui_app.QDesktopServices, "openUrl", return_value=False), \
                mock.patch.object(gui_app.QMessageBox, "information") as info:
            window._open_link(gui_app.KOFI_URL)
        self.assertEqual(QApplication.clipboard().text(), gui_app.KOFI_URL)
        info.assert_called_once()

    def test_every_input_control_has_a_tooltip(self):
        from PySide6.QtWidgets import (QAbstractButton, QAbstractSpinBox, QComboBox, QLineEdit,
                                       QPlainTextEdit, QSpinBox)
        window = self.make_window()
        window._add_trip_item(str(Path.home()))
        missing = []
        for root in (window, window.options_dialog):
            for kind in (QAbstractButton, QComboBox, QPlainTextEdit, QSpinBox):
                for widget in root.findChildren(kind):
                    if not widget.toolTip():
                        missing.append(f"{type(widget).__name__} {getattr(widget, 'text', lambda: '')()}")
            for widget in root.findChildren(QLineEdit):
                # a combo or spin box's own line edit uses that box's tooltip
                if not widget.toolTip() and not isinstance(widget.parent(),
                                                           (QComboBox, QAbstractSpinBox)):
                    missing.append(f"QLineEdit {widget.placeholderText()}")
        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()
