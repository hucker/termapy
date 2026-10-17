"""Shared constants and helpers for the dialog submodules.

Underscore prefix marks this as package-private; consumers go through
``termapy.dialogs`` (the package public API).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from rich.text import Text
from textual import events
from textual.widgets import OptionList
from textual.widgets.option_list import Option

from termapy.folder_ops import file_columns, format_file_header

# Shared CSS for modal dialog buttons.
_MODAL_BTN_CSS = """
    min-width: 0; width: auto; height: 1; min-height: 1;
    border: none; margin: 0 0 0 1;
"""

# Narrowest width of the three file pickers (script / proto / config).
# Wide enough for ``name  size  age`` plus a trailing detail column that,
# for configs, holds a macOS-length port (``/dev/cu.usbserial-A50285BI @
# 115200``) AND the config's title.  A picker grows past it to show its
# widest row whole (FileOptionList.dialog_width); ``max-width`` in the
# picker CSS keeps it inside the terminal, where the rows truncate instead.
_FILE_PICKER_WIDTH = 104
# Columns the dialog spends around a row: its border 2 + padding 4 + the
# list's thick border 2 + OptionList's own ``padding: 0 1`` 2 + a scrollbar 2.
# (Until 2026-10-13 this was 10, without the list padding, so a row built to
# fill the dialog wrapped even in a wide terminal.)
_FILE_PICKER_CHROME = 12
# What a row may use before layout has measured the list.
_FILE_PICKER_ROW_WIDTH = _FILE_PICKER_WIDTH - _FILE_PICKER_CHROME
_ELLIPSIS = "..."


class FileOptionList(OptionList):
    """A file picker's list: a header and one ``name  size  age  detail`` row per file.

    Shared by the script, proto, and config pickers so a file looks the
    same wherever it is picked.  ``files`` should already be in display
    order (the pickers pass ``folder_ops.list_entries``, newest first).
    The name is plain; size, age, and the optional detail are ``dim`` so
    the eye lands on the name and reads the rest on demand.

    Fitting is two steps.  A picker sizes its dialog to ``dialog_width()``,
    wide enough to show every row whole, so a long summary widens the
    dialog rather than being cut.  The CSS caps the dialog at the terminal
    (``max-width: 100%``), so in a narrower terminal the room a row has is
    known only after layout: every resize rebuilds the rows to the measured
    width, truncating the detail with an ellipsis (dropping it when not
    even that fits), and keeps the highlighted row.

    OptionList has no header concept, so the header is a DISABLED first
    option (the port picker does the same): it renders, the cursor skips
    it, and it can never be selected.  Its cells use the same widths as
    the rows.  No header when there are no files.
    """

    def __init__(
        self,
        files: list[Path],
        *,
        detail: Callable[[Path], str] | None = None,
        detail_header: str = "",
        label: Callable[[Path, str], str] | None = None,
        id: str | None = None,
    ) -> None:
        """Build the list and fill it at the pre-layout width.

        Args:
            files: Files in display order.
            detail: Optional per-file trailing text (a docstring summary,
                a config's port).  Called once per file, here, not on
                every resize.
            detail_header: Header text for the detail column.
            label: Optional ``(path, padded_name) -> str`` override for the
                name column (the config picker shows the stem).
            id: Widget id.
        """
        super().__init__(id=id)
        rows = file_columns(files)
        self._header = format_file_header(rows, detail_header) if rows else ""
        # (path, name and size/age cells, detail), computed once: a resize
        # only re-truncates the detail.
        self._rows = [
            (
                path,
                Text.assemble(
                    label(path, row.name) if label else row.name,
                    (f"  {row.size}  {row.age}", "dim"),
                ),
                detail(path) if detail else "",
            )
            for path, row in zip(files, rows, strict=True)
        ]
        self._row_width = _FILE_PICKER_ROW_WIDTH
        #: Index of the first FILE option (past the header) -- what a picker
        #: highlights, offset by any index it computed over ``files``.
        self.first_file = 1 if files else 0
        self._fill()

    def dialog_width(self) -> int:
        """The picker width that shows every row whole, never below ``_FILE_PICKER_WIDTH``.

        The picker's CSS ``max-width`` caps it at the terminal; past that
        point the rows truncate on resize.
        """
        widest = max(
            [len(self._header)]
            + [base.cell_len + (len(extra) + 2 if extra else 0) for _, base, extra in self._rows]
        )
        return max(_FILE_PICKER_WIDTH, widest + _FILE_PICKER_CHROME)

    def _fill(self) -> None:
        """Add the header and the file rows at ``self._row_width``."""
        if not self._rows:
            return
        self.add_option(Option(Text(self._header, style="bold"), disabled=True))
        for path, base, extra in self._rows:
            text = base.copy()
            room = self._row_width - text.cell_len - 2
            if extra and room > len(_ELLIPSIS):
                if len(extra) > room:
                    extra = extra[: room - len(_ELLIPSIS)] + _ELLIPSIS
                text.append(f"  {extra}", style="dim")
            self.add_option(Option(text, id=str(path)))

    def on_resize(self, _event: events.Resize) -> None:
        """Rebuild the rows to the width layout gave the list."""
        # content_size includes the vertical scrollbar's gutter; reserve it
        # whether or not the scrollbar shows, so the row count growing past
        # the height never tips a row into wrapping.
        width = self.content_size.width - self.styles.scrollbar_size_vertical
        if width <= 0 or width == self._row_width:
            return
        self._row_width = width
        highlighted = self.highlighted
        self.clear_options()
        self._fill()
        self.highlighted = highlighted


def _highlighted_file(ol: OptionList) -> str | None:
    """The path id of the highlighted option, or ``None``.

    ``None`` covers "list is empty", "nothing highlighted", and the
    disabled header row (which has no id) -- callers treat all three as
    "no-op, don't dismiss".
    """
    if ol.highlighted is None:
        return None
    option_id = ol.get_option_at_index(ol.highlighted).id
    return str(option_id) if option_id is not None else None


# Dismiss bindings shared by all modal dialogs.
_DISMISS_BINDINGS = [
    ("ctrl+q", "dismiss_modal", "Close"),
    ("escape", "dismiss_modal", "Close"),
]


def _populate_port_option_list(
    ol: OptionList,
    ports: list,
    row_width: int,
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
        port_control.gather_chip_facts(port.device, fast=True, enrich=True) for port in ports
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
