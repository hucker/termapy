"""Behavioral tests for StrongSelectionList -- the glyph flips per row.

The sibling of test_checkbox, for the other widget that draws a
checkbox.  Textual's SelectionList renders ToggleButton.BUTTON_INNER --
always ``X`` -- on every row and tells checked from unchecked by color
alone, so a list of six reads as six identical ``X`` glyphs at slightly
different weights.  Chuck hit this on the Protocol Debug screen: with
Hex checked and Text unchecked, both drew an ``X``.

The contract is one line per row: ``X`` when that row is selected, a
blank when it is not, whatever the colors do.  ASCII glyphs on purpose,
the reason recorded on StrongCheckbox -- a tick is EAW-ambiguous and
clips on 2-cell fonts.  The square root ``/`` (U+221A) a reader might
reach for instead is Ambiguous too, so it is no safer.

Same pattern as test_checkbox: mount just the widget, drive it, assert.
"""

from __future__ import annotations

import asyncio

from textual.app import App, ComposeResult
from textual.widgets._toggle_button import ToggleButton

from termapy.widgets import StrongSelectionList

# The three cells the widget draws before a row's label.
_BUTTON_CELLS = 3


class _Host(App):
    """Minimal host: mounts one list of three rows, the middle one selected."""

    def compose(self) -> ComposeResult:
        selection_list = StrongSelectionList[int](id="probe")
        selection_list.add_option(("first", 0, False))
        selection_list.add_option(("second", 1, True))
        selection_list.add_option(("third", 2, False))
        yield selection_list


def _run(scenario) -> None:
    asyncio.run(scenario())


def _glyph(selection_list: StrongSelectionList, row: int) -> str:
    """The toggle character the widget draws on ``row``."""
    segments = list(selection_list.render_line(row))
    return segments[1].text


def _label(selection_list: StrongSelectionList, row: int) -> str:
    """The row's text, past the button cells."""
    segments = list(selection_list.render_line(row))
    return "".join(segment.text for segment in segments[_BUTTON_CELLS:]).strip()


def test_a_selected_row_shows_an_x():
    """A checked row draws X, so "this one runs" needs no comparison with its neighbors."""

    async def scenario():
        async with _Host().run_test() as pilot:
            # Arrange
            selection_list = pilot.app.query_one(StrongSelectionList)

            # Act / Assert -- row 1 is the one added selected
            assert _glyph(selection_list, 1) == "X", "a selected row is marked X"
            assert _label(selection_list, 1) == "second", "and it is the row we think"

    _run(scenario)


def test_an_unselected_row_shows_a_blank():
    """An unchecked row draws a blank -- the state Textual renders as another X."""

    async def scenario():
        async with _Host().run_test() as pilot:
            # Arrange
            selection_list = pilot.app.query_one(StrongSelectionList)

            # Act / Assert
            for row in (0, 2):
                assert _glyph(selection_list, row) == " ", (
                    f"row {row} is unselected, so its button is blank, not an X"
                )

    _run(scenario)


def test_the_two_states_differ_in_the_character():
    """The whole point: checked and unchecked are different CHARACTERS, not shades.

    Textual's own widget passes a color-based version of this test and
    still reads as identical rows on screen, so the assertion is on the
    glyph.
    """

    async def scenario():
        async with _Host().run_test() as pilot:
            # Arrange
            selection_list = pilot.app.query_one(StrongSelectionList)

            # Act
            on_glyph = _glyph(selection_list, 1)
            off_glyph = _glyph(selection_list, 0)

            # Assert
            assert on_glyph != off_glyph, (
                f"checked and unchecked both draw {on_glyph!r}; the state is then "
                "invisible without comparing two rows, which is the defect this fixes"
            )

    _run(scenario)


def test_the_glyph_follows_a_toggle():
    """Clicking a row flips its glyph, so the list stays honest as it is used."""

    async def scenario():
        async with _Host().run_test() as pilot:
            # Arrange
            selection_list = pilot.app.query_one(StrongSelectionList)
            before = _glyph(selection_list, 0)

            # Act
            selection_list.select(selection_list.get_option_at_index(0).value)
            await pilot.pause()

            # Assert
            assert before == " ", "row 0 started unselected"
            assert _glyph(selection_list, 0) == "X", (
                "selecting a row redraws its button as X without a remount"
            )

    _run(scenario)


def test_the_glyphs_are_single_cell_ascii():
    """A 2-cell glyph clips in some terminals, so both states stay narrow ASCII."""

    async def scenario():
        async with _Host().run_test() as pilot:
            # Arrange
            selection_list = pilot.app.query_one(StrongSelectionList)

            # Act
            glyphs = {_glyph(selection_list, row) for row in range(3)}

            # Assert
            for glyph in glyphs:
                assert glyph.isascii(), (
                    f"{glyph!r} is not ASCII; a tick or a square root is "
                    "East-Asian-Width ambiguous and draws two cells wide on some fonts"
                )
                assert len(glyph) == 1, f"{glyph!r} must be exactly one cell"

    _run(scenario)


def test_rows_past_the_end_are_left_alone():
    """A line with no option behind it is the base widget's, untouched."""

    async def scenario():
        async with _Host().run_test() as pilot:
            # Arrange
            selection_list = pilot.app.query_one(StrongSelectionList)

            # Act -- one line past the three options
            segments = list(selection_list.render_line(3))

            # Assert
            assert segments and segments[0].text != ToggleButton.BUTTON_LEFT, (
                "a blank line below the list draws no button"
            )

    _run(scenario)


class TestOptionTooltips:
    """A tooltip per row: Textual has no place for one, so the widget carries it."""

    def test_the_tooltip_follows_the_hovered_row(self):
        """Each row shows its own help while the pointer is on it."""

        async def scenario():
            async with _Host().run_test(size=(40, 10)) as pilot:
                # Arrange
                selection_list = pilot.app.query_one(StrongSelectionList)
                selection_list.set_option_tooltips({0: "the first", 2: "the third"})

                # Act / Assert -- each row answers with its own text
                await pilot.hover(StrongSelectionList, offset=(6, 0))
                await pilot.pause()
                assert selection_list.tooltip == "the first", "row 0 shows its own tip"

                await pilot.hover(StrongSelectionList, offset=(6, 2))
                await pilot.pause()
                assert selection_list.tooltip == "the third", (
                    "and moving to row 2 shows that row's, not the one before it -- "
                    "reading the row from the base class's hover state trailed by one"
                )

        _run(scenario)

    def test_a_row_without_a_tip_shows_none(self):
        """A row with no entry clears the tooltip rather than keeping its neighbor's."""

        async def scenario():
            async with _Host().run_test(size=(40, 10)) as pilot:
                # Arrange
                selection_list = pilot.app.query_one(StrongSelectionList)
                selection_list.set_option_tooltips({0: "the first"})

                # Act
                await pilot.hover(StrongSelectionList, offset=(6, 0))
                await pilot.pause()
                had = selection_list.tooltip
                await pilot.hover(StrongSelectionList, offset=(6, 1))
                await pilot.pause()

                # Assert
                assert had == "the first", "row 0 has a tip"
                assert selection_list.tooltip is None, (
                    "row 1 has none, so no tooltip shows -- a stale one would name the wrong row"
                )

        _run(scenario)

    def test_a_list_without_tooltips_is_untouched(self):
        """The facility is opt-in: a list that sets none never gains a tooltip."""

        async def scenario():
            async with _Host().run_test(size=(40, 10)) as pilot:
                # Arrange / Act
                selection_list = pilot.app.query_one(StrongSelectionList)
                await pilot.hover(StrongSelectionList, offset=(6, 1))
                await pilot.pause()

                # Assert
                assert selection_list.tooltip is None, (
                    "no tooltips were set, so hovering sets nothing"
                )

        _run(scenario)
