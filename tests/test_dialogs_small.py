"""The seven small dialogs: what each shows and what it dismisses with.

Pushed into a bare host app and driven with Pilot (the
``tests/test_dialogs.py`` pattern).  The two file editors share one
shape, so their tests are parametrized over the pair.  The port picker's
list always ends with the pyserial loopback row, which keeps its
selection test independent of the machine's ports.
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

import pytest
from textual.app import App
from textual.widgets import Button, Input, OptionList, Static, TextArea

from termapy.defaults import PROTO_TEMPLATE, SCRIPT_TEMPLATE
from termapy.dialogs import (
    CfgConfirm,
    PortPicker,
    ProtoEditor,
    ProtoPicker,
    ScriptEditor,
    SetVarDialog,
    UpdateAvailableDialog,
)
from termapy.dialogs._common import PathLink


class _Host(App):
    """Bare host: no widgets of its own, just a stage for modals."""


# Wide enough for the pickers' button rows and the port picker's table.
_SIZE = (160, 50)


def _run(scenario) -> None:
    asyncio.run(scenario())


async def _push(app: App, pilot, screen):
    """Push ``screen`` and return the list its dismiss value lands in."""
    results: list = []
    app.push_screen(screen, callback=results.append)
    await pilot.pause()
    return results


async def _submit(pilot, widget: Input, value: str) -> None:
    """Type ``value`` into ``widget`` and press Enter."""
    widget.value = value
    widget.focus()
    await pilot.pause()
    await pilot.press("enter")
    await pilot.pause()


def _plain(widget: Static) -> str:
    return str(widget.render())


async def _click_again(pilot, selector: str) -> None:
    """Click a button that was just clicked.

    Textual drops a click while the button's 0.2 s active effect is still
    showing (``Button._on_click`` checks for the ``-active`` class), so a
    repeat click right after the first is silently lost.
    """
    button = pilot.app.screen.query_one(selector, Button)
    for _ in range(50):
        if not button.has_class("-active"):
            break
        await pilot.pause(0.02)
    await pilot.click(selector)
    await pilot.pause()


def _age(path: Path, seconds: float) -> None:
    """Push a file's mtime forward, past any filesystem timestamp coarseness."""
    when = time.time() + seconds
    os.utime(path, (when, when))


# ── ProtoPicker ──────────────────────────────────────────────────────────────


class TestProtoPicker:
    def test_the_title_path_follows_the_highlight_and_a_click_reveals_it(self, tmp_path):
        """The title row shows the highlighted .pro file; moving the highlight moves it, and a click reveals that file."""

        async def scenario():
            # Arrange -- newer.pro lists first
            older = tmp_path / "older.pro"
            newer = tmp_path / "newer.pro"
            older.write_text("# a\n", encoding="utf-8")
            newer.write_text("# b\n", encoding="utf-8")
            _age(newer, 60)
            opened: list[str] = []
            app = _Host()
            async with app.run_test(size=_SIZE) as pilot:
                await _push(app, pilot, ProtoPicker(tmp_path, opener=opened.append))
                link = app.screen.query_one("#proto-path", PathLink)
                first_shown = link.path

                # Act
                app.screen.query_one("#proto-list", OptionList).highlighted = 2
                await pilot.pause()
                await pilot.click("#proto-path")
                await pilot.pause()

                # Assert
                assert first_shown == newer.resolve(), "it opens on the newest script"
                assert link.path == older.resolve(), "the path follows the highlight"
                assert opened == [str(older.resolve())], "a click reveals the highlighted file"

        _run(scenario)

    def test_rows_show_the_name_each_script_declares(self, tmp_path):
        """A TITLE column holds each script's declared name, as the Run picker holds a summary."""

        async def scenario():
            # Arrange
            (tmp_path / "read.pro").write_text(
                'name = "Modbus read"\n[[test]]\nsend = "01"\nexpect = "02"\n', encoding="utf-8"
            )
            app = _Host()
            async with app.run_test(size=_SIZE) as pilot:
                await _push(app, pilot, ProtoPicker(tmp_path, opener=lambda path: None))
                ol = app.screen.query_one("#proto-list", OptionList)
                header, row = (str(ol.get_option_at_index(i).prompt) for i in range(2))

                # Assert
                assert header.rstrip().endswith("TITLE"), f"a TITLE column: {header!r}"
                assert row.rstrip().endswith("Modbus read"), f"holding the name: {row!r}"

        _run(scenario)

    def test_an_empty_folder_shows_the_folder_path(self, tmp_path):
        """With nothing to highlight the title row shows the folder the picker lists."""

        async def scenario():
            app = _Host()
            async with app.run_test(size=_SIZE) as pilot:
                await _push(app, pilot, ProtoPicker(tmp_path, opener=lambda path: None))

                # Assert
                actual = app.screen.query_one("#proto-path", PathLink).path
                assert actual == tmp_path.resolve(), "the folder stands in for a file"

        _run(scenario)

    def test_empty_folder_leaves_only_new_and_cancel_live(self, tmp_path):
        """With no scripts every per-file button is disabled; New stays live; Cancel dismisses None."""

        async def scenario():
            app = _Host()
            async with app.run_test(size=_SIZE) as pilot:
                results = await _push(app, pilot, ProtoPicker(tmp_path))
                screen = app.screen
                disabled = {
                    name: screen.query_one(f"#proto-{name}", Button).disabled
                    for name in ("run", "debug", "edit", "new", "rename", "delete", "cancel")
                }
                await pilot.click("#proto-cancel")
                await pilot.pause()
                assert disabled == {
                    "run": True,
                    "debug": True,
                    "edit": True,
                    "new": False,
                    "rename": True,
                    "delete": True,
                    "cancel": False,
                }
                assert results == [None]

        _run(scenario)

    @pytest.mark.parametrize("button", ["run", "debug", "edit", "rename", "delete"])
    def test_each_file_button_dismisses_with_its_verb_and_the_newest_script(self, tmp_path, button):
        """Each per-file button dismisses with (verb, path) for the highlighted row, which is the newest script."""

        async def scenario():
            older = tmp_path / "older.pro"
            newer = tmp_path / "newer.pro"
            older.write_text("# a\n", encoding="utf-8")
            newer.write_text("# b\n", encoding="utf-8")
            _age(newer, 60)
            app = _Host()
            async with app.run_test(size=_SIZE) as pilot:
                results = await _push(app, pilot, ProtoPicker(tmp_path))
                await pilot.click(f"#proto-{button}")
                await pilot.pause()
                assert results == [(button, str(newer))], "the newest is highlighted first"

        _run(scenario)

    def test_enter_in_the_list_runs_the_highlighted_script(self, tmp_path):
        """Enter with the list focused is Run."""

        async def scenario():
            script = tmp_path / "only.pro"
            script.write_text("# x\n", encoding="utf-8")
            app = _Host()
            async with app.run_test(size=_SIZE) as pilot:
                results = await _push(app, pilot, ProtoPicker(tmp_path))
                app.screen.query_one("#proto-list", OptionList).focus()
                await pilot.pause()
                await pilot.press("enter")
                await pilot.pause()
                assert results == [("run", str(script))]

        _run(scenario)

    def test_new_and_escape(self, tmp_path):
        """New dismisses with ("new",); Escape with None."""

        async def scenario():
            app = _Host()
            async with app.run_test(size=_SIZE) as pilot:
                results = await _push(app, pilot, ProtoPicker(tmp_path))
                await pilot.click("#proto-new")
                await pilot.pause()
                results_escape = await _push(app, pilot, ProtoPicker(tmp_path))
                await pilot.press("escape")
                await pilot.pause()
                assert (results, results_escape) == ([("new",)], [None])

        _run(scenario)

    def test_read_only_disables_the_editing_buttons_but_not_run(self, tmp_path):
        """A read-only picker keeps Run and Debug live and disables Edit, Rename, and Delete even with scripts present."""

        async def scenario():
            (tmp_path / "a.pro").write_text("# a\n", encoding="utf-8")
            app = _Host()
            async with app.run_test(size=_SIZE) as pilot:
                await _push(app, pilot, ProtoPicker(tmp_path, read_only=True))
                screen = app.screen
                state = {
                    name: screen.query_one(f"#proto-{name}", Button).disabled
                    for name in ("run", "debug", "edit", "rename", "delete")
                }
                assert state == {
                    "run": False,
                    "debug": False,
                    "edit": True,
                    "rename": True,
                    "delete": True,
                }

        _run(scenario)


# ── SetVarDialog ─────────────────────────────────────────────────────────────


class TestSetVarDialog:
    def test_enter_on_the_name_moves_to_the_value_then_submits_the_pair(self, tmp_path):
        """Enter on a filled name focuses the value field; Enter there dismisses with (name stripped, value as typed)."""

        async def scenario():
            app = _Host()
            async with app.run_test(size=_SIZE) as pilot:
                results = await _push(app, pilot, SetVarDialog())
                name = app.screen.query_one("#setvar-name", Input)
                value = app.screen.query_one("#setvar-value", Input)
                await _submit(pilot, name, "  timeout ")
                moved = app.focused is value
                await _submit(pilot, value, " 5000 ")
                assert moved is True, "Enter on the name is a tab to the value"
                assert results == [("timeout", " 5000 ")], "name stripped, value kept as typed"

        _run(scenario)

    def test_an_empty_name_is_not_a_submit(self, tmp_path):
        """Enter on an empty name stays put; Enter on the value with no name sends focus back to the name."""

        async def scenario():
            app = _Host()
            async with app.run_test(size=_SIZE) as pilot:
                results = await _push(app, pilot, SetVarDialog())
                name = app.screen.query_one("#setvar-name", Input)
                value = app.screen.query_one("#setvar-value", Input)
                await _submit(pilot, name, "   ")
                stayed = app.focused is name
                await _submit(pilot, value, "5000")
                assert stayed is True, "nothing to move on to"
                assert results == [] and app.focused is name, "the missing name is pointed at"

        _run(scenario)

    def test_cancel_and_escape_dismiss_none(self, tmp_path):
        """Cancel and Escape both dismiss with None."""

        async def scenario():
            app = _Host()
            async with app.run_test(size=_SIZE) as pilot:
                results = await _push(app, pilot, SetVarDialog())
                await pilot.click("#setvar-cancel")
                await pilot.pause()
                results_escape = await _push(app, pilot, SetVarDialog())
                await pilot.press("escape")
                await pilot.pause()
                assert (results, results_escape) == ([None], [None])

        _run(scenario)


# ── UpdateAvailableDialog ────────────────────────────────────────────────────


class TestUpdateAvailableDialog:
    def test_shows_both_versions_and_info_asks_for_the_help_page(self, tmp_path):
        """The body names the installed and latest versions; Info dismisses with "info"."""

        async def scenario():
            app = _Host()
            async with app.run_test(size=_SIZE) as pilot:
                results = await _push(app, pilot, UpdateAvailableDialog("0.76.0", "0.77.0"))
                body = _plain(app.screen.query_one("#update-body", Static))
                await pilot.click("#update-info")
                await pilot.pause()
                assert "Installed:  0.76.0" in body and "Latest:     0.77.0" in body
                assert results == ["info"]

        _run(scenario)

    def test_ok_and_escape_both_mean_ok(self, tmp_path):
        """OK and Escape dismiss with "ok", so the caller just closes."""

        async def scenario():
            app = _Host()
            async with app.run_test(size=_SIZE) as pilot:
                results = await _push(app, pilot, UpdateAvailableDialog("1", "2"))
                await pilot.click("#update-ok")
                await pilot.pause()
                results_escape = await _push(app, pilot, UpdateAvailableDialog("1", "2"))
                await pilot.press("escape")
                await pilot.pause()
                assert (results, results_escape) == (["ok"], ["ok"])

        _run(scenario)


# ── CfgConfirm ───────────────────────────────────────────────────────────────


class TestCfgConfirm:
    def test_shows_the_change_and_yes_confirms(self, tmp_path):
        """The message reads key: old -> new; Yes dismisses True."""

        async def scenario():
            app = _Host()
            async with app.run_test(size=_SIZE) as pilot:
                results = await _push(app, pilot, CfgConfirm("baud_rate", 9600, 115200))
                message = _plain(app.screen.query_one("#cfg-confirm-msg", Static))
                await pilot.click("#cfg-yes")
                await pilot.pause()
                assert message == "baud_rate: 9600 -> 115200"
                assert results == [True]

        _run(scenario)

    def test_no_and_escape_both_refuse(self, tmp_path):
        """No and Escape dismiss False, so the change is not applied."""

        async def scenario():
            app = _Host()
            async with app.run_test(size=_SIZE) as pilot:
                results = await _push(app, pilot, CfgConfirm("eol", "\r", "\n"))
                await pilot.click("#cfg-no")
                await pilot.pause()
                results_escape = await _push(app, pilot, CfgConfirm("eol", "\r", "\n"))
                await pilot.press("escape")
                await pilot.pause()
                assert (results, results_escape) == ([False], [False])

        _run(scenario)


# ── PortPicker ───────────────────────────────────────────────────────────────


class TestPortPicker:
    def test_selecting_a_row_dismisses_with_its_port(self, tmp_path):
        """Enter on a highlighted row dismisses with that row's port id; the loopback row is always there to pick."""

        async def scenario():
            app = _Host()
            async with app.run_test(size=_SIZE) as pilot:
                results = await _push(app, pilot, PortPicker())
                port_list = app.screen.query_one("#port-list", OptionList)
                last = port_list.option_count - 1
                expected = port_list.get_option_at_index(last).id
                port_list.highlighted = last
                port_list.focus()
                await pilot.pause()
                await pilot.press("enter")
                await pilot.pause()
                assert port_list.option_count >= 3, "header, separator, and at least the loopback"
                assert results == [expected] and isinstance(expected, str)

        _run(scenario)

    def test_a_re_render_keeps_the_highlighted_port(self, tmp_path):
        """Re-rendering the list (what a plug or unplug triggers) keeps the user's highlighted row by its port id."""

        async def scenario():
            app = _Host()
            async with app.run_test(size=_SIZE) as pilot:
                await _push(app, pilot, PortPicker())
                screen = app.screen
                assert isinstance(screen, PortPicker)
                port_list = screen.query_one("#port-list", OptionList)
                last = port_list.option_count - 1
                port_list.highlighted = last
                chosen = port_list.get_option_at_index(last).id

                # Act
                screen._render_port_list()
                await pilot.pause()

                # Assert
                assert port_list.highlighted is not None
                assert port_list.get_option_at_index(port_list.highlighted).id == chosen

        _run(scenario)

    def test_cancel_and_escape_dismiss_none(self, tmp_path):
        """Cancel and Escape both dismiss with None."""

        async def scenario():
            app = _Host()
            async with app.run_test(size=_SIZE) as pilot:
                results = await _push(app, pilot, PortPicker())
                await pilot.click("#port-cancel")
                await pilot.pause()
                results_escape = await _push(app, pilot, PortPicker())
                await pilot.press("escape")
                await pilot.pause()
                assert (results, results_escape) == ([None], [None])

        _run(scenario)


# ── the two file editors ─────────────────────────────────────────────────────

# (editor class, widget id prefix, file extension, the new-file title, the new-file text)
_EDITORS = [
    pytest.param(ProtoEditor, "ped", ".pro", "New Protocol Script", PROTO_TEMPLATE, id="proto"),
    pytest.param(
        ScriptEditor,
        "sed",
        ".run",
        "New Script",
        SCRIPT_TEMPLATE.format(name="untitled"),
        id="script",
    ),
]


class TestFileEditors:
    """ProtoEditor and ScriptEditor share one shape: a name row, a text area, Save, Save As, Cancel."""

    @pytest.mark.parametrize(("editor_cls", "prefix", "ext", "title", "template"), _EDITORS)
    def test_a_new_file_starts_from_the_template(
        self, tmp_path, editor_cls, prefix, ext, title, template
    ):
        """Opened without a path, the editor titles itself new, loads the template, and leaves the name empty."""

        async def scenario():
            app = _Host()
            async with app.run_test(size=_SIZE) as pilot:
                await _push(app, pilot, editor_cls(tmp_path / "folder"))
                screen = app.screen
                assert _plain(screen.query_one(f"#{prefix}-title", Static)) == title
                assert screen.query_one(f"#{prefix}-editor", TextArea).text == template
                assert screen.query_one(f"#{prefix}-name", Input).value == ""

        _run(scenario)

    @pytest.mark.parametrize(("editor_cls", "prefix", "ext", "title", "template"), _EDITORS)
    def test_save_needs_a_name_then_writes_it_with_the_extension(
        self, tmp_path, editor_cls, prefix, ext, title, template
    ):
        """Save with no name shows an error; with a name it creates the folder, writes name+ext, and dismisses with the path."""

        async def scenario():
            folder = tmp_path / "folder"
            app = _Host()
            async with app.run_test(size=_SIZE) as pilot:
                results = await _push(app, pilot, editor_cls(folder))
                screen = app.screen
                await pilot.click(f"#{prefix}-save")
                await pilot.pause()
                error = _plain(screen.query_one(f"#{prefix}-error", Static))
                screen.query_one(f"#{prefix}-editor", TextArea).text = "# body\n"
                screen.query_one(f"#{prefix}-name", Input).value = "mine"
                await _click_again(pilot, f"#{prefix}-save")
                written = folder / f"mine{ext}"
                assert error == "Enter a script name"
                assert written.read_text(encoding="utf-8") == "# body\n", (
                    "folder created, file written"
                )
                assert results == [str(written)]

        _run(scenario)

    @pytest.mark.parametrize(("editor_cls", "prefix", "ext", "title", "template"), _EDITORS)
    def test_editing_an_existing_file_loads_it_and_enter_saves_it(
        self, tmp_path, editor_cls, prefix, ext, title, template
    ):
        """Opened on a file, the editor shows its name and text; Enter in the name row saves back to it."""

        async def scenario():
            folder = tmp_path / "folder"
            folder.mkdir()
            existing = folder / f"old{ext}"
            existing.write_text("# before\n", encoding="utf-8")
            app = _Host()
            async with app.run_test(size=_SIZE) as pilot:
                results = await _push(app, pilot, editor_cls(folder, str(existing)))
                screen = app.screen
                shown = (
                    _plain(screen.query_one(f"#{prefix}-title", Static)),
                    screen.query_one(f"#{prefix}-editor", TextArea).text,
                    screen.query_one(f"#{prefix}-name", Input).value,
                )
                screen.query_one(f"#{prefix}-editor", TextArea).text = "# after\n"
                await _submit(pilot, screen.query_one(f"#{prefix}-name", Input), "old")
                assert shown == (f"Edit: old{ext}", "# before\n", "old")
                assert existing.read_text(encoding="utf-8") == "# after\n"
                assert results == [str(existing)]

        _run(scenario)

    @pytest.mark.parametrize(("editor_cls", "prefix", "ext", "title", "template"), _EDITORS)
    def test_an_unreadable_path_is_reported_in_the_text(
        self, tmp_path, editor_cls, prefix, ext, title, template
    ):
        """A path that cannot be read opens with an error comment as the text instead of failing to open."""

        async def scenario():
            missing = tmp_path / f"gone{ext}"
            app = _Host()
            async with app.run_test(size=_SIZE) as pilot:
                await _push(app, pilot, editor_cls(tmp_path, str(missing)))
                text = app.screen.query_one(f"#{prefix}-editor", TextArea).text
                assert text == f"# Error: could not read {missing}\n"

        _run(scenario)

    @pytest.mark.parametrize(("editor_cls", "prefix", "ext", "title", "template"), _EDITORS)
    def test_save_as_asks_twice_before_overwriting(
        self, tmp_path, editor_cls, prefix, ext, title, template
    ):
        """Save As reveals the name row; an empty name errors; an existing name warns once, then a second Save overwrites it."""

        async def scenario():
            folder = tmp_path / "folder"
            folder.mkdir()
            taken = folder / f"taken{ext}"
            taken.write_text("# original\n", encoding="utf-8")
            app = _Host()
            async with app.run_test(size=_SIZE) as pilot:
                results = await _push(app, pilot, editor_cls(folder))
                screen = app.screen
                error = screen.query_one(f"#{prefix}-error", Static)

                # Act -- reveal, empty name, taken name, confirm
                await pilot.click(f"#{prefix}-save-as")
                await pilot.pause()
                revealed = screen.query_one(f"#{prefix}-save-as-row").has_class("visible")
                await pilot.click(f"#{prefix}-save")
                await pilot.pause()
                empty_error = _plain(error)
                screen.query_one(f"#{prefix}-editor", TextArea).text = "# replaced\n"
                screen.query_one(f"#{prefix}-save-as-input", Input).value = "taken"
                await _click_again(pilot, f"#{prefix}-save")
                warned = _plain(error)
                still_original = taken.read_text(encoding="utf-8")
                await _click_again(pilot, f"#{prefix}-save")

                # Assert
                assert revealed is True and empty_error == "Enter a filename"
                assert warned == f"taken{ext} exists - click Save again to overwrite"
                assert still_original == "# original\n", "the first Save only warns"
                assert taken.read_text(encoding="utf-8") == "# replaced\n", (
                    "the second Save overwrites"
                )
                assert results == [str(taken)]

        _run(scenario)

    @pytest.mark.parametrize(("editor_cls", "prefix", "ext", "title", "template"), _EDITORS)
    def test_save_as_enter_writes_a_new_name(
        self, tmp_path, editor_cls, prefix, ext, title, template
    ):
        """Enter in the Save As row writes the text under the new name and dismisses with that path."""

        async def scenario():
            folder = tmp_path / "folder"
            app = _Host()
            async with app.run_test(size=_SIZE) as pilot:
                results = await _push(app, pilot, editor_cls(folder))
                screen = app.screen
                screen.query_one(f"#{prefix}-editor", TextArea).text = "# copy\n"
                await pilot.click(f"#{prefix}-save-as")
                await pilot.pause()
                await _submit(pilot, screen.query_one(f"#{prefix}-save-as-input", Input), "fresh")
                written = folder / f"fresh{ext}"
                assert written.read_text(encoding="utf-8") == "# copy\n"
                assert results == [str(written)]

        _run(scenario)

    @pytest.mark.parametrize(("editor_cls", "prefix", "ext", "title", "template"), _EDITORS)
    def test_cancel_and_escape_dismiss_none(
        self, tmp_path, editor_cls, prefix, ext, title, template
    ):
        """Cancel and Escape both dismiss with None and write nothing."""

        async def scenario():
            folder = tmp_path / "folder"
            app = _Host()
            async with app.run_test(size=_SIZE) as pilot:
                results = await _push(app, pilot, editor_cls(folder))
                await pilot.click(f"#{prefix}-cancel")
                await pilot.pause()
                results_escape = await _push(app, pilot, editor_cls(folder))
                await pilot.press("escape")
                await pilot.pause()
                assert (results, results_escape) == ([None], [None])
                assert not folder.exists(), "nothing written"

        _run(scenario)
