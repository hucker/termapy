"""StrongSelectionList -- a SelectionList with a clearer on/off glyph."""

from __future__ import annotations

from typing import Generic, TypeVar

from rich.segment import Segment
from textual import events
from textual.strip import Strip
from textual.widgets import SelectionList
from textual.widgets._toggle_button import ToggleButton
from textual.widgets.option_list import OptionDoesNotExist

SelectionType = TypeVar("SelectionType")


class StrongSelectionList(SelectionList[SelectionType], Generic[SelectionType]):
    """A SelectionList whose glyph FLIPS between blank (off) and ``X`` (on).

    The same defect ``StrongCheckbox`` fixes, in the other widget that
    draws a checkbox: Textual renders ``ToggleButton.BUTTON_INNER`` --
    always ``X`` -- for every row and distinguishes checked from
    unchecked by text color alone.  Against the Protocol Debug screen's
    theme the two colors read as the same character at slightly
    different weights, so a list of six tests gives no reliable answer
    to "which of these will run".

    Flipping the character makes the state legible without comparing
    rows: ``[ ]`` and ``[X]``, the convention ``StrongCheckbox`` already
    uses, so the two widgets agree wherever both appear.

    ASCII on purpose, for the reason recorded on ``StrongCheckbox``:
    ``/``-style tick glyphs are East-Asian-Width *ambiguous*, measured
    as one cell by Rich and drawn as two by some terminal fonts (VS
    Code's integrated terminal among them), which clips them.  Blank and
    ``X`` can never mis-measure.

    Textual reads ``BUTTON_INNER`` off the CLASS while rendering each
    row, so -- unlike ``StrongCheckbox``, which owns one value and can
    set an instance attribute -- the glyph here has to be chosen per row
    as the row is drawn.  ``render_line`` rebuilds the strip the base
    class produced, replacing only that one segment and keeping its
    style, so the colors, the click target and the highlight all stay
    the base widget's.
    """

    def set_option_tooltips(self, tips: dict[object, str]) -> None:
        """Give each option value a tooltip, shown while the mouse is over its row."""
        self._option_tips = dict(tips)

    def _on_mouse_move(self, event: events.MouseMove) -> None:
        """Follow the hovered row with the widget's tooltip."""
        super()._on_mouse_move(event)
        tips = getattr(self, "_option_tips", None)
        if not tips:
            return
        # From the pointer's own y, not the base class's
        # ``_mouse_hovering_over``: that is read out of the style under
        # the cursor, which on the first move into a row still carries
        # the row before it, so the tooltip trailed the mouse by one.
        # Measured from the content area, not the widget's edge: a border
        # puts row 0 one line down, and event.y counted the border as a row.
        offset = event.get_content_offset(self)
        tip = None
        if offset is not None:
            _, scroll_y = self.scroll_offset
            index = offset.y + scroll_y
            if 0 <= index < self.option_count:
                tip = tips.get(self.get_option_at_index(index).value)
        if tip != self.tooltip:
            self.tooltip = tip

    def _on_leave(self, _: events.Leave) -> None:
        """Drop the tooltip when the mouse leaves, so it never names the wrong row."""
        super()._on_leave(_)
        if getattr(self, "_option_tips", None):
            self.tooltip = None

    def render_line(self, y: int) -> Strip:
        """The base widget's line, with the toggle glyph set from this row's state."""
        strip = super().render_line(y)
        segments = list(strip)
        # The base builds: LEFT, INNER, RIGHT, " ", *prompt.  A line with
        # no button (past the end of the list) is returned untouched.
        if len(segments) < 3 or segments[0].text != ToggleButton.BUTTON_LEFT:
            return strip
        _, scroll_y = self.scroll_offset
        try:
            selection = self.get_option_at_index(scroll_y + y)
        except OptionDoesNotExist:
            return strip
        glyph = "X" if selection.value in self._selected else " "
        if segments[1].text == glyph:
            return strip
        segments[1] = Segment(glyph, style=segments[1].style)
        return Strip(segments, strip.cell_length)

    DEFAULT_CSS = """
    StrongSelectionList > .selection-list--button-selected {
        text-style: bold;
    }
    StrongSelectionList > .selection-list--button-selected-highlighted {
        text-style: bold;
    }
    """
