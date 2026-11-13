"""Print-and-exit handlers for termapy's CLI flags.

Every function here is the implementation of one ``termapy --<flag>``
command.  Each one writes plain text to stdout, never touches Textual
or the TUI infrastructure, and exits the process with status 0 on
success or 1 on a named failure.

Architectural constraint: **this module must not import Textual, Rich,
prompt-toolkit, or any other UI framework.**  The entry point
(``termapy.entry``) dispatches to these helpers *before* any TUI-or-
REPL imports fire, so ``termapy --ports`` stays fast and small even on
a machine where importing Textual costs ~300 ms.  The flags load neither
``importlib.metadata`` (``--version`` alone) nor ``frist``; ``--info``
alone loads Rich, to flatten the ``/port.chip`` page to plain text.
**Enforced by ``tests/test_cli.py::TestPathsStayLight`` (registry
``flag-paths-light``)**, which runs each flag and checks what loaded.

Available flags:

    --info [PORT]      full multi-line chip dump (reuses /port.info)
    --ports [PORT]     one-line-per-port picker-style table
    --watch            poll 2 Hz, print events on presence/in-use/SN change
    --chips [FILTER]   dump the USB_SERIAL_CHIPS lookup table
    --check            validate config, print JSON status
    --new NAME PORT .. create a config, then hand it back to main()

Signatures are intentionally uniform: ``_run_*(args) -> None`` where
``args`` is the ``argparse.Namespace``.  Each calls ``sys.exit(...)``
directly rather than returning a status code so callers never have to
remember to propagate the exit.

The port-facing handlers additionally accept a keyword-only ``source``
that is forwarded to port discovery unchanged -- see
``port_control.resolve_port_source``.  Nothing on the command line sets
it; it exists so a caller can drive one of these handlers against a
known fleet without owning the machine's real ports.
"""

from __future__ import annotations

import json
import shutil
import sys
import time
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import argparse
    from collections.abc import Callable, Iterable
    from pathlib import Path
    from typing import NoReturn

    from rich.console import Console

    from termapy.breadcrumbs import Crumb
    from termapy.plugins.views import Piece
    from termapy.port_control import ChipFacts, PortSource
    from termapy.usb_tree import UsbSource


# ─────────────────────────────────────────────────────────────────────────────
# --info: the existing verbose per-port dump
# ─────────────────────────────────────────────────────────────────────────────


def run_info(args: argparse.Namespace, *, source: PortSource | None = None) -> None:
    """Print serial port chip info to stdout and exit.

    Calls ``port_control.chip_info()`` directly -- the underlying
    function uses pyserial's ``comports()`` and works without any
    config or open Serial object.

    The page is the one ``/port.chip`` shows, flattened to plain text
    because stdout output is intended for piping to grep/awk/jq, not for
    terminal rendering.  Run termapy interactively and use
    ``/port.chip`` if you want colored output.

    Exits with status 0 if at least one port matched and was printed,
    or 1 if the named port wasn't found or no ports are connected.
    """
    from termapy import port_control
    from termapy.markup import markup_to_plain
    from termapy.plugins import RecordView

    report = port_control.chip_info(args.info, current_port="", source=source)
    if report.problem is not None:
        print(report.problem[0])
        sys.exit(1)
    for line in RecordView(report.items).lines():
        print(markup_to_plain(line))
    sys.exit(0)


# ─────────────────────────────────────────────────────────────────────────────
# --ports: one-line-per-port table
# ─────────────────────────────────────────────────────────────────────────────


# Fallback row width when stdout isn't a TTY (piped to a file or
# through grep).  A table's drop-cascade shouldn't kick in for piped
# output -- scripts expect all columns -- so we give it enough budget
# that natural widths always win.
_PIPED_ROW_WIDTH = 200


def _table_row_width() -> int:
    """Return the row budget for a table a flag prints (--ports, --alive).

    Uses the real terminal width when stdout is a TTY so the
    drop-cascade can hide less-important columns (for --ports: speed,
    chip, vid_pid, in that priority order) when the user's terminal is
    narrower than the natural row width.  Falls back to a wide
    budget when output is piped -- scripts expect every column.
    """
    if sys.stdout.isatty():
        return shutil.get_terminal_size((80, 24)).columns
    return _PIPED_ROW_WIDTH


def _ports_matching(pattern: str, facts_list: list[ChipFacts]) -> list[ChipFacts]:
    """The ports any of whose fields match ``pattern``: a grep across each row.

    Read by ``matching.matches``, the one reader of a list filter: plain text
    is contained (``FTDI``, ``Microchip``, a serial number, ``COM4``), a
    wildcard makes a glob anchored at both ends, and case is ignored.  The
    fields are the ``--json`` record's, so what the filter sees is what a
    script would.  A simulator port (``DEMO``) named exactly is shown too:
    it is not enumerated, and CI lists it without hardware.
    """
    from termapy import port_control
    from termapy.matching import matches
    from termapy.port_format import facts_to_json_record

    matched = [
        fact
        for fact in facts_list
        if any(
            isinstance(value, str) and matches(value, pattern)
            for value in facts_to_json_record(fact).values()
        )
    ]
    synthetic = port_control.synthetic_facts_for_reserved(pattern.upper())
    return [*matched, synthetic] if synthetic is not None else matched


def port_rows_styled(lines: list[str], facts_list: list[ChipFacts]) -> list[list[Piece]]:
    """``format_table``'s lines as pieces, each row's port name in its state color.

    The table is the header, the rule, then one row per port in
    ``facts_list``'s order, each starting with the port's name; that
    name takes ``port_control.port_state_style``.  The text is unchanged.
    """
    from termapy.plugins.views import Piece
    from termapy.port_control import port_state_style

    styled: list[list[Piece]] = [[Piece(line)] for line in lines[:2]]
    for line, fact in zip(lines[2:], facts_list, strict=True):
        name = fact.device or ""
        color = port_state_style(fact.session)
        if color and name and line.startswith(name):
            styled.append([Piece(name, style=color), Piece(line[len(name) :])])
        else:
            styled.append([Piece(line)])
    return styled


def run_ports(args: argparse.Namespace, *, source: PortSource | None = None) -> None:
    """List serial ports one line per port and exit.

    With ``args.ports == "*"`` (the argparse ``const`` when the flag
    is given bare), lists every port.  Any other value is a pattern
    matched against every field of each row (``_ports_matching``):
    ``--ports FTDI``, ``--ports Microchip``, a serial number.

    Output is the picker table by default (PORT / MFG / DESCRIPTION /
    CHIP / SPEED / VID:PID / SN / DRIVER), or a JSON array when
    ``--json`` is given.  Row width adapts to the real terminal when
    run interactively so low-priority columns drop before the row
    wraps.  When piped, a wide budget is used so scripts see every
    column.

    Exits 0 if at least one row was shown, 1 if nothing matched.
    """
    from termapy import port_control
    from termapy.port_format import format_table

    # The table has no in_use column; only --json surfaces it.  So probe
    # (fast=False) only for --json -- non-invasive via lsof on POSIX, and
    # a hardened opt-in open on Windows.  Plain --ports never opens a port.
    # It does show LOCATION and DRIVER though, so it asks for enrichment --
    # sysfs / registry reads, no port opened.
    all_facts = port_control._gather_all_chip_facts(
        fast=not getattr(args, "json", False), enrich=True, source=source, sessions=True
    )
    # On stderr, so --json stays one JSON document.
    note = port_control.ports_note()
    if note:
        print(f"termapy: ports {note}", file=sys.stderr)

    facts_list = all_facts
    if args.ports and args.ports != "*":
        facts_list = _ports_matching(args.ports, all_facts)
        if not facts_list and not args.json:
            print(f"No port matching {args.ports!r}", file=sys.stderr)
            sys.exit(1)

    if args.json:
        from termapy.port_format import facts_to_json_record

        records = [facts_to_json_record(fact) for fact in facts_list]
        print(json.dumps(records, indent=2))
        sys.exit(0 if records else 1)

    lines = format_table(facts_list, _table_row_width())
    if facts_list and sys.stdout.isatty() and not getattr(args, "no_color", False):
        print_styled(port_rows_styled(lines, facts_list))
    else:
        for line in lines:
            print(line)

    # format_table returns a single "(no ports found)" line for empty
    # input; treat that as failure for script-friendly exit codes.
    if not facts_list:
        sys.exit(1)
    sys.exit(0)


# ─────────────────────────────────────────────────────────────────────────────
# --watch: poll 2 Hz, print changes
# ─────────────────────────────────────────────────────────────────────────────


# 200 ms = 5 Hz polling.  Below the ~250 ms human perceptual threshold
# for plug events, so the response feels instant.  Watch uses the
# fast-gather path (skips _check_in_use, which costs ~250 ms per port
# on Windows) so the poll loop doesn't scale linearly with port count.
# CPU during a watch session is ~3% on a typical laptop; tightening
# below 100 ms doesn't help because comports() itself is ~5 ms and the
# perceived gain is zero for human consumers.
_WATCH_INTERVAL_S = 0.2

# Column widths for --watch log lines, so rows line up column-for-column
# across the whole run (a log shouldn't re-layout mid-stream).  The
# text columns -- port, mfg, description, chip -- are floors that each
# watch session grows to its fleet's longest cell (``_watch_widths``):
# until 2026-10-31 a chip name longer than its fixed width (the Pico and
# Espressif rows) pushed the rest of its row out of line.
#
# Column 1 is a single-char event marker: ``+`` added, ``-`` removed,
# ``~`` changed, blank for baseline and open/close transitions.  The
# state column (``open``/``closed``) carries the open/close signal on
# its own line -- no verb needed.
_WATCH_WIDTHS = {
    "marker": 1,  # '+' / '-' / '~' / ' '
    # "port" is NOT here: device-name length is the one platform-shaped
    # column (COMxxx on Windows vs /dev/cu.Bluetooth-Incoming-Port on
    # macOS), so its width is computed per watch session by
    # ``_watch_port_width`` -- a platform floor grown to the initial
    # fleet's longest name -- and threaded through the formatters.
    "state": 6,  # closed / open (matches pyserial's is_open)
    "mfg": 9,  # floor: Microchip, Espressif, Parallels, SparkFun, ...
    "description": 18,  # floor: "USB Serial Device" = 17
    "chip": 32,  # floor: "FTDI FT230X / FT231X / FT234XD" = 30
    "speed": 10,  # "Full-Speed" / "High-Speed"
    "vid_pid": 9,  # 0403:6001
    "sn": 20,  # "020026702RYN040952" = 18
}


def _watch_port_width(devices: Iterable[str], floor: int | None = None) -> int:
    """Port-column width for one watch session.

    A platform floor -- 6 on Windows (``COMxxx``) and 32 on POSIX
    (``/dev/cu.Bluetooth-Incoming-Port`` is 31) -- grown to the longest
    device name in the fleet.  ``run_watch`` re-evaluates this every
    poll and keeps the running max (grow-only), so a longer late
    arrival bumps the column once and every FUTURE row aligns; rows
    printed before the bump keep their old offsets -- watch is an
    append-only log, and a printed row can never be re-rendered.
    Never shrinks, so no flapping.

    Args:
        devices: Device names present at watch start.
        floor: Minimum width; defaults from the platform.

    Returns:
        Column width in characters.
    """
    if floor is None:
        floor = 6 if sys.platform == "win32" else 32
    return max([floor] + [len(device) for device in devices])


# The watch columns whose width follows the fleet, beside the port.
_WATCH_GROWN = ("mfg", "description", "chip")


def _watch_widths(
    fleet: Iterable[ChipFacts], start: dict[str, int] | None = None
) -> dict[str, int]:
    """Column widths for one watch session, grown to fit ``fleet``.

    The port column grows as ``_watch_port_width`` describes, and the
    mfg, description and chip columns the same way, from their
    ``_WATCH_WIDTHS`` floors: grow-only, so a longer late arrival bumps
    a column once and every later row aligns.

    Args:
        fleet: The ``ChipFacts`` present now.
        start: The session's widths so far; None at watch start.

    Returns:
        A width for every column, ``port`` included.
    """
    fleet = list(fleet)
    widths = dict(start) if start else dict(_WATCH_WIDTHS)
    widths["port"] = _watch_port_width(
        [facts.device or "-" for facts in fleet], floor=widths.get("port")
    )
    cells = [_watch_cells(facts) for facts in fleet]
    for column in _WATCH_GROWN:
        widths[column] = max([widths[column]] + [len(cell[column]) for cell in cells])
    return widths


def run_usb(args: argparse.Namespace, *, source: UsbSource | None = None) -> None:
    """Print the whole USB tree to stdout and exit.

    Every hub, device and interface as physically connected, with the
    nodes that carry a serial port marked.  This is the counterpart to
    ``--ports``, which shows only serial ports and deliberately keeps it
    that way -- a listing that included keyboards would stop being a
    port listing.

    ``--json`` emits the same tree as nested records instead.  At a
    terminal the serial-port rows are colored as ``/port.usb`` colors
    them; piped, or with ``--no-color``, the text is plain.

    Exits 0 when at least one device was found, 1 when the bus is empty
    or the platform has no backend.

    Args:
        args: Parsed CLI namespace; ``json`` and ``no_color`` are read.
        source: Callable returning UsbRecords, used instead of
            enumerating.  Nothing on the command line sets it.
    """
    from termapy.usb_tree import (
        UnsupportedPlatform,
        gather_usb_tree,
        render_tree,
        render_tree_pieces,
        to_json_records,
    )

    try:
        roots = gather_usb_tree(source=source)
    except UnsupportedPlatform as exc:
        print(f"termapy: {exc}", file=sys.stderr)
        sys.exit(1)

    if getattr(args, "json", False):
        print(json.dumps(to_json_records(roots), indent=2))
        sys.exit(0 if roots else 1)

    if sys.stdout.isatty() and not getattr(args, "no_color", False):
        print_styled(render_tree_pieces(roots))
    else:
        for line in render_tree(roots):
            print(line)
    sys.exit(0 if roots else 1)


def print_styled(lines: Iterable[list[Piece]], console: Console | None = None) -> None:
    """Print lines of pieces in their styles to a terminal.

    Rich is imported here, not at the top: a print-and-exit flag loads it
    only when its output is a terminal it colors, so piped output stays as
    light as before (registry ``flag-paths-light``).  Rich honors
    ``NO_COLOR`` (dropping color, keeping bold) and an older Windows
    console by itself.

    Args:
        lines: One list of pieces per line (``usb_tree.render_tree_pieces``).
        console: Where to print; a new stdout console when None.
    """
    from rich.console import Console

    from termapy.plugins.views import pieces_markup

    console = Console(soft_wrap=True) if console is None else console
    for pieces in lines:
        # No highlighter: only the pieces' own styles, never Rich's guesses
        # (it would bold every number in a hub path).
        console.print(pieces_markup(pieces), highlight=False)


def run_watch(args: argparse.Namespace, *, source: PortSource | None = None) -> None:
    """Monitor serial ports and print changes as log lines.  Ctrl+C to exit.

    Output is a uniform log: every line begins with ``[HH:MM:SS]`` and
    a one-char event marker, followed by the picker column schema
    (port, state, mfg, description, chip, speed, vid_pid, sn).

    Event markers::

        ' '  baseline snapshot or open/close transition (state column
             carries the change)
        '+'  port appeared (new plug-in); followed on the next line by
             a blank-marker state row showing the details
        '-'  port disappeared (unplug); sparse line -- no state row
             follows because the port's data is gone
        '~'  serial number or VID:PID changed on an existing port;
             full state row on the same line

    Returns via ``sys.exit(0)`` on KeyboardInterrupt so the process
    ends cleanly without a traceback.  Output width is ~140 cols and
    does not fit to the terminal; pipe to a file or use ``less -S``
    on narrow terminals.
    """
    from termapy import port_control

    # In-use monitoring is the point of --watch (seeing a port get claimed
    # by, e.g., an MCP server with no visible terminal).  On POSIX the probe
    # is non-invasive (lsof) and safe to run every tick, so populate in_use
    # and the state column works.  On Windows the only probe is an actual
    # open, which at 5 Hz would strobe DTR on every auto-reset board -- so
    # there we stay fast (presence + identity only; state column shows '-').
    watch_fast = sys.platform == "win32"

    banner_ts = datetime.now().strftime("%H:%M:%S")
    # ``device`` is Optional on ChipFacts (dataclass default), but every
    # gather path sets it -- pyserial's ListPortInfo.device, the loopback
    # record, the demo fleet.  Filtering keeps the snapshot keys ``str`` so
    # the sorts below can't hit None < str, and drops nothing real: a port
    # with no device name has nothing to key, display, or diff on.
    initial = {
        f.device: f
        for f in port_control._gather_all_chip_facts(fast=watch_fast, source=source)
        if f.device is not None
    }
    note = " (in-use not shown on Windows)" if watch_fast else ""
    print(f"[{banner_ts}] monitoring {len(initial)} port(s); Ctrl+C to exit{note}")
    widths = _watch_widths(initial.values())
    for device in sorted(initial):
        print(_format_state_line(" ", initial[device], widths))

    previous = initial
    try:
        while True:
            time.sleep(_WATCH_INTERVAL_S)
            current = {
                f.device: f
                for f in port_control._gather_all_chip_facts(fast=watch_fast, source=source)
                if f.device is not None
            }
            # Grow-only: a longer cell than anything seen so far bumps
            # its column so every FUTURE row aligns.  The one visible
            # seam at the growth moment beats the alternative -- that
            # port's rows overflowing forever.  Never shrinks, so no
            # flapping.
            widths = _watch_widths(current.values(), start=widths)
            _emit_diff(previous, current, widths)
            previous = current
    except KeyboardInterrupt:
        print()  # clean newline after ^C
        sys.exit(0)


def _watch_cells(facts: ChipFacts) -> dict[str, str]:
    """The text of each watch column for one port, before padding.

    The one place a cell's text is decided, so ``_watch_widths`` measures
    exactly what ``_format_state_line`` prints.
    """
    from termapy.usb import mfg as _mfg_alias

    description = (facts.description or "-").strip() or "-"
    # Strip trailing "(COMx)" clutter that pyserial sometimes appends.
    device = facts.device or "-"
    if description.endswith(f"({device})"):
        description = description[: -(len(device) + 3)].rstrip() or "-"
    return {
        "port": device,
        "state": _state_of(facts),
        "mfg": _mfg_alias(facts.manufacturer) or "-",
        "description": description,
        "chip": facts.model if facts.model and facts.model != "unknown" else "-",
        "speed": _speed_of(facts),
        "vid_pid": facts.vid_pid if facts.vid_pid and ":" in facts.vid_pid else "-",
        "sn": facts.serial or "-",
    }


# The columns of a state line, left to right, after the marker.
_WATCH_COLUMNS = ("port", "state", "mfg", "description", "chip", "speed", "vid_pid", "sn")


def _format_state_line(marker: str, facts: ChipFacts, widths: dict[str, int]) -> str:
    """Format a full ``[time] <marker> <port> <state> <chip data...>`` line.

    Used for baseline rows, open/close transitions, post-``+`` detail
    rows, and ``~`` change rows.  ``marker`` should be a single char:
    ``' '`` (no event), ``'+'``, ``'-'``, or ``'~'``.  ``widths`` is the
    session's column widths from ``_watch_widths``.
    """
    ts = datetime.now().strftime("%H:%M:%S")
    cells = _watch_cells(facts)
    padded = "  ".join(f"{cells[column]:<{widths[column]}}" for column in _WATCH_COLUMNS)
    return f"[{ts}] {marker:<{widths['marker']}}  {padded}"


def _format_marker_line(marker: str, device: str, widths: dict[str, int]) -> str:
    """Format a sparse ``[time] <marker>  <port>`` line.

    Used for ``-`` removals and the first line of a two-line ``+``
    sequence.  Only the timestamp, marker, and port name are known.
    """
    ts = datetime.now().strftime("%H:%M:%S")
    return f"[{ts}] {marker:<{widths['marker']}}  {device:<{widths['port']}}"


def _state_of(facts: ChipFacts) -> str:
    """Return ``open`` / ``closed`` / ``-`` for the state column.

    Matches pyserial's ``serial.Serial.is_open`` terminology.  ``open``
    means some process (possibly termapy itself) has the port open;
    ``-`` means the in-use state wasn't gathered (fast-gather path used
    by ``--watch`` skips ``_check_in_use`` so the poll loop stays fast).
    """
    if facts.in_use is None:
        return "-"
    return "open" if facts.in_use.startswith("yes") else "closed"


def _speed_of(facts: ChipFacts) -> str:
    """Return ``Full-Speed`` / ``High-Speed`` / ``-`` for the speed column."""
    if facts.usb_speed:
        if "Full-Speed" in facts.usb_speed:
            return "Full-Speed"
        if "High-Speed" in facts.usb_speed:
            return "High-Speed"
    return "-"


def _emit_diff(
    previous: dict[str, ChipFacts],
    current: dict[str, ChipFacts],
    widths: dict[str, int],
) -> None:
    """Print log lines for changes between two snapshots.

    Emits four kinds of event:
      - Port added (present in current, not in previous): two lines --
        a sparse ``+`` marker line, then a blank-marker state row.
      - Port removed (present in previous, not in current): one sparse
        ``-`` marker line (no state row -- port data is gone).
      - Open/close transition (``is_open`` flipped): one blank-marker
        state row; the state column shows the new value.
      - Serial-number or VID:PID changed (rare; chip re-EEPROM'd while
        plugged in): one ``~`` state row with the new data.
    """
    for device, facts in sorted(current.items()):
        if device not in previous:
            print(_format_marker_line("+", device, widths))
            print(_format_state_line(" ", facts, widths))

    for device in sorted(previous):
        if device not in current:
            print(_format_marker_line("-", device, widths))

    for device in sorted(set(previous) & set(current)):
        old, new = previous[device], current[device]
        if _state_of(old) != _state_of(new):
            print(_format_state_line(" ", new, widths))
        if (old.serial or "") != (new.serial or "") or ((old.vid_pid or "") != (new.vid_pid or "")):
            print(_format_state_line("~", new, widths))


# ─────────────────────────────────────────────────────────────────────────────
# --chips: dump USB_SERIAL_CHIPS table
# ─────────────────────────────────────────────────────────────────────────────


def run_chips(args: argparse.Namespace) -> None:
    """Dump the USB-serial chip lookup table to stdout.

    An optional name pattern narrows it to the chips any column of which
    matches (VID:PID, model, speed), through the one filter ``/port.chip.list`` uses
    (``port_format.find_chips``); the bare flag's ``*`` matches them
    all.  ``--json`` produces an array of chip records instead of the
    column-aligned table.

    Exits 0 even on no matches; a zero-match filter is a legitimate
    query, not an error.
    """
    from termapy.port_format import find_chips

    rows = find_chips((args.chips or "").strip())

    if args.json:
        records = [
            {
                "vid": vid,
                "pid": pid,
                "vid_pid": f"{vid:04X}:{pid:04X}",
                "model": info.model,
                "speed": info.speed,
                "max_baud": info.max_baud,
            }
            for vid, pid, info in rows
        ]
        print(json.dumps(records, indent=2))
        sys.exit(0)

    if not rows:
        print(f"No chips match '{args.chips}'", file=sys.stderr)
        sys.exit(0)

    model_w = max(len(info.model) for _, _, info in rows)
    baud_w = max(len(f"{info.max_baud:,}") for _, _, info in rows)

    header = f"{'VID:PID':9}  {'CHIP MODEL':{model_w}}  {'SPEED':5}  {'MAX BAUD':>{baud_w}}"
    print(header)
    print("-" * len(header))
    for vid, pid, info in rows:
        print(
            f"{vid:04X}:{pid:04X}  {info.model:{model_w}}  "
            f"{info.speed:5}  {info.max_baud:>{baud_w},}"
        )
    print(f"Count={len(rows)}")
    sys.exit(0)


# ─────────────────────────────────────────────────────────────────────────────
# --check: config JSON validation
# ─────────────────────────────────────────────────────────────────────────────


def run_check(args: argparse.Namespace) -> None:
    """Validate config and print JSON result to stdout (no TUI).

    Read-only -- does not migrate or write to disk.
    """
    from termapy.config import expand_env_cfg, validate_config
    from termapy.config_resolve import find_config
    from termapy.defaults import DEFAULT_CFG

    # Resolve config
    if args.config:
        config_path = args.config
    else:
        found, _ = find_config()
        if not found:
            print(
                "termapy: no config found. Use --cfg-dir or specify a config.",
                file=sys.stderr,
            )
            sys.exit(1)
        config_path = found

    try:
        with open(config_path) as f:
            cfg = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        result = {"status": "error", "message": str(e)}
        print(json.dumps(result, indent=2))
        sys.exit(1)

    # Backfill defaults in memory only (no disk write, no migration)
    for key, val in DEFAULT_CFG.items():
        if key not in cfg:
            cfg[key] = val

    # A templated boolean is read with the vocabulary here as at load, so
    # --check neither warns "got str" on one nor passes a value a load
    # would refuse.
    expand_warnings: list[str] = []
    try:
        expand_env_cfg(cfg, expand_warnings)
    except ValueError as e:
        result = {"status": "error", "message": str(e)}
        print(json.dumps(result, indent=2))
        sys.exit(1)

    warnings = expand_warnings + validate_config(cfg)
    if warnings:
        result = {"status": "warn", "warnings": warnings}
    else:
        result = {"status": "ok"}
    print(json.dumps(result, indent=2))
    sys.exit(0)


# ─────────────────────────────────────────────────────────────────────────────
# --new: create a config from one line, then run it
# ─────────────────────────────────────────────────────────────────────────────


def _print_sessions(crumbs: Iterable[Crumb]) -> None:
    """Print running termapys as ``/app.alive`` shows them: one table, ``breadcrumbs``'."""
    from termapy import breadcrumbs
    from termapy.plugins.views import TableView

    records = tuple(breadcrumbs.session_record(crumb) for crumb in crumbs)
    view = TableView(breadcrumbs.SESSIONS_TITLE, "session", breadcrumbs.session_columns(), records)
    for text, _color in view.lines(_table_row_width()):
        print(text)


def run_alive() -> NoReturn:
    """``--alive``: list the termapys running on this machine, as ``/app.alive`` does, and exit 0.

    A running termapy is found by the breadcrumb it leaves, so one started
    before breadcrumbs existed is not seen.
    """
    from termapy import breadcrumbs

    running = breadcrumbs.read_running()
    if not running:
        print("No termapy is running.")
    else:
        _print_sessions(running)
    sys.exit(0)


def run_kill(args: argparse.Namespace, *, confirm: Callable[[str], bool]) -> None:
    """``--kill``: stop the termapys a target picks, or, bare, list them as ``--alive`` does.

    Bare ``--kill`` lists them and changes nothing.  ``--kill all``, a PID, a
    port (``COM3``) or a config name lists the ones it picks, asks, and stops
    them (``breadcrumbs.stop_sessions``: each is asked to leave, and one
    still running after the grace period is named with the command that
    ends it, and left running).  A running
    termapy is found by the
    breadcrumb it leaves, so one started before breadcrumbs existed is not
    seen.  Exits 0 when nothing picked is left running, including when
    nothing was running; 1 when the target picks nothing, the answer is no,
    or a process would not stop.

    ``timeout=DUR`` beside the target sets how long to wait for the
    sessions to leave, as on ``/app.kill`` (default ``STOP_GRACE_S``).

    Args:
        args: Parsed CLI namespace; ``kill`` is the words after the flag,
            ``[]`` when bare.
        confirm: Asks the yes/no question (``confirm_on_stdin`` at a terminal).
    """
    from termapy import breadcrumbs
    from termapy.scripting import parse_duration

    words = list(args.kill)
    timeout = breadcrumbs.STOP_GRACE_S
    for word in [word for word in words if word.casefold().startswith("timeout=")]:
        words.remove(word)
        try:
            timeout = parse_duration(word.partition("=")[2])
        except ValueError:
            print(f"termapy: Invalid timeout: {word.partition('=')[2]}", file=sys.stderr)
            sys.exit(1)
    if len(words) > 1:
        print(f"termapy: --kill takes one target, got {' '.join(words)}", file=sys.stderr)
        sys.exit(1)
    target = words[0] if words else ""
    if not target:
        run_alive()
    if target.casefold() == "me":
        print(
            "termapy: --kill me means the session it is typed in, and a command line is "
            "not a session: type app.kill me in the session you mean",
            file=sys.stderr,
        )
        sys.exit(1)
    running = breadcrumbs.read_running()
    if not running:
        print("No termapy is running.")
        sys.exit(0)
    breadcrumbs.prune_stale()
    picked = breadcrumbs.select(running, target)
    if not picked:
        print(
            f"termapy: no running termapy matches {target}{breadcrumbs.suggest(running, target)}",
            file=sys.stderr,
        )
        sys.exit(1)
    _print_sessions(picked)
    noun = "process" if len(picked) == 1 else "processes"
    if not confirm(f"Stop {len(picked)} termapy {noun}?"):
        print("Nothing stopped.")
        sys.exit(1)
    print(f"Asking {len(picked)} termapy {noun} to leave...")
    outcome = breadcrumbs.stop_sessions(picked, grace_s=timeout)
    for line in outcome.stopped_lines():
        print(line)
    for line in outcome.refused_lines():
        print(f"termapy: {line}", file=sys.stderr)
    sys.exit(1 if outcome.refused else 0)


def assume_yes(message: str) -> bool:
    """The answer under ``-y`` / ``--yes``: yes, shown as if typed, so a log says what was assumed."""
    print(f"  {message} [y/N] y (--yes)")
    return True


def confirm_on_stdin(message: str) -> bool:
    """Ask ``message`` on stdin with a ``[y/N]`` default of No.

    The CLI's one yes/no prompt: ``--new``'s overwrite question and the
    REPL's ``CLITerminal._confirm`` both ask through it.  A confirmation
    prompt tests for yes; it does not parse a boolean (the exemption named
    in bool-one-vocabulary).  Any vocabulary yes confirms; a bare Enter, an
    unrecognized word, end of input and Ctrl+C all keep the default of No.
    """
    from termapy.scripting import parse_bool

    try:
        answer = input(f"  {message} [y/N] ").strip()
    except (EOFError, KeyboardInterrupt):
        return False
    try:
        return parse_bool(answer, default=False)
    except ValueError:
        return False


def run_new(
    tokens: list[str],
    *,
    confirm: Callable[[str], bool],
    usage_error: Callable[[str], NoReturn],
) -> Path:
    """Create the config ``--new NAME PORT [SETTING ...]`` describes; return its path.

    The one flag here that does not exit on success: ``main()`` goes on to
    load the new config in whichever frontend was asked for, so the CLI,
    TUI, VT100, web and MCP modes all start from it.  The arguments are read
    as ``/cfg.new`` reads them (``config.new_config_from_args``: the port is
    required and the config connects on load) and written by the same core
    as the TUI's New Config wizard (``config.create_config``).  Unlike
    ``/cfg.new``, which writes where ``/cfg.list`` looks, ``--new`` starts a
    project: a new ``./termapy_cfg`` when no config directory is set
    (``config.new_cfg_root``).  Status lines go to stderr, because stdout
    may be ``--check``'s JSON or ``--mcp``'s protocol.

    Args:
        tokens: ``NAME`` then the settings, as argparse collected them.
        confirm: Asks the overwrite question; True overwrites the ``.cfg``.
        usage_error: Reports a bad argument and exits 2 (``parser.error``).

    Returns:
        The new ``.cfg`` path.
    """
    from termapy.config import (
        ConfigExistsError,
        create_config,
        new_cfg_root,
        new_config_from_args,
    )

    name, *settings = tokens
    try:
        cfg = new_config_from_args(name, " ".join(settings))
    except ValueError as e:
        usage_error(f"--new: {e}")
    path = new_cfg_root() / name / f"{name}.cfg"
    try:
        try:
            create_config(path, cfg)
            verb = "Created"
        except ConfigExistsError as e:
            if not confirm(e.question):
                print(f"termapy: not changed: {e.path}", file=sys.stderr)
                sys.exit(1)
            create_config(path, cfg, overwrite=True)
            verb = "Overwrote"
    except (ValueError, RuntimeError, OSError) as e:
        print(f"termapy: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"termapy: {verb} {path}", file=sys.stderr)
    return path


def run_quick(
    target: str,
    settings: list[str],
    *,
    usage_error: Callable[[str], NoReturn],
    source: PortSource | None = None,
) -> Path:
    """Open ``termapy PORT [SETTING ...]``: the port's quick config, made or updated; return its path.

    ``main()`` calls this for a positional that names no config, so a
    config always wins the name.  The positional is a port when connecting
    would find it now (``port_control.port_present``: a device name, a USB
    serial number, a reserved port, a URL) or when a quick session opened
    it before, so an unplugged adapter's config still opens.  A device name
    is kept as the system spells it (``com3`` is ``COM3``), anything else
    as typed.  The config is ``config.quick_config``'s; like ``--board``,
    this does not exit on success, and its status line goes to stderr.
    Anything else exits 1, naming the configs and ports it most likely
    meant.

    Args:
        target: The positional, as typed.
        settings: The words after it.
        usage_error: Reports a bad setting and exits 2 (``parser.error``).
        source: A substitute port list, as ``resolve_port`` takes it.

    Returns:
        The quick config's ``.cfg`` path.
    """
    from termapy import port_control
    from termapy.config import config_names, quick_config, quick_config_path
    from termapy.matching import closest, did_you_mean

    present = port_control.present_ports(source=source)
    port = next((name for name in present if port_control.same_port(name, target)), target)
    if not port_control.port_present(port, source=source) and not quick_config_path(port).is_file():
        suggestions = closest(target, config_names() + present)
        print(
            f"termapy: Unknown config or port: {target}{did_you_mean(suggestions)}", file=sys.stderr
        )
        sys.exit(1)
    try:
        path, verb = quick_config(port, " ".join(settings))
    except ValueError as e:
        usage_error(str(e))
    except (RuntimeError, OSError) as e:
        print(f"termapy: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"termapy: {verb} {path}", file=sys.stderr)
    return path


def run_board(name: str, *, usage_error: Callable[[str], NoReturn]) -> Path:
    """Find or create the starter config ``--board BOARD`` names; return its path.

    Like ``--new``, it does not exit on success: ``main()`` loads the config
    in whichever frontend was asked for.  The config lives where ``--new``
    puts one (``config.new_cfg_root``), named for the board.  The first run
    creates it through ``boards.create_board_config``, the core behind
    ``/board.new``, connecting by the attached board's serial number; every
    later run reuses it untouched, so the flag is also the everyday way to
    start the board.  A config under another name that already connects to
    the attached board is started instead of making a second one.  Status
    lines go to stderr.

    Args:
        name: The board, as ``boards.BOARDS`` names it.
        usage_error: Reports a bad argument and exits 2 (``parser.error``).

    Returns:
        The ``.cfg`` path.
    """
    from termapy.boards import AlreadyConfigured, create_board_config, find_board
    from termapy.config import new_cfg_root

    try:
        board = find_board(name)
    except ValueError as e:
        usage_error(f"--board: {e}")
    root = new_cfg_root()
    path = root / board.name / f"{board.name}.cfg"
    if path.is_file():
        print(f"termapy: Using {path}", file=sys.stderr)
        return path
    try:
        created = create_board_config(board, root)
    except AlreadyConfigured as e:
        print(f"termapy: Using {e.path}: {e}", file=sys.stderr)
        return e.path
    except (LookupError, ValueError, RuntimeError, OSError) as e:
        print(f"termapy: {e}", file=sys.stderr)
        sys.exit(1)
    print(
        f"termapy: Created {created.path}: {board.title} on {created.device}, "
        f"serial number {created.port}",
        file=sys.stderr,
    )
    return created.path
