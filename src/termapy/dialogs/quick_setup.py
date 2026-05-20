"""Modal dialog: QuickSetup.

Extracted from the original monolithic ``dialogs.py``.  See
``termapy.dialogs.__init__`` for the package-level public API and
the ``_common`` submodule for shared constants and helpers.
"""

from __future__ import annotations

from pathlib import Path

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Input, OptionList
from textual.widgets.option_list import Option

from termapy.dialogs._common import _DISMISS_BINDINGS, _MODAL_BTN_CSS, _populate_port_option_list


class QuickSetup(ModalScreen[tuple | None]):
    """Quick setup dialog - name, port, baud rate in one screen.

    Returns ``(action, name, port, baud_rate, custom_baud, add_icon)``
    tuple on submit, or None on cancel.  ``action`` is "connect" or
    "advanced".  ``add_icon`` is the user's choice to also create a
    desktop / menu launcher for the cfg (default False; checked via
    the inline checkbox).
    """

    BINDINGS = _DISMISS_BINDINGS

    CSS = f"""
    QuickSetup {{ align: center middle; }}
    QuickSetup Button {{ {_MODAL_BTN_CSS} }}
    #qs-dialog {{
        width: 90%; min-width: 100; max-width: 200; height: auto;
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
        # Cached at compose-time so on_mount can re-render the port
        # list against the dialog's measured cell width.  Cheap to
        # call once; expensive to scan every resize.
        self._ports: list = []

    def action_dismiss_modal(self) -> None:
        self.dismiss(None)

    def compose(self) -> ComposeResult:
        from serial.tools.list_ports import comports
        from textual.widgets import Static

        self._ports = sorted(comports(), key=lambda p: p.device)
        dialog = Vertical(id="qs-dialog")
        dialog.border_title = self._title
        with dialog:
            yield Static("Config Name:", classes="qs-label qs-first")
            yield Input(placeholder="e.g. my_device", id="qs-name")
            yield Static("Serial Port:", classes="qs-label")
            # Initial population uses a conservative row_width.
            # on_mount() re-renders with the actual measured width
            # so wide terminals show wider columns (incl. Location).
            port_list = OptionList(id="qs-port-list")
            _populate_port_option_list(
                port_list, self._ports, row_width=120,
            )
            if self._ports:
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
            # Optional add-icon checkbox; off by default so existing
            # users who just want a cfg aren't surprised.  Targets
            # the non-CLI audience who's setting termapy up the first
            # time and would benefit from a double-clickable launcher.
            yield Checkbox(
                "Add a desktop / menu launcher for this config",
                id="qs-add-icon",
                value=False,
            )
            with Horizontal(id="qs-buttons"):
                connect_btn = Button("Connect", id="qs-connect", variant="success")
                if not self._ports:
                    connect_btn.label = "No Ports"
                    connect_btn.variant = "error"
                    connect_btn.disabled = True
                yield connect_btn
                adv = Button("Advanced", id="qs-advanced")
                adv.styles.background = "darkorchid"
                yield adv
                yield Button("Cancel", id="qs-cancel", variant="error")

    def on_mount(self) -> None:
        """Defer the real port-list render to after first layout.

        At on_mount time the OptionList hasn't yet been positioned,
        so ``size`` / ``content_size`` are 0 (or stale).  By the
        time ``call_after_refresh`` fires, Textual has done a full
        layout pass and ``content_size.width`` is the actual cell
        budget the option text gets to live in.
        """
        self.call_after_refresh(self._render_port_list)

    def on_resize(self, _event) -> None:
        """Re-render the port list when the terminal/dialog resizes.

        Without this, columns dropped while the screen was narrow
        stay dropped after the user widens the terminal -- the
        port list keeps showing the cramped layout even though
        more room is now available.
        """
        self.call_after_refresh(self._render_port_list)

    def _render_port_list(self) -> None:
        """Repopulate the port list against the measured content width.

        Preserves the user's current selection across re-renders so
        a resize doesn't reset their port pick to the default.
        """
        port_list = self.query_one("#qs-port-list", OptionList)
        # content_size excludes the OptionList's border but may
        # still include the scrollbar column.  Subtract 1 for the
        # scrollbar to be safe; if the OptionList isn't scrollable
        # the cost is one wasted cell.
        measured = port_list.content_size.width
        row_width = max(40, measured - 1) if measured else 100

        # Remember which port (by id) was selected so we can
        # restore it after repopulating.
        selected_id: str | None = None
        if port_list.highlighted is not None:
            opt = port_list.get_option_at_index(port_list.highlighted)
            if opt is not None and opt.id is not None and not opt.disabled:
                selected_id = str(opt.id)

        port_list.clear_options()
        _populate_port_option_list(port_list, self._ports, row_width=row_width)

        if selected_id is not None:
            for i in range(port_list.option_count):
                opt = port_list.get_option_at_index(i)
                if opt is not None and opt.id == selected_id:
                    port_list.highlighted = i
                    return
        if self._ports:
            port_list.highlighted = 2  # skip header + separator rows

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
        add_icon = self.query_one("#qs-add-icon", Checkbox).value

        self.dismiss(("connect", name, port, baud, custom_baud, add_icon))

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
        add_icon = self.query_one("#qs-add-icon", Checkbox).value
        self.dismiss(("advanced", name, port, baud, custom_baud, add_icon))

    @on(Button.Pressed, "#qs-cancel")
    def cancel(self) -> None:
        self.dismiss(None)
