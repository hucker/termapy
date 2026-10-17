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

A status page (``/term.info``, ``/mcp.info``) is the same idea for ONE
record: the handler returns ``CmdResult.record(...)``, the record with
real JSON types is ``data``, and a ``RecordView`` carries the aligned
label/value rows a person reads::

    pid             : 17140
    port            : DEMO (connected)

Registry ``record-one-format``: this module is the only caller of
``format_kv_lines``, so every status page reads the same way and none
prints its rows into a JSON answer.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from termapy.markup import escape_markup
from termapy.plugins.output_levels import format_kv_lines

Record = dict[str, Any]
# One display row: (label, text) or (label, text, color).
KvRow = tuple[str, str] | tuple[str, str, str]
# One plain line around the rows: (text, color); "" is the plain color.
Line = tuple[str, str]


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


@dataclass(frozen=True)
class TextLine:
    """A plain line among a record page's rows: a heading, a note, a hint.

    Attributes:
        text: The line's text (escaped when rendered); ``""`` is a blank line.
        color: Its color, ``""`` for plain.
    """

    text: str
    color: str = ""


# One item of a record page: a row, a plain line, or ``None`` for a blank
# line.  Consecutive rows align as one group.
Item = KvRow | TextLine | None


@dataclass(frozen=True)
class RecordView:
    """The prose form of a record answer: aligned label/value rows.

    Attributes:
        items: The page top to bottom.  Each run of consecutive rows is
            aligned on its own (``/port.info``'s settings, chip and signal
            groups), a ``TextLine`` is a plain line (``/port.chip``'s
            device heading), and ``None`` is a blank line.
    """

    items: tuple[Item, ...]

    def lines(self) -> list[str]:
        """The page as markup lines, every value escaped (registry ``markup-escape``)."""
        lines: list[str] = []
        group: list[KvRow] = []

        def flush() -> None:
            lines.extend(format_kv_lines(group))
            group.clear()

        for item in self.items:
            if isinstance(item, tuple):
                group.append(item)
                continue
            flush()
            if item is None or not item.text:
                lines.append("")
            else:
                body_markup = escape_markup(item.text)
                lines.append(f"  [{item.color}]{body_markup}[/]" if item.color else f"  {body_markup}")
        flush()
        return lines
