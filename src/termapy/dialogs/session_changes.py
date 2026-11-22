"""Modal dialog: SessionChanges -- the settings a session changed, asked about before they would be lost.

The config editor starts from the file and its Save is a load; a load and an
exit end the session too.  Each would drop what the session changed
(``config.session_changes``), so the TUI lists those settings, the file's
value beside the session's, and asks: save them and go on, go on without
them, or cancel.
"""

from __future__ import annotations

import json

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from termapy.config import SessionChange
from termapy.dialogs._common import _DISMISS_BINDINGS, _MODAL_BTN_CSS

# What going on without saving does to the changes, by the verb on the buttons.
_LOSS = {
    "Edit": "The editor starts from the file, and its Save ends the session's changes.",
    "Exit": "Exiting ends the session's changes.",
    "Load": "Loading a config ends the session's changes.",
    "Switch": "The next mode loads the config file, so the session's changes end.",
}


def _spelled(value: object) -> str:
    """A value as the config file spells it: ``true``, ``"\\r\\n"``, ``9600``."""
    return json.dumps(value)


class SessionChanges(ModalScreen[tuple]):
    """Ask whether to save the session's changed settings before they would be lost.

    Dismisses with ``("save",)``, ``("proceed",)`` or ``("cancel",)`` (also
    Escape and Ctrl+Q).

    Args:
        changes: What the session changed, in ``CFG_KEYS`` order.
        file_name: The config file's name, for the question.
        verb: What going on does, on the buttons: ``Edit``, ``Exit``, ``Load`` or ``Switch``.
    """

    BINDINGS = _DISMISS_BINDINGS

    CSS = f"""
    SessionChanges {{ align: center middle; }}
    SessionChanges Button {{ {_MODAL_BTN_CSS} }}
    #changes-dialog {{
        width: auto; height: auto; max-height: 30;
        border: solid $primary; background: $surface; padding: 1 2;
        border-title-align: left;
    }}
    #changes-dialog Static {{ width: auto; height: auto; }}
    #changes-rows {{ margin: 1 0 1 2; }}
    #changes-buttons {{ width: auto; height: 1; margin-top: 1; }}
    """

    def __init__(self, changes: list[SessionChange], file_name: str, verb: str = "Edit") -> None:
        super().__init__()
        self._changes = changes
        self._file_name = file_name
        self._verb = verb

    def action_dismiss_modal(self) -> None:
        """Close the modal on Ctrl+Q or Escape: nothing saved, nothing done."""
        self.dismiss(("cancel",))

    def compose(self) -> ComposeResult:
        width = max(len(change.path) for change in self._changes)
        rows = "\n".join(
            f"{change.path:<{width}}  {_spelled(change.saved)} -> {_spelled(change.session)}"
            for change in self._changes
        )
        dialog = Vertical(id="changes-dialog")
        dialog.border_title = "Unsaved Session Settings"
        with dialog:
            yield Static(
                f"This session changed settings {self._file_name} does not have:", markup=False
            )
            yield Static(rows, id="changes-rows", markup=False)
            yield Static(_LOSS[self._verb], markup=False)
            with Horizontal(id="changes-buttons"):
                yield Button(Text(f"Save and {self._verb}"), id="changes-save", variant="success")
                yield Button(
                    Text(f"{self._verb} Without Saving"), id="changes-proceed", variant="warning"
                )
                yield Button("Cancel", id="changes-cancel", variant="error")

    def on_mount(self) -> None:
        save = self.query_one("#changes-save", Button)
        save.tooltip = f"Write these settings into {self._file_name}, then go on."
        save.focus()
        self.query_one("#changes-proceed", Button).tooltip = "Go on; these settings are dropped."
        self.query_one("#changes-cancel", Button).tooltip = "Keep the session as it is."

    @on(Button.Pressed, "#changes-save")
    def save(self) -> None:
        """Dismiss with ``("save",)``."""
        self.dismiss(("save",))

    @on(Button.Pressed, "#changes-proceed")
    def proceed(self) -> None:
        """Dismiss with ``("proceed",)``."""
        self.dismiss(("proceed",))

    @on(Button.Pressed, "#changes-cancel")
    def cancel(self) -> None:
        """Dismiss with ``("cancel",)``."""
        self.dismiss(("cancel",))
