"""Modal dialog: WelcomeDialog.

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


class WelcomeDialog(ModalScreen[None]):
    """Modal welcome message with a single OK button."""

    BINDINGS = _DISMISS_BINDINGS

    CSS = f"""
    WelcomeDialog {{ align: center middle; }}
    WelcomeDialog Button {{ {_MODAL_BTN_CSS} }}
    #welcome-dialog {{
        width: 70; height: 12;
        border: solid $primary; background: $surface; padding: 1 2;
    }}
    #welcome-title {{ height: 1; text-style: bold; }}
    #welcome-msg {{ height: 1fr; }}
    #welcome-buttons {{ height: 1; align: center middle; }}
    """

    def action_dismiss_modal(self) -> None:
        self.dismiss(None)

    def __init__(self, title: str, message: str) -> None:
        super().__init__()
        self.title_text = title
        self.message = message

    def compose(self) -> ComposeResult:
        from textual.widgets import Static

        with Vertical(id="welcome-dialog"):
            yield Static(self.title_text, id="welcome-title")
            yield Static(self.message, id="welcome-msg")
            with Horizontal(id="welcome-buttons"):
                yield Button("OK", id="welcome-ok", variant="success")

    @on(Button.Pressed, "#welcome-ok")
    def ok_pressed(self) -> None:
        self.dismiss(None)
