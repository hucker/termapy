"""Modal dialog: PortPicker -- pick a serial port from a list that follows USB plug and unplug, or type a network port.

Extracted from the original monolithic ``dialogs.py``.  See
``termapy.dialogs.__init__`` for the package-level public API and
the ``_common`` submodule for shared constants and helpers.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, OptionList

from termapy.dialogs._common import (
    _DISMISS_BINDINGS,
    _MODAL_BTN_CSS,
    NetworkPortBox,
    PortOptionList,
    _port_facts,
    box_css,
)

if TYPE_CHECKING:
    from termapy.plugins import CmdResult


class PortPicker(ModalScreen[str | None]):
    """Modal dialog to select an available serial port, or to type a network port.

    Polls ``system_ports()`` once a second so the list reflects USB
    plug / unplug events while the dialog is open -- useful when
    you're trying to identify which COM port belongs to which
    physical device by plugging it in and watching the new row
    appear (or by unplugging and watching one disappear).  A port a
    local ``termapy --serve`` shares is a row too, by its URL.

    Network Port... swaps the list for a field: nothing finds a port on
    another machine, so its ``rfc2217://`` or ``socket://`` URL is typed,
    checked with Test (``test_port``, which the host answers with
    ``/port.test``) and taken with Use.  The dialog dismisses the URL as it
    dismisses a picked device, so whatever applies a pick applies it.

    The dialog is as wide as its table (``PortOptionList.dialog_width``),
    capped at the terminal, where the table drops columns instead of
    wrapping.
    """

    BINDINGS = _DISMISS_BINDINGS

    CSS = f"""
    PortPicker {{ align: center middle; }}
    PortPicker Button {{ {_MODAL_BTN_CSS} }}
    #port-dialog {{
        max-width: 100%; min-width: 60; height: 24;
        border: solid $primary; background: $surface; padding: 1 2;
    }}
    #port-title {{ height: 1; text-style: bold; }}
    #port-list {{ height: 1fr; }}
    {box_css("#port-list")}
    #port-net {{ height: 1fr; }}
    {box_css("#net-url", "tall")}
    #port-network {{ background: slategray; }}
    .port-hidden {{ display: none; }}
    #port-buttons {{ height: 1; align: right middle; }}
    """

    def __init__(self, test_port: Callable[[str], CmdResult] | None = None) -> None:
        """Build the dialog; ``test_port`` answers the network field's Test (none: no Test button)."""
        super().__init__()
        # Cached at compose-time so _poll_ports can diff against the
        # previously-rendered set and skip the re-render when nothing
        # changed (avoids OptionList flicker and selection reset).
        self._ports: list = []
        self._test_port = test_port
        self._network = False

    def action_dismiss_modal(self) -> None:
        """Close the modal on Ctrl+Q or Escape."""
        self.dismiss(None)

    def on_mount(self) -> None:
        self.query_one(
            "#port-list", OptionList
        ).tooltip = "Connected serial ports.  Click a row or press Enter to select."
        self.query_one("#port-cancel", Button).tooltip = "Close without selecting a port."
        self.query_one("#port-use", Button).tooltip = "Use the network port typed above."
        # Live refresh: poll once a second so plug / unplug events
        # appear without the user re-opening the dialog.  Textual
        # auto-cancels set_interval timers on widget unmount, so no
        # teardown to manage.
        self.set_interval(1.0, self._poll_ports)

    def compose(self) -> ComposeResult:
        from textual.widgets import Static

        from termapy.port_control import system_ports

        self._ports = sorted(system_ports(), key=lambda p: p.device)
        port_list = PortOptionList(_port_facts(self._ports), id="port-list")
        with Vertical(id="port-dialog") as dialog:
            dialog.styles.width = port_list.dialog_width()
            yield Static("Select Serial Port", id="port-title")
            yield port_list
            net = NetworkPortBox(self._test_port, id="port-net")
            net.add_class("port-hidden")
            yield net
            with Horizontal(id="port-buttons"):
                network = Button("Network Port...", id="port-network")
                network.tooltip = "Type a port on another machine: rfc2217:// or socket://."
                yield network
                use = Button("Use", id="port-use", variant="success")
                use.add_class("port-hidden")
                yield use
                yield Button("Cancel", id="port-cancel", variant="error")

    def _poll_ports(self) -> None:
        """Re-scan; re-render only if the device set changed.

        Early-returns when port names are unchanged to avoid
        OptionList flicker and to keep the user's highlighted row
        stable when nothing has changed.
        """
        from termapy.port_control import system_ports

        fresh = sorted(system_ports(), key=lambda p: p.device)
        if [p.device for p in fresh] == [p.device for p in self._ports]:
            return
        self._ports = fresh
        self._render_port_list()

    def _render_port_list(self) -> None:
        """Repopulate the list from ``self._ports`` and refit the dialog to the new table."""
        port_list = self.query_one("#port-list", PortOptionList)
        port_list.set_facts(_port_facts(self._ports))
        self.query_one("#port-dialog", Vertical).styles.width = port_list.dialog_width()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(str(event.option.id))

    @on(Button.Pressed, "#port-network")
    def _toggle_network(self) -> None:
        """Swap the port list for the network field, or back."""
        self._network = not self._network
        button = self.query_one("#port-network", Button)
        net = self.query_one("#port-net", NetworkPortBox)
        self.query_one("#port-list").set_class(self._network, "port-hidden")
        net.set_class(not self._network, "port-hidden")
        self.query_one("#port-use").set_class(not self._network, "port-hidden")
        if self._network:
            button.label = "Local Ports"
            button.tooltip = "Back to the ports on this machine."
            net.focus_field()
        else:
            button.label = "Network Port..."
            button.tooltip = "Type a port on another machine: rfc2217:// or socket://."
            self.query_one("#port-list").focus()

    @on(Button.Pressed, "#port-use")
    @on(Input.Submitted, "#net-url")
    def _use_network_port(self) -> None:
        """Dismiss with the typed URL, or say what a network port looks like."""
        url = self.query_one("#port-net", NetworkPortBox).checked_url()
        if url is not None:
            self.dismiss(url)

    @on(Button.Pressed, "#port-cancel")
    def cancel_port_picker(self) -> None:
        self.dismiss(None)
