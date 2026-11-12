"""Modal dialog: PortPicker -- pick a serial port from a list that follows USB plug and unplug.

Extracted from the original monolithic ``dialogs.py``.  See
``termapy.dialogs.__init__`` for the package-level public API and
the ``_common`` submodule for shared constants and helpers.
"""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, OptionList

from termapy.dialogs._common import _DISMISS_BINDINGS, _MODAL_BTN_CSS, PortOptionList, _port_facts


class PortPicker(ModalScreen[str | None]):
    """Modal dialog to select an available serial port.

    Polls ``system_ports()`` once a second so the list reflects USB
    plug / unplug events while the dialog is open -- useful when
    you're trying to identify which COM port belongs to which
    physical device by plugging it in and watching the new row
    appear (or by unplugging and watching one disappear).

    The dialog is as wide as its table (``PortOptionList.dialog_width``),
    capped at the terminal, where the table drops columns instead of
    wrapping.
    """

    BINDINGS = _DISMISS_BINDINGS

    CSS = f"""
    PortPicker {{ align: center middle; }}
    PortPicker Button {{ {_MODAL_BTN_CSS} }}
    #port-dialog {{
        max-width: 100%; height: 24;
        border: solid $primary; background: $surface; padding: 1 2;
    }}
    #port-title {{ height: 1; text-style: bold; }}
    #port-list {{ height: 1fr; border: thick $primary; }}
    #port-buttons {{ height: 1; align: right middle; }}
    """

    def __init__(self) -> None:
        super().__init__()
        # Cached at compose-time so _poll_ports can diff against the
        # previously-rendered set and skip the re-render when nothing
        # changed (avoids OptionList flicker and selection reset).
        self._ports: list = []

    def action_dismiss_modal(self) -> None:
        """Close the modal on Ctrl+Q or Escape."""
        self.dismiss(None)

    def on_mount(self) -> None:
        self.query_one(
            "#port-list", OptionList
        ).tooltip = "Connected serial ports.  Click a row or press Enter to select."
        self.query_one("#port-cancel", Button).tooltip = "Close without selecting a port."
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
            with Horizontal(id="port-buttons"):
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

    @on(Button.Pressed, "#port-cancel")
    def cancel_port_picker(self) -> None:
        self.dismiss(None)
