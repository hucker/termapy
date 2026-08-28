"""Shared constants and helpers for the dialog submodules.

Underscore prefix marks this as package-private; consumers go through
``termapy.dialogs`` (the package public API).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from rich.text import Text
from textual.widgets import OptionList
from textual.widgets.option_list import Option

from termapy.folder_ops import file_columns

# Shared CSS for modal dialog buttons.
_MODAL_BTN_CSS = """
    min-width: 0; width: auto; height: 1; min-height: 1;
    border: none; margin: 0 0 0 1;
"""

# Width of the three file pickers (script / proto / config).  Wide enough
# for ``name  size  age`` plus a trailing detail column that, for configs,
# holds a macOS-length port (``/dev/cu.usbserial-A50285BI @ 115200``) AND
# the config's title; ``max-width`` in the picker CSS keeps it inside a
# narrow terminal, where the detail column simply truncates harder.
_FILE_PICKER_WIDTH = 104
# Columns a row may use inside that dialog: border 2 + padding 4 + list's
# thick border 2 + a scrollbar 2.
_FILE_PICKER_ROW_WIDTH = _FILE_PICKER_WIDTH - 10
_ELLIPSIS = "..."


def _populate_file_option_list(
    ol: OptionList,
    files: list[Path],
    *,
    detail: Callable[[Path], str] | None = None,
    label: Callable[[Path, str], str] | None = None,
    row_width: int = _FILE_PICKER_ROW_WIDTH,
) -> None:
    """Fill an OptionList with one ``name  size  age  detail`` row per file.

    Shared by the script, proto, and config pickers so a file looks the
    same wherever it is picked.  ``files`` should already be in display
    order (the pickers pass ``folder_ops.list_entries``, newest first).
    The name is plain; size, age, and the optional detail are ``dim`` so
    the eye lands on the name and reads the rest on demand.  A detail
    that would overflow ``row_width`` is truncated with an ellipsis.

    Args:
        ol: The list to fill.
        files: Files in display order.
        detail: Optional per-file trailing text (a docstring summary, a
            config's port).  Called once per file.
        label: Optional ``(path, padded_name) -> str`` override for the
            name column (the config picker shows the stem).
    """
    for path, row in zip(files, file_columns(files), strict=True):
        name = label(path, row.name) if label else row.name
        text = Text(name)
        text.append(f"  {row.size}  {row.age}", style="dim")
        extra = detail(path) if detail else ""
        if extra:
            room = row_width - text.cell_len - 2
            if room > len(_ELLIPSIS):
                if len(extra) > room:
                    extra = extra[: room - len(_ELLIPSIS)] + _ELLIPSIS
                text.append(f"  {extra}", style="dim")
        ol.add_option(Option(text, id=str(path)))

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
    # enrich=True: the picker DOES show location and driver; those come from
    # sysfs / the registry and open nothing, for single-digit ms per port.
    facts_list = [
        port_control.gather_chip_facts(port.device, fast=True, enrich=True)
        for port in ports
    ]
    facts_list = [fact for fact in facts_list if fact is not None]
    # Always offer the pyserial loopback as a selectable virtual port,
    # appended below real hardware.  It echoes writes back -- handy for
    # testing without a device.  Honest facts (no invented VID/PID/SN).
    facts_list.append(port_control.loopback_port_facts())
    rows = [row_from_facts(fact) for fact in facts_list]
    columns = active_columns(rows)
    widths, columns = compute_widths(rows, row_width, columns)
    header, separator = format_header(widths, columns)
    ol.add_option(Option(header, disabled=True))
    ol.add_option(Option(separator, disabled=True))
    for port_id, row_data in rows:
        ol.add_option(
            Option(format_row(row_data, widths, columns), id=port_id),
        )
