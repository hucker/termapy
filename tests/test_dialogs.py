"""Behavioral tests for the modal dialogs' result contracts.

The dialogs are already self-contained ModalScreens, so they need no
extraction to be testable: push one in a bare host app, drive it with
Pilot, and assert what it dismisses with.  The dismiss values are
load-bearing (app.py callbacks branch on them -- see the modal-result
conventions in CLAUDE.md) and had no tests pinning them.
"""

from __future__ import annotations

import asyncio
import json
import os
import time

from textual.app import App
from textual.widgets import Input, OptionList

from termapy.defaults import DEFAULT_CFG
from termapy.dialogs import ConfirmDialog, FilenameDialog, ScriptPicker
from termapy.dialogs.config_picker import _config_details


class _Host(App):
    """Bare host: no widgets of its own, just a stage for modals."""


def _run(scenario) -> None:
    asyncio.run(scenario())


class TestConfirmDialog:
    def test_yes_dismisses_true(self):
        async def scenario():
            app = _Host()
            async with app.run_test() as pilot:
                results: list[bool] = []
                app.push_screen(ConfirmDialog("Delete it?"), callback=results.append)
                await pilot.pause()
                await pilot.click("#confirm-yes")
                await pilot.pause()
                assert results == [True], "Yes dismisses with True"

        _run(scenario)

    def test_cancel_dismisses_false(self):
        async def scenario():
            app = _Host()
            async with app.run_test() as pilot:
                results: list[bool] = []
                app.push_screen(ConfirmDialog("Delete it?"), callback=results.append)
                await pilot.pause()
                await pilot.click("#confirm-no")
                await pilot.pause()
                assert results == [False], "Cancel dismisses with False"

        _run(scenario)

    def test_escape_dismisses_false(self):
        async def scenario():
            app = _Host()
            async with app.run_test() as pilot:
                results: list[bool] = []
                app.push_screen(ConfirmDialog("Delete it?"), callback=results.append)
                await pilot.pause()
                await pilot.press("escape")
                await pilot.pause()
                assert results == [False], "Escape is a safe no (False)"

        _run(scenario)


class TestFilenameDialog:
    def test_submit_dismisses_with_stripped_name(self):
        async def scenario():
            app = _Host()
            async with app.run_test() as pilot:
                results: list[str | None] = []
                app.push_screen(FilenameDialog(), callback=results.append)
                await pilot.pause()
                inp = app.screen.query_one("#filename-input", Input)
                inp.value = "  capture1  "
                inp.focus()
                await pilot.pause()
                await pilot.press("enter")
                await pilot.pause()
                assert results == ["capture1"], "submit returns the stripped name"

        _run(scenario)

    def test_cancel_dismisses_none(self):
        async def scenario():
            app = _Host()
            async with app.run_test() as pilot:
                results: list[str | None] = []
                app.push_screen(FilenameDialog(), callback=results.append)
                await pilot.pause()
                await pilot.click("#filename-cancel")
                await pilot.pause()
                assert results == [None], "Cancel dismisses with None"

        _run(scenario)

    def test_empty_submit_keeps_dialog_open(self):
        async def scenario():
            app = _Host()
            async with app.run_test() as pilot:
                results: list[str | None] = []
                app.push_screen(FilenameDialog(), callback=results.append)
                await pilot.pause()
                inp = app.screen.query_one("#filename-input", Input)
                inp.focus()
                await pilot.pause()
                await pilot.press("enter")
                await pilot.pause()
                assert results == [], "empty submit must not dismiss"
                assert isinstance(app.screen, FilenameDialog), "dialog stays open"

        _run(scenario)

    def test_escape_dismisses_none(self):
        async def scenario():
            app = _Host()
            async with app.run_test() as pilot:
                results: list[str | None] = []
                app.push_screen(FilenameDialog(), callback=results.append)
                await pilot.pause()
                await pilot.press("escape")
                await pilot.pause()
                assert results == [None], "Escape cancels with None"

        _run(scenario)


class TestScriptPicker:
    """The file pickers list newest first and show size, age, and a detail column."""

    def test_rows_show_newest_first_with_size_age_and_summary(self, tmp_path):
        async def scenario():
            # Arrange -- the script that sorts FIRST by name is an hour old.
            run_dir = tmp_path / "run"
            run_dir.mkdir()
            older = run_dir / "a_older.run"
            older.write_bytes(b"# Old summary.\n/echo old\n")
            hour_ago = time.time() - 3600
            os.utime(older, (hour_ago, hour_ago))
            newer = run_dir / "z_newer.run"
            newer.write_bytes(b"/echo new\n")

            app = _Host()
            async with app.run_test() as pilot:
                results: list = []
                app.push_screen(ScriptPicker(run_dir), callback=results.append)
                await pilot.pause()
                ol = app.screen.query_one("#script-list", OptionList)
                options = [ol.get_option_at_index(i) for i in range(ol.option_count)]
                rows = [str(option.prompt) for option in options]

                # Assert -- header, order, columns, and the id the app will act on
                header, *rows = rows
                assert header.startswith("NAME") and header.endswith("SUMMARY"), (
                    f"a header row names the columns: {header!r}"
                )
                assert options[0].disabled and options[0].id is None, (
                    "the header is a disabled option: skipped by the cursor, never selected"
                )
                assert header.index("SIZE") + len("SIZE") == rows[0].index("10 B") + len("10 B"), (
                    "SIZE is right-aligned over the size cells like the values"
                )
                assert header.index("UPDATED") == rows[0].index("just now"), (
                    "UPDATED sits over the age column"
                )
                assert rows[0].startswith("z_newer.run"), (
                    f"newest file lists first even though it sorts last by name: {rows}"
                )
                assert "10 B" in rows[0] and "just now" in rows[0], "size and age columns"
                assert "1 hr ago" in rows[1] and rows[1].endswith("Old summary."), (
                    "age plus the docstring summary as the detail column"
                )
                assert options[1].id == str(newer), "option id is the path the app runs"
                assert ol.highlighted == 1, "initial highlight is the first FILE, not the header"

                # Act -- Enter runs the highlighted (newest) script
                await pilot.press("enter")
                await pilot.pause()
                assert results == [("run", str(newer))], "Enter dismisses with the newest"

        _run(scenario)


class TestConfigPickerDetails:
    """The config picker's detail cell: ``PORT  BAUD  TITLE`` columns padded
    across the batch (a macOS port is ~30 characters, a Windows one 4) so
    every column lines up under its header."""

    @staticmethod
    def _write_cfg(tmp_path, name: str, port: str, title: str):
        folder = tmp_path / name
        folder.mkdir()
        path = folder / f"{name}.cfg"
        serial = DEFAULT_CFG["serial"]
        assert isinstance(serial, dict), "DEFAULT_CFG['serial'] is a dict"
        cfg = {**DEFAULT_CFG, "serial": {**serial, "port": port}, "title": title}
        path.write_text(json.dumps(cfg), encoding="utf-8")
        return path

    def test_titles_align_after_the_widest_port(self, tmp_path):
        # Arrange
        mac = self._write_cfg(tmp_path, "mac", "/dev/cu.usbserial-A50285BI", "Bench board")
        win = self._write_cfg(tmp_path, "win", "COM4", "Logger")
        untitled = self._write_cfg(tmp_path, "bare", "COM7", "")
        # New configs are written with their own name as the title.
        self_named = self._write_cfg(tmp_path, "seven", "COM7", "Seven")

        # Act
        details, header = _config_details([mac, win, untitled, self_named])

        # Assert
        assert details[mac] == "/dev/cu.usbserial-A50285BI  115200  Bench board"
        assert details[win].index("Logger") == details[mac].index("Bench board"), (
            "the short port is padded so the title column lines up"
        )
        assert header.startswith("PORT") and header.endswith("TITLE")
        assert header.index("TITLE") == details[mac].index("Bench board"), (
            "the TITLE header sits over the title column"
        )
        baud_end = details[mac].index("115200") + len("115200")
        assert header.index("BAUD") + len("BAUD") == baud_end, (
            "BAUD is right-aligned over the baud numbers"
        )
        assert details[untitled].split() == ["COM7", "115200"], "port and baud, no title"
        assert not details[untitled].endswith(" "), "an empty title leaves no trailing padding"
        assert details[self_named].split() == ["COM7", "115200"], (
            "a title that only repeats the config name is shown as blank"
        )

    def test_unloadable_cfg_gets_an_empty_cell(self, tmp_path):
        # Arrange -- not JSON at all
        broken = tmp_path / "broken.cfg"
        broken.write_text("{not json", encoding="utf-8")

        # Act
        details, _ = _config_details([broken])

        # Assert -- a row, not a crash
        assert details == {broken: ""}
