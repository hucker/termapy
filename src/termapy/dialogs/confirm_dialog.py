"""Modal dialog: ConfirmDialog.

Extracted from the original monolithic ``dialogs.py``.  See
``termapy.dialogs.__init__`` for the package-level public API and
the ``_common`` submodule for shared constants and helpers.
"""

from __future__ import annotations


from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button

from termapy.dialogs._common import _DISMISS_BINDINGS, _MODAL_BTN_CSS


class ConfirmDialog(ModalScreen[bool]):
    """Generic Yes/Cancel confirmation dialog.

    Args:
        message: Text to display in the dialog.
    """

    BINDINGS = _DISMISS_BINDINGS

    CSS = f"""
    ConfirmDialog {{ align: center middle; }}
    ConfirmDialog Button {{ {_MODAL_BTN_CSS} }}
    #confirm-dialog {{
        width: 60; height: auto; max-height: 15;
        border: solid $primary; background: $surface; padding: 1 2;
    }}
    #confirm-msg {{ height: auto; }}
    #confirm-buttons {{ height: 1; align: right middle; }}
    """

    def action_dismiss_modal(self) -> None:
        """Close the modal on Ctrl+Q or Escape."""
        self.dismiss(False)

    def __init__(self, message: str) -> None:
        super().__init__()
        self.message = message

    def compose(self) -> ComposeResult:
        from textual.widgets import Static

        with Vertical(id="confirm-dialog"):
            yield Static(self.message, id="confirm-msg")
            with Horizontal(id="confirm-buttons"):
                yield Button("Yes", id="confirm-yes", variant="success")
                yield Button("Cancel", id="confirm-no", variant="error")

    def on_mount(self) -> None:
        yes_btn = self.query_one("#confirm-yes", Button)
        yes_btn.tooltip = "Confirm the action."
        yes_btn.focus()
        self.query_one("#confirm-no", Button).tooltip = (
            "Close without confirming."
        )

    @on(Button.Pressed, "#confirm-yes")
    def confirm(self) -> None:
        """Dismiss with True."""
        self.dismiss(True)

    @on(Button.Pressed, "#confirm-no")
    def cancel(self) -> None:
        """Dismiss with False."""
        self.dismiss(False)
