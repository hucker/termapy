"""SelectableRichLog -- a RichLog whose rendered lines select and copy.

Textual selects text in a widget that does two things: stamps each
rendered line with content offsets, so the screen can map a mouse cell
to a column and a line, and extracts the text under a ``Selection``.
``Log`` does both; ``RichLog`` does neither (Textual discussion #6249),
so a drag in a RichLog selects the whole widget invisibly and Ctrl+C
copies an empty string.  This subclass adds the two hooks over the
rendered strips, so the copy is the visible text: wrapped as shown,
table rules and all, styles dropped.  The screen does the rest: the
drag, the ``screen--selection`` highlight, Ctrl+C to the clipboard over
OSC 52.

Carried here until Textual's ``RichLog`` does this itself (decision
2026-10-29); the same change, made in place, is the upstream pull
request.
"""

from __future__ import annotations

from typing import Self

from rich.console import RenderableType
from textual.binding import Binding
from textual.selection import Selection
from textual.strip import Strip
from textual.widgets import RichLog


class SelectableRichLog(RichLog):
    """A RichLog whose text can be selected with the mouse and copied.

    Overrides ``_render_line`` (RichLog's one private hook, and the reason
    the fix belongs upstream) to paint the selected span and stamp the
    offsets, and ``get_selection`` to return the selected text.  A
    selection whose line numbers stop meaning what they did -- the log
    was cleared, or ``max_lines`` dropped lines off the top -- is
    cleared rather than left drifting.  With the focus, which a click
    gives it, Ctrl+A selects every line; the input line keeps its own
    Ctrl+A while it is focused.
    """

    BINDINGS = [Binding("ctrl+a", "select_all", "Select all", show=False)]

    def action_select_all(self) -> None:
        """Select every line (Ctrl+A while the log has focus)."""
        self.text_select_all()

    def get_selection(self, selection: Selection) -> tuple[str, str] | None:
        """The text under ``selection``: the rendered lines, as shown."""
        text = "\n".join(strip.text for strip in self.lines)
        return selection.extract(text), "\n"

    def _render_line(self, y: int, scroll_x: int, width: int) -> Strip:
        selection = self.text_selection
        if selection is None:
            line = super()._render_line(y, scroll_x, width)
        elif y >= len(self.lines):
            return Strip.blank(width, self.rich_style)
        else:
            line = self.lines[y]
            span = selection.get_span(y)
            if span is not None:
                start, end = span
                if end == -1:
                    end = line.cell_length
                style = self.screen.get_component_rich_style("screen--selection")
                line = Strip.join(
                    [
                        line.crop(0, start),
                        line.crop(start, end).apply_style(style),
                        line.crop(end),
                    ]
                )
            line = line.crop_extend(scroll_x, scroll_x + width, self.rich_style)
        # Offsets after the crop, as Log stamps them: a segment's offset is
        # its content column, and the first visible one starts at scroll_x.
        return line.apply_offsets(scroll_x, y)

    def write(
        self,
        content: RenderableType | object,
        width: int | None = None,
        expand: bool = False,
        shrink: bool = True,
        scroll_end: bool | None = None,
        animate: bool = False,
    ) -> Self:
        """Write as RichLog does; a write that drops lines off the top drops the selection."""
        start_line = self._start_line
        result = super().write(content, width, expand, shrink, scroll_end, animate)
        if self._start_line != start_line:
            self._drop_selection()
        return result

    def clear(self) -> Self:
        """Clear as RichLog does, and the selection with it."""
        result = super().clear()
        self._drop_selection()
        return result

    def _drop_selection(self) -> None:
        """Forget a selection whose line numbers no longer mean what they did."""
        if self.is_attached and self.text_selection is not None:
            self.screen.clear_selection()
