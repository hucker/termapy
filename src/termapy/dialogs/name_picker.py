"""Modal dialog: NamePicker.

Extracted from the original monolithic ``dialogs.py``.  See
``termapy.dialogs.__init__`` for the package-level public API and
the ``_common`` submodule for shared constants and helpers.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from rich.errors import MarkupError
from textual import events, on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.css.query import NoMatches
from textual.screen import ModalScreen
from textual.widgets import Button, Input, OptionList, TextArea
from textual.widgets.option_list import Option

from termapy.config import (
    cfg_dir,
    cfg_path_for_name,
    migrate_json_to_cfg,
    open_with_system,
)
from termapy.defaults import DEFAULT_CFG, PROTO_TEMPLATE, SCRIPT_TEMPLATE
from termapy.dialogs._common import _DISMISS_BINDINGS, _MODAL_BTN_CSS


class NamePicker(ModalScreen[str | None]):
    """Modal dialog to enter a name for a new config."""

    BINDINGS = _DISMISS_BINDINGS

    CSS = f"""
    NamePicker {{ align: center middle; }}
    NamePicker Button {{ {_MODAL_BTN_CSS} }}
    #name-dialog {{
        width: 40; height: auto;
        border: solid $primary; background: $surface; padding: 1 2;
    }}
    #name-label {{ height: 1; text-style: bold; }}
    #name-buttons {{ height: 1; margin-top: 1; }}
    """

    def action_dismiss_modal(self) -> None:
        """Close the modal on Ctrl+Q or Escape."""
        self.dismiss(None)

    def compose(self) -> ComposeResult:
        from textual.widgets import Static

        with Vertical(id="name-dialog"):
            yield Static("New Config Name:", id="name-label")
            yield Input(placeholder="e.g. iot_dev", id="name-input")
            with Horizontal(id="name-buttons"):
                yield Button("Cancel", id="name-cancel")

    @on(Input.Submitted, "#name-input")
    def submit_name(self) -> None:
        name = self.query_one("#name-input", Input).value.strip()
        if name:
            # Strip extension if they typed it
            name = Path(name).stem
            self.dismiss(name)

    @on(Button.Pressed, "#name-cancel")
    def cancel(self) -> None:
        self.dismiss(None)
