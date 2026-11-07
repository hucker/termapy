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

The keyboard selects too, which Textual offers for no log widget:
Shift with an arrow, Home, End, PageUp or PageDown moves the selection's
end from where it began -- the last click, a mouse selection's far end,
or the newest row -- and the pane scrolls to keep that end in view.
While anything is selected the log stops following new output, so the
selection cannot scroll away from under the keys, and catches up when
the selection ends.

Carried here until Textual's ``RichLog`` does this itself (decision
2026-10-29); the mouse half, made in place, is the upstream change.
"""

from __future__ import annotations

from typing import Any, Self

from rich.console import RenderableType
from textual import events
from textual.binding import Binding
from textual.geometry import Offset
from textual.message import Message
from textual.selection import Selection
from textual.strip import Strip
from textual.widgets import RichLog

# The keys that move a keyboard selection's end, by the move they make.
_MOVES = {
    "shift+up": "up",
    "shift+down": "down",
    "shift+left": "left",
    "shift+right": "right",
    "shift+home": "home",
    "shift+end": "end",
    "shift+pageup": "pageup",
    "shift+pagedown": "pagedown",
}


class SelectableRichLog(RichLog):
    """A RichLog whose text can be selected with the mouse or the keyboard and copied.

    Overrides ``_render_line`` (RichLog's one private hook, and the reason
    the fix belongs upstream) to paint the selected span and stamp the
    offsets, and ``get_selection`` to return the selected text.  A
    selection whose line numbers stop meaning what they did -- the log
    was cleared, or ``max_lines`` dropped lines off the top -- is
    cleared rather than left drifting.  With the focus, which a click
    gives it, Ctrl+A selects every line and Shift with a movement key
    extends a selection (module docstring); a row is a rendered row, so
    a wrapped line moves a row at a time, as it is shown and copied.
    """

    class SelectionDismissed(Message):
        """Escape cleared this log's selection; the host may move the focus on."""

        def __init__(self, log: SelectableRichLog) -> None:
            super().__init__()
            self.log = log

        @property
        def control(self) -> SelectableRichLog:
            """The log whose selection Escape cleared."""
            return self.log

    BINDINGS = [
        Binding("ctrl+a", "select_all", "Select all", show=False),
        *(Binding(key, f"extend_selection('{move}')", show=False) for key, move in _MOVES.items()),
    ]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # The keyboard selection: where it began, where its moving end is,
        # and the Selection it last set (a different one means the mouse
        # moved it, and its ends are read afresh).
        self._anchor: Offset | None = None
        self._cursor: Offset | None = None
        self._keyboard_selection: Selection | None = None
        # The content position of the last click: where keys start from
        # when nothing is selected.
        self._click: Offset | None = None
        # auto_scroll as it was before a selection held the view still.
        self._held_follow: bool | None = None

    # ── selection hooks ───────────────────────────────────────────────────

    def action_select_all(self) -> None:
        """Select every line (Ctrl+A while the log has focus)."""
        self.text_select_all()

    def get_selection(self, selection: Selection) -> tuple[str, str] | None:
        """The text under ``selection``: the rendered lines, as shown."""
        text = "\n".join(strip.text for strip in self.lines)
        return selection.extract(text), "\n"

    def selection_updated(self, selection: Selection | None) -> None:
        """Hold the view still while anything is selected; follow new output again after."""
        if selection is not None and self._held_follow is None:
            self._held_follow = self.auto_scroll
            self.auto_scroll = False
        elif selection is None:
            self._forget_keyboard()
            if self._held_follow is not None:
                self.auto_scroll = self._held_follow
                self._held_follow = None
                if self.auto_scroll:
                    self.scroll_end(animate=False)
        self.refresh()

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

    # ── the keyboard ──────────────────────────────────────────────────────

    def on_click(self, event: events.Click) -> None:
        """Remember the character clicked: a key with nothing selected starts there."""
        widget, offset = self.screen.get_widget_and_offset_at(event.screen_x, event.screen_y)
        if widget is self and offset is not None:
            self._click = offset

    def action_extend_selection(self, move: str) -> None:
        """Move the selection's end by ``move`` (a value of ``_MOVES``), selecting from where it began."""
        if not self.lines:
            return
        anchor, cursor = self._ends()
        self._select(anchor, self._moved(cursor, move))

    def select_newest_row(self) -> None:
        """Select the newest row that has text, from its end back to its start.

        The start of a keyboard selection made from outside the log: Shift+Up
        from a prompt selects the last row, and more presses grow it upward.
        """
        row = self._newest_row()
        if row is None:
            return
        self._select(Offset(self._row_width(row), row), Offset(0, row))

    def key_escape(self, event: events.Key) -> bool:
        """Escape with something selected clears it and goes no further.

        The key stops here, before the screen (which would clear the
        selection too) and the app (where Escape may stop a script), and
        ``SelectionDismissed`` tells the host.  With nothing selected the
        key goes on as usual.
        """
        if not self.deselect():
            return False
        event.stop()
        event.prevent_default()
        self.post_message(self.SelectionDismissed(self))
        return True

    def deselect(self) -> bool:
        """Clear a selection of this log; whether there was one."""
        if self.text_selection is None:
            return False
        self.screen.clear_selection()
        return True

    def _ends(self) -> tuple[Offset, Offset]:
        """Where the selection began and where its moving end is now."""
        selection = self.text_selection
        if (
            selection is not None
            and selection == self._keyboard_selection
            and self._anchor is not None
            and self._cursor is not None
        ):
            return self._anchor, self._cursor
        if selection is not None:
            last = len(self.lines) - 1
            start = selection.start or Offset(0, 0)
            end = selection.end or Offset(self._row_width(last), last)
            return self._clamped(start), self._clamped(end)
        if self._click is not None:
            start = self._clamped(self._click)
            return start, start
        row = self._newest_row() or 0
        start = Offset(self._row_width(row), row)
        return start, start

    def _moved(self, cursor: Offset, move: str) -> Offset:
        """``cursor`` after one ``move``: a character, a row, a row end or a page."""
        x, y = cursor
        last = len(self.lines) - 1
        if move == "left":
            if x > 0:
                x -= 1
            elif y > 0:
                y -= 1
                x = self._row_width(y)
        elif move == "right":
            if x < self._row_width(y):
                x += 1
            elif y < last:
                y += 1
                x = 0
        elif move == "up":
            if y > 0:
                y -= 1
                x = min(x, self._row_width(y))
            else:
                x = 0
        elif move == "down":
            if y < last:
                y += 1
                x = min(x, self._row_width(y))
            else:
                x = self._row_width(y)
        elif move == "home":
            x = 0
        elif move == "end":
            x = self._row_width(y)
        elif move in ("pageup", "pagedown"):
            step = max(1, self.scrollable_content_region.height - 1)
            y = max(0, y - step) if move == "pageup" else min(last, y + step)
            x = min(x, self._row_width(y))
        return Offset(x, y)

    def _select(self, anchor: Offset, cursor: Offset) -> None:
        """Select from ``anchor`` to ``cursor`` and bring ``cursor``'s row into view."""
        selection = Selection.from_offsets(anchor, cursor)
        self.screen.selections = {self: selection}
        self._anchor, self._cursor, self._keyboard_selection = anchor, cursor, selection
        top = round(self.scroll_offset.y)
        height = self.scrollable_content_region.height
        if cursor.y < top:
            self.scroll_to(y=cursor.y, animate=False)
        elif cursor.y >= top + height:
            self.scroll_to(y=cursor.y - height + 1, animate=False)

    def _row_width(self, row: int) -> int:
        """How many characters rendered row ``row`` holds."""
        return len(self.lines[row].text)

    def _clamped(self, offset: Offset) -> Offset:
        """``offset`` moved onto the rows that exist."""
        row = min(max(offset.y, 0), len(self.lines) - 1)
        return Offset(min(max(offset.x, 0), self._row_width(row)), row)

    def _newest_row(self) -> int | None:
        """The last row with visible text, or the last row; None for an empty log."""
        for row in range(len(self.lines) - 1, -1, -1):
            if self.lines[row].text.strip():
                return row
        return len(self.lines) - 1 if self.lines else None

    def _forget_keyboard(self) -> None:
        """Drop the keyboard selection's ends; the next key starts afresh."""
        self._anchor = self._cursor = self._keyboard_selection = None

    # ── line bookkeeping ──────────────────────────────────────────────────

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
            self._click = None
            self._drop_selection()
        return result

    def clear(self) -> Self:
        """Clear as RichLog does, and the selection with it."""
        result = super().clear()
        self._click = None
        self._drop_selection()
        return result

    def _drop_selection(self) -> None:
        """Forget a selection whose line numbers no longer mean what they did."""
        if self.is_attached and self.text_selection is not None:
            self.screen.clear_selection()
