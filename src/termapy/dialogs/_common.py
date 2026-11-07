"""Shared constants and helpers for the dialog submodules.

Underscore prefix marks this as package-private; consumers go through
``termapy.dialogs`` (the package public API).
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from rich.text import Text
from textual import events, on
from textual.binding import Binding, BindingType
from textual.containers import Horizontal
from textual.widgets import Button, Input, Label, OptionList, Static, TextArea
from textual.widgets.option_list import Option
from textual.widgets.text_area import Selection

from termapy.folder_ops import file_columns, format_file_header

if TYPE_CHECKING:
    from textual.app import ComposeResult

    from termapy.port_control import ChipFacts


class EditorTextArea(TextArea):
    """A ``TextArea`` with Ctrl+A as select-all, for termapy's modal editors.

    Textual's ``TextArea`` binds Ctrl+A to ``cursor_line_start`` (an Emacs
    convention) and leaves ``action_select_all`` with no key.  The config,
    script and proto editors are full-screen replace-the-whole-file
    editors, where the familiar "select all, then paste over it" is what a
    user reaches for -- and Textual's ``paste`` already replaces the
    selection, so Ctrl+A then Ctrl+V overwrites the file.  Line-start stays
    on Home (its other default key), so nothing is lost.  Copy / cut /
    paste (Ctrl+C / X / V) are already bound by the base class; this adds
    only the missing select-all.
    """

    BINDINGS = [Binding("ctrl+a", "select_all", "Select all", show=False)]


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
# Columns the dialog spends around its title row: border 2 + padding 4.
_TITLE_ROW_CHROME = 6
# Room kept between a picker's title and its path when the dialog sizes
# itself to show both (``_title_row_width``).  PathLink's left margin is
# the floor when the terminal caps the width instead.
_TITLE_PATH_GAP = 4


#: How a picker renames or deletes a file and stays open: ``(verb, path, done)``,
#: where ``verb`` is ``"rename"`` or ``"delete"`` and ``done(select)`` runs on the
#: main thread once the command has finished, with the file to highlight.
FileAction = Callable[[str, str, Callable[[str | None], None]], None]


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
        self._detail = detail
        self._detail_header = detail_header
        self._label = label
        self._row_width = _FILE_PICKER_ROW_WIDTH
        self._load(files)
        self._fill()

    def _load(self, files: list[Path]) -> None:
        """Compute the header and the rows for ``files``."""
        rows = file_columns(files)
        self._header = format_file_header(rows, self._detail_header) if rows else ""
        # (path, name and size/age cells, detail), computed once: a resize
        # only re-truncates the detail.
        self._rows = [
            (
                path,
                Text.assemble(
                    self._label(path, row.name) if self._label else row.name,
                    (f"  {row.size}  {row.age}", "dim"),
                ),
                self._detail(path) if self._detail else "",
            )
            for path, row in zip(files, rows, strict=True)
        ]
        #: Index of the first FILE option (past the header) -- what a picker
        #: highlights, offset by any index it computed over ``files``.
        self.first_file = 1 if files else 0

    def refill(self, files: list[Path], select: str | None = None) -> None:
        """Show ``files`` instead, keeping the place: ``select`` if it is listed, else the row the cursor was on.

        A picker that renamed or deleted a file calls this and stays open.
        After a delete the row under the cursor is the next file, so deleting
        several is one press each; after a rename ``select`` is the new name.
        """
        index = self.highlighted
        self._load(files)
        self.clear_options()
        self._fill()
        if not files:
            return
        names = [str(path) for path in files]
        if select in names:
            self.highlighted = self.first_file + names.index(select)
            return
        last = self.first_file + len(files) - 1
        self.highlighted = max(self.first_file, min(index if index is not None else 0, last))

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


# Dismiss bindings shared by all modal dialogs.  Typed as Textual's own
# element type: list is invariant, so an inferred list[tuple[str, str, str]]
# is not a list[BindingType] and every ``BINDINGS =`` site would mismatch.
_DISMISS_BINDINGS: list[BindingType] = [
    ("ctrl+q", "dismiss_modal", "Close"),
    ("escape", "dismiss_modal", "Close"),
]

# Find bindings for an editor dialog that composes a ``FindRow``.  Alt+F is
# the VS Code fallback: its integrated terminal keeps Ctrl+F for its own
# find (help: environment).
_FIND_BINDINGS: list[BindingType] = [
    ("ctrl+f", "find", "Find"),
    ("alt+f", "find", "Find"),
]


def find_occurrences(lines: Sequence[str], needle: str) -> list[tuple[int, int, int]]:
    """``(row, col, length)`` of every occurrence of ``needle`` in ``lines``, in document order.

    Literal and case-insensitive: a config holds ``\\r``, ``$(`` and
    ``[purple]``, which a regex would read as syntax.  Every occurrence on a
    line is listed, non-overlapping (``aa`` in ``aaa`` is one).  An empty
    needle matches nothing.  A line is lowered only when that keeps its
    length, so a column always maps back to the line as shown.
    """
    if not needle:
        return []
    found: list[tuple[int, int, int]] = []
    key = needle.lower()
    for row, line in enumerate(lines):
        haystack = line.lower()
        if len(haystack) != len(line):
            haystack, key_here = line, needle
        else:
            key_here = key
        col = haystack.find(key_here)
        while col != -1:
            found.append((row, col, len(needle)))
            col = haystack.find(key_here, col + len(needle))
    return found


def pick_occurrence(
    matches: Sequence[tuple[int, int, int]],
    anchor: tuple[int, int],
    direction: int,
    *,
    inclusive: bool,
) -> int | None:
    """The index in ``matches`` to show next from ``anchor``, wrapping; ``None`` when there are none.

    Forward (``direction`` 1): the first match starting at or after the
    anchor when ``inclusive`` (typing keeps the match under the cursor as
    the needle grows), strictly after it otherwise (Enter moves on).
    Backward (-1): the last match starting before the anchor.  Past either
    end the search wraps.
    """
    if not matches:
        return None
    starts = [(row, col) for row, col, _ in matches]
    if direction > 0:
        for index, start in enumerate(starts):
            if start >= anchor if inclusive else start > anchor:
                return index
        return 0
    for index in range(len(starts) - 1, -1, -1):
        if starts[index] < anchor:
            return index
    return len(starts) - 1


class FindRow(Horizontal):
    """A find bar for a modal editor: any text, literal, case-insensitive.

    Hidden until ``open()``; the dialog binds ``_FIND_BINDINGS`` and a Find
    button to it.  While its field has focus the row's own bindings are in
    the key chain ahead of the screen's, so Escape closes the bar rather
    than the dialog, and Up / Down step through the matches; Enter steps
    forward through the field's own Submitted.  The current match is the
    editor's selection, centered, so the dialog's selection handler (the
    config editor's help panel) follows it for free.  Textual's TextArea
    has no search of its own (8.2.8), hence the helpers above.
    """

    DEFAULT_CSS = """
    FindRow { height: 1; display: none; }
    FindRow.visible { display: block; }
    FindRow Input { width: 1fr; }
    FindRow Label {
        width: auto; height: 1; content-align: right middle;
        color: $accent; padding: 0 1;
    }
    FindRow Button {
        min-width: 3; width: auto; height: 1; min-height: 1;
        border: none; margin: 0 0 0 1; background: $boost;
    }
    """

    BINDINGS = [
        Binding("escape", "find_close", "Close find", show=False),
        Binding("up", "find_prev", "Previous match", show=False),
        Binding("down", "find_next", "Next match", show=False),
    ]

    def __init__(self, editor: TextArea, *, id: str) -> None:
        """Build the row for ``editor``; ``id`` also prefixes the field's and count's ids."""
        super().__init__(id=id)
        self._editor = editor

    def compose(self) -> ComposeResult:
        """The field, the ``n/m`` count, and the same ▲ ▼ × the output pane's find bar has."""
        yield Input(placeholder="Find", id=f"{self.id}-input", compact=True)
        yield Label("", id=f"{self.id}-count", markup=False)
        yield Button("▲", classes="find-prev", variant="default")
        yield Button("▼", classes="find-next", variant="default")
        yield Button("×", classes="find-close", variant="default")

    def on_mount(self) -> None:
        """Tooltips."""
        self.query_one(Input).tooltip = "Any text, as typed; case does not matter."
        self.query_one(".find-prev", Button).tooltip = "Previous match (Up)"
        self.query_one(".find-next", Button).tooltip = "Next match (Down, Enter)"
        self.query_one(".find-close", Button).tooltip = "Close find (Escape)"

    def open(self) -> None:
        """Show the bar and focus its field; a needle left from last time is searched afresh."""
        self.add_class("visible")
        field = self.query_one(Input)
        field.focus()
        if field.value:
            self._jump(1, inclusive=True)

    def action_find_close(self) -> None:
        """Hide the bar, keep the cursor at the match, and give the editor the focus back."""
        self.remove_class("visible")
        end = self._editor.selection.end
        self._editor.selection = Selection(end, end)
        self._editor.focus()

    def action_find_next(self) -> None:
        """Select the next match, wrapping."""
        self._jump(1, inclusive=False)

    def action_find_prev(self) -> None:
        """Select the previous match, wrapping."""
        self._jump(-1, inclusive=False)

    @on(Input.Changed)
    def _needle_changed(self, event: Input.Changed) -> None:
        """Each keystroke searches again from the match under the cursor."""
        event.stop()
        self._jump(1, inclusive=True)

    @on(Input.Submitted)
    def _needle_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self.action_find_next()

    @on(Button.Pressed, ".find-prev")
    def _prev_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.action_find_prev()

    @on(Button.Pressed, ".find-next")
    def _next_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.action_find_next()

    @on(Button.Pressed, ".find-close")
    def _close_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.action_find_close()

    def _jump(self, direction: int, *, inclusive: bool) -> None:
        """Select the match ``pick_occurrence`` chooses and show the count."""
        needle = self.query_one(Input).value
        count = self.query_one(Label)
        editor = self._editor
        if not needle:
            count.update("")
            return
        matches = find_occurrences(editor.document.lines, needle)
        # The selection's two ends in document order: a drag upward puts
        # the cursor first.
        anchor = min(editor.selection.start, editor.selection.end)
        index = pick_occurrence(matches, anchor, direction, inclusive=inclusive)
        if index is None:
            count.update("0/0")
            return
        row, col, length = matches[index]
        editor.selection = Selection((row, col), (row, col + length))
        editor.scroll_cursor_visible(center=True)
        count.update(f"{index + 1}/{len(matches)}")


def _port_facts(ports: list) -> list[ChipFacts]:
    """The port table's facts: each listed port's, then the pyserial loopback."""
    from termapy import port_control

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
    return facts_list


def _fill_port_rows(ol: OptionList, rows: list[tuple[str, dict]], row_width: int) -> None:
    """Add a header, a separator, and one option per port row, fit to ``row_width``.

    Formatting lives in ``termapy.port_format`` so the CLI
    (``--ports`` / ``--watch``) can reuse it without pulling Textual
    into its code path.  This maps formatted lines to ``OptionList``
    entries, attaching each data row's port name as the option id so
    selection returns the device string.
    """
    from termapy.port_format import active_columns, compute_widths, format_header, format_row

    widths, columns = compute_widths(rows, row_width, active_columns(rows))
    header, separator = format_header(widths, columns)
    ol.add_option(Option(header, disabled=True))
    ol.add_option(Option(separator, disabled=True))
    for port_id, row_data in rows:
        ol.add_option(Option(format_row(row_data, widths, columns), id=port_id))


def _populate_port_option_list(
    ol: OptionList,
    ports: list,
    row_width: int,
) -> None:
    """Fill QuickSetup's port list: the same rows the port picker shows.

    ``row_width`` is the usable column budget for the list; QuickSetup
    re-renders against its measured width.
    """
    from termapy.port_format import row_from_facts

    _fill_port_rows(ol, [row_from_facts(fact) for fact in _port_facts(ports)], row_width)


class PortOptionList(OptionList):
    """The port picker's list: a header, a separator, and one row per port, fit to its width.

    Sized the way the file pickers are (``FileOptionList``): the picker
    makes its dialog ``dialog_width()`` wide, enough for every column
    whole, so the dialog is as wide as its table rather than a share of
    the terminal, and the CSS caps it at the terminal.  Every resize
    rebuilds the rows to the width layout measured, where
    ``port_format.compute_widths`` drops the least useful columns before a
    row would wrap.  A rebuild keeps the highlighted port by its id.
    """

    def __init__(self, facts: list[ChipFacts], *, id: str | None = None) -> None:
        """Build the list from the ports' facts (``_port_facts``)."""
        super().__init__(id=id)
        self._rows: list[tuple[str, dict]] = []
        # 0 until layout measures the list: fill at the table's full width.
        self._row_width = 0
        self.set_facts(facts)

    def set_facts(self, facts: list[ChipFacts]) -> None:
        """Show these ports instead, as a plug or unplug changes them."""
        from termapy.port_format import row_from_facts

        self._rows = [row_from_facts(fact) for fact in facts]
        self._fill()

    def table_width(self) -> int:
        """The columns the table takes with every column whole."""
        from termapy.port_format import active_columns, compute_widths, format_header

        widths, columns = compute_widths(self._rows, sys.maxsize, active_columns(self._rows))
        return len(format_header(widths, columns)[0])

    def dialog_width(self) -> int:
        """The picker width that shows every column whole: the table plus the chrome around it."""
        return self.table_width() + _FILE_PICKER_CHROME

    def _fill(self) -> None:
        """Rebuild the options at the list's width, keeping the highlighted port."""
        kept = _highlighted_file(self)
        self.clear_options()
        _fill_port_rows(self, self._rows, self._row_width or self.table_width())
        for i in range(self.option_count):
            if kept is not None and self.get_option_at_index(i).id == kept:
                self.highlighted = i
                return
        if self.option_count > 2:
            # The first port, past the header and the separator.
            self.highlighted = 2

    def on_resize(self, _event: events.Resize) -> None:
        """Rebuild the rows to the width layout gave the list."""
        # Reserve the scrollbar's gutter whether or not it shows, as
        # FileOptionList does, so a growing port list never tips a row
        # into wrapping.
        width = self.content_size.width - self.styles.scrollbar_size_vertical
        if width <= 0 or width == self._row_width:
            return
        self._row_width = width
        self._fill()


# Why a link to the file manager does nothing: the detection found no
# display this process can reach.  The override is named because the
# guess can be wrong (mosh, tmux inside SSH, WSLg).
_NO_DISPLAY_TIP = (
    "Not available in this environment: no display this process "
    "can reach.  Set TERMAPY_GUI=1 to override the detection."
)


def _fit_path(path: Path, width: int) -> str:
    """``path`` as text, shortened from the FRONT when it exceeds ``width``.

    CSS ellipsis truncates at the end, which would drop the tail that
    actually identifies the path.  Leading segments are replaced with
    ``...`` instead, so ``.../termapy_cfg/demo/run/crc_tour.run`` survives.
    Only the DISPLAY is shortened; a click still opens the full path.

    Args:
        path: The path to render.
        width: Columns available to the path.

    Returns:
        The full path, or a front-elided form that fits.
    """
    text = str(path)
    if len(text) <= width:
        return text
    parts = list(path.parts)
    while len(parts) > 1:
        parts.pop(0)
        candidate = "..." + os.sep + os.sep.join(parts)
        if len(candidate) <= width:
            return candidate
    return text[-width:]


def _option_path(option_id: str | None, folder: Path) -> Path:
    """The absolute file a highlighted option names, or ``folder`` for none.

    The header row has no id, and an empty list highlights nothing; both
    show the folder the picker lists.
    """
    return Path(option_id).resolve() if option_id else folder.resolve()


class PathLink(Static):
    """A path in a picker's title row; clicking hands it to the opener.

    Shared by the config, run and proto pickers, so a path in a title row
    looks and behaves the same in each: right-aligned in the space the
    title leaves, front-shortened to fit (``_fit_path``), underlined while
    a click does something.  A widget rather than a markup ``@click``
    action so the behaviour does not depend on Textual's action-resolution
    path, and a subclass rather than a handler on the screen so the click
    target is unambiguous.  Built ``markup=False``, so ``set_path`` updates
    stay plain text.

    With no opener the link is inert: the path still shows, without the
    underline, and a click does nothing.  The pickers make it inert where
    a file manager would not reach the user (no display, or ``--web``).
    """

    DEFAULT_CSS = """
    PathLink {
        width: 1fr; margin-left: 2; text-align: right; color: $text-muted;
        text-style: underline; text-overflow: ellipsis;
    }
    PathLink:hover { color: $accent; }
    PathLink.-inert { text-style: none; }
    PathLink.-inert:hover { color: $text-muted; }
    """

    def __init__(
        self,
        path: Path,
        width: int,
        *,
        link_id: str,
        opener: Callable[[str], None] | None,
    ) -> None:
        """Build the link.

        Args:
            path: The path to display and to open on click.
            width: Columns for the path before layout has measured the link.
            link_id: The widget id.
            opener: What a click calls with the path, or ``None`` for an
                inert link.  Injected so a test can prove the click reaches
                it without a file manager appearing -- the ``source=`` seam
                used elsewhere in the codebase, not a patched global.
        """
        super().__init__(_fit_path(path, width), id=link_id, markup=False)
        self.path = path
        self._width = width
        self._opener = opener
        if opener is None:
            self.add_class("-inert")

    def set_path(self, path: Path) -> None:
        """Show and open ``path`` from now on."""
        self.path = path
        self.update(_fit_path(path, self._width))

    def on_resize(self, _event: events.Resize) -> None:
        """Refit the path to the width layout gave the link (``width: 1fr``)."""
        if self.content_size.width > 0:
            self._width = self.content_size.width
            self.update(_fit_path(self.path, self._width))

    def on_click(self) -> None:
        """Open the path with the opener, if there is one."""
        if self._opener is not None:
            self._opener(str(self.path))


def _path_link_tooltip(*, live: bool, gui_apps: bool, action: str) -> str | None:
    """What a picker's ``PathLink`` says on hover.

    A live link says what a click does (``action``).  An inert one says why
    when the reason is fixable (no display found, ``_NO_DISPLAY_TIP``) and
    nothing under ``--web``, where it is inert by design.
    """
    if live:
        return action
    return None if gui_apps else _NO_DISPLAY_TIP


def _title_row_width(title: str, paths: Iterable[Path]) -> int:
    """The picker width that shows ``title`` and the longest of ``paths`` whole.

    A picker takes the larger of this and ``FileOptionList.dialog_width``,
    so a long path widens the dialog instead of crowding the title, as a
    long summary widens it for the rows.  The CSS ``max-width`` still caps
    it at the terminal, where ``PathLink`` front-shortens the path.
    """
    longest = max((len(str(path)) for path in paths), default=0)
    return len(title) + _TITLE_PATH_GAP + longest + _TITLE_ROW_CHROME
