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

    from termapy.port_control import ChipFacts, PortSource


# ─────────────────────────────────────────────────────────────────────────────
# --info: the existing verbose per-port dump
# ─────────────────────────────────────────────────────────────────────────────


def run_info(
    args: argparse.Namespace, *, source: PortSource | None = None
) -> None:
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

    msgs, _ = port_control.chip_info(args.info, current_port="", source=source)
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


def _facts_to_json_record(facts) -> dict:
    """Build a stable JSON record from a ChipFacts.

    Field names are snake_case (matching pyserial convention) and the
    schema is fixed: every record has every field, with ``null`` for
    unknown values, so consumers can rely on the shape.  Numeric
    ``vid``/``pid`` are kept alongside the formatted ``vid_pid`` so
    consumers can do numeric or literal-string filtering.
    """
    # vid_pid on ChipFacts is "0403:6001" (uppercase hex); split it
    # back to integers when present so consumers can do numeric ops.
    vid: int | None = None
    pid: int | None = None
    vp = facts.vid_pid
    if vp and ":" in vp:
        try:
            vid_s, pid_s = vp.split(":", 1)
            vid = int(vid_s, 16)
            pid = int(pid_s, 16)
        except ValueError:
            vid = pid = None
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
    from termapy.usb import mfg as _mfg_alias
    raw_mfg = facts.manufacturer
    return {
        "device": facts.device,
        "manufacturer": _mfg_alias(raw_mfg) or None,
        "manufacturer_raw": raw_mfg,
        "vendor": facts.vendor,
        "description": facts.description,
        "chip": facts.model if facts.model and facts.model != "unknown" else None,
        "speed": _normalize_speed(facts.usb_speed),
        "vid": vid,
        "pid": pid,
        "vid_pid": vp.lower() if vp and ":" in vp else None,
        "serial_number": facts.serial,
        "in_use": (facts.in_use or "").startswith("yes"),
        "driver": facts.driver,
        # Physical bus location, "1-2.3" -- bus, then one hop per hub
        # tier.  Disambiguates two devices with the same VID/PID and
        # serial number, the cheap-clone scenario.  Falls back to the
        # Windows registry's "Hub_#0011.Port_#0003" when the topology
        # can't be read.
        "location": facts.location,
        # bInterfaceNumber, for a device exposing more than one function
        # (a debugger with a CDC port alongside it, or one channel of a
        # multi-port FTDI chip).  null when there is nothing to
        # disambiguate.  Kept separate from location so that field is
        # always just the path.
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
        out = [
            f for f in out
            if f.manufacturer and m in f.manufacturer.lower()
        ]
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
    if facts.vid_pid and ":" in facts.vid_pid:
        try:
            return int(facts.vid_pid.split(":")[0], 16)
        except ValueError:
            return None
    return None


def _pid_of(facts) -> int | None:
    if facts.vid_pid and ":" in facts.vid_pid:
        try:
            return int(facts.vid_pid.split(":")[1], 16)
        except ValueError:
            return None
    return None


def run_ports(
    args: argparse.Namespace, *, source: PortSource | None = None
) -> None:
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
        all_facts = [fact for fact in all_facts if fact.device == args.ports]
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
        records = [_facts_to_json_record(fact) for fact in facts_list]
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


def run_watch(
    args: argparse.Namespace, *, source: PortSource | None = None
) -> None:
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
    print(
        f"[{banner_ts}] monitoring {len(initial)} port(s); Ctrl+C to exit{note}"
    )
    for device in sorted(initial):
        print(_format_state_line(" ", initial[device]))

    previous = initial
    try:
        while True:
            time.sleep(_WATCH_INTERVAL_S)
            current = {
                f.device: f
                for f in port_control._gather_all_chip_facts(
                    fast=watch_fast, source=source
                )
                if f.device is not None
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
    from termapy.usb import mfg as _mfg_alias

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


def _emit_diff(previous: dict[str, ChipFacts], current: dict[str, ChipFacts]) -> None:
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
    ``/port.chip.list`` command.  ``--json`` produces an array of
    chip records instead of the column-aligned table.

    Exits 0 even on no matches; a zero-match filter is a legitimate
    query, not an error.
    """
    from termapy.usb import USB_SERIAL_CHIPS

    needle = (args.chips or "").strip().lower()
    if needle == "*":
        needle = ""

    rows = []
    for (vid, pid), info in USB_SERIAL_CHIPS.items():
        if needle and needle not in info.model.lower():
            continue
        rows.append((vid, pid, info))
    rows.sort(key=lambda row: (row[0], row[1]))

    if args.json:
        records = [
            {
                "vid": vid,
                "pid": pid,
                "vid_pid": f"{vid:04x}:{pid:04x}",
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
