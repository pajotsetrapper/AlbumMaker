# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Pieter Remi Coppens
import html
import json
import os
import re
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from albummaker.gui.commands import (
    _command_prefix,
    build_command,
    ffmpeg_install_command,
    offline_assets_command,
    server_command,
)
from albummaker.gui.progress import RunProgress
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
from albummaker import timezones
from albummaker.timezones import (
    clean_recent_zones,
    get_timezone,
    remember_recent_zone,
    selectable_timezones,
    timezone_choices,
    timezone_dropdown_groups,
    timezone_label,
    timezone_search_choices,
    utc_offset_text,
)


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
        for zone in ("Europe/Brussels", "Asia/Bangkok"):
            self.assertEqual(suggestions[timezone_label(zone)], zone)
        brussels = timezone_label("Europe/Brussels")
        self.assertTrue(brussels.startswith("Brussels \u00b7 UTC"), brussels)
        self.assertTrue(brussels.endswith(" \u00b7 Europe"), brussels)

    def test_timezone_search_choices_are_built_once(self):
        # the app asks for them whenever a time zone is edited; reading the whole
        # time-zone database each time made a long trip list slow to open
        self.assertIs(timezone_search_choices(), timezone_search_choices())

    def test_timezone_label_matches_the_search_choices_and_names_automatic(self):
        self.assertEqual(timezone_label(""), "Automatic")
        winter = datetime(2026, 1, 15, 12, tzinfo=timezone.utc)
        summer = datetime(2026, 7, 15, 12, tzinfo=timezone.utc)
        self.assertEqual(timezone_label("America/Los_Angeles", winter),
                         "Los Angeles \u00b7 UTC-08:00 \u00b7 America")
        self.assertEqual(timezone_label("America/Los_Angeles", summer),
                         "Los Angeles \u00b7 UTC-07:00 \u00b7 America")
        for label, zone in list(timezone_search_choices())[:20]:
            self.assertEqual(timezone_label(zone), label)

    def test_utc_offsets_are_shown_with_the_zone_s_own_sign_and_minutes(self):
        when = datetime(2026, 1, 15, 12, tzinfo=timezone.utc)
        self.assertEqual(utc_offset_text("Europe/Brussels", when), "UTC+01:00")
        self.assertEqual(utc_offset_text("America/New_York", when), "UTC-05:00")
        self.assertEqual(utc_offset_text("Asia/Kolkata", when), "UTC+05:30")
        self.assertEqual(utc_offset_text("Asia/Kathmandu", when), "UTC+05:45")
        self.assertEqual(utc_offset_text("not/a_zone", when), "")

    def test_the_full_list_leaves_out_old_aliases_and_oddities_but_keeps_real_places(self):
        zones = set(selectable_timezones())
        for kept in ("Europe/Brussels", "America/Argentina/Buenos_Aires", "Asia/Kolkata", "UTC"):
            self.assertIn(kept, zones)
        for dropped in ("Factory", "localtime", "EST5EDT", "Etc/GMT+5", "Etc/GMT-2", "GMT"):
            self.assertNotIn(dropped, zones)
        self.assertFalse([zone for zone in zones if zone.startswith("Etc/")])
        self.assertLess(len(zones), len(timezone_choices()))
        labels = [label for label, _zone in timezone_search_choices()]
        self.assertEqual(len(labels), len(set(labels)))          # a label names exactly one zone
        self.assertEqual(timezone_label("UTC"), timezones.UTC_LABEL)

    def test_dropdown_groups_are_automatic_then_pinned_then_everything_else_once(self):
        with mock.patch("albummaker.timezones.local_timezone_name", return_value="Europe/Brussels"):
            groups = timezone_dropdown_groups("", ["Asia/Tokyo", "Europe/Brussels", "Europe/Paris"])
        self.assertEqual(groups[0], [("Automatic", "")])
        self.assertEqual([zone for _label, zone in groups[1]],
                         ["Europe/Brussels", "Asia/Tokyo", "Europe/Paris", "UTC"])
        self.assertTrue(groups[1][0][0].endswith(" \u00b7 this computer"))
        everything = [zone for group in groups for _label, zone in group if zone]
        self.assertEqual(len(everything), len(set(everything)))   # no zone twice
        self.assertEqual(set(everything), set(selectable_timezones()))
        self.assertEqual([label for label, _ in groups[2]],
                         sorted((label for label, _ in groups[2]), key=str.casefold))

    def test_dropdown_groups_without_a_known_local_zone_or_recents(self):
        with mock.patch("albummaker.timezones.local_timezone_name", return_value=""):
            groups = timezone_dropdown_groups("", [])
        self.assertEqual([[zone for _label, zone in group][:1] for group in groups[:2]],
                         [[""], ["UTC"]])
        with mock.patch("albummaker.timezones.local_timezone_name", return_value=""):
            groups = timezone_dropdown_groups("", ["nonsense", "Asia/Tokyo"])   # unusable ones are skipped
        self.assertEqual([zone for _label, zone in groups[1]], ["Asia/Tokyo", "UTC"])

    def test_a_zone_the_full_list_leaves_out_stays_selectable_when_it_is_already_set(self):
        left_out = next(zone for zone in timezone_choices() if zone.startswith("Etc/GMT+"))
        with mock.patch("albummaker.timezones.local_timezone_name", return_value=""):
            groups = timezone_dropdown_groups(left_out, [])
        self.assertEqual([zone for _label, zone in groups[0]], ["", left_out])

    def test_this_computers_zone_is_read_from_the_environment_and_never_guessed(self):
        timezones.local_timezone_name.cache_clear()
        try:
            with mock.patch.dict(os.environ, {"TZ": ":Europe/Brussels"}):
                self.assertEqual(timezones.local_timezone_name(), "Europe/Brussels")
            timezones.local_timezone_name.cache_clear()
            with mock.patch.dict(os.environ, {"TZ": "Etc/UTC"}):
                self.assertEqual(timezones.local_timezone_name(), "UTC")
            timezones.local_timezone_name.cache_clear()
            with mock.patch.dict(os.environ, {"TZ": "CET-1CEST"}), \
                    mock.patch("albummaker.timezones._local_timezone_candidates",
                               return_value=iter(["CET-1CEST", "not/a_zone"])):
                self.assertEqual(timezones.local_timezone_name(), "")
        finally:
            timezones.local_timezone_name.cache_clear()

    def test_recent_zones_go_first_without_repeats_and_are_capped(self):
        recent = []
        for zone in ("Asia/Tokyo", "Europe/Paris", "Asia/Tokyo", "Asia/Bangkok", "Africa/Cairo",
                     "Europe/Rome", "America/Lima"):
            recent = remember_recent_zone(recent, zone)
        self.assertEqual(recent, ["America/Lima", "Europe/Rome", "Africa/Cairo",
                                  "Asia/Bangkok", "Asia/Tokyo"])
        self.assertEqual(remember_recent_zone(recent, ""), recent)      # Automatic is not "recent"

    def test_recent_zones_in_a_settings_file_are_cleaned_not_rejected(self):
        self.assertEqual(clean_recent_zones(["Asia/Tokyo", "nonsense", 7, "Asia/Tokyo", ""]), ["Asia/Tokyo"])
        self.assertEqual(clean_recent_zones("Asia/Tokyo"), [])
        self.assertEqual(len(clean_recent_zones(["Africa/Cairo", "Asia/Tokyo", "Asia/Bangkok", "Europe/Rome",
                                                 "Europe/Paris", "America/Lima", "Asia/Seoul"])), 5)

    def test_recent_zones_are_saved_and_loaded_with_the_settings(self):
        self.assertEqual(default_settings()["recent_time_zones"], [])
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            settings = default_settings()
            settings["recent_time_zones"] = ["Asia/Tokyo", "Europe/Paris"]
            save_settings(settings, path)
            self.assertEqual(load_settings(path)["recent_time_zones"], ["Asia/Tokyo", "Europe/Paris"])
            data = json.loads(path.read_text(encoding="utf-8"))
            data["recent_time_zones"] = ["Asia/Tokyo", "gone/forever", 5]
            path.write_text(json.dumps(data), encoding="utf-8")
            self.assertEqual(load_settings(path)["recent_time_zones"], ["Asia/Tokyo"])
            del data["recent_time_zones"]                        # a file from before this setting
            path.write_text(json.dumps(data), encoding="utf-8")
            self.assertEqual(load_settings(path)["recent_time_zones"], [])

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
            self.assertEqual(loaded["appearance"]["theme"], "dark")
            self.assertEqual(loaded["library"]["title"], "Family Holidays")

            settings["appearance"]["theme"] = "light"
            save_settings(settings, path)
            self.assertEqual(load_settings(path)["appearance"]["theme"], "light")

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
        from albummaker.gui import commands
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "albummaker.pyz"
            archive.touch()
            inside = str(archive / "albummaker" / "gui" / "commands.py")
            # `python albummaker.pyz gui` has replaced argv[0] by the time the window starts
            with mock.patch.object(commands, "_MODULE_FILE", inside), \
                    mock.patch("albummaker.gui.commands.sys.argv", ["albummaker gui"]):
                command = offline_assets_command("python")
            self.assertEqual(command, ["python", "-u", str(archive), "serve", "--fetch-offline-assets"])
            with mock.patch("albummaker.gui.commands.sys.argv", [str(archive)]):   # started some other way
                self.assertEqual(commands._zipapp_archive(), archive)
        self.assertIsNone(commands._zipapp_archive())

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

    def test_ready_window_shows_the_network_address_and_the_editing_link(self):
        from unittest import mock
        from albummaker.gui import app as gui_app
        window = self.make_window()
        window.port.setValue(8123)
        shown = []
        window._present_result = shown.append
        window._server_url = "http://127.0.0.1:8123/"
        window._editor_url = "http://192.168.1.20:8123/?key=abc"
        window._server_lan = True
        with mock.patch.object(gui_app, "_lan_ip", return_value="192.168.1.20"):
            window._show_server_ready()
        texts = [label.text() for label in shown[-1].findChildren(gui_app.QLabel)]
        self.assertIn("http://192.168.1.20:8123/", texts)
        self.assertIn("http://192.168.1.20:8123/?key=abc", texts)
        self.assertNotIn("http://127.0.0.1:8123/", texts)
        # without LAN access only this computer can connect, so the local address stays and says why
        window._server_lan = False
        window._editor_url = ""
        window._show_server_ready()
        texts = " ".join(label.text() for label in shown[-1].findChildren(gui_app.QLabel))
        self.assertIn("http://127.0.0.1:8123/", texts)
        self.assertIn("Allow other devices on my network", texts)
        self.assertNotIn("Editing link", texts)
        # even if a link was reported, it is hidden while nobody needs it (no LAN, checkbox off)
        window._editor_url = "http://127.0.0.1:8123/?key=abc"
        window._show_server_ready()
        texts = " ".join(label.text() for label in shown[-1].findChildren(gui_app.QLabel))
        self.assertNotIn("Editing link", texts)
        self.assertNotIn("key=abc", texts)
        buttons = [b.text() for b in shown[-1].findChildren(gui_app.QPushButton)]
        self.assertNotIn("Open editing link (private)", buttons)
        # with LAN on, the link is how other devices edit; this computer does not need it
        window._server_lan = True
        window._editor_url = "http://192.168.1.20:8123/?key=abc"
        with mock.patch.object(gui_app, "_lan_ip", return_value="192.168.1.20"):
            window._show_server_ready()
        texts = " ".join(label.text() for label in shown[-1].findChildren(gui_app.QLabel))
        self.assertIn("Editing link for other devices", texts)
        self.assertIn("This computer can edit without the link", texts)
        buttons = [b.text() for b in shown[-1].findChildren(gui_app.QPushButton)]
        self.assertIn("Open editing link (private)", buttons)
        # with the checkbox on, even this computer needs it
        window._server_lan = False
        window._server_editor_link_only = True
        window._editor_url = "http://127.0.0.1:8123/?key=abc"
        window._show_server_ready()
        texts = " ".join(label.text() for label in shown[-1].findChildren(gui_app.QLabel))
        self.assertIn("editing only works in a browser opened with it", texts)
        self.assertIn("Editing needs this link, even on this computer", texts)

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


    def test_trip_rows_hold_plain_text_and_no_per_row_widgets(self):
        from albummaker.gui.app import ZONE_ROLE
        window = self.make_window()
        window._add_trip_item(str(Path.home()), "")
        window._add_trip_item(str(Path.home() / "x"), "Europe/Paris")
        table = window.trip_table
        self.assertIsNone(table.cellWidget(0, 1))      # the 500-entry combo is made only when editing
        self.assertIsNone(table.cellWidget(1, 1))
        self.assertEqual(table.item(0, 1).text(), "Automatic")
        self.assertEqual(table.item(1, 1).text(), timezone_label("Europe/Paris"))
        self.assertIn("Europe/Paris", table.item(1, 1).toolTip())     # the IANA name is on hover
        self.assertEqual(table.item(1, 1).data(ZONE_ROLE), "Europe/Paris")
        self.assertEqual([t["timezone"] for t in window._settings_from_ui()["trips"]], ["", "Europe/Paris"])

    def test_editing_a_time_zone_resolves_what_was_picked_or_typed(self):
        from albummaker.gui.app import TimeZoneDelegate, ZONE_ROLE
        window = self.make_window()
        window._add_trip_item(str(Path.home()), "Europe/Paris")
        model = window.trip_table.model()
        index = model.index(0, 1)
        delegate = TimeZoneDelegate(window.trip_table)

        def edit(text=None, current=None):
            editor = delegate.createEditor(window.trip_table, None, index)
            delegate.setEditorData(editor, index)
            if current is not None:
                editor.setCurrentIndex(current)
            if text is not None:
                editor.setCurrentIndex(-1)
                editor.setEditText(text)
            delegate.setModelData(editor, model, index)
            return index.data(ZONE_ROLE), index.data()

        # the editor opens on the trip's current zone
        editor = delegate.createEditor(window.trip_table, None, index)
        delegate.setEditorData(editor, index)
        self.assertEqual(editor.currentData(), "Europe/Paris")
        brussels, bangkok = timezone_label("Europe/Brussels"), timezone_label("Asia/Bangkok")
        tokyo = timezone_label("Asia/Tokyo")
        # (the list does not depend on where the computer running the tests is)
        with mock.patch("albummaker.timezones.local_timezone_name", return_value="Asia/Tokyo"):
            self.assertEqual(edit(text=brussels.lower()), ("Europe/Brussels", brussels))
            self.assertEqual(edit(text="Asia/Bangkok"), ("Asia/Bangkok", bangkok))
            self.assertEqual(edit(text="not a zone"), ("Asia/Bangkok", bangkok))   # unchanged
            self.assertEqual(edit(text="brussels"), ("Europe/Brussels", brussels))  # just the city
            # this computer's zone is listed as "... · this computer"; its plain label works too
            self.assertEqual(edit(text=tokyo), ("Asia/Tokyo", tokyo))
            self.assertEqual(edit(text="automatic"), ("", "Automatic"))
            self.assertEqual(edit(text=""), ("", "Automatic"))

    def test_the_time_zone_list_has_pinned_zones_separators_and_remembers_picks(self):
        from PySide6.QtCore import Qt
        from albummaker.gui.app import TimeZoneDelegate, ZONE_ROLE
        window = self.make_window()
        window._add_trip_item(str(Path.home()), "Europe/Paris")
        model = window.trip_table.model()
        index = model.index(0, 1)
        picked = []
        delegate = TimeZoneDelegate(window.trip_table, recent_zones=lambda: ["Asia/Tokyo"],
                                    remember_zone=picked.append)
        with mock.patch("albummaker.timezones.local_timezone_name", return_value="Europe/Brussels"):
            editor = delegate.createEditor(window.trip_table, None, index)
        delegate.setEditorData(editor, index)
        self.assertEqual(editor.itemText(0), "Automatic")
        self.assertEqual(editor.itemData(2), "Europe/Brussels")       # this computer, after a separator
        self.assertEqual(editor.itemData(3), "Asia/Tokyo")            # then the recent zones
        self.assertEqual(editor.itemData(4), "UTC")
        separators = [row for row in range(editor.count())
                      if editor.itemData(row, Qt.ItemDataRole.AccessibleDescriptionRole) == "separator"]
        self.assertEqual(separators, [1, 5])
        self.assertEqual(editor.currentData(), "Europe/Paris")        # opens on the trip's own zone
        zones = [editor.itemData(row) for row in range(editor.count()) if row not in separators]
        self.assertEqual(len(zones), len(set(zones)))                 # nothing listed twice
        editor.setCurrentIndex(editor.findData("Asia/Tokyo"))
        delegate.setModelData(editor, model, index)
        self.assertEqual((index.data(ZONE_ROLE), picked), ("Asia/Tokyo", ["Asia/Tokyo"]))
        delegate.setModelData(editor, model, index)                   # same zone again: not a new pick
        self.assertEqual(picked, ["Asia/Tokyo"])

    def test_a_picked_time_zone_is_remembered_and_saved_with_the_settings(self):
        window = self.make_window()
        window._connect_persistence()
        window._save_timer.stop()
        window._remember_time_zone("Asia/Tokyo")
        window._remember_time_zone("Europe/Paris")
        window._remember_time_zone("Asia/Tokyo")
        self.assertEqual(window._settings_from_ui()["recent_time_zones"], ["Asia/Tokyo", "Europe/Paris"])
        self.assertTrue(window._save_timer.isActive())

    def test_a_changed_time_zone_is_saved(self):
        from albummaker.gui.app import ZONE_ROLE
        window = self.make_window()
        window._add_trip_item(str(Path.home()), "")
        window._connect_persistence()
        window._save_timer.stop()
        item = window.trip_table.item(0, 1)
        item.setData(ZONE_ROLE, "Asia/Tokyo")
        item.setText("Tokyo (Asia/Tokyo)")
        self.assertTrue(window._save_timer.isActive())

    def test_missing_folders_are_reported_by_the_background_check(self):
        import time
        window = self.make_window()
        here = str(Path.home())
        gone = str(Path(tempfile.gettempdir()) / "albummaker-no-such-folder-xyz")
        window._add_trip_item(here)
        window._add_trip_item(gone)
        window._check_folders(window._trip_paths())
        deadline = time.monotonic() + 5
        while window._folder_checker.busy and time.monotonic() < deadline:
            time.sleep(0.02)
        window._apply_folder_results()
        self.assertNotIn("Folder not found", window.trip_table.item(0, 0).toolTip())
        self.assertIn("Folder not found", window.trip_table.item(1, 0).toolTip())


class RunProgressTests(unittest.TestCase):
    """The progress pop-up reads the task's output lines and says what is going on."""

    def test_a_server_starting_says_so_before_any_line_arrives(self):
        progress = RunProgress("server")
        self.assertEqual(progress.heading, "Preparing your trips\u2026")
        self.assertIsNone(progress.fraction)                    # "busy", not a made-up percentage
        self.assertIn("keep AlbumMaker open", progress.hint)

    def test_a_server_names_the_trip_it_is_reading(self):
        progress = RunProgress("server")
        self.assertTrue(progress.feed("=== Trip 2 of 3: C:\\Photos\\Italy 2026  ->  /italy-2026/ ==="))
        self.assertIn("Trip 2 of 3: Italy 2026", progress.detail)
        progress.feed("Indexing C:\\Photos\\Italy 2026 ...")
        self.assertIn("Reading the photos and videos in Italy 2026", progress.detail)
        progress.feed("Found 120 media files (0 without a usable capture date, filed under Unsorted; 3 with GPS location).")
        self.assertIn("Found 120 photos and videos", progress.detail)
        progress.feed("Grouped into 6 day(s).")
        self.assertIn("Making the pages", progress.detail)
        self.assertAlmostEqual(progress.fraction, 1 / 3)        # two trips to go, one library of three

    def test_a_single_trip_has_no_trip_x_of_y(self):
        progress = RunProgress("server")
        progress.feed("Indexing /photos/Norway ...")
        self.assertEqual(progress.detail, "Reading the photos and videos in Norway\u2026")

    def test_a_site_build_shows_how_far_it_is(self):
        progress = RunProgress("build")
        progress.feed("Processing 4 file(s) with 2 worker process(es)...")
        self.assertEqual(progress.fraction, 0)
        progress.feed("[1/4] image IMG_1.jpg")
        progress.feed("[3/4] video MOV_2.mp4")
        self.assertEqual(progress.fraction, 0.75)
        self.assertEqual(progress.detail, "Preparing photo 3 of 4")

    def test_the_bar_of_a_library_build_counts_across_all_trips(self):
        progress = RunProgress("build")
        progress.feed("=== Trip 2 of 4: /photos/b  ->  /out/b ===")
        progress.feed("Processing 10 file(s) with 2 worker process(es)...")
        progress.feed("[5/10] image x.jpg")
        self.assertAlmostEqual(progress.fraction, (1 + 0.5) / 4)

    def test_unrelated_lines_change_nothing(self):
        progress = RunProgress("server")
        self.assertFalse(progress.feed("Maps and fonts load from the internet."))
        self.assertFalse(progress.feed(""))
        self.assertEqual(progress.detail, "Starting the server\u2026")

    def test_nothing_to_do_is_said_in_words(self):
        progress = RunProgress("build")
        progress.feed("Nothing to (re)generate.")
        self.assertIn("already up to date", progress.detail)

    def test_the_offline_download_explains_itself(self):
        progress = RunProgress("offline assets")
        self.assertIn("only needed once", progress.detail)
        self.assertIn("starts by itself", progress.hint)


if __name__ == "__main__":
    unittest.main()
