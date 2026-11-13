"""One-line-per-port formatting for serial-port tables.

Shared by the TUI port picker (see ``dialogs.PortPicker``) and the CLI
``--ports``/``--watch`` flags.  Textual-free on purpose: importing this
module must not pull in any GUI stack.

Public API:

- ``PORT_COLUMNS`` / ``COLUMN_HEADERS`` / ``DROP_ORDER`` /
  ``OPTIONAL_COLUMNS`` -- data schema, one list of column facts.
- ``PORT_TABLE_COLUMNS`` -- the same columns as a ``TableView`` over
  ``facts_to_json_record`` records, for ``/port.list``: dispatch fits it
  to the frontend's width by the same drop order.
- ``row_from_facts(facts)`` -- build a ``(port_id, row_dict)`` pair from
  a ``ChipFacts`` instance.
- ``active_columns(rows)`` -- drop purely-blank optional columns.
- ``compute_widths(rows, row_width, columns)`` -- fit to a width budget
  by dropping columns, then shrinking flex columns, as a last resort.
- ``format_header(widths, columns)`` -- return (header, separator).
- ``format_row(row, widths, columns)`` -- format one data row.
- ``format_table(rows, row_width)`` -- convenience wrapper that returns
  a full list of lines (header + separator + rows).
- ``find_chips(pattern)`` -- the known USB-serial chips a name pattern
  picks, for ``/port.chip.list`` and ``--chips``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from termapy.matching import matches
from termapy.plugins.views import Column
from termapy.port_control import CHIP_FIELD_LABELS, port_state_style
from termapy.usb import USB_SERIAL_CHIPS, ChipInfo, mfg as _mfg_alias
from termapy.usb_tree import ascii_fold

if TYPE_CHECKING:
    from collections.abc import Callable

    from termapy.port_control import ChipFacts


# Shown in a cell whose underlying fact is missing/unknown.  Single source
# of truth: both the display value and active_columns' "column is entirely
# blank" test use it, so they can never drift.  "?" (not "-") so a blank
# cell reads as "unknown" rather than being mistaken for a value like a
# root-of-tree location.
_EMPTY = "?"


# ─────────────────────────────────────────────────────────────────────────────
# Schema
# ─────────────────────────────────────────────────────────────────────────────


# Column order, left to right.  Optional columns (``sn``, ``driver``,
# ``vendor``, ``product``, ``location``) are conditionally hidden via
# ``active_columns`` when every row is blank, so platforms that don't
# populate them (e.g. macOS for ``driver``, non-USB ports for
# ``vendor``) just don't show them.
PORT_COLUMNS: tuple[str, ...] = (
    "port",
    "manufacturer",
    "vendor",
    "description",
    "product",
    "chip",
    "speed",
    "vid_pid",
    "sn",
    "driver",
    "location",
    "interface_number",
)

# Header labels shown in the table header row.  MANUFACTURER is
# abbreviated to MFG so the column stays compact (most vendor strings
# are shorter than the full header label was -- FTDI, Microsoft,
# Silabs); full value is still available via /port.chip.manufacturer.
COLUMN_HEADERS: dict[str, str] = {
    "port": "PORT",
    "manufacturer": "MFG",
    "vendor": "VENDOR",
    "description": "DESCRIPTION",
    # What the device calls itself (its USB product string), beside the
    # name a driver gave the port in DESCRIPTION and termapy's table name
    # in CHIP.  Labelled as on the /port.chip page, like the serial number.
    "product": CHIP_FIELD_LABELS["product"].upper(),
    "chip": "CHIP",
    "speed": "SPEED",
    "vid_pid": "VID:PID",
    # The serial number reads the same in every view: the label the
    # /port.chip page and the /port.chip.<field> tables use.
    "sn": CHIP_FIELD_LABELS["serial"].upper(),
    "driver": "DRIVER",
    "location": "LOCATION",
    # Two chars wide: bInterfaceNumber, present for a composite device --
    # one with several functions, or a firmware USB stack (TinyUSB) that
    # declares even a lone serial port that way.  Its own column so
    # LOCATION holds nothing but the physical path on every row.
    "interface_number": "IF",
}

# Column separator between adjacent fields.
_COL_SEP = "  "

# Columns dropped first when the row won't fit the usable width, in
# priority order (most-expendable first).  port / description / mfg
# are never dropped: port is required to pick a row, description is
# the primary identifier, and mfg is already very short.
#
# vendor / sn / location all rank near the end:
#   - vendor (silicon vendor by VID) is high-information density
#     compared to chip (which is verbose) or vid_pid (raw hex).
#     "Microchip" tells the user more than "04D8:9036" in 9 chars.
#   - sn and location are the disambiguators for identical adapters,
#     which matters more than chip / speed / driver when the user
#     has duplicate hardware plugged in.
#   - interface_number drops just before location: it qualifies the
#     path, so the path is the more useful of the two to keep.
DROP_ORDER: tuple[str, ...] = (
    "speed",
    "chip",
    "vid_pid",
    "driver",
    "vendor",
    "product",
    "sn",
    "interface_number",
    "location",
)

# Columns hidden when every port leaves them blank: common on built-in
# COM1 / stock adapters, on macOS where ``driver`` isn't gathered yet, or
# for non-USB ports where ``vendor`` doesn't apply.  Other columns like
# chip / speed / vid_pid can also be unknown for non-USB ports but are
# informative enough to always show.
OPTIONAL_COLUMNS: tuple[str, ...] = (
    "sn",
    "driver",
    "vendor",
    "product",
    "location",
    "interface_number",
)


# ─────────────────────────────────────────────────────────────────────────────
# Row construction
# ─────────────────────────────────────────────────────────────────────────────


def display_description(description: str | None, device: str | None) -> str:
    """The description a table shows: the redundant trailing ``(COMx)`` stripped, ``Serial port`` for none."""
    port = device or _EMPTY
    text = (description or "").strip()
    if text.endswith(f"({port})"):
        text = text[: -(len(port) + 3)].rstrip()
    return text or "Serial port"


def row_from_facts(facts: ChipFacts) -> tuple[str, dict]:
    """Build a ``(port_id, row)`` pair from a ``ChipFacts`` instance.

    The port_id is the device name (used as an OptionList id in the
    picker, ignored in CLI output).  The row dict has one entry per
    ``PORT_COLUMNS`` key, all values stringified and non-empty (``?``
    for missing data).
    """
    port = facts.device or _EMPTY
    description = display_description(facts.description, facts.device)

    # Folded to ASCII for the console, as the USB tree folds its names
    # ("MPLAB(R) PICkit 5" drops the sign); the JSON record keeps it as given.
    product = ascii_fold(facts.product or "") or _EMPTY
    chip = facts.model if facts.model and facts.model != "unknown" else _EMPTY

    speed = _EMPTY
    if facts.usb_speed:
        if "Full-Speed" in facts.usb_speed:
            speed = "Full-Speed"
        elif "High-Speed" in facts.usb_speed:
            speed = "High-Speed"

    vid_pid = facts.vid_pid if facts.vid_pid and ":" in facts.vid_pid else _EMPTY
    manufacturer = _mfg_alias(facts.manufacturer) or _EMPTY
    sn = facts.serial or _EMPTY
    driver = facts.driver or _EMPTY
    # Vendor flows through the same alias table so narrow columns stay
    # consistent ("Silicon Labs" -> "SiLabs", "Microchip" -> "Microchip").
    vendor = _mfg_alias(facts.vendor) or _EMPTY
    location = facts.location or _EMPTY
    # Blank, not "?": a non-composite device has no interface to name,
    # which is different from one whose interface we failed to read.
    interface_number = facts.interface_number or ""

    return port, {
        "port": port,
        "manufacturer": manufacturer,
        "vendor": vendor,
        "description": description,
        "product": product,
        "chip": chip,
        "speed": speed,
        "vid_pid": vid_pid,
        "sn": sn,
        "driver": driver,
        "location": location,
        "interface_number": interface_number,
    }


def split_vid_pid(vid_pid: str | None) -> tuple[int | None, int | None]:
    """Read a ``VID:PID`` string into its two integers, the one reader of USB ids.

    ``"0403:6015"`` gives ``(0x0403, 0x6015)``; absent or malformed text gives
    ``(None, None)``.  USB ids are hex by convention, so bare hex is right
    here where it is never guessed elsewhere (``symbols.parse_number``).

    Args:
        vid_pid: The ``ChipFacts.vid_pid`` text, or None.

    Returns:
        ``(vid, pid)``, each None when unknown.
    """
    if not vid_pid or ":" not in vid_pid:
        return None, None
    vid_text, pid_text = vid_pid.split(":", 1)
    try:
        return int(vid_text, 16), int(pid_text, 16)
    except ValueError:
        return None, None


def find_chips(pattern: str = "") -> list[tuple[int, int, ChipInfo]]:
    """The known USB-serial chips any column of which matches ``pattern``, by VID:PID.

    The one filter of ``/port.chip.list`` and ``--chips`` (registry
    ``name-one-matcher``), over every column the table shows: ``ftdi``
    finds the FTDI models, ``0403`` the chips with that vendor ID,
    ``high`` the high-speed chips, and an empty pattern every chip.
    """
    rows = [
        (vid, pid, info)
        for (vid, pid), info in USB_SERIAL_CHIPS.items()
        if any(matches(text, pattern) for text in (f"{vid:04X}:{pid:04X}", info.model, info.speed))
    ]
    return sorted(rows, key=lambda row: (row[0], row[1]))


def _checked_in_use(in_use: str | None) -> bool | None:
    """``ChipFacts.in_use`` as a record's ``in_use``: True or False when a probe checked, None when none did."""
    return None if in_use is None else in_use.startswith("yes")


def facts_to_json_record(facts: ChipFacts) -> dict:
    """Build a stable JSON record from a ChipFacts.

    The structured twin of :func:`row_from_facts` -- same facts, but a
    machine-consumable shape rather than column-fitted display strings.
    Shared by ``--ports --json`` and ``/port.list``'s ``data`` so the
    shell and the MCP agent see the identical record.

    Field names are snake_case (matching pyserial convention) and the
    schema is fixed: every record has every field, with ``null`` for
    unknown values, so consumers can rely on the shape.  Numeric
    ``vid``/``pid`` are kept alongside the formatted ``vid_pid`` so
    consumers can do numeric or literal-string filtering.
    """
    # vid_pid on ChipFacts is "0403:6001" (uppercase hex); split it
    # back to integers when present so consumers can do numeric ops.
    vid, pid = split_vid_pid(facts.vid_pid)
    # Vendor info comes from two independent sources:
    #   - manufacturer / manufacturer_raw: what the device descriptor
    #     or driver INF reports.  manufacturer is the column-friendly
    #     short form (via usb_mfg.mfg()); manufacturer_raw is the
    #     literal string.
    #   - vendor: the silicon-vendor name resolved from the VID per
    #     USB-IF assignment.  Populated even when the (VID, PID) pair
    #     isn't in our chip table; useful when manufacturer is generic
    #     (e.g. "Microsoft" because the device uses usbser.sys).
    # All three are exposed so engineers can see the full picture --
    # they often agree, and when they disagree the disagreement is
    # diagnostic information.
    raw_mfg = facts.manufacturer
    return {
        "device": facts.device,
        "manufacturer": _mfg_alias(raw_mfg) or None,
        "manufacturer_raw": raw_mfg,
        "vendor": facts.vendor,
        "description": facts.description,
        # The device's own product string, as reported: what it calls itself.
        "product": facts.product,
        "chip": facts.model if facts.model and facts.model != "unknown" else None,
        "speed": _normalize_speed(facts.usb_speed),
        "vid": vid,
        "pid": pid,
        "vid_pid": facts.vid_pid.upper() if facts.vid_pid and ":" in facts.vid_pid else None,
        "serial_number": facts.serial,
        # True or False only when something checked: a probe (--ports
        # --json, /port.info) or a termapy session's breadcrumb.  A listing
        # does not check every port, so None means not checked, never "free".
        "in_use": True if facts.session else _checked_in_use(facts.in_use),
        # The termapy session holding it, when a listing asked
        # (``port_control.fill_sessions``): "this session" or "termapy mcp,
        # PID 17140".  Other programs are not seen here.
        "session": facts.session,
        "driver": facts.driver,
        # Physical bus location, "1-2.3" -- bus, then one hop per hub
        # tier.  Disambiguates two devices with the same VID/PID and
        # serial number, the cheap-clone scenario.  Falls back to the
        # Windows registry's "Hub_#0011.Port_#0003" when the topology
        # can't be read.
        "location": facts.location,
        # bInterfaceNumber, for a composite device (a debugger with a CDC
        # port alongside it, one channel of a multi-port FTDI chip, or a
        # TinyUSB board whose serial port is its only function).  null
        # for a non-composite device.  Kept separate from location so
        # that field is always just the path.
        "interface_number": facts.interface_number,
    }


def _normalize_speed(usb_speed: str | None) -> str | None:
    """Reduce the verbose usb_speed string to a short label or None."""
    if not usb_speed:
        return None
    if "Full-Speed" in usb_speed:
        return "Full-Speed"
    if "High-Speed" in usb_speed:
        return "High-Speed"
    if "Super-Speed" in usb_speed:
        return "Super-Speed"
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Column selection + width computation
# ─────────────────────────────────────────────────────────────────────────────


def active_columns(rows: list[tuple[str, dict]]) -> tuple[str, ...]:
    """Drop purely-blank optional columns from the display list.

    ``sn``, ``driver``, ``vendor``, ``product``, ``location`` and
    ``interface_number`` are optional: if
    every port reports ``?`` for one of them (common on built-in
    COM1/stock adapters, on macOS where ``driver`` isn't gathered
    yet, or for non-USB ports where ``vendor`` doesn't apply), we
    hide that column entirely so the row stays readable.  Other
    columns like chip/speed/vid_pid can also be ``?`` for non-USB
    ports but are informative enough to always show.
    """
    cols = list(PORT_COLUMNS)
    for col in OPTIONAL_COLUMNS:
        if rows and all(not row[col] or row[col] == _EMPTY for _, row in rows):
            cols.remove(col)
    return tuple(cols)


# ─────────────────────────────────────────────────────────────────────────────
# The same columns as a TableView, for /port.list
# ─────────────────────────────────────────────────────────────────────────────


def _unknown(value: object) -> str:
    """A required column's cell: the value, or ``?`` when the fact is missing."""
    return str(value) if value else _EMPTY


# The cell of each PORT_COLUMNS column over a ``facts_to_json_record``
# record.  A required column shows ``?`` for a missing fact, as
# ``row_from_facts`` does; an OPTIONAL_COLUMNS column shows ``""`` so the
# table can hide it when every port leaves it blank (``Column.hide_empty``).
_RECORD_CELLS: dict[str, Callable[[dict], str]] = {
    "port": lambda record: _unknown(record["device"]),
    "manufacturer": lambda record: _unknown(record["manufacturer"]),
    "vendor": lambda record: _mfg_alias(record["vendor"]) or "",
    "description": lambda record: display_description(record["description"], record["device"]),
    "product": lambda record: ascii_fold(record["product"] or ""),
    "chip": lambda record: _unknown(record["chip"]),
    "speed": lambda record: _unknown(record["speed"]),
    "vid_pid": lambda record: _unknown(record["vid_pid"]),
    "sn": lambda record: record["serial_number"] or "",
    "driver": lambda record: record["driver"] or "",
    "location": lambda record: record["location"] or "",
    "interface_number": lambda record: record["interface_number"] or "",
}


def _port_column(name: str) -> Column:
    """One ``/port.list`` column from the schema's facts: header, cell, drop rank, optionality."""
    return Column(
        COLUMN_HEADERS[name],
        _RECORD_CELLS[name],
        # A click puts the connect in the input line rather than running it:
        # opening a port toggles DTR, which resets an auto-reset board.
        prefill=(lambda record: f"port.connect {record['device']}") if name == "port" else None,
        # The port's name is colored by who holds it (port_state_style).
        style=(lambda record: port_state_style(record["session"])) if name == "port" else None,
        drop=(len(DROP_ORDER) - DROP_ORDER.index(name)) if name in DROP_ORDER else 0,
        hide_empty=name in OPTIONAL_COLUMNS,
    )


# The port table as ``/port.list`` answers it (registry list-one-format),
# built from the one schema above so the picker, ``--ports`` and the list
# agree on columns, headers and which column goes first on a narrow screen.
PORT_TABLE_COLUMNS: tuple[Column, ...] = tuple(_port_column(name) for name in PORT_COLUMNS)


def compute_widths(
    rows: list[tuple[str, dict]],
    row_width: int,
    columns: tuple[str, ...] = PORT_COLUMNS,
) -> tuple[dict, tuple[str, ...]]:
    """Compute the width of each column and the set that survives.

    Fit strategy, in order:

    1. Start at natural (data-driven) widths.
    2. If still over budget, **drop entire columns** in the
       ``DROP_ORDER`` priority.  Dropping is preferred to compressing
       because a sparse but complete row reads better than a
       truncated dense one.
    3. If still over budget with every droppable column gone, fall
       back to shrinking the description and chip columns (chip only
       if it survived step 2).
    4. If still over after both floors are hit, accept the overrun
       rather than truncating port / description / mfg.

    Returns ``(widths, surviving_columns)``.  Callers thread the
    surviving-column tuple through the header / row formatters so
    dropped columns disappear from both the header and the data rows.
    """

    def _natural_widths(cols: tuple[str, ...]) -> dict:
        return {
            col: max(
                len(COLUMN_HEADERS[col]),
                max((len(row[col]) for _, row in rows), default=0),
            )
            for col in cols
        }

    def _total(cols: tuple[str, ...], widths: dict) -> int:
        sep_total = len(_COL_SEP) * (len(cols) - 1)
        return sum(widths[c] for c in cols) + sep_total

    survivors = list(columns)
    widths = _natural_widths(columns)

    # Step 2: drop columns one at a time until we fit.
    for victim in DROP_ORDER:
        if _total(tuple(survivors), widths) <= row_width:
            break
        if victim in survivors:
            survivors.remove(victim)

    surviving = tuple(survivors)
    widths = {c: widths[c] for c in surviving}

    total = _total(surviving, widths)
    if total <= row_width:
        return widths, surviving

    # Step 3: shrink description (always present), then chip if still there.
    min_desc = 10
    min_chip = 10
    over = total - row_width

    shrink = min(over, widths["description"] - min_desc)
    if shrink > 0:
        widths["description"] -= shrink
        over -= shrink

    if over > 0 and "chip" in widths:
        shrink = min(over, widths["chip"] - min_chip)
        if shrink > 0:
            widths["chip"] -= shrink
            over -= shrink

    return widths, surviving


# ─────────────────────────────────────────────────────────────────────────────
# Rendering
# ─────────────────────────────────────────────────────────────────────────────


def format_header(
    widths: dict,
    columns: tuple[str, ...] = PORT_COLUMNS,
) -> tuple[str, str]:
    """Return ``(header_line, separator_line)`` for the port table header."""
    header = _COL_SEP.join(COLUMN_HEADERS[column].ljust(widths[column]) for column in columns)
    separator = _COL_SEP.join("-" * widths[column] for column in columns)
    return header, separator


def format_row(
    row: dict,
    widths: dict,
    columns: tuple[str, ...] = PORT_COLUMNS,
) -> str:
    """Format one port row as a single line aligned to the given widths.

    Fields longer than their column width are truncated with a
    three-dot ellipsis (``...``) at the end.  ASCII dots are used
    instead of the Unicode ellipsis character so the table renders
    correctly on terminals with limited Unicode support.

    The ``sn`` column truncates from the **left** instead of the right
    (``...DEADBEEF`` rather than ``02002670R...``) because the random
    portion of a USB serial number is usually at the tail.  Vendor
    prefixes at the head of the string are the least useful part to
    preserve when we have to cut.  The full value is always available
    via ``/port.chip.serial``.
    """

    def _fit_head(value: str, width: int) -> str:
        if len(value) <= width:
            return value.ljust(width)
        if width <= 3:
            return "..."[:width].ljust(width)
        return (value[: width - 3] + "...").ljust(width)

    def _fit_tail(value: str, width: int) -> str:
        if len(value) <= width:
            return value.ljust(width)
        if width <= 3:
            return "..."[:width].ljust(width)
        return ("..." + value[-(width - 3) :]).ljust(width)

    def _fit(col: str, value: str, width: int) -> str:
        return _fit_tail(value, width) if col == "sn" else _fit_head(value, width)

    return _COL_SEP.join(_fit(column, row[column], widths[column]) for column in columns)


def format_table(
    facts_list: list[ChipFacts],
    row_width: int,
) -> list[str]:
    """Convenience wrapper: build rows, pick columns, emit full table.

    Returns a list of lines suitable for printing one per line.
    When ``facts_list`` is empty the single line ``"(no ports found)"``
    is returned so callers never have to branch on empty input.
    """
    if not facts_list:
        return ["(no ports found)"]
    rows = [row_from_facts(fact) for fact in facts_list]
    columns = active_columns(rows)
    widths, columns = compute_widths(rows, row_width, columns)
    header, separator = format_header(widths, columns)
    lines = [header, separator]
    for _port_id, row_data in rows:
        lines.append(format_row(row_data, widths, columns))
    return lines
