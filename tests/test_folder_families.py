"""Every data folder has the one command family, from its FolderSpec row: sym, dev, dev.lib, proto.viz and firmware join the rest.

The guard that holds each family to its row is ``tests/test_architecture.py``;
these tests drive the families mounted on 2026-11-24 through the CLI against
the DEMO rig, and the migration that moved the shipped firmware catalog under
``/firmware.lib``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from termapy.firmware import find_target, source_text
from termapy.folder_ops import build_folder_subcommands, edit_command
from termapy.folders import FOLDERS
from termapy.migration import CURRENT_CONFIG_VERSION, migrate_config

pytestmark = pytest.mark.rule("folder-family-from-table")

# A device file as /dev.import writes one: a part with one register.
DEVICE_DOC = {
    "device_version": 1,
    "device": "probe",
    "registers": [{"name": "CTRL", "addr": "0x40000000", "size": 4}],
}


def _folder(cli, name: str) -> Path:
    """The rig's data folder ``name``, created (a test writes into it, as a command would)."""
    folder = Path(cli.ctx.config_path).parent / name
    folder.mkdir(exist_ok=True)
    return folder


def _json_envelope(cli, capsys, line: str) -> dict:
    """Dispatch ``line`` with --json and return the envelope it printed."""
    capsys.readouterr()
    cli.repl.dispatch_full(f"{line} --json")
    return json.loads(capsys.readouterr().out.strip().splitlines()[-1])


class TestTheTable:
    def test_every_row_declares_a_family(self):
        """No FolderSpec is listless: each names the command its family hangs under."""
        assert [spec.name for spec in FOLDERS if not spec.family] == []

    def test_a_list_counts_in_the_rows_noun(self):
        """build_folder_subcommands reads the noun from the row: run/ counts scripts, lib/ parts, cap/ files."""
        actual = {
            name: build_folder_subcommands(name)["list"].handler.__doc__ or ""
            for name in ("run", "lib", "cap")
        }
        assert "scripts" in actual["run"], "run/ holds scripts"
        assert "parts" in actual["lib"], "lib/ holds parts"
        assert "files" in actual["cap"], "cap/ keeps the default noun"

    def test_a_mixed_folder_edits_a_file_not_a_star_file(self):
        """edit_command on a folder of mixed content says file, never * file."""
        actual = (edit_command("firmware").help, edit_command("run").help)
        expected = ("Open a file for editing.", "Open a .run file for editing.")
        assert actual == expected


class TestSym:
    def test_sym_list_answers_the_folders_tables(self, cli, capsys):
        """/sym.list lists sym/, a count as value and file records as data, and --json carries the same records."""
        # Arrange
        (_folder(cli, "sym") / "rig.symbols.json").write_text("{}", encoding="utf-8")

        # Act
        result = cli.repl.dispatch("sym.list")
        envelope = _json_envelope(cli, capsys, "/sym.list")

        # Assert
        assert result.success, result.error
        assert result.value == 1, "the count is the value"
        assert [record["name"] for record in result.data] == ["rig.symbols.json"]
        assert [record["name"] for record in envelope["data"]] == ["rig.symbols.json"], (
            "the JSON answer is the records"
        )
        assert set(envelope["data"][0]) == {"name", "bytes", "mtime", "age_s"}, (
            "the record every .list answers"
        )
        assert envelope["output_lines"] == [], "the table is not repeated as text"

    def test_sym_dump_prints_the_newest_table(self, cli):
        """/sym.dump with no name prints the newest file in sym/, its text the value."""
        (_folder(cli, "sym") / "rig.symbols.json").write_text('{"symbols": []}', encoding="utf-8")

        result = cli.repl.dispatch("sym.dump")

        assert result.success, result.error
        assert result.value == '{"symbols": []}', "the file text is the scriptable value"


class TestDev:
    def test_dev_list_stays_the_loaded_devices(self, cli):
        """/dev.list is the family's list replaced by the devices this config loaded, one record per device."""
        # Arrange -- a device file, loaded the way /dev.use leaves one
        (_folder(cli, "dev") / "probe.device.json").write_text(
            json.dumps(DEVICE_DOC), encoding="utf-8"
        )
        cli.repl.reload_devices()

        # Act
        result = cli.repl.dispatch("dev.list")

        # Assert
        assert result.success, result.error
        assert [record["device"] for record in result.data] == ["probe"], "the device, not the file"

    def test_dev_delete_removes_the_part_from_the_config(self, cli):
        """/dev.delete <part> deletes its dev/ file after a yes, and the device is unloaded at once."""
        # Arrange
        folder = _folder(cli, "dev")
        (folder / "probe.device.json").write_text(json.dumps(DEVICE_DOC), encoding="utf-8")
        cli.repl.reload_devices()
        assert cli.repl.dispatch("dev.list").value == 1, "loaded before the delete"
        cli.ctx.ui._confirm_impl = lambda question: True  # the person answers yes

        # Act
        result = cli.repl.dispatch("dev.delete probe")

        # Assert
        assert result.success, result.error
        assert not (folder / "probe.device.json").exists(), "the file is gone"
        assert cli.repl.dispatch("dev.list").value == 0, "and so is the device"


class TestLib:
    def test_dev_lib_list_is_the_parts(self, cli):
        """/dev.lib.list answers the library's parts as bare /dev.lib does, and a pattern filters them."""
        bare = cli.repl.dispatch("dev.lib")
        listed = cli.repl.dispatch("dev.lib.list")
        filtered = cli.repl.dispatch("dev.lib.list zzz*")

        assert listed.success and bare.success, (listed.error, bare.error)
        assert listed.data == bare.data, "one listing, two spellings"
        assert filtered.success and filtered.value == 0, "a pattern matching nothing is empty"

    def test_dev_lib_dump_reads_a_part_by_its_path_in_the_tree(self, cli):
        """/dev.lib.dump <vendor/part> prints a file nested in this config's lib/ tree."""
        part = _folder(cli, "lib") / "acme" / "adc"
        part.mkdir(parents=True)
        (part / "adc16.device.json").write_text("{}", encoding="utf-8")

        result = cli.repl.dispatch("dev.lib.dump acme/adc/adc16")

        assert result.success, result.error
        assert result.value == "{}", "the extension is optional and the path stays in lib/"


class TestViz:
    def test_proto_viz_lists_this_configs_visualizers(self, cli, capsys):
        """/proto.viz lists viz/ (bare is .list): empty, then the one file dropped in, counted as a visualizer."""
        before = cli.repl.dispatch("proto.viz")
        (_folder(cli, "viz") / "mine.py").write_text("NAME = 'Mine'\n", encoding="utf-8")
        capsys.readouterr()
        after = cli.repl.dispatch("proto.viz.list")

        assert before.success and before.value == 0, before.error
        assert after.success and after.value == 1, after.error
        assert "viz/ - 1 visualizer" in capsys.readouterr().out, "the table counts visualizers"


class TestFirmware:
    def test_firmware_lib_is_the_catalog_and_list_the_folder(self, cli):
        """/firmware.lib (bare /firmware) is what ships; /firmware.list is this config's firmware/, which .copy fills."""
        # Arrange / Act
        catalog = cli.repl.dispatch("firmware.lib")
        empty = cli.repl.dispatch("firmware.list")
        copied = cli.repl.dispatch("firmware.copy mem")
        filled = cli.repl.dispatch("firmware.list")

        # Assert
        assert catalog.success and "mem" in [record["name"] for record in catalog.data]
        assert empty.success and empty.value == 0, "nothing copied yet"
        assert copied.success, copied.error
        assert sorted(record["name"] for record in filled.data) == [
            "termapy_mem.c",
            "termapy_mem.h",
        ], "the folder lists what was copied"

    def test_firmware_lib_dump_is_the_shipped_file(self, cli):
        """/firmware.lib.dump <module> <file> prints a shipped source; /firmware.dump prints a copied one."""
        cli.repl.dispatch("firmware.copy mem")

        shipped = cli.repl.dispatch("firmware.lib.dump mem termapy_mem.h")
        copied = cli.repl.dispatch("firmware.dump termapy_mem.h")

        expected = source_text(find_target("mem"), "termapy_mem.h")
        assert shipped.success and shipped.value == expected, shipped.error
        assert copied.success and copied.value == expected, copied.error


class TestMigration:
    def test_v41_to_v42_renames_the_catalog_verbs(self):
        """A v41 config's /firmware.list, .show and .dump become /firmware.lib, .lib.show and .lib.dump wherever it holds a command line."""
        cfg = {
            "config_version": 41,
            "on_connect_cmd": "/firmware.list",
            "custom_buttons": [
                {"label": "Src", "command": "/firmware.show json"},
                {"label": "Hdr", "command": "/firmware.dump mem termapy_mem.h"},
                {"label": "Copy", "command": "/firmware.copy mem"},
            ],
        }

        migrated = migrate_config(cfg)

        actual = [migrated["on_connect_cmd"]] + [
            button["command"] for button in migrated["custom_buttons"]
        ]
        expected = [
            "/firmware.lib",
            "/firmware.lib.show json",
            "/firmware.lib.dump mem termapy_mem.h",
            "/firmware.copy mem",
        ]
        assert actual == expected
        assert migrated["config_version"] == CURRENT_CONFIG_VERSION
