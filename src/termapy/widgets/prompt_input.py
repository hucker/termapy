"""The REPL prompt: an Input that hands a paste of several lines to the app.

Textual's Input keeps only the first line of a paste.  A serial-terminal
user pasting a block of device commands or code expects every line sent,
as TeraTerm sends it, so a paste of two lines or more becomes a
``MultilinePaste`` message the app answers (``multiline_paste``); a
one-line paste lands in the prompt exactly as before.

Ctrl+D and Ctrl+Z on an empty prompt are passed on to the app, which
sends them to the device (``/term.ctrl d``, ``z``): with nothing to edit,
the keys are free to mean what they mean at a device prompt.  With text
in the prompt Ctrl+D deletes and Ctrl+Z does nothing.
"""

from __future__ import annotations

from textual import events
from textual.actions import SkipAction
from textual.binding import Binding
from textual.message import Message
from textual.widgets import Input


class PromptInput(Input):
    """An Input that posts ``MultilinePaste`` for a paste of two lines or more."""

    # Ctrl+D only; the Delete key keeps Input's delete_right everywhere.
    BINDINGS = [
        Binding("ctrl+d", "delete_right_or_pass", show=False),
        Binding("ctrl+z", "pass_when_empty", show=False),
    ]

    def action_delete_right_or_pass(self) -> None:
        """Delete right of the cursor; on an empty prompt, pass Ctrl+D on to the app."""
        if not self.value:
            raise SkipAction()
        self.action_delete_right()

    def action_pass_when_empty(self) -> None:
        """On an empty prompt, pass Ctrl+Z on to the app; with text, keep it and do nothing."""
        if not self.value:
            raise SkipAction()

    class MultilinePaste(Message):
        """A paste of two lines or more, split into its lines."""

        def __init__(self, lines: list[str]) -> None:
            super().__init__()
            self.lines = lines

    def _on_paste(self, event: events.Paste) -> None:
        """Post a multi-line paste and keep Input's own handler from inserting its first line.

        Textual runs the paste handler of every class in the MRO unless one
        prevents the default, so a one-line paste needs nothing here: it
        falls through to ``Input._on_paste``.
        """
        lines = event.text.splitlines()
        if len(lines) < 2:
            return
        event.prevent_default()
        event.stop()
        self.post_message(self.MultilinePaste(lines))
