"""Modal dialog: FilenameDialog -- one-line filename prompt.

Used by the TUI Record button to collect a filename before
dispatching ``/run.record <name>``.  Returns the raw user input
on OK or Enter (no path resolution, no suffix manipulation -- the
recorder handles all of that).  Returns ``None`` on cancel.
"""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static

from termapy.dialogs._common import _DISMISS_BINDINGS, _MODAL_BTN_CSS


class FilenameDialog(ModalScreen[str | None]):
    """Single-line filename prompt.

    Dismisses with the entered text (stripped) on OK or Enter, or
    ``None`` on Cancel or Escape.  An empty name is refused with a
    message rather than silently ignored.
    """

    BINDINGS = _DISMISS_BINDINGS

    CSS = f"""
    FilenameDialog {{ align: center middle; }}
    FilenameDialog Button {{ {_MODAL_BTN_CSS} }}
    #filename-dialog {{
        width: 50; height: auto;
        border: solid $primary; background: $surface; padding: 1 2;
    }}
    #filename-label {{ height: 1; text-style: bold; }}
    #filename-error {{ height: 1; color: $error; display: none; }}
    #filename-error.visible {{ display: block; }}
    #filename-buttons {{ height: 1; margin-top: 1; }}
    """

    def __init__(
        self,
        *,
        title: str = "Filename:",
        placeholder: str = "",
        value: str = "",
    ) -> None:
        """Initialize the dialog.

        Args:
            title: Header text shown above the input.
            placeholder: Grayed-out hint inside the input box.
            value: Initial text in the input (a rename starts from the
                current name, cursor at the end).
        """
        super().__init__()
        self._title = title
        self._placeholder = placeholder
        self._value = value

    def action_dismiss_modal(self) -> None:
        """Close the modal on Ctrl+Q or Escape."""
        self.dismiss(None)

    def on_mount(self) -> None:
        self.query_one("#filename-input", Input).tooltip = "Filename for the new file."
        self.query_one("#filename-ok", Button).tooltip = "Use this filename (or press Enter)."
        self.query_one("#filename-cancel", Button).tooltip = "Close without saving."

    def compose(self) -> ComposeResult:
        with Vertical(id="filename-dialog"):
            yield Static(self._title, id="filename-label", markup=False)
            yield Input(placeholder=self._placeholder, value=self._value, id="filename-input")
            yield Static("", id="filename-error", markup=False)
            with Horizontal(id="filename-buttons"):
                # The confirming button comes first and is green, as in
                # every other dialog that asks for something.  Without it
                # the only button on screen was Cancel and the way to
                # submit -- Enter -- was written down nowhere.
                yield Button("OK", id="filename-ok", variant="success")
                yield Button("Cancel", id="filename-cancel", variant="error")

    @on(Input.Submitted, "#filename-input")
    @on(Button.Pressed, "#filename-ok")
    def submit(self) -> None:
        """Accept the name, or say why it cannot be accepted."""
        name = self.query_one("#filename-input", Input).value.strip()
        if not name:
            # Say what is missing rather than appearing to ignore the
            # press: the dialog used to just sit there.
            error = self.query_one("#filename-error", Static)
            error.update("Enter a filename")
            error.add_class("visible")
            self.query_one("#filename-input", Input).focus()
            return
        self.dismiss(name)

    @on(Button.Pressed, "#filename-cancel")
    def cancel(self) -> None:
        self.dismiss(None)
