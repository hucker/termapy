"""One format for every ``.list`` command: title, header, dashes, rows.

A list command builds RECORDS -- plain dicts of raw values (byte counts,
ages in seconds, booleans), which are exactly the ``CmdResult.data`` an
agent, ``--json`` or ``/term.request`` gets -- and declares the columns
that show them.  ``emit_listing`` renders the prose from those same
records, or skips the prose when the caller wants data, so the table and
the JSON cannot disagree and every listing reads the same way::

    run/ - 7 scripts
    NAME          SIZE    UPDATED    SUMMARY
    ------------  ------  ---------  ------------------
    crc_tour.run  2.3 KB  3 min ago  the /proto.crc API

Registry ``list-one-format``.  Pure apart from ``emit_listing``'s writes;
no Textual, no pyserial.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Sequence

from termapy.plugins import CmdResult

if TYPE_CHECKING:
    from termapy.plugins import PluginContext

Record = dict[str, Any]


@dataclass(frozen=True)
class Column:
    """One column of a listing.

    Attributes:
        title: Header text (``NAME``, ``SIZE``).
        cell: The cell text for one record -- where a raw value becomes
            display text (``format_size`` of ``bytes``, say).
        right: Right-align the cells (numbers, sizes).  The header stays
            left-aligned at the column's left edge on every column: one
            rule reads as a header row.
    """

    title: str
    cell: Callable[[Record], str]
    right: bool = False


def counted(count: int, noun: str) -> str:
    """``"1 script"`` / ``"7 scripts"`` -- naive plural, as ``folder_line`` does."""
    return f"{count} {noun}{'' if count == 1 else 's'}"


def render_listing(
    title: str, noun: str, columns: Sequence[Column], records: Sequence[Record],
) -> list[tuple[str, str]]:
    """The listing as ``(text, color)`` lines: title, header, dashes, rows.

    Lines carry no indent of their own (the frontend indents termapy's
    output); columns are two spaces apart and as wide as their widest
    cell or title; trailing padding is stripped.  An empty listing is the
    title line alone, ``<title> - empty``.

    Args:
        title: What is listed (``run/``, ``Serial ports``).
        noun: Singular noun the count is in (``script``).
        columns: The columns, left to right.
        records: One record per row, in display order.

    Returns:
        ``(text, color)`` pairs; ``""`` is the frontend's plain color.
    """
    if not records:
        return [(f"{title} - empty", "")]
    cells = [[column.cell(record) for column in columns] for record in records]
    widths = [
        max(len(column.title), *(len(row[index]) for row in cells))
        for index, column in enumerate(columns)
    ]

    def line(parts: list[str]) -> str:
        return "  ".join(parts).rstrip()

    lines = [(f"{title} - {counted(len(records), noun)}", "")]
    lines.append((line([f"{column.title:<{width}}" for column, width in zip(columns, widths)]), "dim"))
    lines.append((line(["-" * width for width in widths]), "dim"))
    for row in cells:
        lines.append((line([
            f"{cell:>{width}}" if column.right else f"{cell:<{width}}"
            for column, cell, width in zip(columns, row, widths)
        ]), ""))
    return lines


def emit_listing(
    ctx: PluginContext,
    title: str,
    noun: str,
    columns: Sequence[Column],
    records: Sequence[Record],
    *,
    value: str,
) -> CmdResult:
    """Answer a list command: the prose table, or the records as data.

    A caller that wants data (``--json``, ``/term.request``, MCP) gets the
    records and no prose -- building both would double the payload.

    Args:
        ctx: The plugin context.
        title: What is listed (``run/``).
        noun: Singular noun the count is in (``script``).
        columns: The columns the prose shows.
        records: The rows, as raw-valued dicts.
        value: The scriptable scalar (usually the newline-joined names).
    """
    if ctx.wants_data:
        return CmdResult.ok(value=value, data=list(records))
    for text, color in render_listing(title, noun, columns, records):
        ctx.io.output(text, color)
    return CmdResult.ok(value=value)
