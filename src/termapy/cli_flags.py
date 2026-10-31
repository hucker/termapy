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
    from collections.abc import Callable
    from pathlib import Path
    from typing import NoReturn

    from termapy.port_control import ChipFacts, PortSource


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
# through grep).  The drop-cascade in port_format shouldn't kick in
# for piped output -- scripts expect all columns -- so we give it
# enough budget that natural widths always win.
_PORTS_PIPED_ROW_WIDTH = 200


def _ports_row_width() -> int:
    """Return the row budget for the --ports table.

    Uses the real terminal width when stdout is a TTY so the
    drop-cascade can hide less-important columns (speed, chip,
    vid_pid, in that priority order) when the user's terminal is
    narrower than the natural row width.  Falls back to a wide
    budget when output is piped -- scripts expect every column.
    """
    if sys.stdout.isatty():
        return shutil.get_terminal_size((80, 24)).columns
    return _PORTS_PIPED_ROW_WIDTH


def _filter_facts(args: argparse.Namespace, facts_list: list) -> list:
    """Apply --vid / --pid / --mfg / --sn filters.

    Each filter is optional; any combination AND-s.  ``--vid`` /
    ``--pid`` accept hex (with or without 0x prefix) or decimal.
    ``--mfg`` is a case-insensitive substring match.  ``--sn`` is an
    exact case-insensitive match.
    """
    out = facts_list
    if args.vid is not None:
        needle = _parse_hex_int(args.vid)
        if needle is None:
            print(f"Invalid --vid: {args.vid!r}", file=sys.stderr)
            sys.exit(2)
        out = [f for f in out if _vid_of(f) == needle]
    if args.pid is not None:
        needle = _parse_hex_int(args.pid)
        if needle is None:
            print(f"Invalid --pid: {args.pid!r}", file=sys.stderr)
            sys.exit(2)
        out = [f for f in out if _pid_of(f) == needle]
    if args.mfg:
        m = args.mfg.lower()
        out = [f for f in out if f.manufacturer and m in f.manufacturer.lower()]
    if args.sn:
        s = args.sn.lower()
        out = [f for f in out if f.serial and f.serial.lower() == s]
    return out


def _parse_hex_int(s: str) -> int | None:
    """Parse a VID/PID string as hex.  Accepts ``0x0403``, ``0403``, ``403``."""
    s = s.strip().lower()
    if s.startswith("0x"):
        s = s[2:]
    try:
        return int(s, 16)
    except ValueError:
        return None


def _vid_of(facts) -> int | None:
    from termapy.port_format import split_vid_pid

    return split_vid_pid(facts.vid_pid)[0]


def _pid_of(facts) -> int | None:
    from termapy.port_format import split_vid_pid

    return split_vid_pid(facts.vid_pid)[1]


def run_ports(args: argparse.Namespace, *, source: PortSource | None = None) -> None:
    """List serial ports one line per port and exit.

    With ``args.ports == "*"`` (the argparse ``const`` when the flag
    is given bare), lists every port.  ``args.ports`` as a literal
    name filters to that one device.  Multi-axis filters (``--vid``,
    ``--pid``, ``--mfg``, ``--sn``) AND with the name match.

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
        fast=not getattr(args, "json", False), enrich=True, source=source
    )

    if args.ports and args.ports != "*":
        all_facts = [
            fact
            for fact in all_facts
            if fact.device is not None and port_control.same_port(fact.device, args.ports)
        ]
        # Reserved virtual ports (DEMO, DEMO_FAIL) aren't OS-enumerated
        # but are reachable at runtime; surface a synthetic record when
        # the user names one explicitly so CI can exercise --ports
        # without hardware.
        if not all_facts:
            synthetic = port_control.synthetic_facts_for_reserved(args.ports)
            if synthetic is not None:
                all_facts = [synthetic]
        if not all_facts and not args.json:
            print(f"No port matching {args.ports!r}", file=sys.stderr)
            sys.exit(1)

    facts_list = _filter_facts(args, all_facts)

    if args.json:
        from termapy.port_format import facts_to_json_record

        records = [facts_to_json_record(fact) for fact in facts_list]
        print(json.dumps(records, indent=2))
        sys.exit(0 if records else 1)

    if not facts_list:
        if args.ports and args.ports != "*":
            # Already printed the no-match message above.
            sys.exit(1)
        # Filters narrowed to empty; format_table prints a header still.
    lines = format_table(facts_list, _ports_row_width())
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


def _watch_port_width(devices, floor: int | None = None) -> int:
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


def _watch_widths(fleet, start: dict[str, int] | None = None) -> dict[str, int]:
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


def run_usb(args: argparse.Namespace, *, source=None) -> None:
    """Print the whole USB tree to stdout and exit.

    Every hub, device and interface as physically connected, with the
    nodes that carry a serial port marked.  This is the counterpart to
    ``--ports``, which shows only serial ports and deliberately keeps it
    that way -- a listing that included keyboards would stop being a
    port listing.

    ``--json`` emits the same tree as nested records instead.

    Exits 0 when at least one device was found, 1 when the bus is empty
    or the platform has no backend.

    Args:
        args: Parsed CLI namespace; only ``json`` is read.
        source: Callable returning UsbRecords, used instead of
            enumerating.  Nothing on the command line sets it.
    """
    from termapy.usb_tree import (
        UnsupportedPlatform,
        gather_usb_tree,
        render_tree,
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

    for line in render_tree(roots):
        print(line)
    sys.exit(0 if roots else 1)


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


def _watch_cells(facts) -> dict[str, str]:
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


def _format_state_line(marker: str, facts, widths: dict[str, int]) -> str:
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


def _state_of(facts) -> str:
    """Return ``open`` / ``closed`` / ``-`` for the state column.

    Matches pyserial's ``serial.Serial.is_open`` terminology.  ``open``
    means some process (possibly termapy itself) has the port open;
    ``-`` means the in-use state wasn't gathered (fast-gather path used
    by ``--watch`` skips ``_check_in_use`` so the poll loop stays fast).
    """
    if facts.in_use is None:
        return "-"
    return "open" if facts.in_use.startswith("yes") else "closed"


def _speed_of(facts) -> str:
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

    An optional name pattern narrows it to the chips whose model or
    speed matches, through the one filter ``/port.chip.list`` uses
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


# The columns of the --kill listing.
_KILL_COLUMNS = ("PID", "MODE", "CONFIG", "PORT", "STARTED")


def _crumb_summary(crumb) -> str:
    """One line naming a running termapy: frontend, config and open port."""
    return (
        f"{crumb.frontend}, {crumb.config_name or 'no config'}, {crumb.open_port or 'no port open'}"
    )


def _print_crumbs(crumbs) -> None:
    """Print running termapys as a table, each column as wide as its widest cell."""
    rows = [
        [
            str(crumb.pid),
            crumb.frontend,
            crumb.config_name or "-",
            crumb.open_port or "-",
            crumb.started.replace("T", " "),
        ]
        for crumb in crumbs
    ]
    widths = [
        max([len(header)] + [len(row[i]) for row in rows]) for i, header in enumerate(_KILL_COLUMNS)
    ]
    for cells in [list(_KILL_COLUMNS), ["-" * width for width in widths], *rows]:
        print("  ".join(cell.ljust(width) for cell, width in zip(cells, widths)).rstrip())


def run_kill(args: argparse.Namespace, *, confirm: Callable[[str], bool]) -> None:
    """``--kill``: list the termapys running on this machine, or stop the ones a target picks.

    Bare ``--kill`` lists them and changes nothing.  ``--kill all``, a port
    (``COM3``) or a config name lists the ones it picks, asks, and stops
    them (``breadcrumbs.stop``).  A running termapy is found by the
    breadcrumb it leaves, so one started before breadcrumbs existed is not
    seen.  Exits 0 when nothing picked is left running, including when
    nothing was running; 1 when the target picks nothing, the answer is no,
    or a process would not stop.

    Args:
        args: Parsed CLI namespace; ``kill`` is the target, ``""`` when bare.
        confirm: Asks the yes/no question (``confirm_on_stdin`` at a terminal).
    """
    from termapy import breadcrumbs

    running = breadcrumbs.read_running()
    if not running:
        print("No termapy is running.")
        sys.exit(0)
    target = args.kill
    if not target:
        _print_crumbs(running)
        sys.exit(0)
    breadcrumbs.prune_stale()
    picked = breadcrumbs.select(running, target)
    if not picked:
        from termapy.matching import closest, did_you_mean

        names = {crumb.config_name for crumb in running if crumb.config_name}
        names |= {crumb.open_port for crumb in running if crumb.open_port}
        print(
            f"termapy: no running termapy matches {target}"
            f"{did_you_mean(closest(target, sorted(names)))}",
            file=sys.stderr,
        )
        sys.exit(1)
    _print_crumbs(picked)
    noun = "process" if len(picked) == 1 else "processes"
    if not confirm(f"Stop {len(picked)} termapy {noun}?"):
        print("Nothing stopped.")
        sys.exit(1)
    failed = 0
    for crumb in picked:
        if breadcrumbs.stop(crumb):
            print(f"Stopped {crumb.pid} ({_crumb_summary(crumb)})")
        else:
            failed += 1
            print(f"termapy: could not stop {crumb.pid} ({_crumb_summary(crumb)})", file=sys.stderr)
    sys.exit(1 if failed else 0)


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
