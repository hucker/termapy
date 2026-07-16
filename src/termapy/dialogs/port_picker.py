"""Modal dialog: PortPicker.

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

from termapy.dialogs._common import _DISMISS_BINDINGS, _MODAL_BTN_CSS, _populate_port_option_list


class PortPicker(ModalScreen[str | None]):
    """Modal dialog to select an available serial port.

    Polls ``comports()`` once a second so the list reflects USB
    plug / unplug events while the dialog is open -- useful when
    you're trying to identify which COM port belongs to which
    physical device by plugging it in and watching the new row
    appear (or by unplugging and watching one disappear).
    """

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
        self.query_one("#port-list", OptionList).tooltip = (
            "Connected serial ports.  Click a row or press Enter to select."
        )
        self.query_one("#port-cancel", Button).tooltip = (
            "Close without selecting a port."
        )
        # Live refresh: poll once a second so plug / unplug events
        # appear without the user re-opening the dialog.  Textual
        # auto-cancels set_interval timers on widget unmount, so no
        # teardown to manage.
        self.set_interval(1.0, self._poll_ports)

    def compose(self) -> ComposeResult:
        from serial.tools.list_ports import comports
        from textual.widgets import Static

        self._ports = sorted(comports(), key=lambda p: p.device)
        with Vertical(id="port-dialog"):
            yield Static("Select Serial Port", id="port-title")
            ol = OptionList(id="port-list")
            _populate_port_option_list(ol, self._ports, self._ROW_WIDTH)
            yield ol
            with Horizontal(id="port-buttons"):
                yield Button("Cancel", id="port-cancel", variant="error")

    def _poll_ports(self) -> None:
        """Re-scan; re-render only if the device set changed.

        Early-returns when port names are unchanged to avoid
        OptionList flicker and to keep the user's highlighted row
        stable when nothing has changed.
        """
        from serial.tools.list_ports import comports
        fresh = sorted(comports(), key=lambda p: p.device)
        if [p.device for p in fresh] == [p.device for p in self._ports]:
            return
        self._ports = fresh
        self._render_port_list()

    def _render_port_list(self) -> None:
        """Repopulate the OptionList, preserving the highlighted port by id."""
        port_list = self.query_one("#port-list", OptionList)
        selected_id: str | None = None
        if port_list.highlighted is not None:
            opt = port_list.get_option_at_index(port_list.highlighted)
            if opt is not None and opt.id is not None and not opt.disabled:
                selected_id = str(opt.id)
        port_list.clear_options()
        _populate_port_option_list(port_list, self._ports, self._ROW_WIDTH)
        if selected_id is not None:
            for i in range(port_list.option_count):
                opt = port_list.get_option_at_index(i)
                if opt is not None and opt.id == selected_id:
                    port_list.highlighted = i
                    return
        if port_list.option_count > 2:
            # highlight the first data row (real port, or the loopback row
            # which is always present) -- skip the header + separator.
            port_list.highlighted = 2

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(str(event.option.id))

    @on(Button.Pressed, "#port-cancel")
    def cancel_port_picker(self) -> None:
        self.dismiss(None)
