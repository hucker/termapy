"""Shared constants and helpers for the dialog submodules.

Underscore prefix marks this as package-private; consumers go through
``termapy.dialogs`` (the package public API).
"""

from __future__ import annotations

from textual.widgets import OptionList
from textual.widgets.option_list import Option


# Shared CSS for modal dialog buttons.
_MODAL_BTN_CSS = """
    min-width: 0; width: auto; height: 1; min-height: 1;
    border: none; margin: 0 0 0 1;
"""

# Dismiss bindings shared by all modal dialogs.
_DISMISS_BINDINGS = [
    ("ctrl+q", "dismiss_modal", "Close"),
    ("escape", "dismiss_modal", "Close"),
]


def _populate_port_option_list(
    ol: OptionList, ports: list, row_width: int,
) -> None:
    """Fill an OptionList with a header, separator, and one row per port.

    Called by both PortPicker and QuickSetup so their port lists look
    identical.  ``row_width`` is the usable column budget for the list,
    which differs between dialogs based on their CSS width.

    Formatting lives in ``termapy.port_format`` so the CLI
    (``--ports`` / ``--watch``) can reuse it without pulling Textual
    into its code path.  This wrapper just maps formatted lines to
    ``OptionList`` entries, attaching each data row's port name as
    the option id so selection returns the device string.
    """
    from termapy import port_control
    from termapy.port_format import (
        active_columns,
        compute_widths,
        format_header,
        format_row,
        row_from_facts,
    )

    # fast=True: the picker table has no in_use column, and this runs on
    # the Textual main thread -- probing here would both freeze the UI and
    # (on Windows) open every listed port, pulsing DTR on auto-reset boards.
    facts_list = [port_control.gather_chip_facts(p.device, fast=True) for p in ports]
    facts_list = [f for f in facts_list if f is not None]
    # Always offer the pyserial loopback as a selectable virtual port,
    # appended below real hardware.  It echoes writes back -- handy for
    # testing without a device.  Honest facts (no invented VID/PID/SN).
    facts_list.append(port_control.loopback_port_facts())
    rows = [row_from_facts(f) for f in facts_list]
    columns = active_columns(rows)
    widths, columns = compute_widths(rows, row_width, columns)
    header, separator = format_header(widths, columns)
    ol.add_option(Option(header, disabled=True))
    ol.add_option(Option(separator, disabled=True))
    for port_id, row_data in rows:
        ol.add_option(
            Option(format_row(row_data, widths, columns), id=port_id),
        )
