"""The transient status line beside the REPL input.

Split out of ``app.py`` as a self-contained widget: it owns its own
auto-clear timer and visibility, and reaches into nothing outside itself.
That's the point of the extraction -- the show / auto-clear / progress
behavior can be Pilot-tested in isolation (see ``tests/test_status_bar.py``),
whereas the same logic embedded in ``SerialTerminal`` needed the whole app
running and so had no coverage.
"""

from __future__ import annotations

from functools import partial

from rich.cells import cell_len
from textual.timer import Timer
from textual.widgets import Label


class StatusBar(Label):
    """A one-line status label with two modes and its own visibility.

    - ``show(text, timeout)`` -- transient: appears, then auto-clears after
      ``timeout`` seconds.  A newer message resets the timer; empty text
      clears immediately.
    - ``set_progress(text)``  -- persistent: shows text with no auto-clear
      (empty hides).  Used by the capture/progress overlays, and by the
      second instance (``#traffic-bar``) that shows ``/term.traffic``'s counts.
    - ``clear_status()``      -- hide immediately.

    Visibility is the ``visible`` CSS class, so the label occupies no space
    when empty.
    """

    DEFAULT_CSS = """
    StatusBar {
        width: auto;
        max-width: 50%;
        height: 1;
        display: none;
        color: $text-muted;
        content-align: right middle;
        padding: 0 1;
    }
    StatusBar.visible {
        display: block;
    }
    """

    def __init__(self, **kwargs) -> None:
        # The text comes from plugins (ctx.io.status_bar) and progress
        # overlays: text, never markup (registry widget-text-literal).
        super().__init__("", markup=False, **kwargs)
        self._timer: Timer | None = None
        # Counts every message, so an auto-clear timer that fires late
        # (after a newer message) knows its message is gone and does nothing.
        self._shown = 0
        self._text = ""
        # The widest text laid out since the label was last hidden.
        self._width = 0

    def _set_text(self, text: str) -> None:
        """Show ``text``, laying out the screen only when it grows wider.

        A layout pass repaints the bottom row and the output pane: a plugin
        refreshing a counter twice a second (``ctx.io.status_bar``) made the
        screen flicker.  Unchanged text repaints nothing, and text no wider
        than the label already is repaints the label alone (Textual's
        ``layout=False``): a shorter text keeps the label's width,
        right-aligned in it, until the label is hidden.
        """
        if text == self._text:
            return
        self._text = text
        grows = cell_len(text) > self._width
        if grows:
            self._width = cell_len(text)
        self.update(text, layout=grows)

    def show(self, text: str, timeout: float = 5.0) -> None:
        """Show transient text that auto-clears after ``timeout`` seconds."""
        if not text:
            self.clear_status()
            return
        self._set_text(text)
        self.add_class("visible")
        self._drop_timer()
        self._timer = self.set_timer(timeout, partial(self._clear_if_current, self._shown))

    def _drop_timer(self) -> None:
        """Forget the auto-clear timer: a new message, or progress text, outlives it."""
        self._shown += 1
        if self._timer is not None:
            self._timer.stop()
            self._timer = None

    def _clear_if_current(self, shown: int) -> None:
        """The auto-clear timer fired: clear, unless a newer message replaced the one it was set for.

        A stopped timer can still fire once (Textual's Windows sleep waits
        on an executor thread, and ``stop()`` does not always beat a wait
        that has already run out), and that fire would clear the newer
        text at once.
        """
        if shown == self._shown:
            self.clear_status()

    def set_progress(self, text: str) -> None:
        """Show persistent text with no auto-clear.  Empty text hides; a transient message's timer no longer clears it."""
        self._drop_timer()
        self._set_text(text)
        if text:
            self.add_class("visible")
        else:
            self.remove_class("visible")
            self._width = 0

    def clear_status(self) -> None:
        """Clear the text and hide the label; the next text lays it out afresh."""
        self._set_text("")
        self.remove_class("visible")
        self._width = 0
        self._timer = None
