import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tripsite.gui.commands import build_command, offline_assets_command, server_command
from tripsite.gui.settings import (
    SettingsError,
    default_settings,
    load_settings,
    prepare_metadata_cache,
    prepare_tile_cache,
    save_settings,
    write_library_file,
)
from tripsite.library import read_trip_list, trip_args


class GuiSettingsTests(unittest.TestCase):
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

    def test_legacy_trip_paths_are_migrated_and_time_zones_are_validated(self):
        settings = default_settings()
        settings["trips"] = ["/photos/Italy"]
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
            trip = Path(tmp) / "Trip|Name"
            trip.mkdir()
            with self.assertRaisesRegex(SettingsError, "cannot contain"):
                write_library_file([str(trip)], Path(tmp) / "library.txt")


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

    def test_server_command_includes_editor_link_and_server_settings(self):
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
        self.assertEqual(command[command.index("--port") + 1], "8123")
        self.assertEqual(
            command[command.index("--tile-cache") + 1],
            self.settings["advanced"]["tile_cache"],
        )
        from tripsite.server import build_parser
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
        self.settings["build"].update({
            "no_dedupe": True,
            "no_live_photos": True,
            "workers": 3,
            "force": True,
            "gpx_files": ["/tracks/route.gpx"],
        })
        output = str(self.root / "Generated Site")
        command = build_command("python", self.library, output, self.settings)

        from tripsite.build import build_parser
        build_parser().parse_args(command[command.index("build") + 1:])
        self.assertIn("--no-dedupe", command)
        self.assertIn("--no-live-photos", command)
        self.assertIn("--force", command)
        self.assertEqual(command[command.index("--workers") + 1], "3")
        self.assertEqual(command[command.index("--gpx") + 1], "/tracks/route.gpx")
        self.assertEqual(command[command.index("--output") + 1], output)
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
            ["python", "-u", "-m", "tripsite", "serve", "--fetch-offline-assets"],
        )

    def test_zipapp_launches_its_bundled_cli(self):
        with mock.patch("tripsite.gui.commands.sys.argv", ["/tmp/tripsite.pyz"]):
            command = offline_assets_command("python")
        self.assertEqual(
            command,
            ["python", "-u", "/tmp/tripsite.pyz", "serve", "--fetch-offline-assets"],
        )

    def test_explicit_ignore_options_take_precedence(self):
        self.settings["build"]["no_date_check"] = True
        self.settings["build"]["no_gpx"] = True
        self.settings["build"]["gpx_files"] = ["/tracks/ignored.gpx"]
        command = build_command("python", self.library, str(self.root / "out"), self.settings)

        self.assertIn("--no-date-check", command)
        self.assertIn("--no-gpx", command)
        self.assertNotIn("--date-range", command)
        self.assertNotIn("--gpx", command)


if __name__ == "__main__":
    unittest.main()
