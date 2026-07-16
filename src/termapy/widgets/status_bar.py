"""The transient status line beside the REPL input.

Split out of ``app.py`` as a self-contained widget: it owns its own
auto-clear timer and visibility, and reaches into nothing outside itself.
That's the point of the extraction -- the show / auto-clear / progress
behavior can be Pilot-tested in isolation (see ``tests/test_status_bar.py``),
whereas the same logic embedded in ``SerialTerminal`` needed the whole app
running and so had no coverage.
"""

from __future__ import annotations

from textual.timer import Timer
from textual.widgets import Label


class StatusBar(Label):
    """A one-line status label with two modes and its own visibility.

    - ``show(text, timeout)`` -- transient: appears, then auto-clears after
      ``timeout`` seconds.  A newer message resets the timer; empty text
      clears immediately.
    - ``set_progress(text)``  -- persistent: shows text with no auto-clear
      (empty hides).  Used by the capture/progress overlays.
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
        super().__init__("", **kwargs)
        self._timer: Timer | None = None

    def show(self, text: str, timeout: float = 5.0) -> None:
        """Show transient text that auto-clears after ``timeout`` seconds."""
        if not text:
            self.clear_status()
            return
        self.update(text)
        self.add_class("visible")
        if self._timer is not None:
            self._timer.stop()
        self._timer = self.set_timer(timeout, self.clear_status)

    def set_progress(self, text: str) -> None:
        """Show persistent text with no auto-clear.  Empty text hides."""
        self.update(text)
        if text:
            self.add_class("visible")
        else:
            self.remove_class("visible")

    def clear_status(self) -> None:
        """Clear the text and hide the label."""
        self.update("")
        self.remove_class("visible")
        self._timer = None
