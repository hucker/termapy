"""Modal dialog: FilenameDialog -- one-line filename prompt.

Used by the TUI Record button to collect a filename before
dispatching ``/run.record <name>``.  Returns the raw user input
on submit (no path resolution, no suffix manipulation -- the
recorder handles all of that).  Returns ``None`` on cancel.

Modeled on the existing ``NamePicker`` shape; kept separate to
avoid coupling Record's UX to that dialog's stem-stripping
behaviour.
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

    Dismisses with the entered text (stripped) on submit, or ``None``
    on cancel.  Empty input is treated as cancel (no-op).
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
    #filename-buttons {{ height: 1; margin-top: 1; }}
    """

    def __init__(
        self,
        *,
        title: str = "Filename:",
        placeholder: str = "",
    ) -> None:
        """Initialize the dialog.

        Args:
            title: Header text shown above the input.
            placeholder: Greyed-out hint inside the input box.
        """
        super().__init__()
        self._title = title
        self._placeholder = placeholder

    def action_dismiss_modal(self) -> None:
        """Close the modal on Ctrl+Q or Escape."""
        self.dismiss(None)

    def on_mount(self) -> None:
        self.query_one("#filename-input", Input).tooltip = (
            "Filename for the new file."
        )
        self.query_one("#filename-cancel", Button).tooltip = (
            "Close without saving."
        )

    def compose(self) -> ComposeResult:
        with Vertical(id="filename-dialog"):
            yield Static(self._title, id="filename-label")
            yield Input(placeholder=self._placeholder, id="filename-input")
            with Horizontal(id="filename-buttons"):
                yield Button("Cancel", id="filename-cancel")

    @on(Input.Submitted, "#filename-input")
    def submit(self) -> None:
        name = self.query_one("#filename-input", Input).value.strip()
        if name:
            self.dismiss(name)
        # Empty input: stay in the dialog so the user can either
        # type something or hit Cancel.  Silently dismissing with
        # None would be surprising.

    @on(Button.Pressed, "#filename-cancel")
    def cancel(self) -> None:
        self.dismiss(None)
