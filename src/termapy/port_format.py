"""One-line-per-port formatting for serial-port tables.

Shared by the TUI port picker (see ``dialogs.PortPicker``) and the CLI
``--ports``/``--watch`` flags.  Textual-free on purpose: importing this
module must not pull in any GUI stack.

Public API:

- ``PORT_COLUMNS`` / ``COLUMN_HEADERS`` / ``DROP_ORDER`` -- data schema.
- ``row_from_facts(facts)`` -- build a ``(port_id, row_dict)`` pair from
  a ``ChipFacts`` instance.
- ``active_columns(rows)`` -- drop purely-blank optional columns.
- ``compute_widths(rows, row_width, columns)`` -- fit to a width budget
  by dropping columns, then shrinking flex columns, as a last resort.
- ``format_header(widths, columns)`` -- return (header, separator).
- ``format_row(row, widths, columns)`` -- format one data row.
- ``format_table(rows, row_width)`` -- convenience wrapper that returns
  a full list of lines (header + separator + rows).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from termapy.usb import mfg as _mfg_alias

if TYPE_CHECKING:
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
# ``vendor``, ``location``) are conditionally hidden via
# ``active_columns`` when every row is blank, so platforms that don't
# populate them (e.g. macOS for ``driver``, non-USB ports for
# ``vendor``) just don't show them.
PORT_COLUMNS: tuple[str, ...] = (
    "port",
    "manufacturer",
    "vendor",
    "description",
    "chip",
    "speed",
    "vid_pid",
    "sn",
    "driver",
    "location",
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
    "chip": "CHIP",
    "speed": "SPEED",
    "vid_pid": "VID:PID",
    "sn": "SN",
    "driver": "DRIVER",
    "location": "LOCATION",
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
DROP_ORDER: tuple[str, ...] = (
    "speed", "chip", "vid_pid", "driver", "vendor", "sn", "location",
)


# ─────────────────────────────────────────────────────────────────────────────
# Row construction
# ─────────────────────────────────────────────────────────────────────────────


def row_from_facts(facts: ChipFacts) -> tuple[str, dict]:
    """Build a ``(port_id, row)`` pair from a ``ChipFacts`` instance.

    The port_id is the device name (used as an OptionList id in the
    picker, ignored in CLI output).  The row dict has one entry per
    ``PORT_COLUMNS`` key, all values stringified and non-empty (``?``
    for missing data).
    """
    port = facts.device or _EMPTY

    # Description, with the redundant trailing "(COMx)" stripped.
    description = (facts.description or "").strip()
    if description.endswith(f"({port})"):
        description = description[: -(len(port) + 3)].rstrip()
    if not description:
        description = "Serial port"

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

    return port, {
        "port": port,
        "manufacturer": manufacturer,
        "vendor": vendor,
        "description": description,
        "chip": chip,
        "speed": speed,
        "vid_pid": vid_pid,
        "sn": sn,
        "driver": driver,
        "location": location,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Column selection + width computation
# ─────────────────────────────────────────────────────────────────────────────


def active_columns(rows: list[tuple[str, dict]]) -> tuple[str, ...]:
    """Drop purely-blank optional columns from the display list.

    ``sn``, ``driver``, ``vendor``, and ``location`` are optional: if
    every port reports ``?`` for one of them (common on built-in
    COM1/stock adapters, on macOS where ``driver`` isn't gathered
    yet, or for non-USB ports where ``vendor`` doesn't apply), we
    hide that column entirely so the row stays readable.  Other
    columns like chip/speed/vid_pid can also be ``?`` for non-USB
    ports but are informative enough to always show.
    """
    cols = list(PORT_COLUMNS)
    optional = ("sn", "driver", "vendor", "location")
    for col in optional:
        if rows and all(row[col] == _EMPTY for _, row in rows):
            cols.remove(col)
    return tuple(cols)


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
    header = _COL_SEP.join(
        COLUMN_HEADERS[col].ljust(widths[col]) for col in columns
    )
    separator = _COL_SEP.join("-" * widths[col] for col in columns)
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
        return ("..." + value[-(width - 3):]).ljust(width)

    def _fit(col: str, value: str, width: int) -> str:
        return _fit_tail(value, width) if col == "sn" else _fit_head(value, width)

    return _COL_SEP.join(
        _fit(col, row[col], widths[col]) for col in columns
    )


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
    rows = [row_from_facts(f) for f in facts_list]
    columns = active_columns(rows)
    widths, columns = compute_widths(rows, row_width, columns)
    header, separator = format_header(widths, columns)
    lines = [header, separator]
    for _port_id, row_data in rows:
        lines.append(format_row(row_data, widths, columns))
    return lines
