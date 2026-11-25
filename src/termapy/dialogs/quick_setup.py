"""Modal dialog: QuickSetup -- the first-run / new-config wizard: name, port and baud rate on one screen.

Extracted from the original monolithic ``dialogs.py``.  See
``termapy.dialogs.__init__`` for the package-level public API and
the ``_common`` submodule for shared constants and helpers.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from textual import events, on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, OptionList
from textual.widgets.option_list import Option

from termapy.dialogs._common import (
    _DISMISS_BINDINGS,
    _MODAL_BTN_CSS,
    NetworkPortBox,
    _populate_port_option_list,
    box_css,
)
from termapy.widgets import StrongCheckbox

if TYPE_CHECKING:
    from termapy.plugins import CmdResult


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
    #qs-standard-baud {{ background: slategray; }}
    #qs-network {{ background: slategray; }}
    {box_css("#net-url", "tall")}
    #qs-port-list {{ height: 10; }}
    {box_css("#qs-name", "tall")}
    {box_css("#qs-port-list", "tall")}
    #qs-baud-list {{ height: 6; }}
    {box_css("#qs-baud-list", "tall")}
    {box_css("#qs-baud-input", "tall")}
    .qs-hidden {{ display: none; }}
    #qs-buttons {{ height: 1; margin-top: 1; align: right middle; }}
    .qs-label {{ height: 1; margin-top: 1; padding-left: 1; text-style: bold; }}
    .qs-first {{ margin-top: 0; }}
    """

    _COMMON_BAUDS = [
        300,
        600,
        1200,
        2400,
        4800,
        9600,
        19200,
        38400,
        57600,
        115200,
        230400,
        460800,
        921600,
        1500000,
        2000000,
        3000000,
    ]

    # The rows every port list starts with: a header and a separator,
    # both disabled.  The first port a user can pick is the one after
    # them, which is also what ``PortOptionList`` highlights.
    _FIRST_PORT_ROW = 2

    def __init__(
        self, title: str = "New Config", test_port: Callable[[str], CmdResult] | None = None
    ) -> None:
        """Build the dialog; ``test_port`` answers the network field's Test (none: no Test button)."""
        super().__init__()
        self._title = title
        self._test_port = test_port
        self._network = False
        # Cached at compose-time so on_mount can re-render the port
        # list against the dialog's measured cell width.  Cheap to
        # call once; expensive to scan every resize.
        self._ports: list = []

    def _has_a_port_to_pick(self, port_list: OptionList) -> bool:
        """Whether the list offers a row the user can actually choose.

        Counts the LIST, not the hardware.  ``_populate_port_option_list``
        always appends the ``loop://`` loopback, which is a real,
        selectable port and the one thing to connect to on a machine with
        no adapter attached -- so with zero hardware ports the list still
        holds one pickable row.  Asking ``if self._ports`` instead left
        that row on screen, unhighlighted, beside a disabled "No Ports"
        button: the list said pick this and the button said there is
        nothing to pick (found 2026-10-28).  ``PortOptionList._fill``
        has always counted the list this way.
        """
        return port_list.option_count > self._FIRST_PORT_ROW

    def action_dismiss_modal(self) -> None:
        self.dismiss(None)

    def compose(self) -> ComposeResult:
        from textual.widgets import Static

        from termapy.port_control import system_ports

        self._ports = sorted(system_ports(), key=lambda p: p.device)
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
                port_list,
                self._ports,
                row_width=120,
            )
            if self._has_a_port_to_pick(port_list):
                port_list.highlighted = self._FIRST_PORT_ROW
            yield port_list
            net = NetworkPortBox(self._test_port, id="qs-net")
            net.add_class("qs-hidden")
            yield net
            # Under the box it swaps, as Custom Rate... is.
            network = Button("Network Port...", id="qs-network")
            network.tooltip = "Type a port on another machine: rfc2217:// or socket://."
            yield network
            yield Static("Baud Rate:", classes="qs-label")
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
            # Under the box it swaps, not where the heading goes: a button
            # in a heading's place reads as the heading.
            baud_mode = Button("Custom Rate...", id="qs-standard-baud")
            baud_mode.tooltip = "Type a baud rate the list does not have."
            yield baud_mode
            # Optional add-icon checkbox; off by default so existing
            # users who just want a cfg aren't surprised.  Targets
            # the non-CLI audience who's setting termapy up the first
            # time and would benefit from a double-clickable launcher.
            yield StrongCheckbox(
                "Add a desktop / menu launcher for this config",
                id="qs-add-icon",
                value=False,
            )
            with Horizontal(id="qs-buttons"):
                connect_btn = Button("Connect", id="qs-connect", variant="success")
                # ``port_list`` is the one built just above, so it can be
                # asked directly; after mount the same question goes
                # through _set_connect_state.
                if not self._has_a_port_to_pick(port_list):
                    connect_btn.label = "No Ports"
                    connect_btn.variant = "error"
                    connect_btn.disabled = True
                yield connect_btn
                adv = Button("Advanced", id="qs-advanced")
                adv.styles.background = "darkorchid"
                yield adv
                yield Button("Cancel", id="qs-cancel", variant="error")

    def on_mount(self) -> None:
        """Set tooltips and defer the real port-list render.

        At on_mount time the OptionList hasn't yet been positioned,
        so ``size`` / ``content_size`` are 0 (or stale).  By the
        time ``call_after_refresh`` fires, Textual has done a full
        layout pass and ``content_size.width`` is the actual cell
        budget the option text gets to live in.

        Tooltip on ``qs-standard-baud`` is already set inline in
        compose() because it flips dynamically when the user toggles
        between standard/custom baud entry; we don't touch it here.
        """
        self.query_one(
            "#qs-name", Input
        ).tooltip = "Config name.  Becomes the folder name under termapy_cfg/."
        self.query_one(
            "#qs-port-list", OptionList
        ).tooltip = "Connected serial ports.  Click a row to select."
        self.query_one("#qs-baud-list", OptionList).tooltip = "Pick a standard baud rate."
        self.query_one("#qs-baud-input", Input).tooltip = "Type a custom baud rate (>= 300)."
        self.query_one(
            "#qs-add-icon", StrongCheckbox
        ).tooltip = "Also create a desktop / menu launcher for this config."
        self.query_one("#qs-connect", Button).tooltip = "Save the config and connect immediately."
        self.query_one(
            "#qs-advanced", Button
        ).tooltip = "Save the config and open the full Config Editor."
        self.query_one("#qs-cancel", Button).tooltip = "Close without creating a config."
        self.call_after_refresh(self._render_port_list)
        # Poll comports once a second so the dialog reflects USB
        # plug / unplug events without the user re-opening it.
        # set_interval returns a Timer that auto-cancels when the
        # widget unmounts, so there is no teardown to manage.
        self.set_interval(1.0, self._poll_ports)

    def _poll_ports(self) -> None:
        """Re-scan USB serial ports; re-render the list if it changed.

        Early-returns when the set of device names is unchanged to
        avoid OptionList flicker and to keep the user's current
        selection stable while they're mid-edit.  When the set HAS
        changed, also sync the Connect button between its
        "Connect" / "No Ports" states so the button label tells the
        truth about whether clicking it will do anything.
        """
        from termapy.port_control import system_ports

        fresh = sorted(system_ports(), key=lambda p: p.device)
        if [p.device for p in fresh] == [p.device for p in self._ports]:
            return
        self._ports = fresh
        self._render_port_list()
        self._set_connect_state()

    def _set_connect_state(self) -> None:
        """Make the Connect button agree with what the list is offering."""
        port_list = self.query_one("#qs-port-list", OptionList)
        btn = self.query_one("#qs-connect", Button)
        if self._has_a_port_to_pick(port_list):
            btn.label = "Connect"
            btn.variant = "success"
            btn.disabled = False
        else:
            btn.label = "No Ports"
            btn.variant = "error"
            btn.disabled = True

    def on_resize(self, _event: events.Resize) -> None:
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
        if self._has_a_port_to_pick(port_list):
            port_list.highlighted = self._FIRST_PORT_ROW

    @on(Button.Pressed, "#qs-network")
    def _toggle_network(self) -> None:
        """Swap the port list for the network field, or back."""
        self._network = not self._network
        button = self.query_one("#qs-network", Button)
        net = self.query_one("#qs-net", NetworkPortBox)
        self.query_one("#qs-port-list").set_class(self._network, "qs-hidden")
        net.set_class(not self._network, "qs-hidden")
        if self._network:
            button.label = "Local Ports"
            button.tooltip = "Back to the ports on this machine."
            net.focus_field()
        else:
            button.label = "Network Port..."
            button.tooltip = "Type a port on another machine: rfc2217:// or socket://."

    _standard_baud: bool = True

    @on(Button.Pressed, "#qs-standard-baud")
    def _toggle_standard_baud(self) -> None:
        self._standard_baud = not self._standard_baud
        btn = self.query_one("#qs-standard-baud", Button)
        baud_list = self.query_one("#qs-baud-list", OptionList)
        baud_input = self.query_one("#qs-baud-input", Input)
        if self._standard_baud:
            btn.label = "Custom Rate..."
            btn.tooltip = "Type a baud rate the list does not have."
            baud_list.remove_class("qs-hidden")
            baud_input.add_class("qs-hidden")
        else:
            btn.label = "Standard Rates"
            btn.tooltip = "Pick from the standard baud rates."
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

    def _submit(self, action: str) -> None:
        """Dismiss with ``(action, name, port, baud, custom_baud, add_icon)``, or say what is missing.

        Args:
            action: ``connect`` (the Connect button, Enter in the name) or
                ``advanced`` (the Advanced button).
        """
        name_input = self.query_one("#qs-name", Input)
        name = name_input.value.strip()
        if not name:
            # The cursor goes where the answer is typed, not where the
            # click that asked for it landed.
            name_input.focus()
            self.notify("Enter a config name", severity="warning", timeout=2)
            return
        name = Path(name).stem

        if self._network:
            url = self.query_one("#qs-net", NetworkPortBox).checked_url()
            if url is None:
                return
            port = url
        else:
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
        add_icon = self.query_one("#qs-add-icon", StrongCheckbox).value

        self.dismiss((action, name, port, baud, custom_baud, add_icon))

    @on(Button.Pressed, "#qs-connect")
    def connect(self) -> None:
        self._submit("connect")

    @on(Input.Submitted, "#qs-name")
    def submit_name(self) -> None:
        self._submit("connect")

    @on(Button.Pressed, "#qs-advanced")
    def advanced(self) -> None:
        self._submit("advanced")

    @on(OptionList.OptionSelected, "#qs-port-list, #qs-baud-list")
    def _picked(self) -> None:
        """A port or baud picked with no name yet: put the cursor in the name.

        Picking a row (a click, Enter) is a choice made; the dialog's one
        typed answer is still owed.  Arrow keys only highlight, so a
        keyboard user still moves through the list.
        """
        name_input = self.query_one("#qs-name", Input)
        if not name_input.value.strip():
            name_input.focus()

    @on(Button.Pressed, "#qs-cancel")
    def cancel(self) -> None:
        self.dismiss(None)
