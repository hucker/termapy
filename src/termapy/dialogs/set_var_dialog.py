"""Modal dialog: SetVarDialog -- two-field name/value prompt for /var.set.

Used by the palette's "Set Variable..." entry to collect a variable
name and value without forcing the user to recall the /var.set syntax.

Returns ``(name, value)`` on submit (name stripped, value as-typed),
or ``None`` on cancel.  An empty name is refused with a message rather
than the dialog appearing to ignore the press.
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
    #setvar-error {{ height: 1; color: $error; display: none; }}
    #setvar-error.visible {{ display: block; }}
    #setvar-buttons {{ height: 1; margin-top: 1; }}
    """

    def action_dismiss_modal(self) -> None:
        """Close the modal on Ctrl+Q or Escape."""
        self.dismiss(None)

    def on_mount(self) -> None:
        self.query_one("#setvar-name", Input).tooltip = "Variable name.  Accepts NAME or $(NAME)."
        self.query_one(
            "#setvar-value", Input
        ).tooltip = "Variable value.  Use $(OTHER) to reference another variable."
        self.query_one("#setvar-ok", Button).tooltip = "Set the variable (or press Enter)."
        self.query_one("#setvar-cancel", Button).tooltip = "Close without setting a variable."

    def compose(self) -> ComposeResult:
        with Vertical(id="setvar-dialog"):
            yield Static("Set Variable", id="setvar-title")
            yield Static("Name:", id="setvar-name-label")
            yield Input(placeholder="e.g. timeout", id="setvar-name")
            yield Static("Value:", id="setvar-value-label")
            yield Input(placeholder="e.g. 5000", id="setvar-value")
            yield Static("", id="setvar-error", markup=False)
            with Horizontal(id="setvar-buttons"):
                # The confirming button comes first and is green, as in
                # every other dialog that asks for something.  Without it
                # the only button on screen was Cancel and the way to
                # submit -- Enter -- was written down nowhere.
                yield Button("OK", id="setvar-ok", variant="success")
                yield Button("Cancel", id="setvar-cancel", variant="error")

    def _needs_a_name(self) -> bool:
        """True when the name is missing; says so on screen before returning."""
        if self.query_one("#setvar-name", Input).value.strip():
            return False
        # Say what is missing rather than appearing to ignore the press:
        # the dialog used to only move the focus.
        error = self.query_one("#setvar-error", Static)
        error.update("Enter a variable name")
        error.add_class("visible")
        self.query_one("#setvar-name", Input).focus()
        return True

    @on(Input.Submitted, "#setvar-name")
    def name_submitted(self) -> None:
        """Enter on the name field moves to the value field (web-form convention).

        Nothing is said about an empty name here: the user is still
        filling the form in, and complaining before they have reached
        the end of it would be nagging.  The refusal belongs on submit.
        """
        if self.query_one("#setvar-name", Input).value.strip():
            self.query_one("#setvar-value", Input).focus()

    @on(Input.Submitted, "#setvar-value")
    @on(Button.Pressed, "#setvar-ok")
    def value_submitted(self) -> None:
        """Set the variable, or say what is missing."""
        if self._needs_a_name():
            return
        name = self.query_one("#setvar-name", Input).value.strip()
        value = self.query_one("#setvar-value", Input).value
        self.dismiss((name, value))

    @on(Button.Pressed, "#setvar-cancel")
    def cancel(self) -> None:
        self.dismiss(None)
