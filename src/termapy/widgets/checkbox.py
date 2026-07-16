"""StrongCheckbox -- a Checkbox with a clearer on/off glyph."""

from __future__ import annotations

from textual.widgets import Checkbox


class StrongCheckbox(Checkbox):
    """A Checkbox whose glyph FLIPS between blank (off) and ``X`` (on).

    Textual's default Checkbox renders the same ``X`` glyph in both
    states, distinguishing them only by a subtle text-color shift
    (dim gray vs success-green).  That reads as a weak signal on
    most terminals -- users often have to look twice to tell whether
    a checkbox is checked.

    ``StrongCheckbox`` keeps the Checkbox API (same value, same
    Changed event) but:

    - swaps the inner glyph: blank when off, ``X`` when on (the
      classic ``[ ]`` / ``[X]`` convention)
    - tints via CSS: red for off, green-bold for on

    ASCII on purpose.  The previous glyphs (``✓``/``✗``) are
    East-Asian-Width *ambiguous*: Rich measures them as one cell but
    some terminal fonts (e.g. VS Code's integrated terminal) draw
    them two cells wide, clipping the glyph.  Blank/``X`` can never
    mis-measure.
    """

    DEFAULT_CSS = """
    StrongCheckbox > .toggle--button {
        color: $error;
        background: $surface;
    }
    StrongCheckbox.-on > .toggle--button {
        color: $success;
        background: $surface;
        text-style: bold;
    }
    """

    # Initial glyph -- overridden per-instance in __init__ and on every
    # value change in watch_value.  Class-level default covers the case
    # where Textual queries BUTTON_INNER before the instance attr is set.
    BUTTON_INNER: str = " "

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        # Belt-and-suspenders: set the instance glyph from the current
        # value here in case watch_value didn't fire during super's
        # __init__ (Textual's reactive init=False can skip the watcher
        # on the initial assignment).
        self.BUTTON_INNER = "X" if self.value else " "

    def watch_value(self) -> None:
        # Match parent's signature (no value arg); parent's watcher
        # sets the -on class and posts the Changed message, so we
        # super() first, then refresh the glyph.
        super().watch_value()
        self.BUTTON_INNER = "X" if self.value else " "
        self.refresh()
