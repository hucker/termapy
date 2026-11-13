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

A column can also say what clicking its cell does (``run=`` / ``prefill=``,
a :class:`Link`).  The link is data on the view, never markup in the text:
:meth:`TableView.segment_lines` hands a frontend that can click (the TUI)
each line as pieces with their links, and :meth:`TableView.lines` joins the
same pieces, so the CLI and MCP print exactly the bytes they always did and
nothing has to be stripped.  Registry ``links-are-data``.

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

import re
from collections.abc import Callable, Container, Iterable
from dataclasses import dataclass
from typing import Any, NamedTuple

from termapy.markup import escape_markup
from termapy.matching import DID_YOU_MEAN, edit_distance, split_did_you_mean
from termapy.plugins.output_levels import format_kv_lines

Record = dict[str, Any]
# One display row: (label, text) or (label, text, color).
KvRow = tuple[str, str] | tuple[str, str, str]
# One plain line around the rows: (text, color); "" is the plain color.
Line = tuple[str, str]


# The color an error line prints in, everywhere one does: dispatch's
# ``Error: ...`` lines and a help page's example of one.
ERROR_COLOR = "red"


@dataclass(frozen=True)
class Link:
    """What clicking a piece of output does, in a frontend that can click.

    Attributes:
        command: The REPL command without its prefix (``cfg.load demo``);
            the frontend adds the configured prefix when it submits it.
        run: True runs it as if typed, echoed and into history.  False
            puts it in the input line to finish and press Enter -- for a
            command that needs more, or one that changes something.
        tip: More for the tooltip, after the definition of whatever ``command``
            names (``ReplEngine.link_summary``) and before the line saying what
            a click does: plain text, or pieces to style parts of it (a piece's
            own link is ignored).  Rarely needed: the definition is usually enough.
    """

    command: str
    run: bool = True
    tip: str | tuple[Piece, ...] = ""


class Piece(NamedTuple):
    """One piece of an output line: its text, what clicking it does, its style.

    A line of pieces with no style renders like an ``output`` line in the
    line's color (a table row); a line with any styled piece renders like
    an ``output_markup`` line, each piece in its own style (a help row) --
    in every frontend, so the two kinds read as they always did.

    Attributes:
        text: The text, literal (never markup).
        link: What clicking it does, in a frontend that can click.
        style: A Rich style for this piece (``cyan``, ``bold``); ``""``
            takes the line's.
    """

    text: str
    link: Link | None = None
    style: str = ""


def pieces_markup(pieces: Iterable[Piece]) -> str:
    """The markup a styled line renders as where nothing can click: text escaped, style kept."""
    return "".join(
        f"[{piece.style}]{escape_markup(piece.text)}[/]"
        if piece.style
        else escape_markup(piece.text)
        for piece in pieces
    )


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
        run: The command clicking a cell runs, for its record
            (``cfg.load <name>``), without the prefix.  None: not clickable.
        prefill: The command clicking a cell puts in the input line
            instead of running it.  At most one of ``run`` and ``prefill``.
        style: The Rich style a cell is drawn in, for its record
            (``/help.group``'s level column: green, yellow, red).  None:
            the row's color.  Plain consumers see the text alone.
        drop: How expendable the column is when the table is wider than the
            frontend (registry ``table-fits-the-frontend``): 0 is never
            dropped; of the rest the highest goes first.
        hide_empty: Leave the column out when every record's cell is
            ``""`` (a serial number no port has).
    """

    title: str
    cell: Callable[[Record], str]
    right: bool = False
    run: Callable[[Record], str] | None = None
    prefill: Callable[[Record], str] | None = None
    style: Callable[[Record], str] | None = None
    drop: int = 0
    hide_empty: bool = False

    def __post_init__(self) -> None:
        """Refuse a column that would both run and prefill, or a negative drop rank."""
        if self.run is not None and self.prefill is not None:
            raise ValueError(f"Column {self.title}: run= and prefill= are exclusive")
        if self.drop < 0:
            raise ValueError(f"Column {self.title}: drop= is 0 or more")

    def link(self, record: Record) -> Link | None:
        """What clicking this column's cell does for ``record``, or None."""
        if self.run is not None:
            return Link(self.run(record))
        if self.prefill is not None:
            return Link(self.prefill(record), run=False)
        return None


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
        total: How many there are when ``records`` are the first rows of
            a longer list (``/help ss`` shows ten), so the title still
            counts them all; None counts ``records``.
    """

    title: str
    noun: str
    columns: tuple[Column, ...]
    records: tuple[Record, ...]
    total: int | None = None

    def lines(self, width: int | None = None) -> list[tuple[str, str]]:
        """The table as ``(text, color)`` lines; ``""`` is the plain color.

        Lines carry no indent of their own (the frontend indents termapy's
        output); columns are two spaces apart and as wide as their widest
        cell or title; trailing padding is stripped.  The header and the
        dash row are dim.  An empty table is its title alone,
        ``<title> - empty``.  The text of :meth:`segment_lines`, joined.
        """
        return [
            ("".join(piece.text for piece in pieces), color)
            for pieces, color in self.segment_lines(width)
        ]

    def segment_lines(self, width: int | None = None) -> list[tuple[list[Piece], str]]:
        """The table as lines of pieces, each with what clicking it does.

        The one layout: :meth:`lines` joins these, so the plain table and
        the clickable one cannot disagree.  A cell's link covers its text,
        never the padding beside it; an empty cell has none.

        Args:
            width: How many columns a row may take, the frontend's
                (``IOHandle.width``); None is unbounded.  A ``hide_empty``
                column whose cells are all blank goes first; then, while a
                row is still too wide, the column with the highest ``drop``
                goes, the rightmost of equals; a ``drop`` of 0 never goes
                (registry ``table-fits-the-frontend``).
        """
        if not self.records:
            return [([Piece(f"{self.title} - empty")], "")]
        columns, cells = self._fitted(width)
        widths = [
            max(len(column.title), *(len(row[index]) for row in cells))
            for index, column in enumerate(columns)
        ]
        header = "  ".join(
            f"{column.title:<{width}}" for column, width in zip(columns, widths)
        ).rstrip()
        count = len(self.records) if self.total is None else self.total
        lines: list[tuple[list[Piece], str]] = [
            ([Piece(f"{self.title} - {counted(count, self.noun)}")], ""),
            ([Piece(header)], "dim"),
            ([Piece("  ".join("-" * width for width in widths).rstrip())], "dim"),
        ]
        for record, row in zip(self.records, cells):
            pieces: list[Piece] = []
            for index, (column, cell, width) in enumerate(zip(columns, row, widths)):
                if index:
                    pieces.append(Piece("  "))
                pad = Piece(" " * (width - len(cell)))
                style = column.style(record) if column.style is not None and cell else ""
                piece = Piece(cell, column.link(record) if cell else None, style)
                pieces.extend((pad, piece) if column.right else (piece, pad))
            lines.append((_rstripped(pieces), ""))
        return lines

    def _fitted(self, width: int | None) -> tuple[list[Column], list[list[str]]]:
        """The columns that show at ``width`` and their cells, one row per record."""
        kept = [
            (column, [column.cell(record) for record in self.records]) for column in self.columns
        ]
        kept = [
            (column, values)
            for column, values in kept
            if not (column.hide_empty and not any(values))
        ]

        def row_width() -> int:
            return sum(
                max(len(column.title), *(len(value) for value in values)) for column, values in kept
            ) + 2 * (len(kept) - 1)

        while width is not None and row_width() > width:
            expendable = [index for index, (column, _) in enumerate(kept) if column.drop > 0]
            if not expendable:
                break
            del kept[max(expendable, key=lambda index: (kept[index][0].drop, index))]
        columns = [column for column, _ in kept]
        cells = [[values[row] for _, values in kept] for row in range(len(self.records))]
        return columns, cells


def _rstripped(pieces: list[Piece]) -> list[Piece]:
    """``pieces`` without trailing whitespace or empty pieces: the joined
    text is exactly ``"".join(...).rstrip()``, as a plain line always was."""
    length = len("".join(piece.text for piece in pieces).rstrip())
    kept: list[Piece] = []
    used = 0
    for piece in pieces:
        text = piece.text[: length - used]
        if text:
            kept.append(piece._replace(text=text))
            used += len(text)
    return kept


@dataclass(frozen=True)
class TextLine:
    """A plain line among a record page's rows: a heading, a note, a hint.

    Attributes:
        text: The line's text (escaped when rendered); ``""`` is a blank line.
        color: Its color, ``""`` for plain.
    """

    text: str
    color: str = ""


@dataclass(frozen=True)
class PieceLine:
    """A line among a record page's rows whose parts differ in style: a drawing.

    Attributes:
        pieces: The line's pieces, each text literal and in its own style.
    """

    pieces: tuple[Piece, ...]


# One item of a record page: a row, a plain line, a line of styled pieces, or
# ``None`` for a blank line.  Consecutive rows align as one group.
Item = KvRow | TextLine | PieceLine | None


@dataclass(frozen=True)
class RecordView:
    """The prose form of a record answer: aligned label/value rows.

    Attributes:
        items: The page top to bottom.  Each run of consecutive rows is
            aligned on its own (``/port.info``'s settings, chip and signal
            groups), a ``TextLine`` is a plain line (``/port.chip``'s
            device heading), a ``PieceLine`` a line styled in parts
            (``/port.usb``'s tree), and ``None`` is a blank line.
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
            if isinstance(item, PieceLine):
                lines.append(f"  {pieces_markup(item.pieces)}")
            elif item is None or not item.text:
                lines.append("")
            else:
                body_markup = escape_markup(item.text)
                lines.append(
                    f"  [{item.color}]{body_markup}[/]" if item.color else f"  {body_markup}"
                )
        flush()
        return lines


def error_pieces(
    text: str,
    line: str,
    *,
    prefix: str,
    commands: Container[str],
    click_runs: Callable[[str], bool] | None = None,
) -> list[Piece] | None:
    """An error line as pieces with its fixes clickable, or None when it offers none.

    The error stays one string (``CmdResult.error``); its fixes are read
    back out of it, so every failure gets them and no command has to
    build anything.  A click runs the fixed command when ``click_runs``
    says its command only shows or opens something (``Command.shows``: a
    page, a list, an editor); any other link fills the input line for
    Enter, so nothing that acts runs on a click.

    - Each name the did-you-mean tail offers links to the typed line with
      that name in place of the word it corrects: ``/run welcom`` offers
      ``welcome``, which fills in ``/run welcome``.  An offered command
      (``/help``) replaces the command typed and keeps its arguments; an
      offered command line (``/dev.use X``) stands as it is.
    - Each command the rest of the text names (``try /dev.lib``) links to
      that command.

    Args:
        text: The error line as printed (``Error: ...``).
        line: The command as typed, without the prefix (``run welcom``).
        prefix: The REPL prefix.
        commands: The registered command names.
        click_runs: Whether a click may run a command line at once; None
            means never.

    Returns:
        Pieces whose text joins to exactly ``text``, or None when no piece
        links, so a frontend prints the plain line.
    """
    runs = click_runs or (lambda _line: False)
    head, names, after = split_did_you_mean(text)
    pieces = _command_reference_pieces(head, prefix, commands, runs)
    if names:
        pieces.append(Piece(DID_YOU_MEAN))
        for index, name in enumerate(names):
            if index:
                pieces.append(Piece(", "))
            fixed = _fixed_line(line, name, prefix)
            pieces.append(Piece(name, None if fixed is None else Link(fixed, run=runs(fixed))))
        pieces.append(Piece("?"))
        pieces.extend(_command_reference_pieces(after, prefix, commands, runs))
    return pieces if any(piece.link is not None for piece in pieces) else None


def _command_reference_pieces(
    text: str, prefix: str, commands: Container[str], runs: Callable[[str], bool]
) -> list[Piece]:
    """``text`` as pieces, each ``{prefix}<command>`` it names linked to fill in that command.

    A reference starts a word (``try /dev.lib``, never ``cfg/demo``) and
    names a registered command, so a path or a stray slash stays text.
    """
    reference = re.compile(rf"(?<!\S){re.escape(prefix)}([A-Za-z][\w.-]*\w|[A-Za-z])")
    pieces: list[Piece] = []
    last = 0
    for match in reference.finditer(text):
        command = match.group(1)
        if command.lower() not in commands:
            continue
        pieces.append(Piece(text[last : match.start()]))
        pieces.append(Piece(match.group(0), Link(command, run=runs(command))))
        last = match.end()
    pieces.append(Piece(text[last:]))
    return [piece for piece in pieces if piece.text]


def _fixed_line(line: str, name: str, prefix: str) -> str | None:
    """The typed ``line`` with ``name`` taken, the line a suggestion's click fills in; None if no word to replace.

    An offered command line (``/dev.use X``) stands as it is; an offered
    command (``/help``) replaces the command and keeps the arguments; any
    other name replaces the argument nearest it, which is the one it
    corrects (the suggestion was computed from it).
    """
    if name.startswith(prefix):
        offered = name[len(prefix) :]
        if " " in offered:
            return offered
        _, _, rest = line.partition(" ")
        return f"{offered} {rest}".rstrip()
    words = line.split()
    if len(words) < 2:
        return None
    command, *args = words
    nearest = min(
        range(len(args)),
        key=lambda index: edit_distance(args[index].casefold(), name.casefold()),
    )
    args[nearest] = name
    return " ".join([command, *args])
