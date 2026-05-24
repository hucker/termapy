"""Modal dialog: SetVarDialog -- two-field name/value prompt for /var.set.

Used by the palette's "Set Variable..." entry to collect a variable
name and value without forcing the user to recall the /var.set syntax.

Returns ``(name, value)`` on submit (name stripped, value as-typed),
or ``None`` on cancel.  Empty name is rejected at submit -- the dialog
re-focuses the name field rather than dismissing silently.
"""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static

from termapy.dialogs._common import _DISMISS_BINDINGS, _MODAL_BTN_CSS


class SetVarDialog(ModalScreen[tuple[str, str] | None]):
    """Two-line name/value prompt that drives ``/var.set``."""

    BINDINGS = _DISMISS_BINDINGS

    CSS = f"""
    SetVarDialog {{ align: center middle; }}
    SetVarDialog Button {{ {_MODAL_BTN_CSS} }}
    #setvar-dialog {{
        width: 60; height: auto;
        border: solid $primary; background: $surface; padding: 1 2;
    }}
    #setvar-title {{ height: 1; text-style: bold; }}
    #setvar-name-label, #setvar-value-label {{
        height: 1; margin-top: 1;
    }}
    #setvar-buttons {{ height: 1; margin-top: 1; }}
    """

    def action_dismiss_modal(self) -> None:
        """Close the modal on Ctrl+Q or Escape."""
        self.dismiss(None)

    def compose(self) -> ComposeResult:
        with Vertical(id="setvar-dialog"):
            yield Static("Set Variable", id="setvar-title")
            yield Static("Name:", id="setvar-name-label")
            yield Input(placeholder="e.g. timeout", id="setvar-name")
            yield Static("Value:", id="setvar-value-label")
            yield Input(placeholder="e.g. 5000", id="setvar-value")
            with Horizontal(id="setvar-buttons"):
                yield Button("Cancel", id="setvar-cancel")

    @on(Input.Submitted, "#setvar-name")
    def name_submitted(self) -> None:
        # Enter on the name field jumps to the value field (web-form
        # convention).  If name is empty, stay put -- the user needs
        # to type something or hit Cancel.
        if self.query_one("#setvar-name", Input).value.strip():
            self.query_one("#setvar-value", Input).focus()

    @on(Input.Submitted, "#setvar-value")
    def value_submitted(self) -> None:
        name = self.query_one("#setvar-name", Input).value.strip()
        value = self.query_one("#setvar-value", Input).value
        if not name:
            # Empty name: focus it so the user sees what's missing.
            self.query_one("#setvar-name", Input).focus()
            return
        self.dismiss((name, value))

    @on(Button.Pressed, "#setvar-cancel")
    def cancel(self) -> None:
        self.dismiss(None)
