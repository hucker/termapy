"""SelectableRichLog: the output pane selects by mouse or keyboard, and Ctrl+C copies the text shown.

Textual's selection machinery (the drag, the highlight style, the
``ctrl+c`` binding, OSC 52) is the screen's; a widget takes part by
stamping its lines with offsets and extracting text for a ``Selection``.
``RichLog`` does neither, so these tests pin the two hooks
``SelectableRichLog`` adds and the cases a log has that a static widget
does not: scrolling, wrapped and composite renderables, lines arriving
while a selection stands, and the two events that invalidate one.  The
keyboard half moves a selection's end with Shift and a movement key,
holds the view still while anything is selected, and hands the keys
between the prompt and the pane.  A bare host app holds the widget; the
prompt and pane tests drive the real app.
"""

from __future__ import annotations

import asyncio
import json

from rich.table import Table
from rich.text import Text
from textual.app import App, ComposeResult
from textual.widgets import Input

from termapy.defaults import DEFAULT_CFG
from termapy.widgets import LinkLog, SelectableRichLog


class _Host(App[None]):
    """A bare app holding one selectable log."""

    def compose(self) -> ComposeResult:
        yield SelectableRichLog(id="log", markup=True, wrap=True, max_lines=60)


def _run(scenario) -> None:
    """Run one async Pilot scenario (the suite is otherwise sync)."""
    asyncio.run(scenario())


async def _drag(pilot, widget, start: tuple[int, int], end: tuple[int, int]) -> None:
    """Press at ``start``, move to ``end``, release: one mouse selection."""
    await pilot.mouse_down(widget, offset=start)
    await pilot.hover(widget, offset=end)
    await pilot.mouse_up(widget, offset=end)
    await pilot.pause()


def _write_lines(log: SelectableRichLog, count: int, first: int = 0) -> None:
    """Write ``count`` numbered lines, each 15 cells wide."""
    for i in range(first, first + count):
        log.write(Text(f"line {i:02d} payload"))


def _painted(app: App, log: SelectableRichLog, y: int) -> bool:
    """Whether rendered row ``y`` carries the screen's selection background."""
    style = app.screen.get_component_rich_style("screen--selection")
    return any(
        segment.style is not None and segment.style.bgcolor == style.bgcolor
        for segment in log.render_line(y)
    )


class TestMouseSelection:
    """The two hooks, driven the way a user drives them."""

    def test_a_drag_selects_the_dragged_text(self):
        """Dragging from one cell to another selects exactly the characters between them."""

        async def scenario():
            app = _Host()
            async with app.run_test(size=(40, 12)) as pilot:
                # Arrange
                log = app.query_one(SelectableRichLog)
                _write_lines(log, 5)
                await pilot.pause()
                # Act
                await _drag(pilot, log, (2, 0), (8, 2))
                # Assert
                actual = app.screen.get_selected_text()
                expected = "ne 00 payload\nline 01 payload\nline 02 p"
                assert actual == expected, "the copy is the dragged span, line by line"

        _run(scenario)

    def test_the_selected_span_is_highlighted_and_cleared(self):
        """Selected rows paint the screen's selection style; others and a cleared selection do not."""

        async def scenario():
            app = _Host()
            async with app.run_test(size=(40, 12)) as pilot:
                log = app.query_one(SelectableRichLog)
                _write_lines(log, 5)
                await pilot.pause()
                assert _painted(app, log, 1) is False, "no selection, no highlight"
                await _drag(pilot, log, (2, 0), (8, 2))
                assert _painted(app, log, 1) is True, "a row inside the selection is highlighted"
                assert _painted(app, log, 4) is False, "a row outside it is not"
                app.screen.clear_selection()
                await pilot.pause()
                assert _painted(app, log, 1) is False, "clearing the selection clears the paint"

        _run(scenario)

    def test_a_drag_after_scrolling_selects_the_rows_shown(self):
        """After scrolling to the bottom, a drag over visible rows selects those lines, not the top ones."""

        async def scenario():
            app = _Host()
            async with app.run_test(size=(40, 12)) as pilot:
                # Arrange
                log = app.query_one(SelectableRichLog)
                _write_lines(log, 40)
                await pilot.pause()
                log.scroll_end(animate=False)
                await pilot.pause()
                top = log.scroll_offset.y
                assert top > 0, "the log scrolled; otherwise this test proves nothing"
                # Act
                await _drag(pilot, log, (0, 8), (12, 10))
                # Assert
                actual = app.screen.get_selected_text()
                rows = [log.lines[top + y].text for y in (8, 9, 10)]
                expected = f"{rows[0]}\n{rows[1]}\n{rows[2][:13]}"
                assert actual == expected, "the selection maps rows through the scroll offset"

        _run(scenario)

    def test_composite_renderables_copy_as_shown(self):
        """Markup, a Rich table and a wrapped line copy as the characters on screen, without styles."""

        async def scenario():
            app = _Host()
            async with app.run_test(size=(40, 12)) as pilot:
                # Arrange
                log = app.query_one(SelectableRichLog)
                log.write("[bold red]markup[/] line with [cyan]colors[/]")
                table = Table("col1", "col2")
                table.add_row("r1a", "r1b")
                log.write(table)
                log.write(Text("wrapped " * 12))
                await pilot.pause()
                # Act
                log.text_select_all()
                await pilot.pause()
                # Assert
                actual = app.screen.get_selected_text()
                assert actual is not None, "select-all selects something"
                expected = "\n".join(strip.text for strip in log.lines).rstrip("\n")
                assert actual == expected, "the copy is every rendered line, as shown"
                assert "[bold red]" not in actual, "markup tags are not in the copy"
                assert "│ r1a  │ r1b  │" in actual, "the table copies with its rules"
                assert actual.count("wrapped") == 12, "a wrapped line copies whole, over its rows"

        _run(scenario)

    def test_a_second_drag_replaces_the_first(self):
        """A new drag is the selection; the earlier one leaves no text and no paint behind."""

        async def scenario():
            app = _Host()
            async with app.run_test(size=(40, 12)) as pilot:
                log = app.query_one(SelectableRichLog)
                _write_lines(log, 5)
                await pilot.pause()
                await _drag(pilot, log, (0, 0), (6, 0))
                await _drag(pilot, log, (0, 3), (6, 3))
                actual = app.screen.get_selected_text()
                expected = "line 03"
                assert actual == expected, "only the second drag is selected"
                assert _painted(app, log, 0) is False, "the first drag's paint is gone"

        _run(scenario)

    def test_ctrl_c_copies_the_selection_to_the_clipboard(self):
        """Ctrl+C with a selection puts its text on the app clipboard (the OSC 52 payload)."""

        async def scenario():
            app = _Host()
            async with app.run_test(size=(40, 12)) as pilot:
                log = app.query_one(SelectableRichLog)
                _write_lines(log, 5)
                await pilot.pause()
                await _drag(pilot, log, (0, 1), (6, 1))
                await pilot.press("ctrl+c")
                await pilot.pause()
                assert app.clipboard == "line 01", "Ctrl+C copies the selected text"

        _run(scenario)


class TestKeyboard:
    """Ctrl+A belongs to the pane only while it has focus."""

    def test_ctrl_a_selects_everything_when_the_log_has_focus(self):
        """With the log focused, Ctrl+A selects every rendered line."""

        async def scenario():
            app = _Host()
            async with app.run_test(size=(40, 12)) as pilot:
                log = app.query_one(SelectableRichLog)
                _write_lines(log, 5)
                log.focus()
                await pilot.pause()
                await pilot.press("ctrl+a")
                await pilot.pause()
                actual = app.screen.get_selected_text()
                expected = "\n".join(strip.text for strip in log.lines)
                assert actual == expected, "Ctrl+A selects every line"

        _run(scenario)


class TestKeyboardSelection:
    """Shift with a movement key moves a selection's end from where it began."""

    def test_shift_right_extends_from_the_clicked_character(self):
        """After a click, Shift+Right selects one character per press from the clicked one."""

        async def scenario():
            app = _Host()
            async with app.run_test(size=(40, 12)) as pilot:
                # Arrange
                log = app.query_one(SelectableRichLog)
                _write_lines(log, 5)
                await pilot.pause()
                await pilot.click(log, offset=(2, 1))
                # Act
                for _ in range(3):
                    await pilot.press("shift+right")
                await pilot.pause()
                # Assert
                assert app.screen.get_selected_text() == "ne ", "three characters from column 2"

        _run(scenario)

    def test_shift_down_moves_a_row_and_keeps_the_column(self):
        """Shift+Down moves the end to the next row, same column."""

        async def scenario():
            app = _Host()
            async with app.run_test(size=(40, 12)) as pilot:
                log = app.query_one(SelectableRichLog)
                _write_lines(log, 5)
                await pilot.pause()
                await pilot.click(log, offset=(2, 1))
                await pilot.press("shift+down")
                await pilot.pause()
                actual = app.screen.get_selected_text()
                assert actual == "ne 01 payload\nli", "from column 2 of row 1 to column 2 of row 2"

        _run(scenario)

    def test_shift_left_at_a_row_start_wraps_to_the_row_above(self):
        """At column 0, Shift+Left moves the end to the end of the row above."""

        async def scenario():
            app = _Host()
            async with app.run_test(size=(40, 12)) as pilot:
                log = app.query_one(SelectableRichLog)
                _write_lines(log, 5)
                await pilot.pause()
                await pilot.click(log, offset=(0, 2))
                await pilot.press("shift+left", "shift+left")
                await pilot.pause()
                assert app.screen.get_selected_text() == "d", (
                    "the row break, then the last character of the row above"
                )

        _run(scenario)

    def test_shift_home_and_end_reach_the_row_ends(self):
        """Shift+End selects to the end of the row; Shift+Home from there to its start."""

        async def scenario():
            app = _Host()
            async with app.run_test(size=(40, 12)) as pilot:
                log = app.query_one(SelectableRichLog)
                _write_lines(log, 5)
                await pilot.pause()
                await pilot.click(log, offset=(5, 1))
                await pilot.press("shift+end")
                await pilot.pause()
                assert app.screen.get_selected_text() == "01 payload", "to the row's end"
                await pilot.press("shift+home")
                await pilot.pause()
                assert app.screen.get_selected_text() == "line ", (
                    "back past the start point to column 0"
                )

        _run(scenario)

    def test_keys_extend_a_mouse_selection(self):
        """After a drag, Shift+Down moves the drag's end a row on."""

        async def scenario():
            app = _Host()
            async with app.run_test(size=(40, 12)) as pilot:
                # Arrange: the drag selects "line " on row 1
                log = app.query_one(SelectableRichLog)
                _write_lines(log, 5)
                await pilot.pause()
                await _drag(pilot, log, (0, 1), (4, 1))
                assert app.screen.get_selected_text() == "line ", "the drag"
                # Act
                await pilot.press("shift+down")
                await pilot.pause()
                # Assert
                actual = app.screen.get_selected_text()
                assert actual == "line 01 payload\nline ", "the drag's end moved one row down"

        _run(scenario)

    def test_page_keys_move_a_page_and_keep_the_end_in_view(self):
        """Shift+PageUp moves the end a page up and scrolls it into view."""

        async def scenario():
            app = _Host()
            async with app.run_test(size=(40, 12)) as pilot:
                # Arrange: scrolled to the bottom, keys starting on the bottom row
                log = app.query_one(SelectableRichLog)
                _write_lines(log, 40)
                await pilot.pause()
                log.scroll_end(animate=False)
                await pilot.pause()
                top = round(log.scroll_offset.y)
                await pilot.click(log, offset=(0, 11))
                # Act: two pages up from the bottom row
                await pilot.press("shift+pageup", "shift+pageup")
                await pilot.pause()
                # Assert
                cursor_row = top + 11 - 2 * 11
                selected = app.screen.get_selected_text()
                assert selected is not None, "something is selected"
                rows = selected.splitlines()
                assert rows[0] == f"line {cursor_row:02d} payload", "the end moved two pages up"
                assert len(rows) == 2 * 11, "two pages of eleven rows each"
                assert round(log.scroll_offset.y) == cursor_row, (
                    "the moving end is the top row shown"
                )

        _run(scenario)

    def test_a_second_keyboard_selection_starts_fresh(self):
        """After a selection ends, the next one starts from the new click, not the old anchor."""

        async def scenario():
            app = _Host()
            async with app.run_test(size=(40, 12)) as pilot:
                log = app.query_one(SelectableRichLog)
                _write_lines(log, 5)
                await pilot.pause()
                await pilot.click(log, offset=(2, 1))
                await pilot.press("shift+right")
                await pilot.pause()
                assert log.deselect() is True, "the first selection existed"
                await pilot.click(log, offset=(0, 3))
                await pilot.press("shift+right", "shift+right")
                await pilot.pause()
                assert app.screen.get_selected_text() == "li", (
                    "two characters from the second click"
                )

        _run(scenario)

    def test_the_newest_row_with_text_is_selected(self):
        """select_newest_row selects the last row that has text, skipping trailing blank rows."""

        async def scenario():
            app = _Host()
            async with app.run_test(size=(40, 12)) as pilot:
                log = app.query_one(SelectableRichLog)
                _write_lines(log, 3)
                log.write("")
                await pilot.pause()
                log.select_newest_row()
                await pilot.pause()
                assert app.screen.get_selected_text() == "line 02 payload"

        _run(scenario)


class TestFollowHold:
    """While anything is selected the log holds still; it follows new output again after."""

    def test_a_selection_holds_the_view_and_its_end_follows_again(self):
        """New lines leave a selected view where it is; deselecting returns to the newest line."""

        async def scenario():
            app = _Host()
            async with app.run_test(size=(40, 12)) as pilot:
                # Arrange
                log = app.query_one(SelectableRichLog)
                _write_lines(log, 30)
                await pilot.pause()
                log.scroll_end(animate=False)
                log.select_newest_row()
                await pilot.pause()
                held = round(log.scroll_offset.y)
                # Act: output keeps arriving while the selection stands
                _write_lines(log, 5, first=30)
                await pilot.pause()
                # Assert
                assert round(log.scroll_offset.y) == held, "the view held still under the selection"
                log.deselect()
                await pilot.pause()
                assert log.auto_scroll is True, "following resumes"
                assert round(log.scroll_offset.y) == round(log.max_scroll_y), (
                    "and catches up to the end"
                )

        _run(scenario)

    def test_a_trim_ends_the_hold(self):
        """A write that trims the top drops the selection and resumes following."""

        async def scenario():
            app = _Host()
            async with app.run_test(size=(40, 12)) as pilot:
                log = app.query_one(SelectableRichLog)
                _write_lines(log, 50)
                await pilot.pause()
                log.select_newest_row()
                await pilot.pause()
                assert log.auto_scroll is False, "held while selected"
                _write_lines(log, 20, first=50)
                await pilot.pause()
                assert log.text_selection is None, "the trim dropped the selection"
                assert log.auto_scroll is True, "and the hold with it"

        _run(scenario)


class TestSelectionLifetime:
    """A selection stands while its line numbers mean the same thing, and not a moment longer."""

    def test_lines_arriving_keep_the_selection_until_the_top_is_trimmed(self):
        """Appended lines leave the selection intact; a write that trims the top drops it."""

        async def scenario():
            app = _Host()
            async with app.run_test(size=(40, 12)) as pilot:
                # Arrange: 50 of a 60-line cap
                log = app.query_one(SelectableRichLog)
                _write_lines(log, 50)
                await pilot.pause()
                log.scroll_home(animate=False)
                await pilot.pause()
                await _drag(pilot, log, (0, 1), (6, 1))
                # Act / Assert: five more lines fit under the cap
                _write_lines(log, 5, first=50)
                await pilot.pause()
                assert app.screen.get_selected_text() == "line 01", (
                    "lines appended below the cap leave the selection where it was"
                )
                # Act / Assert: ten more push the top off
                _write_lines(log, 10, first=55)
                await pilot.pause()
                assert log.text_selection is None, "a trim off the top drops the selection"
                assert app.screen.get_selected_text() is None, "and nothing is left to copy"

        _run(scenario)

    def test_clear_drops_the_selection(self):
        """Clearing the log clears the selection with it."""

        async def scenario():
            app = _Host()
            async with app.run_test(size=(40, 12)) as pilot:
                log = app.query_one(SelectableRichLog)
                _write_lines(log, 5)
                await pilot.pause()
                await _drag(pilot, log, (0, 1), (6, 1))
                log.clear()
                await pilot.pause()
                assert log.text_selection is None, "a cleared log has no selection"

        _run(scenario)


def _terminal(tmp_path):
    """The real app over a DEMO config that does not connect."""
    from termapy.app import SerialTerminal

    proj = tmp_path / "proj"
    proj.mkdir()
    serial = DEFAULT_CFG["serial"]
    assert isinstance(serial, dict), "DEFAULT_CFG['serial'] is a dict"
    cfg = {**DEFAULT_CFG, "serial": {**serial, "port": "DEMO"}, "auto_connect": False}
    path = proj / "proj.cfg"
    path.write_text(json.dumps(cfg, indent=4), encoding="utf-8")
    cfg = {**cfg, "_config_path": str(path)}
    app = SerialTerminal(cfg, str(path))
    app.repl.global_root = tmp_path  # keep the checkout's plugin/ out of the boot
    return app


class TestPromptAndPane:
    """The keyboard moves between the prompt and the output pane without the mouse."""

    def test_shift_up_in_the_prompt_selects_the_newest_output_row(self, tmp_path):
        """Shift+Up from the prompt selects the newest output row; again grows it upward."""

        async def scenario():
            app = _terminal(tmp_path)
            async with app.run_test(size=(100, 30)) as pilot:
                # Arrange
                await pilot.pause()
                output = app.query_one("#output", LinkLog)
                output.clear()
                for word in ("alpha", "beta", "gamma"):
                    output.write(Text(word))
                app.query_one("#cmd").focus()
                await pilot.pause()
                # Act / Assert
                await pilot.press("shift+up")
                await pilot.pause()
                assert app.focused is output, "the keyboard moved into the pane"
                assert app.screen.get_selected_text() == "gamma", "the newest row"
                await pilot.press("shift+up")
                await pilot.pause()
                assert app.screen.get_selected_text() == "beta\ngamma", "grown one row up"

        _run(scenario)

    def test_typing_in_the_pane_types_in_the_prompt(self, tmp_path):
        """A printable key in the pane goes to the prompt, which takes the focus, and ends the selection."""

        async def scenario():
            app = _terminal(tmp_path)
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.pause()
                output = app.query_one("#output", LinkLog)
                output.clear()
                output.write(Text("hello"))
                output.focus()
                output.select_newest_row()
                await pilot.pause()
                await pilot.press("x")
                await pilot.pause()
                prompt = app.query_one("#cmd", Input)
                assert app.focused is prompt, "the prompt has the keyboard again"
                assert prompt.value == "x", "and the key was typed there"
                assert output.text_selection is None, "the selection ended"

        _run(scenario)

    def test_escape_clears_a_selection_without_stopping_a_script(self, tmp_path):
        """Escape in the pane clears its selection and returns to the prompt; the stop flag stays down."""

        async def scenario():
            app = _terminal(tmp_path)
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.pause()
                output = app.query_one("#output", LinkLog)
                output.clear()
                output.write(Text("hello"))
                output.focus()
                output.select_newest_row()
                await pilot.pause()
                await pilot.press("escape")
                await pilot.pause()
                assert output.text_selection is None, "the selection is gone"
                assert app.focused is app.query_one("#cmd", Input), "back at the prompt"
                assert app.repl._script_stop.is_set() is False, "Escape did not also stop scripts"

        _run(scenario)

    def test_escape_with_nothing_selected_returns_and_keeps_its_job(self, tmp_path):
        """With nothing selected, Escape in the pane returns to the prompt and still does what it always did."""

        async def scenario():
            app = _terminal(tmp_path)
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.pause()
                output = app.query_one("#output", LinkLog)
                output.focus()
                await pilot.pause()
                await pilot.press("escape")
                await pilot.pause()
                assert app.focused is app.query_one("#cmd", Input), "back at the prompt"
                assert app.repl._script_stop.is_set() is True, "Escape's stop-script job ran"

        _run(scenario)


class TestOutputPane:
    """The real app's output pane and find view are selectable logs."""

    def test_the_output_pane_selects_and_the_find_view_is_one_too(self, tmp_path):
        """A drag in #output selects its text, and #output-find shares the widget."""

        async def scenario():
            from termapy.app import SerialTerminal

            # Arrange: a DEMO config that does not connect
            proj = tmp_path / "proj"
            proj.mkdir()
            serial = DEFAULT_CFG["serial"]
            assert isinstance(serial, dict), "DEFAULT_CFG['serial'] is a dict"
            cfg = {**DEFAULT_CFG, "serial": {**serial, "port": "DEMO"}, "auto_connect": False}
            path = proj / "proj.cfg"
            path.write_text(json.dumps(cfg, indent=4), encoding="utf-8")
            cfg = {**cfg, "_config_path": str(path)}
            app = SerialTerminal(cfg, str(path))
            app.repl.global_root = tmp_path  # keep the checkout's plugin/ out of the boot
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.pause()
                output = app.query_one("#output", LinkLog)
                output.clear()
                output.write(Text("hello from the device"))
                await pilot.pause()
                # Act: Pilot offsets are the outer box, and the pane has a border
                left, top = output.gutter.top_left
                await _drag(pilot, output, (left, top), (left + 4, top))
                # Assert
                assert app.screen.get_selected_text() == "hello", (
                    "a drag in the output pane selects its text"
                )
                assert isinstance(app.query_one("#output-find"), SelectableRichLog), (
                    "the find view selects the same way"
                )
                # Act / Assert: Ctrl+A is the input line's while it has focus
                app.query_one("#cmd").focus()
                await pilot.pause()
                await pilot.press("ctrl+a")
                await pilot.pause()
                assert app.screen.get_selected_text() == "hello", (
                    "Ctrl+A in the input line leaves the pane's selection alone"
                )
                # Act / Assert: and selects everything once the pane has focus
                output.focus()
                await pilot.pause()
                await pilot.press("ctrl+a")
                await pilot.pause()
                assert app.screen.get_selected_text() == "hello from the device", (
                    "Ctrl+A with the pane focused selects all of it"
                )

        _run(scenario)
