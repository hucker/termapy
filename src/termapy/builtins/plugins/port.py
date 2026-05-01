"""Built-in plugin: serial port control - list, connect, configure, signals."""

from __future__ import annotations

from typing import TYPE_CHECKING

from termapy import port_control, usb_serial_chips
from termapy.help_dynamic import compose, green, port_status, state_line
from termapy.legacy import make_forwarder
from termapy.plugins import CmdResult, Command

if TYPE_CHECKING:
    from termapy.plugins import PluginContext


def _apply(ctx: PluginContext, result: port_control.Result) -> None:
    """Output messages and apply side effects from a port_control function."""
    msgs, effects = result
    for text, color in msgs:
        if color:
            ctx.write(text, color)
        else:
            ctx.write(text)
    if effects:
        ctx.engine.apply_port_effects(effects)


# ── Handlers ────────────────────────────────────────────────────────────────


def _handler_root(ctx: PluginContext, args: str) -> CmdResult:
    name = args.strip()
    if name:
        ctx.engine.update_port(name)
        return CmdResult.ok()
    # Bare /port -- TUI opens the port picker (matches clicking the
    # left title-bar button); CLI shows /help port.
    if ctx.engine.open_picker is not None:
        return ctx.engine.open_picker("port")
    return _handler_help(ctx, args)


def _handler_help(ctx: PluginContext, args: str) -> CmdResult:
    """Same as /help port."""
    from termapy.builtins.plugins.help import _show_command_help

    return _show_command_help(ctx, "port")


def _handler_list(ctx: PluginContext, args: str) -> CmdResult:
    _apply(ctx, port_control.list_ports())
    return CmdResult.ok()


def _handler_connect(ctx: PluginContext, args: str) -> CmdResult:
    port, baud, mode, line_ending, echo, err = port_control.parse_open_args(args)
    if err:
        ctx.write(err, "red")
        return CmdResult.fail(msg=err)
    # Apply all optional settings to config before connecting so the
    # port opens with the requested settings.  Each branch is a no-op
    # when the user didn't supply that field.
    if baud is not None:
        ctx.engine.apply_port_effects({"cfg_update": {"baud_rate": baud}})
    if mode is not None:
        parity, byte_size, stop_bits = mode
        ctx.engine.apply_port_effects(
            {
                "cfg_update": {
                    "parity": parity,
                    "byte_size": byte_size,
                    "stop_bits": stop_bits,
                }
            }
        )
    if line_ending is not None:
        ctx.engine.apply_port_effects(
            {"cfg_update": {"line_ending": line_ending}}
        )
    if echo is not None:
        ctx.engine.apply_port_effects(
            {"cfg_update": {"echo_input": echo}}
        )
    ctx.engine.connect(port)
    return CmdResult.ok()


def _handler_disconnect(ctx: PluginContext, args: str) -> CmdResult:
    ctx.engine.disconnect()
    return CmdResult.ok()


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
                f"/port.info only reports on the connected port. "
                f"For chip info on {arg!r}, use /port.chip {arg}"
            )
        )
    _apply(ctx, port_control.port_info(ctx.cfg, ctx.port()))
    return CmdResult.ok()


def _handler_mode(ctx: PluginContext, args: str) -> CmdResult:
    _apply(ctx, port_control.set_mode(ctx.port(), ctx.cfg, args))
    return CmdResult.ok()


def _handler_flow(ctx: PluginContext, args: str) -> CmdResult:
    _apply(ctx, port_control.get_set_flow(ctx.port(), ctx.cfg, args))
    return CmdResult.ok()


def _handler_break(ctx: PluginContext, args: str) -> CmdResult:
    _apply(ctx, port_control.send_break(ctx.port(), args))
    return CmdResult.ok()


def _handler_chip(ctx: PluginContext, args: str) -> CmdResult:
    current = ctx.cfg.get("port", "") or ""
    connected = current if ctx.is_connected() else ""
    _apply(ctx, port_control.chip_info(args, current, connected))
    return CmdResult.ok()


def _handler_chip_list(ctx: PluginContext, args: str) -> CmdResult:
    """Dump the USB-serial chip lookup table.

    Prints one row per known (VID:PID, chip model, speed, max_baud).
    An optional filter argument narrows the output to rows whose chip
    model contains the filter substring (case-insensitive).  Typical
    use: ``/port.chip.list ftdi``, ``/port.chip.list high``.

    ``CmdResult.value`` is ``"Count=<N>"`` where ``<N>`` is the number
    of chips matching the filter (or the total when unfiltered), so a
    script can capture the match count with
    ``$(COUNT) <- /port.chip.list ftdi``.
    """
    needle = args.strip().lower()
    rows: list[tuple[int, int, usb_serial_chips.ChipInfo]] = []
    for (vid, pid), info in usb_serial_chips.USB_SERIAL_CHIPS.items():
        if needle and needle not in info.model.lower():
            continue
        rows.append((vid, pid, info))
    rows.sort(key=lambda row: (row[0], row[1]))

    if not rows:
        ctx.write(f"No chips match '{args.strip()}'.", "yellow")
        return CmdResult.ok(value="Count=0")

    # Compute column widths from actual data so nothing truncates.
    model_w = max(len(info.model) for _, _, info in rows)
    baud_w = max(len(f"{info.max_baud:,}") for _, _, info in rows)

    header = (
        f"{'VID:PID':9}  {'CHIP MODEL':{model_w}}  "
        f"{'SPEED':5}  {'MAX BAUD':>{baud_w}}"
    )
    ctx.write(header)
    ctx.write("-" * len(header))
    for vid, pid, info in rows:
        ctx.write(
            f"{vid:04X}:{pid:04X}  {info.model:{model_w}}  "
            f"{info.speed:5}  {info.max_baud:>{baud_w},}"
        )
    count_line = f"Count={len(rows)}"
    ctx.write(count_line, "dim")
    return CmdResult.ok(value=count_line)


def _make_chip_field_handler(field: str):
    """Build a handler for /port.chip.<field>.

    The handler takes an optional port name (or ``*`` for all ports).
    With no argument it queries the current port from cfg["port"].
    """

    def _handler(ctx: PluginContext, args: str) -> CmdResult:
        current = ctx.cfg.get("port", "") or ""
        connected = current if ctx.is_connected() else ""
        result = port_control.chip_field(field, args, current, connected)
        _apply(ctx, result)
        # Single-port single-field call: return value via CmdResult.value
        # so .quiet mode is useful for scripting.
        msgs, _ = result
        if len(msgs) == 1:
            return CmdResult.ok(value=msgs[0][0])
        return CmdResult.ok()

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


def _make_prop_handler(key: str):
    def _handler(ctx: PluginContext, args: str) -> CmdResult:
        result = port_control.get_set_prop(ctx.port(), ctx.cfg, key, args)
        _apply(ctx, result)
        # Only return value on read (no args); set returns prose not useful as value
        value = _read_value(result) if not args.strip() else ""
        return CmdResult.ok(value=value)

    return _handler


def _make_hw_handler(line: str):
    def _handler(ctx: PluginContext, args: str) -> CmdResult:
        result = port_control.get_set_hw_line(ctx.port(), line, args)
        _apply(ctx, result)
        value = _read_value(result) if not args.strip() else ""
        return CmdResult.ok(value=value)

    return _handler


def _make_signal_handler(signal: str):
    def _handler(ctx: PluginContext, args: str) -> CmdResult:
        # Signals are always read-only; extract value from the single msg
        result = port_control.read_signal(ctx.port(), signal, args)
        _apply(ctx, result)
        return CmdResult.ok(value=_read_value(result))

    return _handler


# ── Dynamic long_help helpers ────────────────────────────────────────────────

# Single-value subcommands just print "Current <label> = <value>" using the
# config dict as the source of truth. Config holds the mode even when no
# port is open, so these still show something useful while disconnected.


def _cfg_value_long_help(key: str, label: str):
    """Build a callable that renders a state_line for a single cfg key."""

    def _long(ctx: PluginContext) -> str:
        value = ctx.cfg.get(key, "?")
        return state_line(label, value)

    return _long


def _port_root_long_help(ctx: PluginContext) -> str:
    return compose(
        port_status(ctx),
        "Connect to, disconnect from, list, or configure the serial\n"
        "port.  Subcommands cover live signals (DTR/RTS/CTS/DSR/RI/CD),\n"
        "USB chip identification ({prefix}port.chip.*), and the four mode\n"
        "settings baud_rate, byte_size, parity, stop_bits.",
    )


def _port_info_long_help(ctx: PluginContext) -> str:
    return compose(
        port_status(ctx),
        "Dumps configured serial parameters, USB chip identification,\n"
        "and live hardware signal lines (DTR/RTS/CTS/DSR/RI/CD). Works\n"
        "only on the currently-connected port because the live signals\n"
        "come from an open Serial object. For chip info on another\n"
        "port, use {prefix}port.chip which has a compatible argument shape.",
    )


def _port_mode_long_help(ctx: PluginContext) -> str:
    byte = ctx.cfg.get("byte_size", "?")
    par = ctx.cfg.get("parity", "?")
    stop = ctx.cfg.get("stop_bits", "?")
    baud = ctx.cfg.get("baud_rate", "?")
    return compose(
        green(f"Current mode = {baud} {par}{byte}{stop}"),
        "Combined form for baud + mode triple. Accepts '{prefix}port.mode\n"
        "115200 N81' or a subset. Individual subcommands\n"
        "({prefix}port.baud_rate, {prefix}port.byte_size, {prefix}port.parity,\n"
        "{prefix}port.stop_bits) exist too.",
    )


def _port_parity_long_help(ctx: PluginContext) -> str:
    return compose(
        state_line("parity", ctx.cfg.get("parity", "?")),
        "Parity bit mode. Values:\n"
        "  N - none (default for most modern devices)\n"
        "  E - even\n"
        "  O - odd\n"
        "  M - mark (always 1)\n"
        "  S - space (always 0)",
    )


def _port_flow_long_help(ctx: PluginContext) -> str:
    return compose(
        state_line("flow control", ctx.cfg.get("flow_control", "none")),
        "Serial flow-control mode. Values:\n"
        "  none    - no flow control (default; most modern devices)\n"
        "  rtscts  - hardware handshake using the RTS/CTS lines\n"
        "  xonxoff - software handshake using the 0x11/0x13 bytes\n"
        "  manual  - leave DTR/RTS under plugin control\n"
        "Use rtscts only when both ends agree -- a mismatch will\n"
        "hang transmission silently.",
    )


def _port_hw_line_long_help(line: str, direction: str):
    """Build long_help for dtr/rts (set) or cts/dsr/ri/cd (read-only)."""

    def _long(ctx: PluginContext) -> str:
        # ctx.port is a callable returning a port-or-None, and
        # getattr(..., default) can't raise -- there's no real failure
        # mode here to catch.  A simple None-check suffices.
        p = ctx.port()
        value = getattr(p, line, "?") if p is not None else "?"
        if value is None:
            value = "?"
        return state_line(f"{line.upper()} ({direction})", value)

    return _long


# ── COMMAND (must be at end of file) ──────────────────────────────────────────

COMMAND = Command(
    name="port",
    args="{name}",
    help="Serial port tools: connect, disconnect, list, configure.",
    long_help=_port_root_long_help,
    handler=_handler_root,
    sub_commands={
        "list": Command(
            help="List available serial ports.",
            handler=_handler_list,
        ),
        "help": Command(
            help="Show /port help.",
            handler=_handler_help,
        ),
        "connect": Command(
            args="{name} {baud} {mode}",
            help="Connect to the serial port (e.g. {prefix}port.connect COM3 9600 N81).",
            long_help=(
                "Connect to the serial port, optionally setting baud rate, "
                "frame mode, line ending, or echo in one stroke.\n"
                "\n"
                "Syntax: {prefix}port.connect [name] [baud] [mode] [cr|lf|crlf] [echo|noecho]\n"
                "\n"
                "  name         Port device or USB serial number. MUST come\n"
                "               first. Falls back to cfg[\"port\"].\n"
                "  baud         Baud rate (e.g. 9600, 115200).\n"
                "  mode         Frame, e.g. N81 or E72. Parity: N/E/O/M/S.\n"
                "  cr|lf|crlf   Line ending. Stored as cfg[\"line_ending\"].\n"
                "  echo|noecho  Toggle cfg[\"echo_input\"].\n"
                "\n"
                "Tokens after name are order-independent. Mutations are\n"
                "session-only -- edit the config file to persist.\n"
                "\n"
                "Example: {prefix}port.connect COM3 9600 N81 crlf echo"
            ),
            handler=_handler_connect,
        ),
        "open": Command(
            help="Legacy alias for /port.connect.",
            handler=make_forwarder("port.open", "port.connect"),
            hidden=True,
        ),
        "mode": Command(
            args="{baud} {mode}",
            help="Show or set serial mode (e.g. {prefix}port.mode 9600 N81).",
            long_help=_port_mode_long_help,
            handler=_handler_mode,
        ),
        "disconnect": Command(
            help="Disconnect from the serial port.",
            long_help=port_status,
            handler=_handler_disconnect,
        ),
        "close": Command(
            help="Legacy alias for /port.disconnect.",
            handler=make_forwarder("port.close", "port.disconnect"),
            hidden=True,
        ),
        "info": Command(
            help="Show status, params, chip, and live signals for the connected port.",
            long_help=_port_info_long_help,
            handler=_handler_info,
        ),
        "baud_rate": Command(
            args="{value}",
            help="Show or set baud rate.",
            long_help=_cfg_value_long_help("baud_rate", "baud rate"),
            handler=_make_prop_handler("baud_rate"),
        ),
        "byte_size": Command(
            args="{value}",
            help="Show or set data bits.",
            long_help=_cfg_value_long_help("byte_size", "byte size"),
            handler=_make_prop_handler("byte_size"),
        ),
        "parity": Command(
            args="{value}",
            help="Show or set parity.",
            long_help=_port_parity_long_help,
            handler=_make_prop_handler("parity"),
        ),
        "stop_bits": Command(
            args="{value}",
            help="Show or set stop bits.",
            long_help=_cfg_value_long_help("stop_bits", "stop bits"),
            handler=_make_prop_handler("stop_bits"),
        ),
        "flow_control": Command(
            args="{mode}",
            help="Show or set flow control (none/rtscts/xonxoff/manual).",
            long_help=_port_flow_long_help,
            handler=_handler_flow,
        ),
        "dtr": Command(
            args="{0|1}",
            help="Show or set DTR line (hardware only).",
            long_help=_port_hw_line_long_help("dtr", "out"),
            handler=_make_hw_handler("dtr"),
        ),
        "rts": Command(
            args="{0|1}",
            help="Show or set RTS line (hardware only).",
            long_help=_port_hw_line_long_help("rts", "out"),
            handler=_make_hw_handler("rts"),
        ),
        "cts": Command(
            help="Show CTS state (read-only).",
            long_help=_port_hw_line_long_help("cts", "in"),
            handler=_make_signal_handler("cts"),
        ),
        "dsr": Command(
            help="Show DSR state (read-only).",
            long_help=_port_hw_line_long_help("dsr", "in"),
            handler=_make_signal_handler("dsr"),
        ),
        "ri": Command(
            help="Show Ring Indicator state (read-only).",
            long_help=_port_hw_line_long_help("ri", "in"),
            handler=_make_signal_handler("ri"),
        ),
        "cd": Command(
            help="Show Carrier Detect state (read-only).",
            long_help=_port_hw_line_long_help("cd", "in"),
            handler=_make_signal_handler("cd"),
        ),
        "break": Command(
            args="{duration_ms}",
            help="Send a break signal (default 250ms).",
            handler=_handler_break,
        ),
        "chip": Command(
            args="{name|*}",
            help="Identify USB-serial chip(s) and report USB speed class.",
            handler=_handler_chip,
            sub_commands={
                "list": Command(
                    args="{filter}",
                    help="Dump the USB-serial chip lookup table.",
                    long_help=(
                        "Show every chip termapy recognizes.  With a filter\n"
                        "argument, only chips whose model name contains the\n"
                        "filter substring (case-insensitive) are listed.\n"
                        "\n"
                        "Examples:\n"
                        "  {prefix}port.chip.list           -- every chip\n"
                        "  {prefix}port.chip.list ftdi      -- FTDI chips only\n"
                        "  {prefix}port.chip.list arduino   -- Arduino boards only\n"
                        "  {prefix}port.chip.list high      -- high-speed chips"
                    ),
                    handler=_handler_chip_list,
                ),
                "device": Command(
                    args="{name|*}",
                    help="Show port device name.",
                    handler=_make_chip_field_handler("device"),
                ),
                "description": Command(
                    args="{name|*}",
                    help="Show OS-reported port description.",
                    handler=_make_chip_field_handler("description"),
                ),
                "manufacturer": Command(
                    args="{name|*}",
                    help="Show USB manufacturer string.",
                    handler=_make_chip_field_handler("manufacturer"),
                ),
                "product": Command(
                    args="{name|*}",
                    help="Show USB product string.",
                    handler=_make_chip_field_handler("product"),
                ),
                "serial": Command(
                    args="{name|*}",
                    help="Show USB serial number of the cable.",
                    handler=_make_chip_field_handler("serial"),
                ),
                "location": Command(
                    args="{name|*}",
                    help="Show USB topology location.",
                    handler=_make_chip_field_handler("location"),
                ),
                "interface": Command(
                    args="{name|*}",
                    help="Show interface label (multi-channel chips).",
                    handler=_make_chip_field_handler("interface"),
                ),
                "vid_pid": Command(
                    args="{name|*}",
                    help="Show USB VID:PID hex string.",
                    handler=_make_chip_field_handler("vid_pid"),
                ),
                "model": Command(
                    args="{name|*}",
                    help="Show identified chip model from the lookup table.",
                    handler=_make_chip_field_handler("model"),
                ),
                "usb_speed": Command(
                    args="{name|*}",
                    help="Show theoretical USB speed class for the chip.",
                    handler=_make_chip_field_handler("usb_speed"),
                ),
                "negotiated": Command(
                    args="{name|*}",
                    help="Show OS-reported negotiated USB link speed (Linux only).",
                    handler=_make_chip_field_handler("negotiated"),
                ),
                "driver": Command(
                    args="{name|*}",
                    help="Show kernel driver name (Linux only).",
                    handler=_make_chip_field_handler("driver"),
                ),
                "latency_timer": Command(
                    args="{name|*}",
                    help="Show FTDI latency timer value (Linux + FTDI only).",
                    handler=_make_chip_field_handler("latency_timer"),
                ),
                "max_baud": Command(
                    args="{name|*}",
                    help="Show maximum baud rate the chip supports.",
                    handler=_make_chip_field_handler("max_baud"),
                ),
                "permissions": Command(
                    args="{name|*}",
                    help="Show read/write permission status for the device.",
                    handler=_make_chip_field_handler("permissions"),
                ),
                "in_use": Command(
                    args="{name|*}",
                    help="Show whether another process has the port open.",
                    handler=_make_chip_field_handler("in_use"),
                ),
            },
        ),
    },
)
