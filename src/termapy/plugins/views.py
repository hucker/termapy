"""Views: how a typed answer looks in prose, kept apart from the answer itself.

A handler that answers with a table returns ``CmdResult.table(...)``: the
records (raw values -- byte counts, ages in seconds, booleans) become
``data``, and a ``TableView`` describing their prose form rides along in
``CmdResult.view``.  The handler renders nothing.  Dispatch's single exit
(``ReplEngine.dispatch``'s ``_finish``) decides: a caller that wants data
(``--json``, ``/term.request``, MCP) gets the records; everyone else gets
the view's lines on the output channel.  The prose and the JSON come from
the same records, every table reads the same way, and a view never leaves
dispatch::

    run/ - 7 scripts
    NAME          SIZE    UPDATED    SUMMARY
    ------------  ------  ---------  ------------------
    crc_tour.run  2.3 KB  3 min ago  the /proto.crc API

Registry ``list-one-format``.  Pure: no I/O, no Textual, no pyserial.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

Record = dict[str, Any]


@dataclass(frozen=True)
class Column:
    """One column of a table.

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


@dataclass(frozen=True)
class TableView:
    """The prose form of a list answer: title with count, header, dashes, rows.

    Attributes:
        title: What is listed (``run/``, ``Serial ports``).
        noun: Singular noun the count is in (``script``).
        columns: The columns, left to right.
        records: One record per row, in display order -- the same
            records the result carries as ``data``.
    """

    title: str
    noun: str
    columns: tuple[Column, ...]
    records: tuple[Record, ...]

    def lines(self) -> list[tuple[str, str]]:
        """The table as ``(text, color)`` lines; ``""`` is the plain color.

        Lines carry no indent of their own (the frontend indents termapy's
        output); columns are two spaces apart and as wide as their widest
        cell or title; trailing padding is stripped.  The header and the
        dash row are dim.  An empty table is its title alone,
        ``<title> - empty``.
        """
        if not self.records:
            return [(f"{self.title} - empty", "")]
        cells = [[column.cell(record) for column in self.columns] for record in self.records]
        widths = [
            max(len(column.title), *(len(row[index]) for row in cells))
            for index, column in enumerate(self.columns)
        ]

        def line(parts: list[str]) -> str:
            return "  ".join(parts).rstrip()

        lines = [(f"{self.title} - {counted(len(self.records), self.noun)}", "")]
        lines.append((line([f"{column.title:<{width}}" for column, width in zip(self.columns, widths)]), "dim"))
        lines.append((line(["-" * width for width in widths]), "dim"))
        for row in cells:
            lines.append((line([
                f"{cell:>{width}}" if column.right else f"{cell:<{width}}"
                for column, cell, width in zip(self.columns, row, widths)
            ]), ""))
        return lines
