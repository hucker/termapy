"""Built-in plugin: serial port control - list, connect, configure, signals."""

from __future__ import annotations

import textwrap
from typing import TYPE_CHECKING

from termapy import port_control
from termapy.defaults import STANDARD_BAUD_RATES, cfg_value
from termapy.help_dynamic import compose, port_status
from termapy.legacy import make_forwarder
from termapy.plugins import CapabilitySet, CmdResult, Column, Command, TextLine, UsageError
from termapy.port_format import find_chips

if TYPE_CHECKING:
    from termapy.plugins import PluginContext


# The standard rates as /help port.baud_rate lists them, from the one table.
_STANDARD_RATES_HELP = textwrap.fill(
    ", ".join(str(rate) for rate in STANDARD_BAUD_RATES),
    width=62,
    initial_indent="  ",
    subsequent_indent="  ",
)


def _apply(ctx: PluginContext, result: port_control.Result) -> None:
    """Output messages and apply side effects from a port_control function.

    Every line is text on the ``(text, color)`` ``ctx.io.output`` path,
    whatever brackets it holds; the label/value pages are record answers
    instead (``_page``).
    """
    msgs, effects = result
    for text, color in msgs:
        if color:
            ctx.io.output(text, color)
        else:
            ctx.io.output(text)
    if effects:
        ctx.serial.apply_port_effects(effects)


def _answer(ctx: PluginContext, result: port_control.Result, value: str) -> CmdResult:
    """A setter's answer: its refusal as the failure, else its lines and ``value``.

    A refusal (``port_control.problem``) applies nothing and prints nothing
    here; dispatch prints the failure once.
    """
    refusal = port_control.problem(result)
    if refusal is not None:
        return CmdResult.fail(msg=refusal)
    _apply(ctx, result)
    return CmdResult.ok(value=value)


def _page(ctx: PluginContext, report: port_control.Report) -> CmdResult:
    """A port page as a record answer, or its problem line and an empty value.

    ``data`` is the record and ``value`` the port name (registry
    ``record-one-format``): the page itself rides once, as data or as the
    rows dispatch prints.
    """
    if report.problem is not None:
        text, color = report.problem
        ctx.io.output(text, color or "")
        return CmdResult.ok(value="")
    return CmdResult.record(report.data, report.items, value=report.value)


# ── Handlers ─────────────────────────────────────────────────────────────────
# A handler's docstring is its developer help: /help.dev <command> prints it
# and developer /search indexes it, so keep it true (registry handler-docstring).


def _handler_root(ctx: PluginContext, args: str) -> CmdResult:
    """``port <name>``: set the config's port name without connecting; bare never arrives (the picker or the list runs)."""
    name = args.strip()
    if name:
        ctx.serial.update_port(name)
        return CmdResult.ok(value=name)
    # Bare /port never arrives: the dispatcher opens the port picker in
    # the TUI (picker="port", matching the left title-bar button) and
    # runs /port.list elsewhere (bare_sub="list").
    raise UsageError()


def _handler_help(ctx: PluginContext, args: str) -> CmdResult:
    """Same as /help port."""
    return ctx.internal.dispatch("help port")


def _handler_list(ctx: PluginContext, args: str) -> CmdResult:
    """List the serial ports: a table for a person, records for a structured consumer; the value is the device names."""
    # Structured consumer: records only, no table.  ``value`` is the
    # comma-joined device names (the useful scalar for an agent or a
    # capture) rather than the rendered table text the human branch
    # returns -- the table is exactly the big prose ``wants_data``
    # exists to skip.
    if ctx.wants_data:
        records = port_control.list_port_records()
        return CmdResult.ok(
            value=",".join(record["device"] or "" for record in records),
            data=records,
        )
    result = port_control.list_ports()
    _apply(ctx, result)
    return CmdResult.ok(value=_joined_value(result))


def _handler_usb(ctx: PluginContext, args: str) -> CmdResult:
    """Render the USB device tree, or its records for a structured consumer; fails on an unsupported platform."""
    from termapy.usb_tree import (
        UnsupportedPlatform,
        gather_usb_tree,
        render_tree,
        to_json_records,
    )

    try:
        roots = gather_usb_tree()
    except UnsupportedPlatform as exc:
        return CmdResult.fail(msg=str(exc))
    # Structured consumer: the nested record tree (same shape as
    # ``--usb --json``), skipping the box-drawing render entirely.
    if ctx.wants_data:
        return CmdResult.ok(value="", data=to_json_records(roots))
    lines = render_tree(roots)
    for line in lines:
        ctx.io.output(line)
    return CmdResult.ok(value="\n".join(lines))


def _handler_connect(ctx: PluginContext, args: str) -> CmdResult:
    """Open the named port (or the config's), applying any baud, frame, line-ending and echo settings first; URL ports need network egress."""
    port, baud, mode, line_ending, echo, custom, err = port_control.parse_open_args(args)
    if err:
        return CmdResult.fail(msg=err)
    # Network egress: a serial-over-URL "port" (socket://, rfc2217://)
    # opens an outbound TCP connection.  Refuse under the MCP sandbox so
    # an automated peer can't reach arbitrary hosts; physical ports are
    # unaffected.  Checked before any cfg mutation.
    if port and "://" in port and not ctx.capabilities.network_egress:
        msg = (
            "Network ports (socket://, rfc2217://) are disabled under the MCP "
            "sandbox. Set TERMAPY_MCP_NET_EGRESS=1 in the server's shell to "
            "allow them."
        )
        return CmdResult.fail(msg=msg)
    # Apply all optional settings to config before connecting so the
    # port opens with the requested settings.  Each branch is a no-op
    # when the user didn't supply that field.  custom=true asked for a rate
    # off the standard list, so the session's config says so too.
    if custom:
        ctx.serial.apply_port_effects({"cfg_update": {"custom_baud": True}})
    if baud is not None:
        ctx.serial.apply_port_effects({"cfg_update": {"baud_rate": baud}})
    if mode is not None:
        parity, byte_size, stop_bits = mode
        ctx.serial.apply_port_effects(
            {
                "cfg_update": {
                    "parity": parity,
                    "byte_size": byte_size,
                    "stop_bits": stop_bits,
                }
            }
        )
    if line_ending is not None:
        ctx.serial.apply_port_effects({"cfg_update": {"eol": line_ending}})
    if echo is not None:
        ctx.serial.apply_port_effects({"cfg_update": {"echo": echo}})
    # The port not opening (bad name, in use, ...) raises its reason, which
    # is this command's failure.  A /port.connect issued while already
    # connected only re-applies the cfg settings above; that is not a failure.
    try:
        ctx.serial.connect(port)
    except OSError as e:
        return CmdResult.fail(msg=str(e))
    return CmdResult.ok(value=port or "")


def _handler_disconnect(ctx: PluginContext, args: str) -> CmdResult:
    """Close the port; the value is the name of the port that was open."""
    # Snapshot the configured port name BEFORE disconnect so scripts
    # capturing the value know which port just went down.  ctx.cfg
    # still holds the name after disconnect, but capturing first
    # protects against any future cfg-mutation on disconnect.
    last_port = ctx.cfg["serial"]["port"] or ""
    ctx.serial.disconnect()
    return CmdResult.ok(value=last_port)


def _handler_info(ctx: PluginContext, args: str) -> CmdResult:
    """Show port info for the currently-connected port.

    Dumps configured serial parameters, USB chip identification, and
    live hardware signal lines (DTR/RTS/CTS/DSR/RI/CD).  This is the
    richest port report termapy can produce, but it only works on the
    port termapy is actually connected to because the live signals
    come from an open Serial object.

    For chip info on any other port (or all ports at once), use
    /port.chip which has a compatible argument shape.
    """
    arg = args.strip()
    if arg:
        return CmdResult.fail(
            msg=(
                f"{ctx.prefix}port.info only reports on the connected port. "
                f"For chip info on {arg!r}, use {ctx.prefix}port.chip {arg}"
            )
        )
    return _page(ctx, port_control.port_info(ctx.cfg, ctx.internal.port()))


def _handler_mode(ctx: PluginContext, args: str) -> CmdResult:
    """Query (bare) or set the frame as one token such as 8N1, with an optional baud rate."""
    result = port_control.set_mode(ctx.internal.port(), ctx.cfg, args)
    return _answer(ctx, result, _joined_value(result))


def _handler_flow(ctx: PluginContext, args: str) -> CmdResult:
    """Query (bare) or set flow control: none, rtscts, xonxoff or manual."""
    result = port_control.get_set_flow(ctx.internal.port(), ctx.cfg, args)
    return _answer(ctx, result, _joined_value(result))


def _handler_break(ctx: PluginContext, args: str) -> CmdResult:
    """Send a break condition on the open port; the value is the confirmation line."""
    result = port_control.send_break(ctx.internal.port(), args)
    # Break is a pure side-effect.  Return the "Break sent" line so silent
    # scripts get something deterministic instead of None.
    return _answer(ctx, result, _joined_value(result))


def _handler_chip(ctx: PluginContext, args: str) -> CmdResult:
    """Identify the USB-serial chip of the current port, a named port, or every port."""
    current = ctx.cfg["serial"]["port"] or ""
    connected = current if ctx.serial.is_connected() else ""
    return _page(ctx, port_control.chip_info(args, current, connected))


def _handler_chip_list(ctx: PluginContext, args: str) -> CmdResult:
    """Dump the USB-serial chip lookup table.

    Prints one row per known (VID:PID, chip model, speed, max_baud).
    An optional name pattern narrows it to the chips whose model or
    speed matches (``port_format.find_chips``).  Typical use:
    ``/port.chip.list ftdi``, ``/port.chip.list high``.

    ``CmdResult.value`` is ``"Count=<N>"`` where ``<N>`` is the number
    of chips matching the filter (or the total when unfiltered), so a
    script can capture the match count with
    ``$(COUNT) <- /port.chip.list ftdi``.  None matching is an empty
    table and ``Count=0``.
    """
    rows = find_chips(args.strip())

    records = [
        {
            "vid_pid": f"{vid:04X}:{pid:04X}",
            "model": info.model,
            "speed": info.speed,
            "max_baud": info.max_baud,
        }
        for vid, pid, info in rows
    ]
    return CmdResult.table(
        "USB-serial chips",
        "chip",
        _CHIP_COLUMNS,
        records,
        value=f"Count={len(records)}",
    )


# /port.chip.list columns: the records keep max_baud an int for a data consumer.
_CHIP_COLUMNS = (
    Column("VID:PID", lambda record: record["vid_pid"]),
    Column("CHIP MODEL", lambda record: record["model"]),
    Column("SPEED", lambda record: record["speed"]),
    Column("MAX BAUD", lambda record: f"{record['max_baud']:,}", right=True),
)


def _make_chip_field_handler(field: str):
    """Build a handler for /port.chip.<field>.

    The handler takes an optional port name, or ``*`` for every port as
    a table (registry ``list-one-format``).  With no argument it queries
    the current port from cfg["serial"]["port"].
    """
    label = port_control.CHIP_FIELD_LABELS[field]
    columns = (
        Column("DEVICE", lambda record: record["device"] or "?"),
        Column(
            label.upper(), lambda record: "n/a" if record[field] is None else str(record[field])
        ),
    )

    def _handler(ctx: PluginContext, args: str) -> CmdResult:
        current = ctx.cfg["serial"]["port"] or ""
        connected = current if ctx.serial.is_connected() else ""
        if args.strip() == "*":
            records = port_control.chip_field_records(field, connected)
            if not records:
                ctx.io.output("No serial ports found", "yellow")
                return CmdResult.ok(value="")
            lines = "\n".join(
                f"{record['device']}: {columns[1].cell(record)}" for record in records
            )
            # The data maps each device to its value, so a consumer looks a port up.
            by_device = {str(record["device"]): record[field] for record in records}
            return CmdResult.table(
                f"Chip {label}", "port", columns, records, value=lines, data=by_device
            )
        result = port_control.chip_field(field, args, current, connected)
        _apply(ctx, result)
        # Single-port single-field call: return value via CmdResult.value
        # so silent mode is useful for scripting.
        msgs, _ = result
        if len(msgs) == 1:
            return CmdResult.ok(value=msgs[0][0])
        return CmdResult.ok(value=_joined_value(result))

    # The /help.dev text of each command built here (registry handler-docstring).
    _handler.__doc__ = f"Report the {field} of the current port's USB-serial chip, of a named port, or of every port (*) as a table."
    return _handler


def _read_value(result: port_control.Result) -> str:
    """Extract the read value from a single-message non-error result.

    Strips "(disconnected)" annotation if present so scripts get clean
    values (e.g. "115200" not "115200 (disconnected)").
    """
    msgs, _ = result
    if len(msgs) == 1 and msgs[0][1] not in ("red", "yellow"):
        text = msgs[0][0]
        if text.endswith(" (disconnected)"):
            text = text[: -len(" (disconnected)")]
        return text
    return ""


def _joined_value(result: port_control.Result) -> str:
    """Join non-error messages with newlines for multi-line scripting values.

    ``_read_value`` only handles the single-line case; this helper
    covers multi-row outputs where the scripting value is the same text
    the user sees, minus error/warning lines.
    """
    msgs, _ = result
    return "\n".join(text for text, color in msgs if color not in ("red", "yellow"))


def _make_prop_handler(key: str):
    def _handler(ctx: PluginContext, args: str) -> CmdResult:
        result = port_control.get_set_prop(ctx.internal.port(), ctx.cfg, key, args)
        # Only return value on read (no args); set returns prose not useful as value
        return _answer(ctx, result, _read_value(result) if not args.strip() else "")

    # The /help.dev text of each command built here (registry handler-docstring).
    _handler.__doc__ = f"Query (bare) or set the port's {key}, on the open port and in the config."
    return _handler


def _handler_baud_rate(ctx: PluginContext, args: str) -> CmdResult:
    """Query (bare) or set the port's baud rate; bare says ``(custom)`` while ``custom_baud`` is on, a set takes ``custom=true``.

    Bare is a one-line record, so ``/help port.baud_rate``'s CURRENT STATE
    shows the mode as the command does, while ``value`` stays the number a
    script captures and ``data`` carries both.
    """
    result = port_control.get_set_prop(ctx.internal.port(), ctx.cfg, "baud_rate", args)
    if args.strip():
        return _answer(ctx, result, "")
    refusal = port_control.problem(result)
    if refusal is not None:
        return CmdResult.fail(msg=refusal)
    rate = _read_value(result)
    custom = bool(cfg_value(ctx.cfg, "serial.custom_baud"))
    connected = ctx.serial.is_connected()
    shown = rate + (" (custom)" if custom else "") + ("" if connected else " (disconnected)")
    data = {"baud_rate": int(rate) if rate.isdigit() else rate, "custom_baud": custom}
    return CmdResult.record(data, [TextLine(shown)], value=rate)


def _make_hw_handler(line: str):
    def _handler(ctx: PluginContext, args: str) -> CmdResult:
        result = port_control.get_set_hw_line(ctx.internal.port(), line, args)
        return _answer(ctx, result, _read_value(result) if not args.strip() else "")

    # The /help.dev text of each command built here (registry handler-docstring).
    _handler.__doc__ = f"Query (bare) or set the {line.upper()} output line of the open port."
    return _handler


def _make_signal_handler(signal: str):
    def _handler(ctx: PluginContext, args: str) -> CmdResult:
        # Signals are always read-only; extract value from the single msg
        result = port_control.read_signal(ctx.internal.port(), signal, args)
        return _answer(ctx, result, _read_value(result))

    # The /help.dev text of each command built here (registry handler-docstring).
    _handler.__doc__ = f"Read the {signal.upper()} input line of the open port."
    return _handler


# ── Dynamic long_help helpers ────────────────────────────────────────────────

# A family's page gets a summary no single command gives.  A setting or a
# signal line declares Command.state instead, and /help shows what it answers.


def _port_info_long_help(ctx: PluginContext) -> str:
    return compose(
        port_status(ctx),
        "Dumps configured serial parameters, USB chip identification,\n"
        "and live hardware signal lines (DTR/RTS/CTS/DSR/RI/CD). Works\n"
        "only on the currently-connected port because the live signals\n"
        "come from an open Serial object. For chip info on another\n"
        "port, use {prefix}port.chip which has a compatible argument shape.",
    )


# The /port.chip.<field> commands, one per chip fact: the help line each
# shows.  The handler and the config declaration are the same for all, so
# a new fact is a row here (and its ChipFacts field), never a new entry.
_CHIP_FIELD_HELP = {
    "device": "Show port device name.",
    "description": "Show OS-reported port description.",
    "manufacturer": "Show USB manufacturer string.",
    "product": "Show USB product string.",
    "serial": "Show USB serial number of the cable.",
    "location": "Show USB topology location.",
    "interface": "Show interface label (multi-channel chips).",
    "vid_pid": "Show USB VID:PID hex string.",
    "model": "Show identified chip model from the lookup table.",
    "usb_speed": "Show theoretical USB speed class for the chip.",
    "negotiated": "Show OS-reported negotiated USB link speed (Linux only).",
    "driver": "Show the OS driver name (Linux + Windows).",
    "latency_timer": "Show FTDI latency timer value (Linux + FTDI only).",
    "max_baud": "Show maximum baud rate the chip supports.",
    "permissions": "Show read/write permission status for the device.",
    "in_use": "Show whether another process has the port open.",
}

# ── COMMAND (must be at end of file) ──────────────────────────────────────────

COMMAND = Command(
    group="terminal",
    name="port",
    args="{name}",
    help="Serial port tools: connect, disconnect, list, configure.",
    intro=(
        "Connect to, disconnect from, list, or configure the serial port. Subcommands"
        " cover live signals (DTR/RTS/CTS/DSR/RI/CD), USB chip identification, and "
        "the mode settings."
    ),
    long_help=port_status,
    handler=_handler_root,
    cfg={"serial.port": "set for the session, then reconnects"},
    picker="port",
    bare_sub="list",
    sub_commands={
        "list": Command(
            help="List available serial ports.",
            handler=_handler_list,
            shows=True,
        ),
        "usb": Command(
            help="Show the whole USB tree, with serial ports marked.",
            long_help=(
                "Draw every USB hub, device and interface as it is\n"
                "physically connected, and mark the nodes that carry a\n"
                "serial port.\n"
                "\n"
                "Use it to answer questions {prefix}port.list cannot: which hub\n"
                "is this adapter on, is that COM port one function of a\n"
                "larger device, did the cable move.  Non-serial devices\n"
                "are shown here and deliberately never in {prefix}port.list.\n"
                "\n"
                "A path reads bus-port, one dot per hub tier: 1-8.4 is\n"
                "bus 1, root-hub port 8, then port 4 of the hub there.\n"
                "A ':N' child is interface N of the device above it, not\n"
                "another tier.\n"
                "\n"
                "Windows and Linux only.\n"
            ),
            handler=_handler_usb,
        ),
        "help": Command(
            help="Show {prefix}port help.",
            handler=_handler_help,
            shows=True,
        ),
        "connect": Command(
            args="{name} {baud} {mode} {custom=<bool>}",
            help="Connect to the serial port (e.g. {prefix}port.connect COM3 9600 N81).",
            long_help=(
                "Connect to the serial port, optionally setting baud rate, "
                "frame mode, line ending, or echo in one stroke.\n"
                "\n"
                "Syntax: {prefix}port.connect [name|port=name] [baud] [mode] [cr|lf|crlf] [echo|noecho] [custom=true]\n"
                "\n"
                "  name         Port device or USB serial number. A bare name\n"
                '               MUST come first. Falls back to cfg["port"].\n'
                "  port=name    Explicit port; may go anywhere. Use it for a\n"
                "               purely-numeric serial number (e.g. port=0001),\n"
                "               which a bare token would read as a baud rate.\n"
                "  baud         Baud rate (e.g. 9600, 115200), a standard one\n"
                "               unless custom=true ({prefix}help port.baud_rate\n"
                "               lists them).\n"
                "  mode         Frame; either field order -- 8N1 or N81, 7E2\n"
                "               or E72. Parity: N/E/O/M/S.\n"
                '  cr|lf|crlf   Line ending. Stored as cfg["eol"].\n'
                '  echo|noecho  Toggle cfg["echo"].\n'
                "  custom=true  Allow a rate off the standard list (300 or\n"
                "               more), so a mistyped one is refused instead.\n"
                "\n"
                "Tokens after name are order-independent. Mutations are\n"
                "session-only -- edit the config file to persist.\n"
                "\n"
                "Example: {prefix}port.connect COM3 9600 N81 crlf echo\n"
                "         {prefix}port.connect COM3 250000 custom=true"
            ),
            handler=_handler_connect,
            cfg={
                "serial.custom_baud": "set true for the session by custom=true",
                "serial.baud_rate": "set for the session before opening",
                "serial.parity": "set for the session before opening",
                "serial.byte_size": "set for the session before opening",
                "serial.stop_bits": "set for the session before opening",
                "eol": "set for the session before opening",
                "echo": "set for the session before opening",
            },
        ),
        "open": Command(
            help="Legacy alias for {prefix}port.connect.",
            handler=make_forwarder("port.open", "port.connect"),
            hidden=True,
        ),
        "mode": Command(
            args="{baud} {mode} {custom=<bool>}",
            help="Show or set serial mode (e.g. {prefix}port.mode 9600 N81).",
            long_help=(
                "Combined form for baud + mode triple. Accepts '{prefix}port.mode\n"
                "115200 N81' or a subset. Individual subcommands\n"
                "({prefix}port.baud_rate, {prefix}port.byte_size, {prefix}port.parity,\n"
                "{prefix}port.stop_bits) exist too.\n"
                "\n"
                "The baud rate must be a standard one ({prefix}help\n"
                "port.baud_rate lists them); add custom=true for a rate off\n"
                "the list (300 or more), so a mistyped one is refused:\n"
                "{prefix}port.mode 250000 8N1 custom=true"
            ),
            handler=_handler_mode,
            cfg={
                "serial.custom_baud": "set true for the session by custom=true",
                "serial.baud_rate": "set for the session; bare shows it",
                "serial.byte_size": "set for the session; bare shows it",
                "serial.parity": "set for the session; bare shows it",
                "serial.stop_bits": "set for the session; bare shows it",
            },
            state=True,
        ),
        "disconnect": Command(
            help="Disconnect from the serial port.",
            long_help=port_status,
            handler=_handler_disconnect,
            cfg={"serial.port": "the port it closes"},
        ),
        "close": Command(
            help="Legacy alias for {prefix}port.disconnect.",
            handler=make_forwarder("port.close", "port.disconnect"),
            hidden=True,
        ),
        "info": Command(
            help="Print status, params, chip, and live signals for the connected port.",
            long_help=_port_info_long_help,
            handler=_handler_info,
            cfg={
                "serial.port": "the port it reports",
                "serial.baud_rate": "on the page",
                "serial.byte_size": "on the page",
                "serial.parity": "on the page",
                "serial.stop_bits": "on the page",
                "serial.flow_control": "on the page",
                "encoding": "on the page",
                "file_xfer_root": "on the page when set",
            },
        ),
        "baud_rate": Command(
            args="{value} {custom=<bool>}",
            help="Show or set baud rate: a standard one, or any from 300 with custom=true.",
            long_help=(
                "Sets the open port's baud rate for the session.  It must be\n"
                "one of the standard rates:\n"
                "\n"
                f"{_STANDARD_RATES_HELP}\n"
                "\n"
                "A rate off the list needs custom=true (300 or more), so a\n"
                "mistyped one is refused instead of opened:\n"
                "\n"
                "  {prefix}port.baud_rate 9600\n"
                "  {prefix}port.baud_rate 9460 custom=true"
            ),
            handler=_handler_baud_rate,
            cfg={
                "serial.baud_rate": "set for the session; bare shows it",
                "serial.custom_baud": "set true for the session by custom=true; bare says (custom)",
            },
            state=True,
        ),
        "byte_size": Command(
            args="{value}",
            help="Show or set data bits.",
            handler=_make_prop_handler("byte_size"),
            cfg={"serial.byte_size": "set for the session; bare shows it"},
            state=True,
        ),
        "parity": Command(
            args="{value}",
            help="Show or set parity.",
            long_help=(
                "Parity bit mode. Values:\n"
                "  N - none (default for most modern devices)\n"
                "  E - even\n"
                "  O - odd\n"
                "  M - mark (always 1)\n"
                "  S - space (always 0)"
            ),
            handler=_make_prop_handler("parity"),
            cfg={"serial.parity": "set for the session; bare shows it"},
            state=True,
        ),
        "stop_bits": Command(
            args="{value}",
            help="Show or set stop bits.",
            handler=_make_prop_handler("stop_bits"),
            cfg={"serial.stop_bits": "set for the session; bare shows it"},
            state=True,
        ),
        "flow_control": Command(
            args="{mode}",
            help="Show or set flow control (none/rtscts/xonxoff/manual).",
            long_help=(
                "Serial flow-control mode. Values:\n"
                "  none    - no flow control (default; most modern devices)\n"
                "  rtscts  - hardware handshake using the RTS/CTS lines\n"
                "  xonxoff - software handshake using the 0x11/0x13 bytes\n"
                "  manual  - leave DTR/RTS under plugin control\n"
                "Use rtscts only when both ends agree -- a mismatch will\n"
                "hang transmission silently."
            ),
            handler=_handler_flow,
            cfg={"serial.flow_control": "set for the session; bare shows it"},
            state=True,
        ),
        "dtr": Command(
            args="{on|off}",
            help="Show or set DTR line (hardware only).",
            handler=_make_hw_handler("dtr"),
            needs=CapabilitySet.SERIAL_CONNECTED,
            state=True,
        ),
        "rts": Command(
            args="{on|off}",
            help="Show or set RTS line (hardware only).",
            handler=_make_hw_handler("rts"),
            needs=CapabilitySet.SERIAL_CONNECTED,
            state=True,
        ),
        "cts": Command(
            help="Show CTS state (read-only).",
            handler=_make_signal_handler("cts"),
            needs=CapabilitySet.SERIAL_CONNECTED,
            state=True,
        ),
        "dsr": Command(
            help="Show DSR state (read-only).",
            handler=_make_signal_handler("dsr"),
            needs=CapabilitySet.SERIAL_CONNECTED,
            state=True,
        ),
        "ri": Command(
            help="Show Ring Indicator state (read-only).",
            handler=_make_signal_handler("ri"),
            needs=CapabilitySet.SERIAL_CONNECTED,
            state=True,
        ),
        "cd": Command(
            help="Show Carrier Detect state (read-only).",
            handler=_make_signal_handler("cd"),
            needs=CapabilitySet.SERIAL_CONNECTED,
            state=True,
        ),
        "break": Command(
            args="{duration_ms}",
            help="Send a break signal (default 250ms).",
            handler=_handler_break,
            needs=CapabilitySet.SERIAL_CONNECTED,
        ),
        "chip": Command(
            args="{name|*}",
            help="Identify USB-serial chip(s) and report USB speed class.",
            intro=(
                "Identify the USB-serial chip behind a port: its maker, model, USB speed and "
                "driver, as a report or one field at a time."
            ),
            handler=_handler_chip,
            cfg={"serial.port": "the port it identifies by default"},
            sub_commands={
                "list": Command(
                    args="{pattern}",
                    help="Dump the USB-serial chip lookup table.",
                    long_help=(
                        "Show every chip termapy recognizes.  With a pattern,\n"
                        "only chips whose model or speed matches it: plain\n"
                        "text is contained, a wildcard (* ? [) makes a glob,\n"
                        "and case is ignored.\n"
                        "\n"
                        "Examples:\n"
                        "  {prefix}port.chip.list           -- every chip\n"
                        "  {prefix}port.chip.list ftdi      -- FTDI chips only\n"
                        "  {prefix}port.chip.list arduino   -- Arduino boards only\n"
                        "  {prefix}port.chip.list high      -- high-speed chips"
                    ),
                    handler=_handler_chip_list,
                    shows=True,
                ),
                **{
                    field: Command(
                        args="{name|*}",
                        help=help_text,
                        handler=_make_chip_field_handler(field),
                        cfg={"serial.port": "the port it reports by default"},
                    )
                    for field, help_text in _CHIP_FIELD_HELP.items()
                },
            },
        ),
    },
)
