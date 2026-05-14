"""Modal dialog: QuickSetup.

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


class QuickSetup(ModalScreen[tuple | None]):
    """Quick setup dialog - name, port, baud rate in one screen.

    Returns (name, port, baud_rate) tuple or None on cancel.
    Used for first-run and New Config flows.
    """

    BINDINGS = _DISMISS_BINDINGS

    CSS = f"""
    QuickSetup {{ align: center middle; }}
    QuickSetup Button {{ {_MODAL_BTN_CSS} }}
    #qs-dialog {{
        width: 130; height: auto;
        border: solid $primary; background: $surface; padding: 1 2;
        border-title-align: left;
    }}
    #qs-standard-baud {{ margin-top: 1; margin-bottom: 1; margin-left: 1; }}
    #qs-port-list {{ height: 10; border: tall $primary; }}
    #qs-baud-list {{ height: 6; border: tall $primary; }}
    #qs-baud-input {{ border: tall $primary; }}
    .qs-hidden {{ display: none; }}
    #qs-buttons {{ height: 1; margin-top: 1; align: right middle; }}
    .qs-label {{ height: 1; margin-top: 1; padding-left: 1; text-style: bold; }}
    .qs-first {{ margin-top: 0; }}
    """

    _COMMON_BAUDS = [
        300, 600, 1200, 2400, 4800, 9600, 19200, 38400, 57600,
        115200, 230400, 460800, 921600, 1500000, 2000000, 3000000,
    ]

    def __init__(self, title: str = "New Config") -> None:
        super().__init__()
        self._title = title

    def action_dismiss_modal(self) -> None:
        self.dismiss(None)

    def compose(self) -> ComposeResult:
        from serial.tools.list_ports import comports
        from textual.widgets import Static

        ports = sorted(comports(), key=lambda p: p.device)
        dialog = Vertical(id="qs-dialog")
        dialog.border_title = self._title
        with dialog:
            yield Static("Config Name:", classes="qs-label qs-first")
            yield Input(placeholder="e.g. my_device", id="qs-name")
            yield Static("Serial Port:", classes="qs-label")
            port_list = OptionList(id="qs-port-list")
            _populate_port_option_list(port_list, ports, row_width=124)
            if ports:
                port_list.highlighted = 2  # skip header + separator rows
            yield port_list
            std_btn = Button("Standard Baud Rates", id="qs-standard-baud", variant="primary")
            std_btn.tooltip = "Click to switch to custom baud rate entry"
            yield std_btn
            baud_list = OptionList(id="qs-baud-list")
            for baud in self._COMMON_BAUDS:
                baud_list.add_option(Option(str(baud), id=str(baud)))
            # Default to 115200
            baud_list.highlighted = self._COMMON_BAUDS.index(115200)
            yield baud_list
            baud_input = Input(
                placeholder="Enter baud rate (>= 300)",
                id="qs-baud-input",
                type="integer",
            )
            baud_input.add_class("qs-hidden")
            yield baud_input
            with Horizontal(id="qs-buttons"):
                connect_btn = Button("Connect", id="qs-connect", variant="success")
                if not ports:
                    connect_btn.label = "No Ports"
                    connect_btn.variant = "error"
                    connect_btn.disabled = True
                yield connect_btn
                adv = Button("Advanced", id="qs-advanced")
                adv.styles.background = "darkorchid"
                yield adv
                yield Button("Cancel", id="qs-cancel", variant="error")

    _standard_baud: bool = True

    @on(Button.Pressed, "#qs-standard-baud")
    def _toggle_standard_baud(self) -> None:
        self._standard_baud = not self._standard_baud
        btn = self.query_one("#qs-standard-baud", Button)
        baud_list = self.query_one("#qs-baud-list", OptionList)
        baud_input = self.query_one("#qs-baud-input", Input)
        if self._standard_baud:
            btn.label = "Standard Baud Rates"
            btn.variant = "primary"
            btn.tooltip = "Click to switch to custom baud rate entry"
            baud_list.remove_class("qs-hidden")
            baud_input.add_class("qs-hidden")
        else:
            btn.label = "Custom Baud Rate"
            btn.variant = "warning"
            btn.tooltip = "Click to switch to standard baud rate list"
            baud_list.add_class("qs-hidden")
            baud_input.remove_class("qs-hidden")
            baud_input.focus()

    def _read_baud(self) -> tuple[int, bool] | None:
        """Read baud rate from the active widget.

        Returns (baud, custom_baud) or None if validation fails.
        """
        standard = self._standard_baud
        custom = not standard
        if custom:
            raw = self.query_one("#qs-baud-input", Input).value.strip()
            if not raw:
                self.notify("Enter a baud rate", severity="warning", timeout=2)
                return None
            try:
                baud = int(raw)
            except ValueError:
                self.notify("Baud rate must be a number", severity="warning", timeout=2)
                return None
            if baud < 300:
                self.notify("Baud rate must be >= 300", severity="warning", timeout=2)
                return None
            return baud, True
        baud_ol = self.query_one("#qs-baud-list", OptionList)
        if baud_ol.highlighted is not None:
            baud = int(str(baud_ol.get_option_at_index(baud_ol.highlighted).id))
        else:
            baud = 115200
        return baud, False

    def _submit(self) -> None:
        name = self.query_one("#qs-name", Input).value.strip()
        if not name:
            self.notify("Enter a config name", severity="warning", timeout=2)
            return
        name = Path(name).stem

        port_ol = self.query_one("#qs-port-list", OptionList)
        if port_ol.highlighted is not None:
            opt = port_ol.get_option_at_index(port_ol.highlighted)
            port = str(opt.id) if not opt.disabled else ""
        else:
            port = ""

        result = self._read_baud()
        if result is None:
            return
        baud, custom_baud = result

        self.dismiss(("connect", name, port, baud, custom_baud))

    @on(Button.Pressed, "#qs-connect")
    def connect(self) -> None:
        self._submit()

    @on(Input.Submitted, "#qs-name")
    def submit_name(self) -> None:
        self._submit()

    @on(Button.Pressed, "#qs-advanced")
    def advanced(self) -> None:
        name = self.query_one("#qs-name", Input).value.strip()
        if not name:
            self.notify("Enter a config name", severity="warning", timeout=2)
            return
        name = Path(name).stem
        port_ol = self.query_one("#qs-port-list", OptionList)
        if port_ol.highlighted is not None:
            opt = port_ol.get_option_at_index(port_ol.highlighted)
            port = str(opt.id) if not opt.disabled else ""
        else:
            port = ""
        result = self._read_baud()
        if result is None:
            return
        baud, custom_baud = result
        self.dismiss(("advanced", name, port, baud, custom_baud))

    @on(Button.Pressed, "#qs-cancel")
    def cancel(self) -> None:
        self.dismiss(None)
