"""Tests for termapy.load_report: the record shape, the table and the session report.

The loaders' own rows (plugins, symbols, devices, the MCP profile) are
tested where each loader is: ``test_plugin_resolve.py``,
``test_sym_commands.py``, ``test_mcp_lifecycle.py``.  This file pins what
they share: one record shape, one table, one replace rule.
"""

from __future__ import annotations

from termapy import load_report
from termapy.load_report import DEVICE, PLUGIN, PROFILE, SYMBOLS, LoadReport, load_record


def _symbols_row(status: str) -> dict:
    return load_record(
        SYMBOLS,
        name="app.elf",
        layer="rig",
        file="sym/rig.symbols.json",
        count=40,
        detail={"source": "build/app.elf", "converter": "elf", "typed": 12, "status": status},
    )


def _notes(records: list[dict]) -> list[str]:
    """Each row's NOTE cell as printed."""
    note = load_report.COLUMNS[-1]
    return [note.cell(record) for record in records]


class TestRecord:
    def test_every_key_is_present_for_every_kind(self):
        """A bare row has the full key set, so a JSON consumer never meets a missing key."""
        # Act
        actual = load_record(DEVICE)

        # Assert
        expected = {
            "kind": "device",
            "name": "",
            "layer": "",
            "file": "",
            "count": 0,
            "detail": {},
            "error": "",
        }
        assert actual == expected, "one shape for every row"

    def test_profile_record_reads_the_installed_profile(self):
        """A profile row is named for its device and counts its commands."""
        # Arrange
        profile = {
            "profile_revision": "2.1",
            "device": {"name": "Widget"},
            "commands": {"PING": {}, "RESET": {}},
            "__source_path": "x",
        }

        # Act
        actual = load_report.profile_record(profile, file="rig.profile.json", layer="rig")

        # Assert
        assert actual["name"] == "Widget", "the device the profile describes"
        assert actual["count"] == 2, "its commands, not its keys"
        assert actual["detail"] == {"revision": "2.1"}, "the revision rides in detail"


class TestNotes:
    def test_stale_symbols_say_so_and_unknown_does_not(self):
        """Only a positive stale verdict reaches the NOTE, as on /sym.info."""
        # Act
        actual = _notes([_symbols_row("stale"), _symbols_row("unknown")])

        # Assert
        expected = ["elf, 12 typed, STALE", "elf, 12 typed"]
        assert actual == expected, "unknown is the normal state of a hand-written table"

    def test_plugin_note_names_each_kind_it_registered(self):
        """The NOTE lists the non-empty kinds in words, no sigils."""
        # Arrange
        row = load_record(
            PLUGIN,
            detail={"commands": ["a", "a.b"], "transforms": [], "hooks": ["on_connect"]},
        )

        # Act
        actual = _notes([row])

        # Assert
        assert actual == ["commands a, a.b; hooks on_connect"], "empty kinds are left out"

    def test_error_row_shows_the_error_and_no_count(self):
        """A failed file's NOTE is its error and its COUNT is blank, not a misleading 0."""
        # Arrange
        row = load_record(DEVICE, error="bad.device.json: invalid JSON")

        # Act
        lines = [text for text, _ in load_report.result([row]).view.lines()]

        # Assert
        assert lines[-1].split() == ["device", "error:", "bad.device.json:", "invalid", "JSON"], (
            "kind, then the error; the blank name, layer, file and count take no cell text"
        )


class TestResult:
    def test_table_and_data_are_the_same_rows(self):
        """The prose table and the JSON data come from one list of records."""
        # Arrange
        records = [
            load_record(PLUGIN, name="x", layer="global", file="plugin/x.py", count=1),
            load_record(PLUGIN, name="y", layer="rig", file="plugin/y.py", error="boom"),
        ]

        # Act
        result = load_report.result(records)

        # Assert
        assert result.data == records, "data is the rows"
        assert result.view.records == tuple(records), "the table shows the same rows"
        assert result.value == "plugin/x.py", "value is the files that loaded"
        assert result.view.lines()[0] == ("Loaded - 2 files", ""), "titled with the file count"

    def test_empty_report_reads_empty(self):
        """/cfg.loaded with nothing loaded says so rather than printing a bare header."""
        # Act
        actual = load_report.result([]).view.lines()

        # Assert
        assert actual == [("Loaded - empty", "")], "the empty table's own form"


class TestLoadReport:
    def test_replace_twice_keeps_only_the_second(self):
        """Each config load's rows are the whole report: replaced, never appended."""
        # Arrange
        report = LoadReport()
        first = [load_record(PLUGIN, file="plugin/a.py")]
        second = [load_record(PLUGIN, file="plugin/b.py")]

        # Act
        report.replace(first)
        report.replace(second)

        # Assert
        assert report.records == second, "the second load alone"

    def test_put_twice_keeps_one_row_of_the_kind(self):
        """Putting a kind twice (a reconnect) replaces its rows and keeps the others."""
        # Arrange
        report = LoadReport()
        plugin = load_record(PLUGIN, file="plugin/a.py")
        report.replace([plugin])

        # Act
        report.put(PROFILE, [load_record(PROFILE, name="one")])
        report.put(PROFILE, [load_record(PROFILE, name="two")])

        # Assert
        actual = [record["name"] for record in report.records]
        assert actual == ["", "two"], "the plugin row stays, one profile row, the latest"

    def test_put_nothing_drops_the_kind(self):
        """A disconnect puts no profile rows, which removes the one there."""
        # Arrange
        report = LoadReport()
        report.put(PROFILE, [load_record(PROFILE, name="one")])

        # Act
        report.put(PROFILE, [])

        # Assert
        assert report.records == [], "no profile, no row"

    def test_readers_get_a_copy(self):
        """Changing what a reader got never changes the report."""
        # Arrange
        report = LoadReport()
        report.replace([load_record(PLUGIN, file="plugin/a.py")])

        # Act
        report.records.clear()

        # Assert
        assert len(report.records) == 1, "the report is unchanged"
