"""Print-and-exit handlers for termapy's CLI flags.

Every function here is the implementation of one ``termapy --<flag>``
command.  Each one writes plain text to stdout, never touches Textual
or the TUI infrastructure, and exits the process with status 0 on
success or 1 on a named failure.

Architectural constraint: **this module must not import Textual, Rich,
prompt-toolkit, or any other UI framework.**  The entry point
(``termapy.entry``) dispatches to these helpers *before* any TUI-or-
REPL imports fire, so ``termapy --ports`` stays fast and small even on
a machine where importing Textual costs ~300 ms.

Available flags:

    --info [PORT]      full multi-line chip dump (reuses /port.info)
    --ports [PORT]     one-line-per-port picker-style table
    --watch            poll 2 Hz, print events on presence/in-use/SN change
    --chips [FILTER]   dump the USB_SERIAL_CHIPS lookup table
    --check            validate config, print JSON status

Signatures are intentionally uniform: ``_run_*(args) -> None`` where
``args`` is the ``argparse.Namespace``.  Each calls ``sys.exit(...)``
directly rather than returning a status code so callers never have to
remember to propagate the exit.
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


# ─────────────────────────────────────────────────────────────────────────────
# --info: the existing verbose per-port dump
# ─────────────────────────────────────────────────────────────────────────────


def run_info(args: argparse.Namespace) -> None:
    """Print serial port chip info to stdout and exit.

    Calls ``port_control.chip_info()`` directly -- the underlying
    function uses pyserial's ``comports()`` and works without any
    config or open Serial object.

    Color hints from the internal Result are dropped because stdout
    output is intended for piping to grep/awk/jq, not for terminal
    rendering.  Run termapy interactively and use ``/port.chip`` if
    you want colored output.

    Exits with status 0 if at least one port matched and was printed,
    or 1 if the named port wasn't found, no ports are connected, or
    any other error condition was reported.
    """
    from termapy import port_control

    msgs, _ = port_control.chip_info(args.info, current_port="")
    error = False
    for text, color in msgs:
        print(text)
        if color in ("red", "yellow") and (
            "No port" in text
            or "No current port" in text
            or "No serial ports" in text
        ):
            error = True
    sys.exit(1 if error else 0)


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


def run_ports(args: argparse.Namespace) -> None:
    """List serial ports one line per port and exit.

    With ``args.ports == "*"`` (the argparse ``const`` when the flag
    is given bare), lists every port.  Otherwise filters to the one
    matching device name.  Output matches the picker table (PORT /
    MFG / DESCRIPTION / CHIP / SPEED / VID:PID / SN).

    Row width adapts to the real terminal when run interactively so
    low-priority columns drop before the row wraps.  When piped, a
    wide budget is used so scripts see every column.

    Exits 0 if at least one row was shown, 1 if nothing matched.
    """
    from termapy import port_control
    from termapy.port_format import format_table

    all_facts = port_control._gather_all_chip_facts()

    if args.ports and args.ports != "*":
        matches = [f for f in all_facts if f.device == args.ports]
        if not matches:
            print(f"No port matching {args.ports!r}", file=sys.stderr)
            sys.exit(1)
        facts_list = matches
    else:
        facts_list = all_facts

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


_WATCH_INTERVAL_S = 0.5

# Fixed-width columns for --watch log lines.  Tuned from the chip
# table's longest plausible values so rows line up column-for-column
# across the whole run (a log shouldn't re-layout mid-stream).  Real
# data that exceeds these widths overflows the column rather than
# forcing every prior row to be re-rendered.
#
# Column 1 is a single-char event marker: ``+`` added, ``-`` removed,
# ``~`` changed, blank for baseline and open/close transitions.  The
# state column (``open``/``closed``) carries the open/close signal on
# its own line -- no verb needed.
_WATCH_WIDTHS = {
    "marker":      1,   # '+' / '-' / '~' / ' '
    "port":        6,   # COMxxx
    "state":       6,   # closed / open (matches pyserial's is_open)
    "mfg":         9,   # Microchip, Espressif, Parallels, SparkFun, ...
    "description": 18,  # "USB Serial Device" = 17
    "chip":       32,   # "FTDI FT230X / FT231X / FT234XD" = 30
    "speed":      10,   # "Full-Speed" / "High-Speed"
    "vid_pid":     9,   # 0403:6001
    "sn":         20,   # "020026702RYN040952" = 18
}


def run_watch(args: argparse.Namespace) -> None:
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

    banner_ts = datetime.now().strftime("%H:%M:%S")
    initial = {f.device: f for f in port_control._gather_all_chip_facts()}
    print(
        f"[{banner_ts}] monitoring {len(initial)} port(s); Ctrl+C to exit"
    )
    for device in sorted(initial):
        print(_format_state_line(" ", initial[device]))

    previous = initial
    try:
        while True:
            time.sleep(_WATCH_INTERVAL_S)
            current = {
                f.device: f for f in port_control._gather_all_chip_facts()
            }
            _emit_diff(previous, current)
            previous = current
    except KeyboardInterrupt:
        print()  # clean newline after ^C
        sys.exit(0)


def _format_state_line(marker: str, facts) -> str:
    """Format a full ``[time] <marker> <port> <state> <chip data...>`` line.

    Used for baseline rows, open/close transitions, post-``+`` detail
    rows, and ``~`` change rows.  ``marker`` should be a single char:
    ``' '`` (no event), ``'+'``, ``'-'``, or ``'~'``.
    """
    from termapy.usb_mfg import mfg as _mfg_alias

    ts = datetime.now().strftime("%H:%M:%S")
    state = _state_of(facts)
    mfg = (_mfg_alias(facts.manufacturer) or "-")
    description = (facts.description or "-").strip() or "-"
    # Strip trailing "(COMx)" clutter that pyserial sometimes appends.
    device = facts.device or "-"
    if description.endswith(f"({device})"):
        description = description[: -(len(device) + 3)].rstrip() or "-"
    chip = facts.model if facts.model and facts.model != "unknown" else "-"
    speed = _speed_of(facts)
    vid_pid = facts.vid_pid if facts.vid_pid and ":" in facts.vid_pid else "-"
    sn = facts.serial or "-"
    w = _WATCH_WIDTHS
    return (
        f"[{ts}] "
        f"{marker:<{w['marker']}}  "
        f"{device:<{w['port']}}  "
        f"{state:<{w['state']}}  "
        f"{mfg:<{w['mfg']}}  "
        f"{description:<{w['description']}}  "
        f"{chip:<{w['chip']}}  "
        f"{speed:<{w['speed']}}  "
        f"{vid_pid:<{w['vid_pid']}}  "
        f"{sn:<{w['sn']}}"
    )


def _format_marker_line(marker: str, device: str) -> str:
    """Format a sparse ``[time] <marker>  <port>`` line.

    Used for ``-`` removals and the first line of a two-line ``+``
    sequence.  Only the timestamp, marker, and port name are known.
    """
    ts = datetime.now().strftime("%H:%M:%S")
    w = _WATCH_WIDTHS
    return f"[{ts}] {marker:<{w['marker']}}  {device:<{w['port']}}"


def _state_of(facts) -> str:
    """Return ``open`` or ``closed`` for the state column.

    Matches pyserial's ``serial.Serial.is_open`` terminology.  ``open``
    means some process (possibly termapy itself) has the port open.
    """
    in_use = facts.in_use or ""
    return "open" if in_use.startswith("yes") else "closed"


def _speed_of(facts) -> str:
    """Return ``Full-Speed`` / ``High-Speed`` / ``-`` for the speed column."""
    if facts.usb_speed:
        if "Full-Speed" in facts.usb_speed:
            return "Full-Speed"
        if "High-Speed" in facts.usb_speed:
            return "High-Speed"
    return "-"


def _emit_diff(previous: dict, current: dict) -> None:
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
            print(_format_marker_line("+", device))
            print(_format_state_line(" ", facts))

    for device in sorted(previous):
        if device not in current:
            print(_format_marker_line("-", device))

    for device in sorted(set(previous) & set(current)):
        old, new = previous[device], current[device]
        if _state_of(old) != _state_of(new):
            print(_format_state_line(" ", new))
        if (old.serial or "") != (new.serial or "") or (
            (old.vid_pid or "") != (new.vid_pid or "")
        ):
            print(_format_state_line("~", new))


# ─────────────────────────────────────────────────────────────────────────────
# --chips: dump USB_SERIAL_CHIPS table
# ─────────────────────────────────────────────────────────────────────────────


def run_chips(args: argparse.Namespace) -> None:
    """Dump the USB-serial chip lookup table to stdout.

    Optional filter narrows to rows whose chip model contains the
    filter substring (case-insensitive).  Mirrors the REPL
    ``/port.chip.list`` command.

    Exits 0 even on no matches; a zero-match filter is a legitimate
    query, not an error.
    """
    from termapy.usb_serial_chips import USB_SERIAL_CHIPS

    needle = (args.chips or "").strip().lower()
    if needle == "*":
        needle = ""

    rows = []
    for (vid, pid), info in USB_SERIAL_CHIPS.items():
        if needle and needle not in info.model.lower():
            continue
        rows.append((vid, pid, info))
    rows.sort(key=lambda row: (row[0], row[1]))

    if not rows:
        print(f"No chips match '{args.chips}'", file=sys.stderr)
        sys.exit(0)

    model_w = max(len(info.model) for _, _, info in rows)
    baud_w = max(len(f"{info.max_baud:,}") for _, _, info in rows)

    header = (
        f"{'VID:PID':9}  {'CHIP MODEL':{model_w}}  "
        f"{'SPEED':5}  {'MAX BAUD':>{baud_w}}"
    )
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
    from termapy.config import validate_config
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

    warnings = validate_config(cfg)
    if warnings:
        result = {"status": "warn", "warnings": warnings}
    else:
        result = {"status": "ok"}
    print(json.dumps(result, indent=2))
    sys.exit(0)
