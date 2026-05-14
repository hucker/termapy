"""Modal dialog: PortPicker.

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
from termapy.dialogs._common import _DISMISS_BINDINGS, _MODAL_BTN_CSS, _populate_port_option_list


class PortPicker(ModalScreen[str | None]):
    """Modal dialog to select an available serial port."""

    BINDINGS = _DISMISS_BINDINGS

    CSS = f"""
    PortPicker {{ align: center middle; }}
    PortPicker Button {{ {_MODAL_BTN_CSS} }}
    #port-dialog {{
        width: 130; height: 24;
        border: solid $primary; background: $surface; padding: 1 2;
    }}
    #port-title {{ height: 1; text-style: bold; }}
    #port-list {{ height: 1fr; border: thick $primary; }}
    #port-buttons {{ height: 1; align: right middle; }}
    """

    # Usable row width inside the dialog (dialog width - border - padding
    # - OptionList border).  Matches #port-dialog width: 130 with
    # border:solid (2), padding:1 2 (4 horizontal), and the OptionList's
    # thick border (2).  Adjust if the dialog width in CSS changes.
    _ROW_WIDTH = 124

    def action_dismiss_modal(self) -> None:
        """Close the modal on Ctrl+Q or Escape."""
        self.dismiss(None)

    def compose(self) -> ComposeResult:
        from serial.tools.list_ports import comports
        from textual.widgets import Static

        ports = sorted(comports(), key=lambda p: p.device)
        with Vertical(id="port-dialog"):
            yield Static("Select Serial Port", id="port-title")
            ol = OptionList(id="port-list")
            _populate_port_option_list(ol, ports, self._ROW_WIDTH)
            yield ol
            with Horizontal(id="port-buttons"):
                yield Button("Cancel", id="port-cancel", variant="error")

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(str(event.option.id))

    @on(Button.Pressed, "#port-cancel")
    def cancel_port_picker(self) -> None:
        self.dismiss(None)
