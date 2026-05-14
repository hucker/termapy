"""Modal dialog: CfgConfirm.

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


class CfgConfirm(ModalScreen[bool]):
    """Modal dialog to confirm a config change."""

    BINDINGS = _DISMISS_BINDINGS

    CSS = f"""
    CfgConfirm {{ align: center middle; }}
    CfgConfirm Button {{ {_MODAL_BTN_CSS} }}
    #cfg-confirm-dialog {{
        width: 50; height: 7;
        border: solid $primary; background: $surface; padding: 1 2;
    }}
    #cfg-confirm-msg {{ height: 1; }}
    #cfg-confirm-buttons {{ height: 1; align: right middle; }}
    """

    def action_dismiss_modal(self) -> None:
        """Close the modal on Ctrl+Q or Escape."""
        self.dismiss(False)

    def __init__(self, key: str, old_val, new_val) -> None:
        super().__init__()
        self.key = key
        self.old_val = old_val
        self.new_val = new_val

    def compose(self) -> ComposeResult:
        from textual.widgets import Static

        with Vertical(id="cfg-confirm-dialog"):
            yield Static(
                f"{self.key}: {self.old_val!r} -> {self.new_val!r}",
                id="cfg-confirm-msg",
            )
            with Horizontal(id="cfg-confirm-buttons"):
                yield Button("Yes", id="cfg-yes", variant="success")
                yield Button("No", id="cfg-no", variant="error")

    @on(Button.Pressed, "#cfg-yes")
    def confirm(self) -> None:
        self.dismiss(True)

    @on(Button.Pressed, "#cfg-no")
    def cancel(self) -> None:
        self.dismiss(False)
